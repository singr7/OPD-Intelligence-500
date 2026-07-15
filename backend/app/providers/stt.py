"""STTProvider — speech to text (doc 02 §2: Sarvam Saarika → Google fallback).

Serves voice tier V2's first hop, WhatsApp voice notes (S12), the doctor's
dictation accuracy pass (S10), and the kiosk's server-STT toggle (S6).

Latency budget is the design constraint: doc 02 §5 gives STT ≤1.2s of a <3.5s p90
V2 turn. That is why `timeout_seconds` is short here — a slow transcription is
worth less than falling to the next provider, and much less than a patient
hearing dead air.

**Confidence is load-bearing, not decoration.** Doc 03 §4 requires the summary to
mark uncertain words `[unclear: ...]` rather than guess, and a drug name guessed
confidently in an oncology OPD is how someone gets the wrong medicine. So
`Transcript` carries confidence and the impls report what the vendor gives us —
never a filled-in 1.0.
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
class Transcript:
    text: str
    lang: str
    provider: str
    #: 0.0-1.0, or None when the vendor did not say. None means "unknown", and
    #: callers must treat it as such — not as 1.0.
    confidence: float | None = None

    @property
    def is_uncertain(self) -> bool:
        """Below this, S5's summarizer marks the span `[unclear: ...]`.

        0.6 is a starting point, not a finding: it wants tuning against the S13
        language QA harness on real Alwar-accented telephony audio.
        """
        return self.confidence is not None and self.confidence < 0.6


class STTProvider(Provider):
    kind: ClassVar[str] = "stt"
    model: ClassVar[str] = ""
    # Tight: doc 02 §5 budgets STT ≤1.2s inside a 3.5s V2 turn. Failing over
    # beats waiting.
    timeout_seconds: ClassVar[float] = 8.0

    async def transcribe(
        self, clip: AudioClip, lang: str, *, purpose: UsagePurpose = UsagePurpose.INTAKE_TURN
    ) -> Transcript:
        return await self._invoke(
            purpose, lambda call: self._transcribe(clip, lang, call), model=self.model
        )

    @abstractmethod
    async def _transcribe(self, clip: AudioClip, lang: str, call: MeterCall) -> Transcript:
        """Transcribe one clip; report `audio_seconds` on `call`."""


class FakeSTTProvider(STTProvider):
    """Deterministic STT. Returns queued transcripts, or a fixed string.

    Tests for the tree walker and intake engine (S4/S5) drive real conversations
    through this, so `queue()` takes the patient's side of the script.
    """

    name: ClassVar[str] = "fake-stt"
    model: ClassVar[str] = "fake-stt-1"

    def __init__(
        self, *, script: list[str] | None = None, confidence: float = 0.95, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.clips: list[AudioClip] = []
        self._script: list[str] = list(script or [])
        self._confidence = confidence
        self.fail_with: Exception | None = None

    def queue(self, *utterances: str) -> None:
        self._script.extend(utterances)

    async def _transcribe(self, clip: AudioClip, lang: str, call: MeterCall) -> Transcript:
        self.clips.append(clip)
        if self.fail_with is not None:
            raise self.fail_with
        text = self._script.pop(0) if self._script else "haan"
        # A fake with no duration would meter (and price) at zero and quietly
        # break the "every fake call is priced" AC, so assume a second of audio.
        call.usage = UsageDelta(audio_seconds=clip.duration() or 1)
        return Transcript(text=text, lang=lang, provider=self.name, confidence=self._confidence)


class SarvamSTTProvider(STTProvider):
    """Sarvam Saarika — the primary (doc 02 §2: telephony-tuned, cheap, Indic).

    Wire notes: multipart upload, `api-subscription-key` header, and the response
    is `{"transcript": ..., "language_code": ...}` with **no confidence field** —
    hence `confidence=None`, honestly, rather than a fabricated 1.0. Getting a
    real confidence signal out of Saarika is open work (see HANDOFF backlog);
    until then S5 leans on Google's when accuracy matters.
    """

    name: ClassVar[str] = "sarvam"
    model: ClassVar[str] = "saarika:v2.5"

    BASE_URL: ClassVar[str] = "https://api.sarvam.ai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(api_key), **kwargs)
        self._api_key = api_key
        if model:
            self.model = model
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=self.timeout_seconds
        )

    async def _transcribe(self, clip: AudioClip, lang: str, call: MeterCall) -> Transcript:
        try:
            response = await self._client.post(
                "/speech-to-text",
                headers={"api-subscription-key": self._api_key},
                files={"file": ("audio.wav", clip.data, "audio/wav")},
                data={"model": self.model, "language_code": bcp47(lang)},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"sarvam stt transport error: {exc}") from exc

        # Metered before the status check: a rejected upload still shipped the bytes.
        call.usage = UsageDelta(audio_seconds=clip.duration())

        if response.status_code in (400, 422):
            raise ProviderBadRequest(f"sarvam stt rejected the clip: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"sarvam stt http {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        return Transcript(
            text=body.get("transcript", ""),
            lang=body.get("language_code", lang),
            provider=self.name,
            confidence=None,  # not reported by this API — do not invent one
        )


class GoogleSTTProvider(STTProvider):
    """Google Speech-to-Text v1 `speech:recognize` — the secondary (doc 02 §2).

    Slower and pricier than Sarvam on Indic audio, but it reports per-result
    confidence, which is what doc 03 §4's `[unclear: ...]` contract needs. So it
    is the fallback on availability *and* the escalation path when a transcript
    matters more than a rupee (dictation, S10).
    """

    name: ClassVar[str] = "google"
    model: ClassVar[str] = "latest_long"

    BASE_URL: ClassVar[str] = "https://speech.googleapis.com/v1"

    def __init__(
        self,
        *,
        api_key: str,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(api_key), **kwargs)
        self._api_key = api_key
        if model:
            self.model = model
        self._client = client or httpx.AsyncClient(
            base_url=self.BASE_URL, timeout=self.timeout_seconds
        )

    async def _transcribe(self, clip: AudioClip, lang: str, call: MeterCall) -> Transcript:
        payload = {
            "config": {
                "encoding": "LINEAR16",
                "sampleRateHertz": clip.sample_rate,
                "languageCode": bcp47(lang),
                "model": self.model,
                "enableAutomaticPunctuation": True,
            },
            "audio": {"content": clip.b64()},
        }
        try:
            response = await self._client.post(
                "/speech:recognize", json=payload, params={"key": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"google stt transport error: {exc}") from exc

        call.usage = UsageDelta(audio_seconds=clip.duration())

        if response.status_code == 400:
            raise ProviderBadRequest(f"google stt rejected the clip: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"google stt http {response.status_code}: {response.text[:200]}"
            )

        results = response.json().get("results") or []
        if not results:
            # Silence, or nothing recognised. An empty transcript is a fact, not
            # a fault: the intake engine re-prompts rather than falling over.
            return Transcript(text="", lang=lang, provider=self.name, confidence=0.0)

        alternative = (results[0].get("alternatives") or [{}])[0]
        return Transcript(
            text=alternative.get("transcript", ""),
            lang=lang,
            provider=self.name,
            confidence=alternative.get("confidence"),
        )
