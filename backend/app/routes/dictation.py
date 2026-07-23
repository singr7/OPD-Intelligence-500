"""The dictation surface (doc 03 §7).

Every route is `require_doctor` — tighter than the coordinator's
`require_staff`, for the same reason the S9 card is: this is a patient's
diagnosis and their prescription, and a queue coordinator has no business in it.
And unlike `/kiosk/stt`, `/dictation/stt` is authenticated: a kiosk clip is an
anonymous chief complaint from a public terminal, while this clip is a named
patient's consult.

The verbs are deliberately separate rather than one save-everything endpoint:

    POST   /dictation/visits/{visit_id}      open the draft, store the transcript
    POST   /dictation/{id}/map               transcript -> structured fields
    PATCH  /dictation/{id}                   the doctor's corrections
    POST   /dictation/{id}/sign              lock it
    GET    /dictation/visits/{visit_id}      read it back
    POST   /dictation/stt                    audio -> transcript (Web Speech fallback)

Splitting map from save is what lets a failed mapping keep the transcript: the
recording is the part that cannot be recreated once the doctor has moved to the
next patient.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import dictation as dictation_svc
from app import doctor as doctor_svc
from app.auth.rbac import Principal, require_doctor
from app.config import Settings, get_settings
from app.db import get_session
from app.models.clinical import Dictation
from app.models.enums import Lang, UsagePurpose
from app.providers import AudioClip, ProviderBadRequest, ProviderError, with_fallback
from app.providers.metering import usage_scope
from app.providers.registry import llm_chain, stt_chain

router = APIRouter(prefix="/dictation", tags=["dictation"])

#: A consult note is a minute or two of speech, not a lecture. Generous enough
#: for a long oncology plan, small enough that a stuck recorder cannot post a
#: gigabyte at the box's Whisper.
_MAX_STT_BYTES = 24 * 1024 * 1024


# -- wire models --------------------------------------------------------------


class SuggestionOut(BaseModel):
    name: str
    generic: str
    score: float


class MedOut(BaseModel):
    #: Exactly what the doctor said. Never a formulary name (doc 03 §7).
    name: str
    dose: str | None = None
    route: str | None = None
    freq: str | None = None
    duration: str | None = None
    as_spoken: str = ""
    known: bool = False
    generic: str | None = None
    drug_class: str | None = None
    ambiguous: bool = False
    suggestions: list[SuggestionOut] = []
    #: The name is not in the transcript — the model renamed or invented it.
    unsaid: bool = False
    acknowledged: bool = False


class TreatmentEventOut(BaseModel):
    cycle: int | None = None
    regimen: str = ""
    date: str | None = None
    next_due: str | None = None
    as_spoken: str = ""


class FollowUpOut(BaseModel):
    when: str | None = None
    as_spoken: str = ""
    instructions: str = ""


class MappingOut(BaseModel):
    diagnosis: str | None = None
    treatment_events: list[TreatmentEventOut] = []
    meds: list[MedOut] = []
    advice: list[str] = []
    follow_up: FollowUpOut = FollowUpOut()
    unclear: list[str] = []


class EditOut(BaseModel):
    at: str
    by: str
    field: str


class DictationOut(BaseModel):
    id: uuid.UUID
    visit_id: uuid.UUID
    status: str
    transcript: str | None = None
    #: What the model produced. Frozen — the review screen diffs against it.
    mapped: MappingOut | None = None
    #: What the record says now, after the doctor's corrections.
    fields: MappingOut | None = None
    edits: list[EditOut] = []
    model: str | None = None
    prompt_ref: str | None = None
    mapping_error: str | None = None
    mapped_at: str | None = None
    signed_at: datetime | None = None
    #: Flagged drugs the doctor has not yet acknowledged — unrecognised, or
    #: named something they are not recorded as having said. The exact list
    #: `sign` will refuse on.
    blocking_meds: list[str] = []


class StartIn(BaseModel):
    transcript: str = Field(default="", max_length=20_000)


class PatchIn(BaseModel):
    """Whole-field replacement; only the doc 03 §7 fields are editable."""

    diagnosis: str | None = None
    treatment_events: list[dict[str, Any]] | None = None
    meds: list[dict[str, Any]] | None = None
    advice: list[str] | None = None
    follow_up: dict[str, Any] | None = None
    unclear: list[str] | None = None

    def patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class SttOut(BaseModel):
    text: str
    provider: str
    lang: str
    confidence: float | None = None
    uncertain: bool = False


# -- serialisation ------------------------------------------------------------


def _out(dictation: Dictation) -> DictationOut:
    structured = dictation.structured or {}
    mapping = dictation_svc.current_mapping(dictation)
    blocking = [m.name for m in mapping.meds_needing_attention] if mapping else []
    return DictationOut(
        id=dictation.id,
        visit_id=dictation.visit_id,
        status=str(dictation.status),
        transcript=dictation.transcript,
        mapped=structured.get("mapped"),
        fields=structured.get("fields"),
        edits=[
            EditOut(at=str(e.get("at")), by=str(e.get("by")), field=str(e.get("field")))
            for e in structured.get("edits") or []
        ],
        model=structured.get("model"),
        prompt_ref=structured.get("prompt_ref"),
        mapping_error=structured.get("mapping_error"),
        mapped_at=structured.get("mapped_at"),
        signed_at=dictation.signed_at,
        blocking_meds=blocking,
    )


async def _doctor(session: AsyncSession, principal: Principal):
    try:
        return await doctor_svc.resolve_doctor(session, user_id=principal.id)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _load(session: AsyncSession, dictation_id: uuid.UUID, doctor) -> Dictation:
    dictation = await session.get(Dictation, dictation_id)
    if dictation is None or dictation.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such dictation")
    # Scope by the visit, not by `doctor_id`: a colleague covering the room reads
    # the same department's notes, and the S9 card already draws that boundary.
    try:
        await dictation_svc.assert_visit_scope(session, visit_id=dictation.visit_id, doctor=doctor)
    except dictation_svc.DictationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return dictation


def _fail(exc: dictation_svc.DictationError) -> HTTPException:
    if isinstance(exc, dictation_svc.DictationLocked):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, dictation_svc.MappingUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# -- routes -------------------------------------------------------------------


@router.get("/visits/{visit_id}", response_model=DictationOut | None)
async def read(
    visit_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> DictationOut | None:
    """This visit's consult note, or null if none has been started."""
    doctor = await _doctor(session, principal)
    try:
        dictation = await dictation_svc.get_draft(session, visit_id=visit_id, doctor=doctor)
    except dictation_svc.DictationError as exc:
        raise _fail(exc) from exc
    return _out(dictation) if dictation else None


@router.post("/visits/{visit_id}", response_model=DictationOut)
async def start(
    visit_id: uuid.UUID,
    body: StartIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> DictationOut:
    """Open the draft and store the transcript. Idempotent per visit."""
    doctor = await _doctor(session, principal)
    try:
        dictation = await dictation_svc.start(
            session, visit_id=visit_id, doctor=doctor, transcript=body.transcript
        )
    except dictation_svc.DictationError as exc:
        raise _fail(exc) from exc
    return _out(dictation)


@router.post("/{dictation_id}/map", response_model=DictationOut)
async def map_fields(
    dictation_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DictationOut:
    """Transcript → structured fields, on whichever LLM is configured.

    Cloud Gemini Flash or the box's own Qwen3 (`LLM_PROVIDER=local_vllm`) — same
    code, same prompt, same formulary validation afterwards.
    """
    doctor = await _doctor(session, principal)
    dictation = await _load(session, dictation_id, doctor)
    mapper = dictation_svc.DictationMapper(llm_chain(settings))
    try:
        # Attributed to the visit, not to a channel: dictation is not a patient
        # channel, and the S18 dashboard wants this rupee amount next to the
        # intake it belongs to.
        with usage_scope(visit_id=dictation.visit_id):
            dictation = await dictation_svc.map_transcript(
                session, dictation=dictation, doctor=doctor, mapper=mapper
            )
    except dictation_svc.DictationError as exc:
        raise _fail(exc) from exc
    return _out(dictation)


@router.patch("/{dictation_id}", response_model=DictationOut)
async def correct(
    dictation_id: uuid.UUID,
    body: PatchIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> DictationOut:
    """The doctor's tap-to-fix. Re-validates any drug name they typed."""
    doctor = await _doctor(session, principal)
    dictation = await _load(session, dictation_id, doctor)
    try:
        dictation = await dictation_svc.apply_corrections(
            session, dictation=dictation, doctor=doctor, patch=body.patch()
        )
    except dictation_svc.DictationError as exc:
        raise _fail(exc) from exc
    return _out(dictation)


@router.post("/{dictation_id}/sign", response_model=DictationOut)
async def sign(
    dictation_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> DictationOut:
    """Sign and lock. 400 while an unrecognised drug is unacknowledged."""
    doctor = await _doctor(session, principal)
    dictation = await _load(session, dictation_id, doctor)
    try:
        dictation = await dictation_svc.sign(session, dictation=dictation, doctor=doctor)
    except dictation_svc.DictationError as exc:
        raise _fail(exc) from exc
    return _out(dictation)


@router.post("/stt", response_model=SttOut)
async def stt(
    file: UploadFile = File(...),
    lang: Lang = Form(Lang.EN),
    duration_seconds: str | None = Form(default=None),
    principal: Principal = Depends(require_doctor),
    settings: Settings = Depends(get_settings),
) -> SttOut:
    """The accuracy pass behind Web Speech (doc 03 §7).

    Chrome's Web Speech API is the fast path in the console, but it ships the
    doctor's voice to a cloud recogniser and it is poor at Hinglish drug names.
    This route is the fallback and, on a V-OSS box, the better one: the
    configured chain is local Whisper, so the consult never leaves the premises.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="empty audio upload")
    if len(data) > _MAX_STT_BYTES:
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
                lambda p: p.transcribe(clip, str(lang), purpose=UsagePurpose.DICTATION),
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
