"""The doctor console's HTTP surface (doc 03 §5).

Two reads and two writes. The console's *queue* actions are the S8 verbs it
already shares with the coordinator — `POST /queue/call-next` and
`POST /queue/entries/{id}/state` — so there is no `/doctor/call-next` here. One
implementation of the queue state machine, one audit trail, one order on the
board; a doctor-flavoured copy would drift from the coordinator's within a
session or two.

The writes are `POST /doctor/visits/{id}/take` and `POST
/doctor/visits/{id}/conclude`, and both exist because what they do is not a queue
transition. `take` changes who the visit belongs to, not where it sits in the
line; routing it through the coordinator's assign endpoint would mean handing
every doctor `require_staff` and a department picker to do the one thing they
actually need — put their own name on the patient in front of them. `conclude`
records *how* the consult ended, including the two ways that leave this system
with no prescription in it, and then moves the queue through the S8 verb.

SESSION-ALLERGY adds three more writes, all under `/visits/{id}/allergies`, for
the same reason `take` and `conclude` are here: what they record is a clinical
fact about a patient, not a position in a line. They are scoped under the visit
so the department check that guards every read of this patient guards them too.

Every route is `require_doctor` (doctor or admin), a tighter guard than the
coordinator's `require_staff`: this is the one surface that returns a patient's
name, phone, answers and history together, which is more than a queue
coordinator needs to move a line.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import allergies as allergy_svc
from app import doctor as doctor_svc
from app.auth.rbac import Principal, require_doctor
from app.db import get_session
from app.models.enums import AllergySeverity, RxMode
from app.queue_hub import QueueHub
from app.routes.queue import get_hub

router = APIRouter(prefix="/doctor", tags=["doctor"])


# -- wire models --------------------------------------------------------------


class DayRowOut(BaseModel):
    entry_id: uuid.UUID
    visit_id: uuid.UUID
    token_no: int
    state: str
    priority: str
    priority_reason: str | None = None
    patient_name: str
    patient_age: int | None = None
    patient_sex: str | None = None
    chief_complaint: str | None = None
    red_flag_count: int
    called_at: datetime | None = None
    assigned_doctor_id: uuid.UUID | None = None
    assigned_doctor_name: str | None = None
    is_mine: bool = False


class DayCountsOut(BaseModel):
    mine: int
    unassigned: int
    department: int
    unassigned_waiting: int
    waiting: int


class DayOut(BaseModel):
    doctor_name: str
    doctor_id: uuid.UUID
    department_key: str
    department_name: str
    date: date_type
    scope: str
    counts: DayCountsOut
    rows: list[DayRowOut]


class RedFlagOut(BaseModel):
    id: str
    severity: str
    label: str
    instruction: str
    source_node: str | None = None


class AnswerOut(BaseModel):
    node_id: str
    question: str
    answer: str
    said: str | None = None
    flagged: bool


class TimelineOut(BaseModel):
    visit_id: uuid.UUID
    date: date_type
    department_name: str
    status: str
    token_no: int | None = None
    chief_complaint: str | None = None
    is_current: bool


class TrendPointOut(BaseModel):
    at: datetime
    value: float


class TrendOut(BaseModel):
    symptom: str
    points: list[TrendPointOut]


class SummaryOut(BaseModel):
    chief_concern: str | None = None
    hpi: list[str] = []
    symptoms: list[dict[str, str]] = []
    history_meds: list[str] = []
    since_last_visit: list[str] = []
    patient_words: dict[str, str] = {}
    unclear: list[str] = []


class DiagnosisOut(BaseModel):
    text: str
    on: date_type
    is_current_visit: bool


class AllergyEntryOut(BaseModel):
    id: uuid.UUID
    kind: str
    substance: str | None = None
    substance_en: str | None = None
    reaction: str | None = None
    severity: str
    source: str
    stated_at: datetime
    confirmed_at: datetime | None = None
    confirmed_by_name: str | None = None
    recorded_by_name: str | None = None
    retracted_at: datetime | None = None
    retracted_by_name: str | None = None
    retracted_reason: str | None = None


class AllergyViewOut(BaseModel):
    """The three states, on the wire, with `state` as the only thing to branch on.

    Note there is no `has_allergies` boolean and there must never be one: a
    two-valued field forces "nobody asked" and "asked, told none" into the same
    bucket, and the whole module exists to keep them apart.
    """

    state: str
    entries: list[AllergyEntryOut] = []
    none_statement: AllergyEntryOut | None = None
    retracted: list[AllergyEntryOut] = []


class CardOut(BaseModel):
    patient_id: uuid.UUID
    visit_id: uuid.UUID
    intake_id: uuid.UUID | None = None
    mrn: str
    name: str
    age: int | None = None
    sex: str | None = None
    lang: str
    village: str | None = None
    phone: str
    token_no: int | None = None
    department_name: str
    visit_date: date_type
    entry_id: uuid.UUID | None = None
    entry_state: str | None = None
    chief_complaint: str | None = None
    chief_complaint_en: str | None = None
    summary: SummaryOut
    summary_md: str | None = None
    red_flags: list[RedFlagOut]
    answers: list[AnswerOut]
    timeline: list[TimelineOut]
    trends: list[TrendOut]
    tier: str | None = None
    intake_lang: str | None = None
    completed_at: datetime | None = None
    assigned_doctor_id: uuid.UUID | None = None
    assigned_doctor_name: str | None = None
    diagnosis: DiagnosisOut | None = None
    caregiver_answered: bool = False
    allergies: AllergyViewOut
    rx_mode: str | None = None
    concluded_at: datetime | None = None
    conclusion_note: str | None = None
    note_signed: bool = False


# -- routes -------------------------------------------------------------------


@router.get("/day", response_model=DayOut)
async def get_day(
    on: date_type | None = Query(default=None, description="defaults to today"),
    scope: str = Query(default="mine", description="mine | unassigned | department"),
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> DayOut:
    """The doctor's worklist for a day, in one scope, in the queue's own order.

    The response always carries counts for all three scopes, so the console can
    keep the `Unassigned` badge honest while its tab is closed without asking a
    second time.
    """
    if scope not in doctor_svc.DAY_SCOPES:
        raise HTTPException(status_code=422, detail=f"no such scope {scope!r}")
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        day = await doctor_svc.day_list(session, doctor=doctor, on=on, scope=scope)  # type: ignore[arg-type]
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return DayOut.model_validate(day, from_attributes=True)


class TakeOut(BaseModel):
    visit_id: uuid.UUID
    assigned_doctor_id: uuid.UUID
    assigned_doctor_name: str


@router.post("/visits/{visit_id}/take", response_model=TakeOut)
async def take_patient(
    visit_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
) -> TakeOut:
    """Take this patient: points the visit at the calling doctor.

    Deliberately not behind a confirm dialog. Taking a patient is cheap to undo —
    the coordinator's assign control, or another doctor doing the same thing —
    and a confirmation step on the one action that unblocks a stalled line
    teaches doctors to click through dialogs.

    It notifies the queue hub because the *coordinator's* screen shows the
    assignment too: a desk still displaying "unassigned" for a patient a doctor
    has already taken is how the same patient gets assigned twice.
    """
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        visit = await doctor_svc.take_visit(session, visit_id=visit_id, doctor=doctor)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()
    await hub.notify_queue_changed()
    return TakeOut(
        visit_id=visit.id, assigned_doctor_id=doctor.id, assigned_doctor_name=doctor.name
    )


class ConcludeIn(BaseModel):
    """How the consult ended, and anything the doctor wants on the record.

    `rx_mode` is required and has no default. A default here would be a guess
    about a clinical fact, and the whole point of this endpoint is that the two
    lossy conclusions are stated rather than inferred from an empty visit.
    """

    rx_mode: RxMode
    note: str | None = Field(default=None, max_length=2000)


class ConcludeOut(BaseModel):
    visit_id: uuid.UUID
    rx_mode: str
    concluded_at: datetime
    conclusion_note: str | None = None
    entry_state: str | None = None


@router.post("/visits/{visit_id}/conclude", response_model=ConcludeOut)
async def conclude_visit(
    visit_id: uuid.UUID,
    body: ConcludeIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
) -> ConcludeOut:
    """Close the consult, saying how it ended (plan §5.3b).

    `external_manual` is the one that matters: it is the doctor telling the
    record that a paper script exists and this system has no copy of it. That is
    a worse outcome than a digital prescription and a much better one than a
    visit that simply stops, which is what happens today.

    The queue moves to `done` through the S8 verb, so the board, the coordinator
    and the console still share one state machine.
    """
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        result = await doctor_svc.conclude_visit(
            session, visit_id=visit_id, doctor=doctor, rx_mode=body.rx_mode, note=body.note
        )
    except doctor_svc.ConclusionRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()
    await hub.notify_queue_changed()
    visit = result.visit
    return ConcludeOut(
        visit_id=visit.id,
        rx_mode=str(visit.rx_mode),
        concluded_at=visit.concluded_at,  # type: ignore[arg-type]
        conclusion_note=visit.conclusion_note,
        entry_state=result.entry_state,
    )


@router.get("/patients/{visit_id}", response_model=CardOut)
async def get_patient(
    visit_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> CardOut:
    """One patient's card: summary, red flags, answers, timeline, trends."""
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        card = await doctor_svc.patient_card(session, visit_id=visit_id, doctor=doctor)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return CardOut.model_validate(card, from_attributes=True)


# -- allergies (SESSION-ALLERGY) ----------------------------------------------
#
# Three writes and no read: the current picture already rides on the patient
# card, which the console has open the whole time it could possibly want it. A
# `GET /allergies` would be a second source of the same derivation, and the two
# would be one refresh apart from disagreeing on the most safety-critical line
# on the screen.
#
# Every one of these returns the whole recomputed view for the same reason.


class AllergyIn(BaseModel):
    """What a doctor is putting on the record.

    `none_known` and `substance` are mutually exclusive in practice and the
    service enforces it — a "none" carrying a substance is a client bug, and
    accepting it would write a row that says two contradictory things.

    There is no `certainty` field and no free-text "notes": the reaction is the
    note, and a second prose field on a line the spine renders in one row would
    fill the top of the console with a paragraph.
    """

    substance: str | None = Field(default=None, max_length=200)
    reaction: str | None = Field(default=None, max_length=500)
    severity: AllergySeverity = AllergySeverity.UNKNOWN
    #: The doctor asked and was told there are none. A statement, with their name
    #: on it — which is what makes it outrank the tablet's version.
    none_known: bool = False


class RetractIn(BaseModel):
    """Why this is being withdrawn.

    Optional, and deliberately not enforced: a doctor who has spotted a wrong
    allergy mid-consult must be able to strike it out in one tap, and a required
    justification field is how a safety control turns into a thing people work
    around. The prompt asks; the record takes whatever it is given.
    """

    reason: str | None = Field(default=None, max_length=500)


@router.post("/visits/{visit_id}/allergies", response_model=AllergyViewOut)
async def record_allergy(
    visit_id: uuid.UUID,
    body: AllergyIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> AllergyViewOut:
    """Record an allergy, or record that the doctor asked and there are none."""
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        view = await doctor_svc.record_allergy(
            session,
            visit_id=visit_id,
            doctor=doctor,
            substance=body.substance,
            reaction=body.reaction,
            severity=body.severity,
            none_known=body.none_known,
        )
    except allergy_svc.AllergyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()
    return AllergyViewOut.model_validate(view, from_attributes=True)


@router.post("/visits/{visit_id}/allergies/{allergy_id}/confirm", response_model=AllergyViewOut)
async def confirm_allergy(
    visit_id: uuid.UUID,
    allergy_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> AllergyViewOut:
    """Stand behind a statement somebody else made — usually the patient's own.

    Scoped under the visit rather than sitting at `/allergies/{id}`, so the
    department check that guards every other read of this patient guards this
    write too, from the same visit the console already has open.
    """
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        view = await doctor_svc.confirm_allergy(
            session, visit_id=visit_id, allergy_id=allergy_id, doctor=doctor
        )
    except allergy_svc.AllergyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()
    return AllergyViewOut.model_validate(view, from_attributes=True)


@router.post("/visits/{visit_id}/allergies/{allergy_id}/retract", response_model=AllergyViewOut)
async def retract_allergy(
    visit_id: uuid.UUID,
    allergy_id: uuid.UUID,
    body: RetractIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> AllergyViewOut:
    """Withdraw a statement. The row stays on file, struck out, with a name on it."""
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        view = await doctor_svc.retract_allergy(
            session,
            visit_id=visit_id,
            allergy_id=allergy_id,
            doctor=doctor,
            reason=body.reason,
        )
    except allergy_svc.AllergyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()
    return AllergyViewOut.model_validate(view, from_attributes=True)
