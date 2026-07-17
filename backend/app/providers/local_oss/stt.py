"""LocalSTTProvider — Whisper on the GPU box (doc 08 §2).

Whisper large-v3-turbo (int8) served by a faster-whisper worker pool behind an
**OpenAI-audio-compatible** `/v1/audio/transcriptions` endpoint — the same shape
`faster-whisper-server` and whisper.cpp's server expose, so this is a multipart
upload and a `{"text": ...}` reply, no bespoke protocol.

Two honesty points carried straight from the cloud STT impls:

- **Confidence is `None`, not 1.0.** Plain Whisper does not return a calibrated
  per-utterance confidence, so — exactly like Sarvam (`app.providers.stt`) — this
  reports `None` rather than inventing certainty. Doc 03 §4's `[unclear: ...]`
  contract leans on a provider that *does* report one (Google, or the S-OSS.1
  IndicConformer eval) when a transcript has to be trusted.
- **Metered even on failure.** `audio_seconds` is billed against the amortized
  `local-whisper` price rows before the status check — a rejected upload still
  spent GPU seconds, and a cost dashboard that hides them understates a bad day.

Telephony-band (8 kHz) Indic audio is the known quality risk (doc 08 §7); the
resample+denoise front-end lives in the STT service on the box, and the WER
bench that decides Whisper-vs-IndicConformer is S-OSS.1, not this adapter.
"""

from __future__ import annotations

from typing import ClassVar

import httpx

from app.providers.audio import AudioClip, bcp47
from app.providers.base import ProviderBadRequest, ProviderUnavailable
from app.providers.metering import MeterCall, UsageDelta
from app.providers.stt import STTProvider, Transcript


class LocalSTTProvider(STTProvider):
    """A local Whisper server (OpenAI-audio-compatible). Config-only, keyless."""

    name: ClassVar[str] = "local-whisper"
    model: ClassVar[str] = "whisper-large-v3-turbo"

    def __init__(
        self,
        *,
        base_url: str,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(configured=bool(base_url), **kwargs)
        if model:
            self.model = model
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=self.timeout_seconds)

    async def _transcribe(self, clip: AudioClip, lang: str, call: MeterCall) -> Transcript:
        try:
            response = await self._client.post(
                "/v1/audio/transcriptions",
                files={"file": ("audio.wav", clip.data, "audio/wav")},
                data={"model": self.model, "language": bcp47(lang).split("-")[0]},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"local-whisper transport error: {exc}") from exc

        # Metered before the status check: the GPU already ran on those bytes.
        call.usage = UsageDelta(audio_seconds=clip.duration())

        if response.status_code in (400, 422):
            raise ProviderBadRequest(f"local-whisper rejected the clip: {response.text[:200]}")
        if response.status_code >= 300:
            raise ProviderUnavailable(
                f"local-whisper http {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        return Transcript(
            text=body.get("text", ""),
            lang=body.get("language", lang),
            provider=self.name,
            confidence=None,  # Whisper gives none — do not fabricate one
        )
