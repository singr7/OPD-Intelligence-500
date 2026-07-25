"""Appointment HTTP surface (doc 03 §2) — slots, booking, and the Exotel callback.

Two audiences on one router:

* **Staff** (`/appointments/...`) — the coordinator's console and the S18-late
  slot editor. Behind `require_staff`, because these endpoints move real
  patients' real appointments.
* **Exotel** (`POST /appointments/telephony/status`) — the vendor's status
  callback. Authenticated by a shared token on the query string rather than a
  JWT, because the caller is a telephony vendor, not a person.

The AI receptionist does **not** go through here: `voice-gw` runs
`app.receptionist` in-process against the same session factory (doc 02 §5 — no
network hop inside a live call). This router exists for humans and for the
vendor, and it shares every rule with the receptionist because both call
`app.scheduling`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import campaign as campaign_svc
from app import scheduling
from app.auth.rbac import Principal, require_staff
from app.config import Settings, get_settings
from app.db import get_session
from app.models.enums import AppointmentStatus, Channel, SlotType
from app.models.org import Doctor, Hospital
from app.models.patient import Patient
from app.models.scheduling import Appointment
from app.notify import notify_appointment
from app.providers.registry import get_telephony_provider
from app.providers.telephony import CallHandle, CallState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/appointments", tags=["appointments"])


# -- wire models ---------------------------------------------------------------


class SlotOut(BaseModel):
    slot_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    doctor_id: uuid.UUID
    doctor_name: str
    department_id: uuid.UUID
    department_code: str
    department_name: str
    slot_type: SlotType
    seats_left: int


class AppointmentOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID | None
    department_id: uuid.UUID
    slot_id: uuid.UUID | None
    seat_no: int | None
    slot_at: datetime
    slot_type: SlotType | None
    status: AppointmentStatus
    source: Channel

    @classmethod
    def of(cls, appointment: Appointment) -> AppointmentOut:
        return cls(
            id=appointment.id,
            patient_id=appointment.patient_id,
            doctor_id=appointment.doctor_id,
            department_id=appointment.department_id,
            slot_id=appointment.slot_id,
            seat_no=appointment.seat_no,
            slot_at=appointment.slot_at,
            slot_type=appointment.slot_type,
            status=appointment.status,
            source=appointment.source,
        )


class BookIn(BaseModel):
    patient_id: uuid.UUID
    slot_id: uuid.UUID
    #: How the booking reached us. A coordinator typing it in is `kiosk`-adjacent
    #: desk work; the default keeps a console booking honest about its origin.
    source: Channel = Channel.KIOSK


class RescheduleIn(BaseModel):
    slot_id: uuid.UUID


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=200)


class GenerateIn(BaseModel):
    start: date | None = None
    days: int = Field(default=scheduling.GENERATION_HORIZON_DAYS, ge=1, le=365)


# -- slots ---------------------------------------------------------------------


@router.get("/slots", response_model=list[SlotOut])
async def list_slots(
    department: str | None = Query(default=None, description="department code"),
    doctor_id: uuid.UUID | None = None,
    slot_type: SlotType | None = None,
    on_date: date | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> list[SlotOut]:
    """Open slots, soonest first — the same list the receptionist reads aloud."""
    try:
        offers = await scheduling.find_slots(
            session,
            department_code=department,
            doctor_id=doctor_id,
            slot_type=slot_type,
            on_date=on_date,
            limit=limit,
        )
    except scheduling.BookingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [SlotOut(**asdict(offer)) for offer in offers]


@router.post("/slots/generate", response_model=dict)
async def generate(
    body: GenerateIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Materialise inventory from the slot templates. Idempotent; the nightly
    `opd.slots.generate` job calls the same function."""
    start = body.start or datetime.now(scheduling.hospital_tz()).date()
    created = await scheduling.generate_slots(session, start=start, days=body.days)
    await session.commit()
    return {"created": len(created), "start": str(start), "days": body.days}


# -- booking -------------------------------------------------------------------


async def _patient(session: AsyncSession, patient_id: uuid.UUID) -> Patient:
    patient = await session.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        raise HTTPException(status_code=404, detail="unknown patient")
    return patient


async def _appointment(session: AsyncSession, appointment_id: uuid.UUID) -> Appointment:
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None or appointment.deleted_at is not None:
        raise HTTPException(status_code=404, detail="unknown appointment")
    return appointment


async def _confirm(session: AsyncSession, appointment: Appointment, *, kind: str) -> None:
    patient = await session.get(Patient, appointment.patient_id)
    hospital = await session.get(Hospital, patient.hospital_id) if patient else None
    doctor = await session.get(Doctor, appointment.doctor_id) if appointment.doctor_id else None
    if patient is None:  # pragma: no cover - FK-guarded
        return
    await notify_appointment(
        session,
        appointment=appointment,
        patient=patient,
        hospital_name=hospital.name if hospital else "the hospital",
        doctor_name=doctor.name if doctor else "",
        kind=kind,
    )


@router.post("", response_model=AppointmentOut, status_code=201)
async def book(
    body: BookIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> AppointmentOut:
    patient = await _patient(session, body.patient_id)
    try:
        appointment = await scheduling.book(
            session, patient=patient, slot_id=body.slot_id, source=body.source
        )
    except scheduling.SlotUnavailable as exc:
        # 409, not 400: the request was fine, the world moved. A console can
        # re-fetch the slot list and offer the next one.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except scheduling.BookingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _confirm(session, appointment, kind="booked")
    await session.commit()
    return AppointmentOut.of(appointment)


@router.post("/{appointment_id}/reschedule", response_model=AppointmentOut)
async def reschedule(
    appointment_id: uuid.UUID,
    body: RescheduleIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> AppointmentOut:
    appointment = await _appointment(session, appointment_id)
    try:
        await scheduling.reschedule(session, appointment=appointment, slot_id=body.slot_id)
    except scheduling.SlotUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except scheduling.BookingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _confirm(session, appointment, kind="booked")
    await session.commit()
    return AppointmentOut.of(appointment)


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel(
    appointment_id: uuid.UUID,
    body: CancelIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> AppointmentOut:
    appointment = await _appointment(session, appointment_id)
    await scheduling.cancel(session, appointment=appointment, reason=body.reason)
    await _confirm(session, appointment, kind="cancelled")
    await session.commit()
    return AppointmentOut.of(appointment)


@router.get("", response_model=list[AppointmentOut])
async def list_for_patient(
    patient_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> list[AppointmentOut]:
    """A patient's upcoming appointments — "when is my appointment?" for staff."""
    upcoming = await scheduling.upcoming_for_patient(session, patient_id=patient_id, limit=20)
    return [AppointmentOut.of(a) for a in upcoming]


# -- the campaign's control surface --------------------------------------------


@router.get("/campaign/plan", response_model=dict)
async def campaign_plan(
    for_date: date | None = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
    settings: Settings = Depends(get_settings),
) -> dict:
    """The D-1 dry run, as JSON. Reads only — nobody's phone rings because a
    coordinator opened this page."""
    plan = await campaign_svc.plan_campaign(
        session, for_date=for_date or campaign_svc.tomorrow(settings=settings)
    )
    return {
        "for_date": str(plan.for_date),
        "targets": [
            {
                "appointment_id": str(t.appointment_id),
                "patient_name": t.patient_name,
                "phone": t.phone,
                "lang": str(t.lang),
                "slot_at": t.slot_at.isoformat(),
            }
            for t in plan.targets
        ],
        "skipped": [{"reason": reason, "appointment_id": str(a)} for reason, a in plan.skipped],
    }


# -- the Exotel status callback ------------------------------------------------


def _duration(raw: str | None) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        logger.warning("exotel sent an unreadable duration %r", raw)
        return None


@router.post("/telephony/status", response_model=dict)
async def telephony_status(
    request: Request,
    token: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Exotel's per-call status callback — where telephony cost comes from.

    Exotel posts form-encoded, with the field names it uses on the connect API
    (`CallSid`, `Status`, `Duration`, `CustomField`). We echo our `OutboundCall`
    id back in `CustomField` when dialling, so this settles the right row even
    when a failed dial never produced a usable sid.

    Always 200s on a payload it cannot place: a vendor that gets a 500 retries
    for hours, and a callback for a call we do not know about is not an error.
    """
    if settings.exotel_webhook_token and token != settings.exotel_webhook_token:
        # 404 rather than 401: an unauthenticated prober learns nothing about
        # whether this path exists.
        raise HTTPException(status_code=404, detail="not found")

    form = await request.form()
    payload = {key: str(value) for key, value in form.items()}
    raw_state = (payload.get("Status") or "").lower()
    try:
        state = CallState(raw_state)
    except ValueError:
        logger.warning("unknown exotel call state %r on callback", raw_state)
        state = CallState.FAILED

    handle = CallHandle(
        provider=get_telephony_provider(settings).name,
        call_sid=payload.get("CallSid", ""),
        state=state,
        duration_seconds=_duration(payload.get("Duration")),
    )
    call = await campaign_svc.record_call_result(
        session, handle=handle, reference=payload.get("CustomField")
    )
    await session.commit()
    return {"ok": True, "matched": call is not None, "state": str(state)}
