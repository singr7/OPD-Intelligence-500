"""The doctor console's HTTP surface (doc 03 §5).

Read-only, on purpose. The console's *actions* are the S8 queue verbs it already
shares with the coordinator — `POST /queue/call-next` and
`POST /queue/entries/{id}/state` — so there is no `/doctor/call-next` here. One
implementation of the queue state machine, one audit trail, one order on the
board; a doctor-flavoured copy would drift from the coordinator's within a
session or two.

Both routes are `require_doctor` (doctor or admin), a tighter guard than the
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


class DayOut(BaseModel):
    doctor_name: str
    department_key: str
    department_name: str
    date: date_type
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


# -- routes -------------------------------------------------------------------


@router.get("/day", response_model=DayOut)
async def get_day(
    on: date_type | None = Query(default=None, description="defaults to today"),
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
) -> DayOut:
    """The doctor's worklist for a day, in the queue's own order."""
    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
        day = await doctor_svc.day_list(session, doctor=doctor, on=on)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return DayOut.model_validate(day, from_attributes=True)


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
