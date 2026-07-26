"""The check-in engine's HTTP surface (doc 03 §9).

Two audiences, two guards, and the split is the point.

**A doctor approves a plan.** `require_doctor` — approving is the clinical act
that turns a draft into messages a patient will receive, and it is one tap
because doc 03 §9 says one tap: the whole plan, or none of it. There is no
`POST /checkins/plans` and no way to author a schedule over HTTP, because a plan
that no signature produced is a follow-up nobody stands behind (the same rule
S11 draws around prescriptions).

**A nurse works the queue.** `require_clinical` (nurse, doctor, admin) — reading
what a patient said about her own symptoms and marking it dealt with is clinical
work, and a queue coordinator moving a line does not need it.

Nothing here delivers anything. Sending is `app.checkins.delivery`, driven by
beat; these routes only decide *what is allowed to be sent* and *what a human has
already looked at*.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import doctor as doctor_svc
from app.auth.rbac import Principal, require_clinical, require_doctor
from app.checkins import grading as grading_svc
from app.checkins import plan as plan_svc
from app.checkins import protocols as protocol_bank
from app.db import get_session
from app.models.content import Checkin, CheckinPlan
from app.models.enums import CheckinPlanStatus
from app.models.patient import Patient

router = APIRouter(prefix="/checkins", tags=["checkins"])


# -- wire models --------------------------------------------------------------


class RungOut(BaseModel):
    day_offset: int
    question_set: str
    asks_about: str
    channel: str
    due_at: datetime
    message: str


class PlanOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    visit_id: uuid.UUID | None
    protocol_key: str
    protocol_label: str
    status: str
    lang: str
    treatment_at: datetime | None
    next_cycle_at: datetime | None
    schedule: list[RungOut]
    #: What the personalisation did — model, prompt ref, how many messages it
    #: wrote, and its one line to the doctor. Shown so the tap is informed.
    personalisation: dict[str, Any]


class ReviewRowOut(BaseModel):
    checkin_id: uuid.UUID
    plan_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    patient_phone: str
    day_offset: int
    question_set: str
    grade: str
    answered_at: datetime | None
    escalated_at: datetime | None
    reasons: list[dict[str, Any]]
    #: The questions as asked and what she answered, paired for reading.
    answers: list[dict[str, Any]]


class ResolveIn(BaseModel):
    note: str = Field(default="", max_length=2000)


# -- helpers ------------------------------------------------------------------


async def _doctor(session: AsyncSession, principal: Principal):
    try:
        return await doctor_svc.resolve_doctor(session, user_id=principal.id)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _plan_out(session: AsyncSession, plan: CheckinPlan) -> PlanOut:
    bank = protocol_bank.get_bank()
    patient = await session.get(Patient, plan.patient_id)
    rungs = []
    for rung in plan.schedule or []:
        qset = bank.question_sets.get(str(rung.get("question_set")))
        rungs.append(
            RungOut(
                day_offset=int(rung["day_offset"]),
                question_set=str(rung["question_set"]),
                asks_about=qset.title.get(plan.lang, "") if qset is not None else "",
                channel=str(rung["channel"]),
                due_at=datetime.fromisoformat(str(rung["due_at"])),
                message=str(rung.get("message", "")),
            )
        )
    protocol = bank.protocols.get(plan.protocol_key)
    return PlanOut(
        id=plan.id,
        patient_id=plan.patient_id,
        patient_name=patient.name if patient is not None else "",
        visit_id=plan.visit_id,
        protocol_key=plan.protocol_key,
        protocol_label=protocol.title(plan.lang) if protocol is not None else plan.protocol_key,
        status=str(plan.status),
        lang=str(plan.lang),
        treatment_at=plan.treatment_at,
        next_cycle_at=plan.next_cycle_at,
        schedule=rungs,
        personalisation=plan.personalisation or {},
    )


def _review_row(
    checkin: Checkin, plan: CheckinPlan | None, patient: Patient | None
) -> ReviewRowOut:
    """One queue line: what fired, and the questions paired with her answers."""
    responses = checkin.responses or {}
    return ReviewRowOut(
        checkin_id=checkin.id,
        plan_id=checkin.plan_id,
        patient_id=plan.patient_id if plan is not None else uuid.UUID(int=0),
        patient_name=patient.name if patient is not None else "",
        patient_phone=patient.phone if patient is not None else "",
        day_offset=checkin.day_offset,
        question_set=checkin.question_set,
        grade=str(checkin.grade),
        answered_at=checkin.answered_at,
        escalated_at=checkin.escalated_at,
        reasons=list(checkin.grade_reasons or []),
        answers=[
            {
                "id": question["id"],
                "prompt": question["prompt"].get(str(checkin.lang))
                or question["prompt"].get("en", ""),
                "answer": responses.get(question["id"]),
            }
            for question in checkin.asked or []
        ],
    )


async def _load_plan(session: AsyncSession, plan_id: uuid.UUID) -> CheckinPlan:
    plan = await session.get(CheckinPlan, plan_id)
    if plan is None or plan.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such check-in plan")
    return plan


# -- the doctor's one tap -----------------------------------------------------


@router.get("/plans/drafts", response_model=list[PlanOut])
async def list_drafts(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_doctor),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PlanOut]:
    """Plans drafted from this doctor's own signed notes, awaiting a tap.

    Scoped by `dictation.signed_by` rather than by department: the doctor who
    signed the note is the one being asked to stand behind the follow-up.
    """
    from app.models.clinical import Dictation

    doctor = await _doctor(session, principal)
    found = await session.scalars(
        select(CheckinPlan)
        .join(Dictation, Dictation.id == CheckinPlan.dictation_id)
        .where(
            CheckinPlan.deleted_at.is_(None),
            CheckinPlan.status == CheckinPlanStatus.DRAFT,
            Dictation.signed_by == doctor.id,
        )
        .order_by(CheckinPlan.created_at.desc())
        .limit(limit)
    )
    return [await _plan_out(session, plan) for plan in found]


@router.get("/plans/{plan_id}", response_model=PlanOut)
async def read_plan(
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_doctor),
) -> PlanOut:
    return await _plan_out(session, await _load_plan(session, plan_id))


@router.post("/plans/{plan_id}/approve", response_model=PlanOut)
async def approve_plan(
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_doctor),
) -> PlanOut:
    """The one tap. Freezes the plan and materialises its check-ins.

    Deliberately takes no body: doc 03 §9's "edit optional" is a *re-draft*, not
    a schedule the client posts back. Letting an approve carry its own schedule
    would put the one thing the protocol bank exists to own — which day a
    chemotherapy patient is asked about fever — on the wire.
    """
    plan = await _load_plan(session, plan_id)
    doctor = await _doctor(session, principal)
    try:
        await plan_svc.approve(session, plan=plan, doctor=doctor)
    except plan_svc.PlanError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _plan_out(session, plan)


@router.post("/plans/{plan_id}/cancel", response_model=PlanOut)
async def cancel_plan(
    plan_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_doctor),
) -> PlanOut:
    """Stop a plan. Answered check-ins keep their answers — they happened."""
    plan = await _load_plan(session, plan_id)
    await plan_svc.cancel(session, plan=plan)
    return await _plan_out(session, plan)


# -- the nurse's queue --------------------------------------------------------


@router.get("/review", response_model=list[ReviewRowOut])
async def review(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_clinical),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[ReviewRowOut]:
    """Every amber and red nobody has dealt with, red first, then oldest."""
    return [
        _review_row(checkin, plan, patient)
        for checkin, plan, patient in await grading_svc.review_queue(session, limit=limit)
    ]


@router.post("/{checkin_id}/resolve", response_model=ReviewRowOut)
async def resolve(
    checkin_id: uuid.UUID,
    body: ResolveIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_clinical),
) -> ReviewRowOut:
    """A nurse has dealt with it. The grade stays: what she said stays true."""
    checkin = await session.get(Checkin, checkin_id)
    if checkin is None or checkin.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such check-in")
    await grading_svc.resolve(session, checkin=checkin, user_id=principal.id, note=body.note)
    plan = await session.get(CheckinPlan, checkin.plan_id)
    patient = await session.get(Patient, plan.patient_id) if plan is not None else None
    return _review_row(checkin, plan, patient)
