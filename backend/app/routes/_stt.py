"""The staff-authenticated speech pass, shared by the two surfaces that record.

Extracted in M4, when the ambient note became the second doctor-facing recorder.
The alternative was for `/notes/stt` to call `/dictation/stt`, which would have
meant an observation was metered as dictation spend — and `analytics._per_dictation`
divides that spend by the count of *signed dictations*, so cost-per-prescription
would have drifted by however many observations a doctor happened to mutter.
One implementation, two `UsagePurpose` values, is the shape that keeps both
numbers reconcilable against an invoice.

Nothing about the transcription itself differs between the two: same chain, same
size ceiling, same refusals. On a V-OSS box that chain is local Whisper and the
consult never leaves the premises, which matters more for an ambient note than
it does anywhere else in this system — it is the doctor thinking aloud.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel

from app.config import Settings
from app.models.enums import Lang, UsagePurpose
from app.providers import AudioClip, ProviderBadRequest, ProviderError, with_fallback
from app.providers.metering import usage_scope
from app.providers.registry import stt_chain

#: A consult note is a minute or two of speech, not a lecture. Generous enough
#: for a long oncology plan, small enough that a stuck recorder cannot post a
#: gigabyte at the box's Whisper.
MAX_STT_BYTES = 24 * 1024 * 1024


class SttOut(BaseModel):
    text: str
    provider: str
    lang: str
    confidence: float | None = None
    uncertain: bool = False


async def transcribe_upload(
    file: UploadFile,
    *,
    lang: Lang,
    duration_seconds: str | None,
    settings: Settings,
    purpose: UsagePurpose,
) -> SttOut:
    """Audio → text on the configured chain, metered under `purpose`."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty audio upload")
    if len(data) > MAX_STT_BYTES:
        raise HTTPException(status_code=413, detail="recording too large")

    duration: Decimal | None = None
    if duration_seconds:
        try:
            duration = Decimal(duration_seconds)
        except (InvalidOperation, ValueError):
            duration = None

    clip = AudioClip(data=data, mime=file.content_type or "audio/webm", duration_seconds=duration)
    try:
        with usage_scope():
            transcript = await with_fallback(
                stt_chain(settings),
                lambda p: p.transcribe(clip, str(lang), purpose=purpose),
            )
    except ProviderBadRequest as exc:
        raise HTTPException(status_code=422, detail=f"could not read that audio: {exc}") from exc
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail="speech recognition is unavailable") from exc

    return SttOut(
        text=transcript.text,
        provider=transcript.provider,
        lang=transcript.lang,
        confidence=transcript.confidence,
        uncertain=transcript.is_uncertain,
    )
