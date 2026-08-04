"""Slot inventory and booking (doc 03 §2).

The session's headline AC is negative — "double-booking impossible (DB
constraint)" — so most of this file is about what the database *refuses*. Two
tests deliberately reach past `app.scheduling` and write the raw rows a bug in
that module could produce, because a service-layer test cannot tell you whether
the constraint exists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import scheduling
from app.models.enums import AppointmentStatus, Channel, SlotType
from app.models.scheduling import Appointment
from tests.factories import (
    build_clinic,
    generation_start,
    make_patient,
    make_slot,
    make_slot_template,
)

pytestmark = pytest.mark.asyncio


def _at(days_ahead: int = 3, hour: int = 10) -> datetime:
    """A slot instant comfortably in the future, on a stable clock."""
    return (datetime.now(UTC) + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


async def _clinic_with_slot(session, **slot_kwargs):
    clinic = await build_clinic(session)
    slot = make_slot(clinic["doctor"], _at(), **slot_kwargs)
    session.add(slot)
    await session.flush()
    return clinic, slot


# -- generation ----------------------------------------------------------------


async def test_generate_slots_materialises_a_template_clinic(session):
    clinic = await build_clinic(session)
    session.add(
        make_slot_template(
            clinic["doctor"], weekday=1, start_time=time(10, 0), end_time=time(11, 0)
        )
    )
    await session.flush()

    # A window guaranteed to contain exactly one Tuesday.
    start = date(2026, 8, 3)  # Monday
    created = await scheduling.generate_slots(session, start=start, days=7)

    assert len(created) == 4  # 10:00–11:00 in 15-minute slots
    assert {s.slot_type for s in created} == {SlotType.FOLLOW_UP}
    local = [s.starts_at.astimezone(scheduling.hospital_tz()) for s in created]
    assert [d.hour for d in local] == [10, 10, 10, 10]
    assert [d.minute for d in local] == [0, 15, 30, 45]
    assert {d.date() for d in local} == {date(2026, 8, 4)}  # the Tuesday


async def test_generate_slots_is_idempotent(session):
    clinic = await build_clinic(session)
    session.add(make_slot_template(clinic["doctor"]))
    await session.flush()

    first = await scheduling.generate_slots(session, start=date(2026, 8, 3), days=7)
    again = await scheduling.generate_slots(session, start=date(2026, 8, 3), days=7)

    assert first and again == []


async def test_regeneration_does_not_reset_a_booked_slot(session):
    clinic = await build_clinic(session)
    session.add(make_slot_template(clinic["doctor"]))
    await session.flush()
    # Generated from tomorrow, not a pinned date: this test *books* the slot, and
    # booking rightly refuses an instant that has already passed. Any seven-day
    # window starting tomorrow contains the template's weekday exactly once.
    start = generation_start()
    [slot, *_] = await scheduling.generate_slots(session, start=start, days=7)

    await scheduling.book(session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE)
    await scheduling.generate_slots(session, start=start, days=7)

    await session.refresh(slot)
    assert slot.booked == 1


# -- booking -------------------------------------------------------------------


async def test_book_takes_a_seat_and_records_the_slot(session):
    clinic, slot = await _clinic_with_slot(session)

    appointment = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
    )

    assert appointment.seat_no == 1
    assert appointment.slot_id == slot.id
    assert appointment.slot_at == slot.starts_at
    assert appointment.doctor_id == clinic["doctor"].id
    assert appointment.status is AppointmentStatus.BOOKED
    await session.refresh(slot)
    assert slot.booked == 1


async def test_a_full_slot_is_refused(session):
    clinic, slot = await _clinic_with_slot(session)
    other = make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()

    await scheduling.book(session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE)

    with pytest.raises(scheduling.SlotUnavailable):
        await scheduling.book(session, patient=other, slot_id=slot.id, source=Channel.PHONE)

    await session.refresh(slot)
    assert slot.booked == 1  # the refused attempt left no trace


async def test_capacity_two_seats_two_patients(session):
    clinic, slot = await _clinic_with_slot(session, capacity=2)
    other = make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()

    first = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
    )
    second = await scheduling.book(session, patient=other, slot_id=slot.id, source=Channel.KIOSK)

    assert {first.seat_no, second.seat_no} == {1, 2}
    await session.refresh(slot)
    assert slot.booked == 2


async def test_a_blocked_slot_is_not_offered_or_bookable(session):
    clinic, slot = await _clinic_with_slot(session, blocked=True)

    assert await scheduling.find_slots(session, doctor_id=clinic["doctor"].id) == []
    with pytest.raises(scheduling.SlotUnavailable):
        await scheduling.book(
            session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
        )


async def test_a_past_slot_is_not_bookable(session):
    clinic = await build_clinic(session)
    slot = make_slot(clinic["doctor"], datetime.now(UTC) - timedelta(hours=2))
    session.add(slot)
    await session.flush()

    with pytest.raises(scheduling.SlotUnavailable):
        await scheduling.book(
            session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
        )


async def test_the_same_patient_cannot_book_one_slot_twice(session):
    clinic, slot = await _clinic_with_slot(session, capacity=2)

    await scheduling.book(session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE)
    with pytest.raises(scheduling.BookingError):
        await scheduling.book(
            session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
        )


# -- the DB constraints themselves ---------------------------------------------


async def test_the_database_refuses_two_appointments_in_one_seat(session):
    """The AC's "double-booking impossible (DB constraint)", proven by trying it.

    This writes the rows `app.scheduling` would produce if `_claim_seat` handed
    out the same seat number twice — the exact bug the constraint exists for.
    """
    clinic, slot = await _clinic_with_slot(session, capacity=2)
    other = make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()

    def _raw(patient_id) -> Appointment:
        return Appointment(
            patient_id=patient_id,
            department_id=slot.department_id,
            doctor_id=slot.doctor_id,
            slot_at=slot.starts_at,
            status=AppointmentStatus.BOOKED,
            source=Channel.PHONE,
            slot_id=slot.id,
            seat_no=1,
            slot_type=slot.slot_type,
            reminders=[],
        )

    session.add(_raw(clinic["patient"].id))
    await session.flush()

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(_raw(other.id))
            await session.flush()


async def test_the_database_refuses_booked_over_capacity(session):
    """`ck_appointment_slots_booked_within_capacity` — the second net under a
    service layer that miscounts."""
    _, slot = await _clinic_with_slot(session, capacity=1)

    with pytest.raises((IntegrityError, DBAPIError)):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE appointment_slots SET booked = 5 WHERE id = :id"), {"id": slot.id}
            )


async def test_the_loser_of_a_race_is_refused_not_crashed(session):
    """The receptionist's real failure mode: a caller is *offered* the last seat,
    hesitates, and someone else takes it before they say yes.

    The competing booker is simulated by filling the slot between the offer and
    the booking — the same state the loser's UPDATE would find. The loser must get
    `SlotUnavailable` (which the receptionist turns into "that one just went — how
    about…"), and must leave the slot exactly as it found it.
    """
    clinic, slot = await _clinic_with_slot(session, capacity=1)
    other = make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()

    [offer] = await scheduling.find_slots(session, doctor_id=clinic["doctor"].id)

    # Somebody else's transaction lands first.
    await scheduling.book(session, patient=other, slot_id=offer.slot_id, source=Channel.WHATSAPP)

    with pytest.raises(scheduling.SlotUnavailable):
        await scheduling.book(
            session, patient=clinic["patient"], slot_id=offer.slot_id, source=Channel.PHONE
        )

    await session.refresh(slot)
    assert slot.booked == 1
    live = await session.execute(
        select(Appointment).where(Appointment.slot_id == slot.id, Appointment.seat_no.is_not(None))
    )
    assert len(live.scalars().all()) == 1


# -- reschedule / cancel -------------------------------------------------------


async def test_reschedule_moves_the_seat_and_frees_the_old_slot(session):
    clinic, first = await _clinic_with_slot(session)
    second = make_slot(clinic["doctor"], _at(days_ahead=4))
    session.add(second)
    await session.flush()

    appointment = await scheduling.book(
        session, patient=clinic["patient"], slot_id=first.id, source=Channel.PHONE
    )
    await scheduling.reschedule(session, appointment=appointment, slot_id=second.id)

    await session.refresh(first)
    await session.refresh(second)
    assert first.booked == 0
    assert second.booked == 1
    assert appointment.slot_id == second.id
    assert appointment.slot_at == second.starts_at
    assert appointment.status is AppointmentStatus.RESCHEDULED


async def test_a_failed_reschedule_leaves_the_original_appointment_intact(session):
    clinic, first = await _clinic_with_slot(session)
    full = make_slot(clinic["doctor"], _at(days_ahead=4), capacity=1, booked=1)
    session.add(full)
    await session.flush()

    appointment = await scheduling.book(
        session, patient=clinic["patient"], slot_id=first.id, source=Channel.PHONE
    )
    with pytest.raises(scheduling.SlotUnavailable):
        await scheduling.reschedule(session, appointment=appointment, slot_id=full.id)

    assert appointment.slot_id == first.id
    assert appointment.seat_no == 1
    await session.refresh(first)
    assert first.booked == 1


async def test_cancel_releases_the_seat_for_someone_else(session):
    clinic, slot = await _clinic_with_slot(session)
    other = make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()

    appointment = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
    )
    await scheduling.cancel(session, appointment=appointment, reason="patient called")

    assert appointment.status is AppointmentStatus.CANCELLED
    assert appointment.seat_no is None
    assert appointment.slot_id == slot.id  # history: which slot was released
    await session.refresh(slot)
    assert slot.booked == 0

    # The freed seat is genuinely bookable, and reuses seat number 1.
    replacement = await scheduling.book(
        session, patient=other, slot_id=slot.id, source=Channel.WHATSAPP
    )
    assert replacement.seat_no == 1


async def test_a_freed_middle_seat_is_reused_not_duplicated(session):
    """Cancelling seat 1 of 2 must hand the next booker seat 1 — handing out
    `booked + 1` would collide with the live seat 2."""
    clinic, slot = await _clinic_with_slot(session, capacity=2)
    second_patient = make_patient(clinic["hospital"])
    third_patient = make_patient(clinic["hospital"])
    session.add_all([second_patient, third_patient])
    await session.flush()

    first = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
    )
    second = await scheduling.book(
        session, patient=second_patient, slot_id=slot.id, source=Channel.PHONE
    )
    assert (first.seat_no, second.seat_no) == (1, 2)

    await scheduling.cancel(session, appointment=first)
    third = await scheduling.book(
        session, patient=third_patient, slot_id=slot.id, source=Channel.PHONE
    )
    assert third.seat_no == 1


# -- lookups -------------------------------------------------------------------


async def test_find_slots_orders_by_time_and_filters_by_type(session):
    clinic = await build_clinic(session)
    later = make_slot(clinic["doctor"], _at(days_ahead=5), slot_type=SlotType.FOLLOW_UP)
    sooner = make_slot(clinic["doctor"], _at(days_ahead=2), slot_type=SlotType.NEW_CONSULT)
    session.add_all([later, sooner])
    await session.flush()

    offers = await scheduling.find_slots(session, doctor_id=clinic["doctor"].id)
    assert [o.slot_id for o in offers] == [sooner.id, later.id]
    assert offers[0].doctor_name == clinic["doctor"].name
    assert offers[0].department_code == clinic["department"].code

    typed = await scheduling.find_slots(
        session, doctor_id=clinic["doctor"].id, slot_type=SlotType.FOLLOW_UP
    )
    assert [o.slot_id for o in typed] == [later.id]


async def test_upcoming_for_patient_skips_cancelled_and_past(session):
    clinic, slot = await _clinic_with_slot(session)
    past = make_slot(clinic["doctor"], datetime.now(UTC) - timedelta(days=1))
    session.add(past)
    await session.flush()

    live = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
    )
    stale = Appointment(
        patient_id=clinic["patient"].id,
        department_id=past.department_id,
        doctor_id=past.doctor_id,
        slot_at=past.starts_at,
        status=AppointmentStatus.BOOKED,
        source=Channel.PHONE,
        reminders=[],
    )
    session.add(stale)
    await session.flush()

    upcoming = await scheduling.upcoming_for_patient(session, patient_id=clinic["patient"].id)
    assert [a.id for a in upcoming] == [live.id]

    await scheduling.cancel(session, appointment=live)
    assert await scheduling.upcoming_for_patient(session, patient_id=clinic["patient"].id) == []
