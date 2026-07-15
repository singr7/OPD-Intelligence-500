"""TTSProvider — text to speech (doc 02 §2: Sarvam Bulbul → Google fallback).

The last hop of voice tier V2, the voice-note reply on WhatsApp (S12), and the
generator for V3's placeholder voice packs (S7) until a human voice artist
records the real ones (S21).

**Billed per character, not per second** — both vendors. That is why `UsageDelta`
and `usage_events` carry `characters`, and why `PriceUnit.CHAR` exists (see
`app.models.enums.PriceUnit`). Metering the output duration instead would have
been an estimate that never reconciles against an invoice.

Characters are counted on the text *we send*, before synthesis, so a failed call
still meters what it cost us. Vendors bill on submitted characters too.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import ClassVar

import httpx

from app.models.enums import UsagePurpose
from app.providers.audio import AudioClip, bcp47
from app.providers.base import Provider, ProviderBadRequest, ProviderUnavailable
from app.providers.metering import MeterCall, UsageDelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Speech:
    audio: AudioClip
    provider: str
    voice: str


class TTSProvider(Provider):
    kind: ClassVar[str] = "tts"
    model: ClassVar[str] = ""
    # doc 02 §5 budgets TTS ≤1.0s inside a 3.5s V2 turn.
    timeout_seconds: ClassVar[float] = 8.0

    async def synthesize(
        self,
        text: str,
        lang: str,
        *,
        voice: str | None = None,
        sample_rate: int = 8000,
        purpose: UsagePurpose = UsagePurpose.INTAKE_TURN,
    ) -> Speech:
        if not text.strip():
            # Cheaper to reject than to pay a vendor to synthesise nothing, and
            # it surfaces the upstream bug that produced an empty prompt.
            raise ProviderBadRequest("refusing to synthesize empty text")
        return await self._invoke(
            purpose,
            lambda call: self._synthesize(text, lang, voice, sample_rate, call),
            model=self.model,
        )

    @abstractmethod
    async def _synthesize(
        self, text: str, lang: str, voice: str | None, sample_rate: int, call: MeterCall
    ) -> Speech:
        """Synthesize one utterance; report `characters` on `call`."""


class FakeTTSProvider(TTSProvider):
    """Deterministic TTS. Produces silence of a plausible length.

    Length tracks the text (~14 chars/second of Hindi speech) so tests that
    assert on turn timing or audio-pack duration get a number that moves in the
    right direction, without a synthesiser.
    """

    name: ClassVar[str] = "fake-tts"
    model: ClassVar[str] = "fake-tts-1"

    CHARS_PER_SECOND: ClassVar[int] = 14

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spoken: list[tuple[str, str]] = []
        self.fail_with: Exception | None = None

    async def _synthesize(
        self, text: str, lang: str, voice: str | None, sample_rate: int, call: MeterCall
    ) -> Speech:
        self.spoken.append((text, lang))
        if self.fail_with is not None:
            raise self.fail_with
        seconds = max(len(text) / self.CHARS_PER_SECOND, 0.5)
        samples = int(seconds * sample_rate)
        call.usage = UsageDelta(characters=len(text))
        return Speech(
            audio=AudioClip(data=b"\x00\x00" * samples, sample_rate=sample_rate),
            provider=self.name,
            voice=voice or "fake-voice",
        )

    @property
    def last(self) -> tuple[str, str] | None:
        return self.spoken[-1] if self.spoken else None


class SarvamTTSProvider(TTSProvider):
    """Sarvam Bulbul — the primary (doc 02 §2).

    Wire notes: `{"inputs": [text], "target_language_code", "speaker", "model"}`,
    response `{"audios": ["<base64 wav>"]}`. Speaker names are model-specific and
    the good Hindi ones change between Bulbul versions — pinning `model` and
    `speaker` together in config is deliberate, not redundant.
    """

    name: ClassVar[str] = "sarvam"
    model: ClassVar[str] = "bulbul:v2"

    BASE_URL: ClassVar[str] = "https://api.sarvam.ai"
    DEFAULT_VOICE: ClassVar[str] = "anushka"

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        voice: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(api_key), **kwargs)
        self._api_key = api_key
        if model:
            self.model = model
        self._voice = voice or self.DEFAULT_VOICE
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=self.timeout_seconds
        )

    async def _synthesize(
        self, text: str, lang: str, voice: str | None, sample_rate: int, call: MeterCall
    ) -> Speech:
        call.usage = UsageDelta(characters=len(text))
        payload = {
            "inputs": [text],
            "target_language_code": bcp47(lang),
            "speaker": voice or self._voice,
            "model": self.model,
            "speech_sample_rate": sample_rate,
        }
        try:
            response = await self._client.post(
                "/text-to-speech",
                json=payload,
                headers={"api-subscription-key": self._api_key},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"sarvam tts transport error: {exc}") from exc

        if response.status_code in (400, 422):
            raise ProviderBadRequest(f"sarvam tts rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"sarvam tts http {response.status_code}: {response.text[:200]}"
            )

        audios = response.json().get("audios") or []
        if not audios:
            raise ProviderUnavailable("sarvam tts returned no audio")

        return Speech(
            audio=AudioClip.from_b64(audios[0], mime="audio/wav", sample_rate=sample_rate),
            provider=self.name,
            voice=voice or self._voice,
        )


class GoogleTTSProvider(TTSProvider):
    """Google Cloud TTS `text:synthesize` — the secondary (doc 02 §2)."""

    name: ClassVar[str] = "google"
    model: ClassVar[str] = "standard"

    BASE_URL: ClassVar[str] = "https://texttospeech.googleapis.com/v1"

    def __init__(
        self,
        *,
        api_key: str,
        voice: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(api_key), **kwargs)
        self._api_key = api_key
        self._voice = voice
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=self.timeout_seconds
        )

    async def _synthesize(
        self, text: str, lang: str, voice: str | None, sample_rate: int, call: MeterCall
    ) -> Speech:
        call.usage = UsageDelta(characters=len(text))
        language_code = bcp47(lang)
        voice_config: dict[str, object] = {"languageCode": language_code}
        # Google picks a voice from the language alone; naming one pins the timbre
        # so a patient does not hear a different assistant on the fallback path.
        if chosen := (voice or self._voice):
            voice_config["name"] = chosen

        payload = {
            "input": {"text": text},
            "voice": voice_config,
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": sample_rate},
        }
        try:
            response = await self._client.post(
                "/text:synthesize", json=payload, params={"key": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"google tts transport error: {exc}") from exc

        if response.status_code == 400:
            raise ProviderBadRequest(f"google tts rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"google tts http {response.status_code}: {response.text[:200]}"
            )

        content = response.json().get("audioContent")
        if not content:
            raise ProviderUnavailable("google tts returned no audio")

        return Speech(
            audio=AudioClip.from_b64(content, mime="audio/wav", sample_rate=sample_rate),
            provider=self.name,
            voice=voice or self._voice or f"{language_code}-default",
        )
