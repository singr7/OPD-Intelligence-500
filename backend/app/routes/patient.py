"""The patient app's HTTP surface (doc 03 §1c) — everything behind one login.

Design rules this router keeps, in the order they matter:

1. **One scope, taken from the token.** Every handler reads
   `principal.patient_id`. There is no `patient_id` path or body parameter
   anywhere below, so "forgot to scope this query" is not a mistake that can be
   made here.
2. **No app-only rules.** Booking goes to `app.scheduling`, intake goes to the
   `IntakeEngine`, urgency comes from the red-flag rules. The app is a *client*
   of what S5–S15 built (HANDOFF, S15: "resist adding app-only endpoints"); what
   it adds is identity and the read shapes a 5-inch screen needs.
3. **Offline is the normal case, not the error case.** The care file is one
   payload with an ETag, the reminder plan is data the phone schedules alarms
   from by itself, and the intake can be walked with the V3 tier. A patient in a
   village with no bars must still be able to open her prescriptions.

Home intake runs the kiosk's four-tool contract through `app.routes.kiosk`'s own
handler bodies (`next_node_impl` / `answer_impl` / `finish_impl`) — one walker,
one node shape, one adaptive path, already guarded by the conformance suite. What
differs is the lock on the door and the two ends: a kiosk session is an anonymous
walk-in whose id is worth nothing to a thief, while an app session hangs off a
named patient's record, so every verb here re-checks the visit against the token.
And `/patient/intake/{sid}/confirm` deliberately issues **no** token —
`/patient/arrive` does that, when she is actually standing in the hospital.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import kiosk as kiosk_svc
from app import patient_app, scheduling
from app.auth.rbac import PatientPrincipal, current_patient, require_patient_self
from app.channels import require_open, resolve_config
from app.db import get_session
from app.intake import IntakeEngine
from app.models.enums import (
    AppointmentStatus,
    Channel,
    DoseStatus,
    Lang,
    QueueEntryState,
    SlotType,
)
from app.models.org import Department, Doctor
from app.models.patient import Patient
from app.models.scheduling import Appointment
from app.notify import notify_appointment
from app.providers.metering import get_meter
from app.queue_hub import QueueHub
from app.routes import kiosk as kiosk_routes
from app.routes.kiosk import _node_out
from app.trees import store as tree_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patient", tags=["patient-app"])


# -- wire models ---------------------------------------------------------------


class MeOut(BaseModel):
    patient_id: uuid.UUID
    name: str
    lang: Lang
    mrn: str
    village: str | None
    #: How the caller is holding this file — "self" or "caregiver". The app shows
    #: a caregiver banner and hides the acts only the patient may perform.
    via: str
    hospital: str | None


class MedOut(BaseModel):
    """One drug, exactly as it was frozen onto the prescription (S11)."""

    name: str
    dose: str | None = None
    route: str | None = None
    freq: str | None = None
    duration: str | None = None
    schedule: dict[str, Any] | None = None
    flagged: bool = False
    flag_reason: str | None = None


class FileEntryOut(BaseModel):
    kind: str
    id: uuid.UUID
    visit_id: uuid.UUID
    at: datetime
    department: str
    doctor: str | None = None
    meds: list[MedOut] = Field(default_factory=list)
    summary_md: str | None = None
    chief_complaint: str | None = None
    red_flags: list[dict[str, Any]] = Field(default_factory=list)


class CareFileOut(BaseModel):
    patient: MeOut
    revision: datetime | None
    entries: list[FileEntryOut]


class QueuePositionOut(BaseModel):
    in_queue: bool
    visit_id: uuid.UUID | None = None
    token_no: int | None = None
    department: str | None = None
    state: QueueEntryState | None = None
    ahead: int | None = None
    est_wait_low: int | None = None
    est_wait_high: int | None = None
    leave_by: datetime | None = None
    now_serving: int | None = None


class ArriveIn(BaseModel):
    visit_id: uuid.UUID | None = None


class ArriveOut(BaseModel):
    token_no: int
    department: str
    already_queued: bool
    position: QueuePositionOut


class IntakeStartIn(BaseModel):
    lang: Lang
    chief_complaint: str = Field(min_length=1, max_length=2000)
    dept_key: str | None = None
    #: A caregiver answering on the patient's behalf. Recorded on the intake the
    #: same way the kiosk records it (doc 03 §1 caregiver mode).
    caregiver: bool = False


class DeptOut(BaseModel):
    key: str
    name: str


class IntakeStartOut(BaseModel):
    status: str
    session_id: str | None = None
    visit_id: uuid.UUID | None = None
    tier: str | None = None
    department: DeptOut | None = None
    tree_key: str | None = None
    node: dict[str, Any] | None = None
    complete: bool = False
    departments: list[DeptOut] = Field(default_factory=list)
    reason: str | None = None


class IntakeConfirmOut(BaseModel):
    visit_id: uuid.UUID | None
    department: DeptOut | None
    red_flags: list[dict[str, Any]]
    #: Always null here, and that is the feature: the token is issued on arrival.
    token_no: int | None = None
    message: str


class DoseOut(BaseModel):
    med_index: int
    drug: str
    dose: str | None
    route: str | None
    duration: str | None
    slot: str
    at: str | None


class ReminderPlanOut(BaseModel):
    prescription_id: uuid.UUID | None
    prescribed_on: Any | None
    doses: list[DoseOut]
    unscheduled: list[str]


class DoseEventIn(BaseModel):
    prescription_id: uuid.UUID
    med_index: int = Field(ge=0)
    scheduled_for: datetime
    status: DoseStatus


class DoseEventOut(BaseModel):
    recorded: bool
    caregiver_notified: bool


class CycleOut(BaseModel):
    appointment_id: uuid.UUID | None
    at: datetime
    cycle_no: int
    doctor: str | None
    department: str
    status: str
    title: str
    expect: list[str]


class CaregiverOut(BaseModel):
    id: uuid.UUID
    phone: str
    name: str | None
    relation: str | None
    status: str
    consented_at: datetime | None


class CaregiverIn(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    relation: str | None = Field(default=None, max_length=60)


class SlotOut(BaseModel):
    slot_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    doctor_name: str
    department_name: str
    slot_type: SlotType
    seats_left: int


class BookIn(BaseModel):
    slot_id: uuid.UUID


class AppointmentOut(BaseModel):
    id: uuid.UUID
    slot_at: datetime
    status: AppointmentStatus
    slot_type: SlotType | None
    doctor_name: str | None
    department_name: str


# -- helpers -------------------------------------------------------------------


def _me(patient: Patient, *, via: str, hospital: str | None = None) -> MeOut:
    return MeOut(
        patient_id=patient.id,
        name=patient.name,
        lang=patient.lang,
        mrn=patient.mrn,
        village=patient.village,
        via=via,
        hospital=hospital,
    )


def _position_out(position: patient_app.QueuePosition | None) -> QueuePositionOut:
    if position is None:
        return QueuePositionOut(in_queue=False)
    return QueuePositionOut(
        in_queue=True,
        visit_id=position.visit_id,
        token_no=position.token_no,
        department=position.department,
        state=position.state,
        ahead=position.ahead,
        est_wait_low=position.est_wait_low,
        est_wait_high=position.est_wait_high,
        leave_by=position.leave_by,
        now_serving=position.now_serving,
    )


def _engine(request: Request) -> IntakeEngine:
    engine = getattr(request.app.state, "intake_engine", None)
    if engine is None:  # pragma: no cover - the lifespan always sets it
        raise HTTPException(status_code=503, detail="intake engine not ready")
    return engine


def _forbid_caregiver_write(principal: PatientPrincipal) -> None:
    """Caregivers read; they do not answer clinical questions.

    A daughter may see her mother's file, her queue position and her medicines.
    She may not run her mother's *intake* — those answers become the doctor's
    summary and the red-flag rules' input, and a second-hand symptom recorded as
    the patient's own is a clinical falsehood. The kiosk has the honest path for
    this: caregiver mode, which marks the intake as answered by someone else.
    """
    if principal.is_caregiver:
        raise HTTPException(status_code=403, detail="only the patient can answer her own intake")


# -- who am I ------------------------------------------------------------------


@router.get("/me", response_model=MeOut)
async def me(
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    patient = await session.get(Patient, principal.patient_id)
    if patient is None:  # pragma: no cover - current_patient already loaded it
        raise HTTPException(status_code=404, detail="no such patient")
    from app.models.org import Hospital

    hospital = await session.get(Hospital, patient.hospital_id)
    return _me(
        patient,
        via=principal.via,
        hospital=hospital.name_in(patient.lang) if hospital else None,
    )


# -- My Cancer Care File (doc 03 §1c.1) ----------------------------------------


@router.get("/file", response_model=CareFileOut)
async def care_file(
    response: Response,
    request: Request,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """The whole file in one payload, with an ETag.

    The ETag is the point on a 2G connection in Alwar: the phone already holds
    every row, so the common case is a conditional GET that answers 304 and costs
    a few hundred bytes. `revision` is the newest `updated_at` in the file, so a
    prescription signed this morning changes it and nothing else does.
    """
    file = await patient_app.care_file(session, patient_id=principal.patient_id)

    etag = f'W/"{file.revision.timestamp() if file.revision else 0}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag

    from app.models.org import Hospital

    hospital = await session.get(Hospital, file.patient.hospital_id)
    return CareFileOut(
        patient=_me(
            file.patient,
            via=principal.via,
            hospital=hospital.name_in(file.patient.lang) if hospital else None,
        ),
        revision=file.revision,
        entries=[
            FileEntryOut(
                kind=entry.kind,
                id=entry.id,
                visit_id=entry.visit_id,
                at=entry.at,
                department=entry.department,
                doctor=entry.doctor,
                meds=[
                    MedOut(**{k: v for k, v in med.items() if k in MedOut.model_fields})
                    for med in entry.meds
                ],
                summary_md=entry.summary_md,
                chief_complaint=entry.chief_complaint,
                red_flags=entry.red_flags,
            )
            for entry in file.entries
        ],
    )


# -- live queue position (doc 03 §1c.3) ----------------------------------------


@router.get("/queue", response_model=QueuePositionOut)
async def queue(
    travel_minutes: int = Query(default=0, ge=0, le=600),
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> QueuePositionOut:
    """ "You are 7th; leave home by 10:30."

    `travel_minutes` is the patient's own estimate from her own phone — the app
    asks once and remembers it. The server does not geolocate anybody.
    """
    position = await patient_app.queue_position(
        session, patient_id=principal.patient_id, travel_minutes=travel_minutes
    )
    return _position_out(position)


@router.post("/arrive", response_model=ArriveOut)
async def arrive(
    payload: ArriveIn,
    request: Request,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> ArriveOut:
    """ "I am here" — the token a home intake was waiting for.

    Idempotent, and safe for a caregiver to tap: checking in is a statement about
    where the patient is standing, not a clinical claim.
    """
    try:
        arrival = await patient_app.arrive(
            session, patient_id=principal.patient_id, visit_id=payload.visit_id
        )
    except patient_app.PatientAppError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    hub: QueueHub | None = getattr(request.app.state, "queue_hub", None)
    if hub is not None:
        await hub.notify_queue_changed()

    return ArriveOut(
        token_no=arrival.token_no,
        department=arrival.department,
        already_queued=arrival.already_queued,
        position=_position_out(arrival.position),
    )


# -- Talk-to-Dhara from home (doc 03 §1c.2) ------------------------------------


@router.post("/intake/start", response_model=IntakeStartOut)
async def intake_start(
    payload: IntakeStartIn,
    request: Request,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> IntakeStartOut:
    """Open tomorrow's intake tonight, attached to this patient.

    The only differences from `/kiosk/start` are identity-shaped: the visit is
    hers (no anonymous walk-in row is created) and the channel is `app`. Routing,
    the tree, the tier ladder and the classifier's `needs_human` behaviour are
    the kiosk's, unchanged — including the department chooser, which the app
    renders as its own screen.

    Gated on the `app` channel being open (S-GL.1). Only intake is gated: her care
    file, her queue position and her reminders keep working with app intake dark,
    because none of those start something the OPD then has to staff. Doc 12 §8
    goes live with exactly this shape — the app installed and useful, its intake
    shut until the APK has been on a real handset.
    """
    _forbid_caregiver_write(principal)
    require_open(await resolve_config(session), Channel.APP, lang=payload.lang)

    try:
        routed = await kiosk_svc.route_complaint(
            session,
            complaint=payload.chief_complaint,
            lang=payload.lang,
            dept_key=payload.dept_key,
        )
    except kiosk_svc.KioskError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if routed.needs_department:
        departments = await kiosk_svc._departments(session)
        return IntakeStartOut(
            status="needs_department",
            departments=[DeptOut(key=d.code, name=d.name) for d in departments],
            reason=routed.guess.reason or "Let's confirm the right doctor for you.",
        )

    assert routed.department is not None and routed.tree is not None
    patient = await session.get(Patient, principal.patient_id)
    assert patient is not None

    visit = await patient_app.open_visit_for(
        session, patient=patient, department=routed.department, lang=payload.lang
    )
    from app.models.clinical import Intake

    intake = Intake(
        visit_id=visit.id,
        tier=kiosk_svc.KIOSK_TIER,
        lang=payload.lang,
        caregiver_answered=payload.caregiver,
    )
    session.add(intake)
    await session.flush()

    engine = _engine(request)
    state = await engine.start_session(
        tree=routed.tree,
        channel=Channel.APP,
        lang=payload.lang,
        configured_tier=kiosk_svc.KIOSK_TIER,
        intake_id=intake.id,
        visit_id=visit.id,
        chief_complaint=payload.chief_complaint,
        open_departments=sorted(await tree_store.active_department_codes(session)),
    )
    dispatcher = engine.dispatcher(state, routed.tree)
    first = await dispatcher.get_next_node()

    return IntakeStartOut(
        status="routed",
        session_id=state.session_id,
        visit_id=visit.id,
        tier=state.active_tier.value,
        department=DeptOut(key=routed.department.code, name=routed.department.name),
        tree_key=routed.tree.key,
        # The kiosk's own node shape, from the kiosk's own mapper: the app walks
        # `/kiosk/{sid}/answer` from here on, and two node contracts for one
        # walker is how a client ends up rendering the wrong screen.
        node=(lambda n: n.model_dump() if n else None)(_node_out(first)),
        complete=bool(first.get("complete", False)),
    )


async def _own_session(
    request: Request,
    session: AsyncSession,
    principal: PatientPrincipal,
    session_id: str,
):
    """The intake session, if it is this patient's. Raises otherwise.

    A session id is a bearer-ish string. The kiosk can afford that (a walk-in has
    no identity to steal); an app session is attached to a named patient's
    record, so every verb below re-checks the visit against the token before it
    touches the walk.
    """
    from app.models.clinical import Visit

    engine = _engine(request)
    state = await engine.store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="no such intake session")
    if state.channel is not Channel.APP:
        raise HTTPException(status_code=409, detail="session is not an app session")
    visit = await session.get(Visit, state.visit_id) if state.visit_id else None
    if visit is None or visit.patient_id != principal.patient_id:
        raise HTTPException(status_code=403, detail="not your intake")
    return engine, state, visit


@router.get("/intake/{session_id}/next")
async def intake_next(
    session_id: str,
    request: Request,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """The current question — for an app resumed mid-intake (doc 04 law 12)."""
    engine, _, _ = await _own_session(request, session, principal, session_id)
    return await kiosk_routes.next_node_impl(engine, session_id, expected=(Channel.APP,))


@router.post("/intake/{session_id}/answer", response_model=kiosk_routes.AnswerOut)
async def intake_answer(
    session_id: str,
    payload: kiosk_routes.AnswerIn,
    request: Request,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> kiosk_routes.AnswerOut:
    """One answer. Literally the kiosk's own handler body, behind this login."""
    _forbid_caregiver_write(principal)
    engine, _, _ = await _own_session(request, session, principal, session_id)
    return await kiosk_routes.answer_impl(engine, session_id, payload, expected=(Channel.APP,))


@router.post("/intake/{session_id}/finish", response_model=kiosk_routes.FinishOut)
async def intake_finish(
    session_id: str,
    request: Request,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> kiosk_routes.FinishOut:
    """The read-back she confirms — same summary contract as every other channel."""
    _forbid_caregiver_write(principal)
    engine, _, _ = await _own_session(request, session, principal, session_id)
    return await kiosk_routes.finish_impl(engine, session_id, expected=(Channel.APP,))


@router.post("/intake/{session_id}/confirm", response_model=IntakeConfirmOut)
async def intake_confirm(
    session_id: str,
    request: Request,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> IntakeConfirmOut:
    """The patient confirmed her read-back, at home, the night before.

    Marks the intake complete and finalises its cost — and stops there. No token,
    no queue entry: she is not at the hospital, and a token issued now would be
    called while she is still travelling. `/patient/arrive` finishes the job.
    """
    _forbid_caregiver_write(principal)

    engine, state, visit = await _own_session(request, session, principal, session_id)

    state.confirmed = True
    await engine.store.save(state)

    meter = get_meter()
    if meter is not None:
        await meter.flush()
    await engine.finalize_cost(state, session)

    from app.models.enums import VisitStatus

    visit.status = VisitStatus.INTAKE_DONE
    department = await session.get(Department, visit.department_id)

    return IntakeConfirmOut(
        visit_id=visit.id,
        department=(DeptOut(key=department.code, name=department.name) if department else None),
        red_flags=state.red_flags,
        token_no=None,
        message="Show this on arrival — your token is issued when you check in.",
    )


# -- medicines (doc 03 §1c.4) --------------------------------------------------


@router.get("/reminders", response_model=ReminderPlanOut)
async def reminders(
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> ReminderPlanOut:
    """The alarms the phone should set, from the newest signed prescription."""
    plan = await patient_app.reminder_plan(session, patient_id=principal.patient_id)
    return ReminderPlanOut(
        prescription_id=plan.prescription_id,
        prescribed_on=plan.prescribed_on,
        doses=[
            DoseOut(
                med_index=d.med_index,
                drug=d.drug,
                dose=d.dose,
                route=d.route,
                duration=d.duration,
                slot=d.slot,
                at=d.at,
            )
            for d in plan.doses
        ],
        unscheduled=plan.unscheduled,
    )


@router.post("/reminders/events", response_model=DoseEventOut)
async def dose_event(
    payload: DoseEventIn,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> DoseEventOut:
    """Report what happened to one dose. A missed one pings the caregiver.

    A caregiver may report on the patient's behalf — that is the point of linking
    her — and the ping simply goes to the *other* caregivers.
    """
    try:
        _, notified = await patient_app.record_dose(
            session,
            patient_id=principal.patient_id,
            prescription_id=payload.prescription_id,
            med_index=payload.med_index,
            scheduled_for=payload.scheduled_for,
            status=payload.status,
        )
    except patient_app.PatientAppError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DoseEventOut(recorded=True, caregiver_notified=notified)


# -- chemo calendar (doc 03 §1c.5) ---------------------------------------------


@router.get("/chemo-calendar", response_model=list[CycleOut])
async def chemo_calendar(
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> list[CycleOut]:
    entries = await patient_app.chemo_calendar(session, patient_id=principal.patient_id)
    return [
        CycleOut(
            appointment_id=entry.appointment_id,
            at=entry.at,
            cycle_no=entry.cycle_no,
            doctor=entry.doctor,
            department=entry.department,
            status=entry.status,
            title=entry.title,
            expect=entry.expect,
        )
        for entry in entries
    ]


# -- family access (doc 03 §1c.6) ----------------------------------------------


@router.get("/caregivers", response_model=list[CaregiverOut])
async def list_caregivers(
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> list[CaregiverOut]:
    links = await patient_app.caregivers_of(session, patient_id=principal.patient_id)
    return [
        CaregiverOut(
            id=link.id,
            phone=link.phone,
            name=link.name,
            relation=link.relation,
            status=link.status.value,
            consented_at=link.consented_at,
        )
        for link in links
    ]


@router.post("/caregivers", response_model=CaregiverOut, status_code=201)
async def add_caregiver(
    payload: CaregiverIn,
    principal: PatientPrincipal = Depends(require_patient_self),
    session: AsyncSession = Depends(get_session),
) -> CaregiverOut:
    """Give a family member access. The patient's own act, always."""
    link = await patient_app.invite_caregiver(
        session,
        patient_id=principal.patient_id,
        phone=payload.phone,
        name=payload.name,
        relation=payload.relation,
    )
    return CaregiverOut(
        id=link.id,
        phone=link.phone,
        name=link.name,
        relation=link.relation,
        status=link.status.value,
        consented_at=link.consented_at,
    )


@router.delete("/caregivers/{link_id}", response_model=CaregiverOut)
async def remove_caregiver(
    link_id: uuid.UUID,
    principal: PatientPrincipal = Depends(require_patient_self),
    session: AsyncSession = Depends(get_session),
) -> CaregiverOut:
    try:
        link = await patient_app.revoke_caregiver(
            session, patient_id=principal.patient_id, link_id=link_id
        )
    except patient_app.PatientAppError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CaregiverOut(
        id=link.id,
        phone=link.phone,
        name=link.name,
        relation=link.relation,
        status=link.status.value,
        consented_at=link.consented_at,
    )


# -- appointments (doc 03 §2, through S15's rules) -----------------------------


@router.get("/appointments", response_model=list[AppointmentOut])
async def my_appointments(
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> list[AppointmentOut]:
    rows = await patient_app.upcoming(session, patient_id=principal.patient_id)
    return [await _appointment_out(session, appointment) for appointment in rows]


@router.get("/appointments/slots", response_model=list[SlotOut])
async def open_slots(
    slot_type: SlotType = Query(default=SlotType.FOLLOW_UP),
    days: int = Query(default=14, ge=1, le=60),
    limit: int = Query(default=20, ge=1, le=50),
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> list[SlotOut]:
    """Free seats the patient may take, from the same inventory the phone line
    reads — a seat offered here is a seat the receptionist can no longer offer."""
    now = datetime.now(UTC)
    offers = await scheduling.find_slots(
        session,
        slot_type=slot_type,
        after=now,
        until=now + timedelta(days=days),
        limit=limit,
    )
    return [
        SlotOut(
            slot_id=offer.slot_id,
            starts_at=offer.starts_at,
            ends_at=offer.ends_at,
            doctor_name=offer.doctor_name,
            department_name=offer.department_name,
            slot_type=offer.slot_type,
            seats_left=offer.seats_left,
        )
        for offer in offers
    ]


@router.post("/appointments", response_model=AppointmentOut, status_code=201)
async def book(
    payload: BookIn,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    """Take a seat. `app.scheduling.book` decides everything; `source=app` is all
    this route contributes, and it is what the analytics tab counts."""
    patient = await session.get(Patient, principal.patient_id)
    assert patient is not None
    try:
        appointment = await scheduling.book(
            session, patient=patient, slot_id=payload.slot_id, source=Channel.APP
        )
    except scheduling.SlotUnavailable as exc:
        # 409, not 400: the request was fine, the world moved — the app re-fetches
        # the slot list and offers the next time.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except scheduling.BookingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await _confirm(session, appointment, kind="booked")
    await session.commit()
    return await _appointment_out(session, appointment)


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel(
    appointment_id: uuid.UUID,
    principal: PatientPrincipal = Depends(current_patient),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    try:
        appointment = await patient_app.owns_appointment(
            session, patient_id=principal.patient_id, appointment_id=appointment_id
        )
        appointment = await scheduling.cancel(session, appointment=appointment)
    except patient_app.PatientAppError as exc:
        # 404 rather than 403 for an appointment that is not hers: whether an id
        # exists is not something a patient app gets to probe.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scheduling.BookingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await _confirm(session, appointment, kind="cancelled")
    await session.commit()
    return await _appointment_out(session, appointment)


async def _confirm(session: AsyncSession, appointment: Appointment, *, kind: str) -> None:
    """Fan the confirmation out to WhatsApp + SMS — the same one the phone line
    sends, because it is the same function (S15's `app.notify`, which records
    every attempt on the appointment and never raises)."""
    from app.models.org import Hospital

    patient = await session.get(Patient, appointment.patient_id)
    if patient is None:  # pragma: no cover - FK-guarded
        return
    hospital = await session.get(Hospital, patient.hospital_id)
    doctor = await session.get(Doctor, appointment.doctor_id) if appointment.doctor_id else None
    await notify_appointment(
        session,
        appointment=appointment,
        patient=patient,
        hospital_name=hospital.name_in(patient.lang) if hospital else "the hospital",
        doctor_name=doctor.name if doctor else "",
        kind=kind,
    )


async def _appointment_out(session: AsyncSession, appointment: Appointment) -> AppointmentOut:
    doctor = await session.get(Doctor, appointment.doctor_id) if appointment.doctor_id else None
    department = await session.get(Department, appointment.department_id)
    return AppointmentOut(
        id=appointment.id,
        slot_at=appointment.slot_at,
        status=appointment.status,
        slot_type=appointment.slot_type,
        doctor_name=doctor.name if doctor else None,
        department_name=department.name if department else "",
    )
