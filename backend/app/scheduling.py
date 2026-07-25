"""Slot inventory and constraint-safe booking (doc 03 §2, doc 01 §4.4).

> "Slot inventory per doctor/dept (admin-configured), capacity-aware; oncology
>  slot types: new consult / follow-up / chemo review."
> "double-booking impossible (DB constraint)" — doc 03 §2

Three callers book: the AI receptionist mid-call (`app.receptionist`), the
coordinator through `app.routes.appointments`, and the WhatsApp bot. They race by
construction, so this module never decides "is there room?" in Python and then
writes — the *database* decides, in one statement:

    UPDATE appointment_slots SET booked = booked + 1
     WHERE id = :slot AND booked < capacity AND NOT blocked

Zero rows back means the slot filled while we were talking, and the caller is
offered the next one. `CHECK (booked <= capacity)` and
`UNIQUE (slot_id, seat_no)` sit underneath as the constraints doc 03 §2 asks for
by name: even a future bug in this file cannot produce two patients in one seat.

## Templates generate slots; slots are booked

`SlotTemplate` is what an admin authors ("Dr Sharma, Tuesdays 10:00–13:00, 15
minutes, follow-ups"). `generate_slots` materialises real `AppointmentSlot` rows
from it for a date range — idempotently, so the nightly job and a manual re-run
produce the same inventory. Nothing here ever deletes a slot: an unwanted clinic
is `blocked`, which is invisible to callers but keeps the bookings that happened.

## Local time is the clinic's time

Templates carry a wall-clock `start_time` in the hospital's timezone. A clinic
that starts at 10:00 starts at 10:00 in Alwar regardless of where the container
runs, so generation composes date + local time in `settings.timezone` and stores
the resulting instant. Everything after that is timezone-aware UTC.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Select, and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.enums import AppointmentStatus, Channel, SlotType
from app.models.org import Department, Doctor
from app.models.patient import Patient
from app.models.scheduling import Appointment, AppointmentSlot, SlotTemplate

logger = logging.getLogger(__name__)

#: How far ahead the nightly generator materialises inventory. Long enough that a
#: caller in July can book an August follow-up; short enough that an admin editing
#: a template does not have to unpick a year of slots.
GENERATION_HORIZON_DAYS = 60

#: Booking against the past is a data-entry bug, not an appointment. A small grace
#: window keeps a call that started before the slot began from failing at the end.
BOOKING_GRACE_MINUTES = 5


class BookingError(Exception):
    """A booking that cannot proceed. Carries a caller-safe reason."""


class SlotUnavailable(BookingError):
    """The slot is full, blocked, in the past, or gone. The caller offers another."""


def hospital_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


@dataclass(frozen=True, slots=True)
class SlotOffer:
    """One slot as a caller (voice, WhatsApp, console) needs to describe it.

    A flat read-model rather than the ORM row: the receptionist reads these aloud
    from a detached call context, where a lazy load would be a `MissingGreenlet`.
    """

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

    def local_starts_at(self) -> datetime:
        return self.starts_at.astimezone(hospital_tz())


# -- inventory generation ------------------------------------------------------


async def active_templates(
    session: AsyncSession, *, doctor_id: uuid.UUID | None = None
) -> list[SlotTemplate]:
    stmt = select(SlotTemplate).where(
        SlotTemplate.active.is_(True), SlotTemplate.deleted_at.is_(None)
    )
    if doctor_id is not None:
        stmt = stmt.where(SlotTemplate.doctor_id == doctor_id)
    return list((await session.execute(stmt.order_by(SlotTemplate.start_time))).scalars().all())


def _instants(template: SlotTemplate, day: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """Every (start, end) the template produces on `day`, in UTC."""
    if day.weekday() != template.weekday:
        return []
    step = timedelta(minutes=template.slot_minutes)
    cursor = datetime.combine(day, template.start_time, tzinfo=tz)
    clinic_end = datetime.combine(day, template.end_time, tzinfo=tz)
    out: list[tuple[datetime, datetime]] = []
    while cursor + step <= clinic_end:
        out.append((cursor.astimezone(UTC), (cursor + step).astimezone(UTC)))
        cursor += step
    return out


async def generate_slots(
    session: AsyncSession,
    *,
    start: date,
    days: int = GENERATION_HORIZON_DAYS,
    doctor_id: uuid.UUID | None = None,
) -> list[AppointmentSlot]:
    """Materialise slots from templates for `days` days starting at `start`.

    Idempotent: an instant that already has a slot for that doctor is skipped, so
    re-running never duplicates inventory or resets `booked`. Returns only the
    slots it created, which is what the nightly job logs.
    """
    tz = hospital_tz()
    templates = await active_templates(session, doctor_id=doctor_id)
    if not templates:
        return []

    wanted: dict[tuple[uuid.UUID, datetime], tuple[SlotTemplate, datetime]] = {}
    for template in templates:
        for offset in range(days):
            for starts_at, ends_at in _instants(template, start + timedelta(days=offset), tz):
                # First template wins a contested instant — two clinics for one
                # doctor at one moment is an authoring error, and silently merging
                # them is friendlier than refusing to generate the whole month.
                wanted.setdefault((template.doctor_id, starts_at), (template, ends_at))

    existing = await session.execute(
        select(AppointmentSlot.doctor_id, AppointmentSlot.starts_at).where(
            AppointmentSlot.starts_at
            >= datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC),
            AppointmentSlot.starts_at
            < datetime.combine(start + timedelta(days=days), time.min, tzinfo=tz).astimezone(UTC),
        )
    )
    have = {(row.doctor_id, row.starts_at) for row in existing}

    created: list[AppointmentSlot] = []
    for (doctor, starts_at), (template, ends_at) in sorted(wanted.items(), key=lambda kv: kv[0][1]):
        if (doctor, starts_at) in have:
            continue
        slot = AppointmentSlot(
            template_id=template.id,
            department_id=template.department_id,
            doctor_id=doctor,
            starts_at=starts_at,
            ends_at=ends_at,
            slot_type=template.slot_type,
            capacity=template.capacity,
            booked=0,
        )
        session.add(slot)
        created.append(slot)
    await session.flush()
    logger.info("generated %d slots from %d templates", len(created), len(templates))
    return created


# -- finding slots -------------------------------------------------------------


def _open_slots_stmt(
    *,
    now: datetime,
    department_id: uuid.UUID | None,
    doctor_id: uuid.UUID | None,
    slot_type: SlotType | None,
    until: datetime | None,
) -> Select:
    stmt = (
        select(AppointmentSlot, Doctor, Department)
        .join(Doctor, Doctor.id == AppointmentSlot.doctor_id)
        .join(Department, Department.id == AppointmentSlot.department_id)
        .where(
            AppointmentSlot.deleted_at.is_(None),
            AppointmentSlot.blocked.is_(False),
            AppointmentSlot.booked < AppointmentSlot.capacity,
            AppointmentSlot.starts_at >= now,
        )
        .order_by(AppointmentSlot.starts_at)
    )
    if department_id is not None:
        stmt = stmt.where(AppointmentSlot.department_id == department_id)
    if doctor_id is not None:
        stmt = stmt.where(AppointmentSlot.doctor_id == doctor_id)
    if slot_type is not None:
        stmt = stmt.where(AppointmentSlot.slot_type == slot_type)
    if until is not None:
        stmt = stmt.where(AppointmentSlot.starts_at < until)
    return stmt


async def find_slots(
    session: AsyncSession,
    *,
    department_code: str | None = None,
    department_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    slot_type: SlotType | None = None,
    on_date: date | None = None,
    after: datetime | None = None,
    until: datetime | None = None,
    limit: int = 5,
) -> list[SlotOffer]:
    """Open slots, soonest first — the list a receptionist reads out.

    "Open" is decided by the same predicate the booking UPDATE uses, so a slot
    that appears here is one the database would currently accept. It can still
    fill in the seconds before the caller says yes; that is what `book`'s
    zero-rows path is for.
    """
    if department_code is not None and department_id is None:
        found = await session.execute(
            select(Department.id).where(
                Department.code == department_code, Department.deleted_at.is_(None)
            )
        )
        department_id = found.scalar_one_or_none()
        if department_id is None:
            raise BookingError(f"unknown department {department_code!r}")

    tz = hospital_tz()
    now = after or datetime.now(UTC)
    if on_date is not None:
        day_start = datetime.combine(on_date, time.min, tzinfo=tz).astimezone(UTC)
        now = max(now, day_start)
        until = min(until or datetime.max.replace(tzinfo=UTC), day_start + timedelta(days=1))

    stmt = _open_slots_stmt(
        now=now,
        department_id=department_id,
        doctor_id=doctor_id,
        slot_type=slot_type,
        until=until,
    ).limit(limit)

    return [
        SlotOffer(
            slot_id=slot.id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            doctor_id=doctor.id,
            doctor_name=doctor.name,
            department_id=department.id,
            department_code=department.code,
            department_name=department.name,
            slot_type=slot.slot_type,
            seats_left=slot.capacity - slot.booked,
        )
        for slot, doctor, department in (await session.execute(stmt)).all()
    ]


# -- booking -------------------------------------------------------------------


async def _claim_seat(session: AsyncSession, slot_id: uuid.UUID, *, now: datetime) -> int:
    """Take one seat in `slot_id`, or raise `SlotUnavailable`.

    The UPDATE is the whole concurrency story: Postgres locks the slot row for the
    rest of the transaction, so the seat-number query below sees a settled world
    and two simultaneous bookers cannot both read `booked = 0`.
    """
    claimed = await session.execute(
        update(AppointmentSlot)
        .where(
            AppointmentSlot.id == slot_id,
            AppointmentSlot.deleted_at.is_(None),
            AppointmentSlot.blocked.is_(False),
            AppointmentSlot.booked < AppointmentSlot.capacity,
            AppointmentSlot.starts_at >= now - timedelta(minutes=BOOKING_GRACE_MINUTES),
        )
        .values(booked=AppointmentSlot.booked + 1)
        .returning(AppointmentSlot.capacity)
    )
    row = claimed.first()
    if row is None:
        raise SlotUnavailable("that time is no longer free")
    capacity = row.capacity

    # The seat *number* is not `booked` — a cancellation frees seat 1 while seat 2
    # is still occupied, so the next booker must take 1, not 2.
    taken = await session.execute(
        select(Appointment.seat_no).where(
            Appointment.slot_id == slot_id, Appointment.seat_no.is_not(None)
        )
    )
    used = {int(value) for (value,) in taken.all()}
    for seat in range(1, capacity + 1):
        if seat not in used:
            return seat
    # Unreachable while the UPDATE guard holds; treated as "full" rather than a
    # crash, because a caller on the phone needs an answer either way.
    raise SlotUnavailable("that time is no longer free")


async def _release_seat(session: AsyncSession, appointment: Appointment) -> None:
    if appointment.slot_id is None or appointment.seat_no is None:
        return
    await session.execute(
        update(AppointmentSlot)
        .where(AppointmentSlot.id == appointment.slot_id, AppointmentSlot.booked > 0)
        .values(booked=AppointmentSlot.booked - 1)
    )
    appointment.seat_no = None


async def book(
    session: AsyncSession,
    *,
    patient: Patient,
    slot_id: uuid.UUID,
    source: Channel,
    status: AppointmentStatus = AppointmentStatus.BOOKED,
) -> Appointment:
    """Book `patient` into `slot_id`. Raises `SlotUnavailable` if it filled."""
    slot = await session.get(AppointmentSlot, slot_id)
    if slot is None or slot.deleted_at is not None:
        raise SlotUnavailable("that appointment time no longer exists")

    now = datetime.now(UTC)
    if await _existing_booking(session, patient_id=patient.id, slot_id=slot_id) is not None:
        raise BookingError("this patient already has that appointment")

    # Claim + insert inside one savepoint: if the seat constraint fires, the seat
    # count is un-incremented with it. Rolling back the *whole* transaction here
    # would take the caller's other work (a patient row, an intake) with it.
    try:
        async with session.begin_nested():
            seat_no = await _claim_seat(session, slot_id, now=now)
            appointment = Appointment(
                patient_id=patient.id,
                department_id=slot.department_id,
                doctor_id=slot.doctor_id,
                slot_at=slot.starts_at,
                status=status,
                source=source,
                slot_id=slot.id,
                seat_no=seat_no,
                slot_type=slot.slot_type,
                reminders=[],
            )
            session.add(appointment)
            await session.flush()
    except IntegrityError as exc:
        # The seat unique constraint fired: another transaction took this seat
        # between our claim and our insert. Same outcome for the caller as a full
        # slot — and the constraint, not this code, is what kept the clinic honest.
        raise SlotUnavailable("that time was just taken") from exc

    await session.refresh(slot)
    return appointment


async def reschedule(
    session: AsyncSession, *, appointment: Appointment, slot_id: uuid.UUID
) -> Appointment:
    """Move an appointment to another slot, releasing the old seat.

    The new seat is claimed *before* the old one is released, so a failed move
    leaves the patient with the appointment they already had rather than none.
    """
    if appointment.status is AppointmentStatus.CANCELLED:
        raise BookingError("that appointment was cancelled; book a new one")
    if appointment.slot_id == slot_id:
        return appointment

    slot = await session.get(AppointmentSlot, slot_id)
    if slot is None or slot.deleted_at is not None:
        raise SlotUnavailable("that appointment time no longer exists")

    old_slot_id, old_seat = appointment.slot_id, appointment.seat_no

    try:
        async with session.begin_nested():
            seat_no = await _claim_seat(session, slot_id, now=datetime.now(UTC))
            # Release the old seat in the same savepoint, so a failed move leaves
            # the patient holding the appointment they already had.
            await _release_seat(session, appointment)
            appointment.slot_id = slot.id
            appointment.seat_no = seat_no
            appointment.slot_at = slot.starts_at
            appointment.doctor_id = slot.doctor_id
            appointment.department_id = slot.department_id
            appointment.slot_type = slot.slot_type
            appointment.status = AppointmentStatus.RESCHEDULED
            await session.flush()
    except IntegrityError as exc:
        raise SlotUnavailable("that time was just taken") from exc

    logger.info(
        "rescheduled appointment %s from slot %s seat %s to slot %s seat %s",
        appointment.id,
        old_slot_id,
        old_seat,
        slot.id,
        seat_no,
    )
    return appointment


async def cancel(
    session: AsyncSession, *, appointment: Appointment, reason: str = ""
) -> Appointment:
    """Cancel and release the seat (doc 03 §2: "cancellations release slots")."""
    if appointment.status is AppointmentStatus.CANCELLED:
        return appointment
    await _release_seat(session, appointment)
    appointment.status = AppointmentStatus.CANCELLED
    appointment.reminders = [
        *(appointment.reminders or []),
        {"at": datetime.now(UTC).isoformat(), "kind": "cancelled", "reason": reason},
    ]
    await session.flush()
    return appointment


# -- lookups the receptionist and the campaign need ----------------------------


async def _existing_booking(
    session: AsyncSession, *, patient_id: uuid.UUID, slot_id: uuid.UUID
) -> Appointment | None:
    found = await session.execute(
        select(Appointment).where(
            Appointment.patient_id == patient_id,
            Appointment.slot_id == slot_id,
            Appointment.seat_no.is_not(None),
            Appointment.deleted_at.is_(None),
        )
    )
    return found.scalars().first()


LIVE_STATUSES = (
    AppointmentStatus.BOOKED,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.RESCHEDULED,
)


async def upcoming_for_patient(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    now: datetime | None = None,
    limit: int = 5,
) -> list[Appointment]:
    """A patient's live future appointments, soonest first — what "when is my
    appointment?" answers and what reschedule/cancel operate on."""
    found = await session.execute(
        select(Appointment)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.deleted_at.is_(None),
            Appointment.status.in_(LIVE_STATUSES),
            Appointment.slot_at >= (now or datetime.now(UTC)),
        )
        .order_by(Appointment.slot_at)
        .limit(limit)
    )
    return list(found.scalars().all())


async def appointments_on(
    session: AsyncSession,
    *,
    day: date,
    statuses: tuple[AppointmentStatus, ...] = LIVE_STATUSES,
) -> list[Appointment]:
    """Every live appointment on one clinic day — the D-1 campaign's raw material."""
    tz = hospital_tz()
    day_start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    found = await session.execute(
        select(Appointment)
        .where(
            and_(
                Appointment.deleted_at.is_(None),
                Appointment.status.in_(statuses),
                Appointment.slot_at >= day_start,
                Appointment.slot_at < day_start + timedelta(days=1),
            )
        )
        .order_by(Appointment.slot_at)
    )
    return list(found.scalars().all())
