"""The ambient consult-note surface (plan §3).

`require_doctor` on every route, like `/dictation` and for the same reason: a
note is a doctor's private working memory about a named patient, and a queue
coordinator has no business in it. Scoped by department, not by author — a
colleague covering the room reads the same notes the S9 card already lets them
read.

    POST   /notes/visits/{visit_id}   open a new note and store what was said
    GET    /notes/visits/{visit_id}   every note on this visit, oldest first
    POST   /notes/{id}/map            transcript -> the four fields and tags
    POST   /notes/{id}/compose        open the fields with no model at all
    PATCH  /notes/{id}                the doctor's edits
    POST   /notes/{id}/confirm        "this is what I meant"
    POST   /notes/stt                 audio -> transcript

**There is no verb here that produces anything.** `confirm` is where a reader
looking for the equivalent of `sign` will land, and it is deliberately the
shortest route in the file: it stamps the note and returns it. The prescription
lives on `/dictation/{id}/sign`, on the other surface, behind the formulary
check. See `app.notes`'s module docstring for why that separation is structural
rather than a convention.

`POST /notes/visits/{id}` is not idempotent, unlike its dictation counterpart:
each capture is its own observation. A client that retries a failed upload will
create a second note, which is the right failure — an observation duplicated is
visible and deletable, an observation silently merged into another is neither.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import doctor as doctor_svc
from app import notes as notes_svc
from app.auth.rbac import Principal, require_doctor
from app.config import Settings, get_settings
from app.db import get_session
from app.models.clinical import ClinicalNote
from app.models.enums import Lang, UsagePurpose
from app.providers.metering import usage_scope
from app.providers.registry import llm_chain
from app.routes._stt import SttOut, transcribe_upload

router = APIRouter(prefix="/notes", tags=["notes"])


# -- wire models --------------------------------------------------------------


class SymptomOut(BaseModel):
    name: str
    #: The grade the doctor **said**. Null means unsaid, never mild.
    grade_mentioned: str | None = None


class TagsOut(BaseModel):
    problems: list[str] = []
    symptoms: list[SymptomOut] = []
    followups: list[str] = []


class NoteFieldsOut(BaseModel):
    """The whole contract. Note the absence of a medication field — it is not an
    omission from this schema, there is nothing upstream that could fill it."""

    subjective: str = ""
    objective: str = ""
    assessment: str = ""
    plan_narrative: str = ""
    tags: TagsOut = TagsOut()


class EditOut(BaseModel):
    at: str
    by: str
    field: str


class NoteOut(BaseModel):
    id: uuid.UUID
    visit_id: uuid.UUID
    status: str
    transcript: str | None = None
    #: What the model produced. Frozen — the review shows edits against it.
    mapped: NoteFieldsOut | None = None
    #: What the note says now.
    fields: NoteFieldsOut | None = None
    edits: list[EditOut] = []
    model: str | None = None
    prompt_ref: str | None = None
    mapping_error: str | None = None
    mapped_at: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime


class StartIn(BaseModel):
    transcript: str = Field(default="", max_length=notes_svc.MAX_TRANSCRIPT)


class PatchIn(BaseModel):
    """Whole-field replacement. Only the five fields of the contract."""

    subjective: str | None = None
    objective: str | None = None
    assessment: str | None = None
    plan_narrative: str | None = None
    tags: dict[str, Any] | None = None

    def patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


# -- serialisation ------------------------------------------------------------


def _out(note: ClinicalNote) -> NoteOut:
    structured = note.structured or {}
    return NoteOut(
        id=note.id,
        visit_id=note.visit_id,
        status=str(note.status),
        transcript=note.transcript,
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
        confirmed_at=note.confirmed_at,
        created_at=note.created_at,
    )


async def _doctor(session: AsyncSession, principal: Principal):
    try:
        return await doctor_svc.resolve_doctor(session, user_id=principal.id)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _load(session: AsyncSession, note_id: uuid.UUID, doctor) -> ClinicalNote:
    note = await session.get(ClinicalNote, note_id)
    if note is None or note.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such note")
    try:
        await notes_svc.assert_visit_scope(session, visit_id=note.visit_id, doctor=doctor)
    except notes_svc.NoteError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return note


def _fail(exc: notes_svc.NoteError) -> HTTPException:
    if isinstance(exc, notes_svc.NoteLocked):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, notes_svc.MappingUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


async def _settle(session: AsyncSession) -> None:
    """Commit before the response goes out, so 200 means "it is on the record".

    `get_session` commits too, but a FastAPI dependency with `yield` tears down
    **after** the response has been sent (the behaviour since 0.106; this repo
    runs 0.139). Everywhere else in this codebase that is harmless, because no
    client uses the id from one write to make another without a round trip in
    between. This module is the first that does: the dock captures an
    observation and immediately asks for it to be mapped, and against a live
    stack that second call arrives before the first request's commit has landed
    and 404s on a row that is about to exist.

    Committing here rather than making the client retry a 404, because a
    404-that-resolves-itself is a contract no caller can be written against —
    and for a clinical write, "the server said 200" should already mean the
    words are safe.
    """
    await session.commit()


# -- routes -------------------------------------------------------------------


@router.get("/visits/{visit_id}", response_model=list[NoteOut])
async def read(
    visit_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> list[NoteOut]:
    """Every note on this visit, oldest first — the order they were observed in."""
    doctor = await _doctor(session, principal)
    try:
        rows = await notes_svc.list_for_visit(session, visit_id=visit_id, doctor=doctor)
    except notes_svc.NoteError as exc:
        raise _fail(exc) from exc
    return [_out(note) for note in rows]


@router.post("/visits/{visit_id}", response_model=NoteOut)
async def start(
    visit_id: uuid.UUID,
    body: StartIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> NoteOut:
    """Open a new note on this visit and store what was said."""
    doctor = await _doctor(session, principal)
    try:
        note = await notes_svc.start(
            session, visit_id=visit_id, doctor=doctor, transcript=body.transcript
        )
    except notes_svc.NoteError as exc:
        raise _fail(exc) from exc
    await _settle(session)
    return _out(note)


@router.post("/{note_id}/map", response_model=NoteOut)
async def map_fields(
    note_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> NoteOut:
    """Transcript → four fields and their tags, on whichever LLM is configured."""
    doctor = await _doctor(session, principal)
    note = await _load(session, note_id, doctor)
    mapper = notes_svc.NoteMapper(llm_chain(settings))
    try:
        # Attributed to the visit, like dictation's: the S18 dashboard wants this
        # rupee amount next to the consult it belongs to.
        with usage_scope(visit_id=note.visit_id):
            note = await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=mapper)
    except notes_svc.NoteError as exc:
        raise _fail(exc) from exc
    await _settle(session)
    return _out(note)


@router.post("/{note_id}/compose", response_model=NoteOut)
async def compose(
    note_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> NoteOut:
    """Open the editable fields with no model in the loop."""
    doctor = await _doctor(session, principal)
    note = await _load(session, note_id, doctor)
    try:
        note = await notes_svc.compose(session, note=note, doctor=doctor)
    except notes_svc.NoteError as exc:
        raise _fail(exc) from exc
    await _settle(session)
    return _out(note)


@router.patch("/{note_id}", response_model=NoteOut)
async def correct(
    note_id: uuid.UUID,
    body: PatchIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> NoteOut:
    """The doctor's edits, with an append-only trail."""
    doctor = await _doctor(session, principal)
    note = await _load(session, note_id, doctor)
    try:
        note = await notes_svc.apply_corrections(
            session, note=note, doctor=doctor, patch=body.patch()
        )
    except notes_svc.NoteError as exc:
        raise _fail(exc) from exc
    await _settle(session)
    return _out(note)


@router.post("/{note_id}/confirm", response_model=NoteOut)
async def confirm(
    note_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> NoteOut:
    """Lock the note. Generates nothing, prints nothing, orders nothing."""
    doctor = await _doctor(session, principal)
    note = await _load(session, note_id, doctor)
    try:
        note = await notes_svc.confirm(session, note=note, doctor=doctor)
    except notes_svc.NoteError as exc:
        raise _fail(exc) from exc
    await _settle(session)
    return _out(note)


@router.post("/stt", response_model=SttOut)
async def stt(
    file: UploadFile = File(...),
    lang: Lang = Form(Lang.EN),
    duration_seconds: str | None = Form(default=None),
    principal: Principal = Depends(require_doctor),
    settings: Settings = Depends(get_settings),
) -> SttOut:
    """The accuracy pass behind Web Speech, metered as a note rather than a
    dictation. Same chain, same refusals — see `app.routes._stt`."""
    return await transcribe_upload(
        file,
        lang=lang,
        duration_seconds=duration_seconds,
        settings=settings,
        purpose=UsagePurpose.NOTE,
    )
