"""LocalTTSProvider + VoiceboxTTSProvider — the voice of V-OSS (doc 08 §1/§2).

Two adapters onto the same open-source TTS story, both speaking the cloned
"Dhara" voice so a patient hears one identity across every channel (doc 08 §1):

- **LocalTTSProvider** — the live path. The bake-off winner (Qwen3-TTS /
  Chatterbox / IndicF5 / Indic Parler-TTS, decided in S-OSS.1) served from a
  dedicated streaming service on the GPU box. Here it is a plain
  `POST /tts → {"audio": "<base64 wav>"}` request; the sentence-chunk *streaming*
  path (crossfade, first-audio ≤0.7s) is the S-OSS.2 realtime wiring — this
  request/response form is what the kiosk, WhatsApp voice-note and V3-pack
  callers need, and what tests can drive.
- **VoiceboxTTSProvider** — Voicebox's REST API (doc 08 §1), for *batch*
  regeneration of the V3 pre-recorded packs from the cloned voice (S-OSS.3, folds
  into S21) and as a low-concurrency fallback host for after-hours calls.

Both bill **per character** like every other TTS (doc 02 §4: vendors bill on
submitted characters, so we count the text we send, before synthesis) and price
against the amortized `local-tts` / `voicebox` rows — a local voice is not free,
it is GPU time, and the dashboard should say so.

Open Indic TTS naturalness is the headline quality risk (doc 08 §7): if Marathi
or Telugu fail MOS in the bake-off, route *those languages* to cloud TTS with one
`TTS_FALLBACK_PROVIDER`/per-language config line while keeping local STT+LLM.
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from app.providers.audio import AudioClip, bcp47
from app.providers.base import ProviderBadRequest, ProviderUnavailable
from app.providers.metering import MeterCall, UsageDelta
from app.providers.tts import Speech, TTSProvider


class LocalTTSProvider(TTSProvider):
    """The local streaming TTS service (bake-off winner). Config-only, keyless."""

    name: ClassVar[str] = "local-tts"
    #: Placeholder until S-OSS.1 picks the winner; the `local-tts` price row is a
    #: `*` wildcard so any chosen model prices at the same amortized rate.
    model: ClassVar[str] = "oss-tts"
    DEFAULT_VOICE: ClassVar[str] = "dhara_hi_v1"

    def __init__(
        self,
        *,
        base_url: str,
        model: str | None = None,
        voice: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(base_url), **kwargs)
        if model:
            self.model = model
        self._voice = voice or self.DEFAULT_VOICE
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds)

    async def _synthesize(
        self, text: str, lang: str, voice: str | None, sample_rate: int, call: MeterCall
    ) -> Speech:
        call.usage = UsageDelta(characters=len(text))
        payload = {
            "text": text,
            "voice": voice or self._voice,
            "language": bcp47(lang),
            "sample_rate": sample_rate,
            "model": self.model,
        }
        try:
            response = await self._client.post("/tts", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"local-tts transport error: {exc}") from exc

        if response.status_code in (400, 422):
            raise ProviderBadRequest(f"local-tts rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"local-tts http {response.status_code}: {response.text[:200]}"
            )

        audio = response.json().get("audio")
        if not audio:
            raise ProviderUnavailable("local-tts returned no audio")

        return Speech(
            audio=AudioClip.from_b64(audio, mime="audio/wav", sample_rate=sample_rate),
            provider=self.name,
            voice=voice or self._voice,
        )


class VoiceboxTTSProvider(TTSProvider):
    """Voicebox REST API — batch V3-pack generation + low-concurrency fallback."""

    name: ClassVar[str] = "voicebox"
    model: ClassVar[str] = "voicebox"
    DEFAULT_VOICE: ClassVar[str] = "dhara_hi_v1"

    #: Batch generation is not on a live turn's critical path, so it tolerates a
    #: slower studio server than the live TTS does.
    timeout_seconds: ClassVar[float] = 30.0

    def __init__(
        self,
        *,
        base_url: str,
        voice: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(base_url), **kwargs)
        self._voice = voice or self.DEFAULT_VOICE
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds)

    async def _synthesize(
        self, text: str, lang: str, voice: str | None, sample_rate: int, call: MeterCall
    ) -> Speech:
        call.usage = UsageDelta(characters=len(text))
        payload = {
            "text": text,
            "voice": voice or self._voice,
            "language": bcp47(lang),
            "sample_rate": sample_rate,
        }
        try:
            response = await self._client.post("/api/tts", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"voicebox transport error: {exc}") from exc

        if response.status_code in (400, 422):
            raise ProviderBadRequest(f"voicebox rejected the request: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"voicebox http {response.status_code}: {response.text[:200]}"
            )

        audio = response.json().get("audio")
        if not audio:
            raise ProviderUnavailable("voicebox returned no audio")

        return Speech(
            audio=AudioClip.from_b64(audio, mime="audio/wav", sample_rate=sample_rate),
            provider=self.name,
            voice=voice or self._voice,
        )
