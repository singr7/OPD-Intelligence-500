"""The doctor console's HTTP surface (doc 03 §5).

Two reads and one write. The console's *queue* actions are the S8 verbs it
already shares with the coordinator — `POST /queue/call-next` and
`POST /queue/entries/{id}/state` — so there is no `/doctor/call-next` here. One
implementation of the queue state machine, one audit trail, one order on the
board; a doctor-flavoured copy would drift from the coordinator's within a
session or two.

The write is `POST /doctor/visits/{id}/take`, and it exists because the thing it
does is not a queue transition at all: it changes who the visit belongs to, not
where it sits in the line. Routing it through the coordinator's assign endpoint
would mean handing every doctor `require_staff` and a department picker to do
the one thing they actually need — put their own name on the patient in front of
them.

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
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import doctor as doctor_svc
from app.auth.rbac import Principal, require_doctor
from app.db import get_session
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
