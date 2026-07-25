"""The inbound appointment line, end to end over the fake Exotel client (S15 AC).

> "**AC:** fake-client books/reschedules/cancels against real slots" — doc 06 S15

This is that AC in full: a fake carrier pushes audio and keypad frames through
the real wire protocol, the real call driver runs, and a real row lands in
Postgres against real inventory. The only fakes are the ones the provider layer
requires — no vendor is touched.

The caller's *words* do not matter here (the STT is scripted), so the utterances
are `speech()` clips; what is asserted is the appointment at the end of it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta


from app import scheduling
from app.models.enums import AppointmentStatus, Channel, Lang, Role, SlotType
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import Patient
from app.models.scheduling import AppointmentSlot
from app.providers import FakeSTTProvider, FakeTTSProvider
from app.providers.telephony import FakeTelephonyProvider
from app.receptionist import Intent, Receptionist

from gw import reception
from gw.fake_exotel import FakeExotelClient, FakeTransport, speech

CLI = "+919876512345"


class ScriptedReceptionist(Receptionist):
    """The real state machine with the classifier pinned.

    `app.receptionist`'s LLM handling has its own tests; pinning the intent here
    keeps this file about the *channel* — frames in, an appointment out — instead
    of re-testing the classifier through six layers of audio.
    """

    def __init__(self, intent: Intent) -> None:
        super().__init__()
        self._intent = intent

    async def _route_intent(self, session, state, utterance):
        from app.receptionist import IntentGuess

        async def _pinned(*args, **kwargs):
            return IntentGuess(intent=self._intent, confidence=0.95, reason="pinned for the test")

        import app.receptionist as rec

        original = rec.classify_intent
        rec.classify_intent = _pinned
        try:
            return await super()._route_intent(session, state, utterance)
        finally:
            rec.classify_intent = original


async def _clinic(session, *, slots: int = 3):
    """A hospital, a doctor, a patient reachable at CLI, and bookable slots."""
    suffix = datetime.now(UTC).strftime("%H%M%S%f")
    hospital = Hospital(name="Alwar Cancer Hospital", code=f"ACH{suffix}")
    session.add(hospital)
    await session.flush()

    department = Department(hospital_id=hospital.id, name="Medical Oncology", code=f"MO{suffix}")
    session.add(department)
    await session.flush()

    user = User(
        hospital_id=hospital.id, name="Dr Meena", phone=f"+9198{suffix[:8]}", role=Role.DOCTOR
    )
    session.add(user)
    await session.flush()
    doctor = Doctor(
        user_id=user.id,
        department_id=department.id,
        name="Meena Sharma",
        phone=user.phone,
        reg_no=f"REG{suffix}",
    )
    patient = Patient(
        hospital_id=hospital.id,
        mrn=f"MRN{suffix}",
        name="Kamla Devi",
        phone=CLI,
        lang=Lang.HI,
    )
    session.add_all([doctor, patient])
    await session.flush()

    made = []
    for day in range(1, slots + 1):
        starts = (datetime.now(UTC) + timedelta(days=day)).replace(
            hour=5, minute=0, second=0, microsecond=0
        )
        slot = AppointmentSlot(
            department_id=department.id,
            doctor_id=doctor.id,
            starts_at=starts,
            ends_at=starts + timedelta(minutes=15),
            slot_type=SlotType.FOLLOW_UP,
            capacity=1,
        )
        made.append(slot)
    session.add_all(made)
    await session.flush()
    return {"hospital": hospital, "doctor": doctor, "patient": patient, "slots": made}


async def _drive(call_sessionmaker, settings, *, intent: Intent, script: list, lang: str = "hi"):
    """Run one receptionist call: greeting, then each scripted caller turn."""
    transport = FakeTransport()
    client = FakeExotelClient(transport)

    async def run_driver():
        return await reception.handle_receptionist_call(
            transport,
            sessionmaker=call_sessionmaker,
            settings=settings,
            tts=FakeTTSProvider(),
            stt=FakeSTTProvider(script=["appointment chahiye"] * 6),
            receptionist=ScriptedReceptionist(intent),
        )

    driver = asyncio.create_task(run_driver())
    await client.start(cli=CLI, lang=lang)
    await client.drain()  # the greeting

    for step in script:
        if isinstance(step, str):  # a keypad press
            await client.dtmf(step)
            await client.drain()
        else:
            await client.say(step)

    await client.hangup()
    record = await driver
    return record, client.result


# -- book ----------------------------------------------------------------------


async def test_a_fake_call_books_a_real_slot(db_session, call_sessionmaker, settings, providers):
    clinic = await _clinic(db_session)

    record, result = await _drive(
        call_sessionmaker, settings, intent=Intent.BOOK, script=[speech(), "1"]
    )

    assert record.booked_appointment_id is not None
    assert record.end_reason == "complete"
    assert result.assistant_frames > 0, "the caller must hear the offer"

    booked = await scheduling.upcoming_for_patient(db_session, patient_id=clinic["patient"].id)
    assert [str(a.id) for a in booked] == [record.booked_appointment_id]
    assert booked[0].slot_id == clinic["slots"][0].id
    assert booked[0].source is Channel.PHONE
    assert booked[0].seat_no == 1


async def test_the_booking_survives_the_caller_hanging_up(
    db_session, call_sessionmaker, settings, providers
):
    """Committed per turn: a caller who drops the line the instant the booking
    lands still has the appointment when they arrive."""
    clinic = await _clinic(db_session)

    record, _ = await _drive(
        call_sessionmaker, settings, intent=Intent.BOOK, script=[speech(), "2"]
    )

    booked = await scheduling.upcoming_for_patient(db_session, patient_id=clinic["patient"].id)
    assert len(booked) == 1
    assert booked[0].slot_id == clinic["slots"][1].id


# -- reschedule ----------------------------------------------------------------


async def test_a_fake_call_reschedules_an_existing_appointment(
    db_session, call_sessionmaker, settings, providers
):
    clinic = await _clinic(db_session)
    existing = await scheduling.book(
        db_session,
        patient=clinic["patient"],
        slot_id=clinic["slots"][0].id,
        source=Channel.KIOSK,
    )
    await db_session.commit()

    await _drive(call_sessionmaker, settings, intent=Intent.RESCHEDULE, script=[speech(), "1"])

    await db_session.refresh(existing)
    assert existing.status is AppointmentStatus.RESCHEDULED
    assert existing.slot_id == clinic["slots"][1].id
    await db_session.refresh(clinic["slots"][0])
    assert clinic["slots"][0].booked == 0, "the old seat must be released"


# -- cancel --------------------------------------------------------------------


async def test_a_fake_call_cancels_on_the_keypad(
    db_session, call_sessionmaker, settings, providers
):
    clinic = await _clinic(db_session)
    existing = await scheduling.book(
        db_session,
        patient=clinic["patient"],
        slot_id=clinic["slots"][0].id,
        source=Channel.KIOSK,
    )
    await db_session.commit()

    await _drive(call_sessionmaker, settings, intent=Intent.CANCEL, script=[speech(), "1"])

    await db_session.refresh(existing)
    assert existing.status is AppointmentStatus.CANCELLED
    assert existing.seat_no is None
    await db_session.refresh(clinic["slots"][0])
    assert clinic["slots"][0].booked == 0


# -- handoff -------------------------------------------------------------------


async def test_a_handoff_transfers_the_call_with_the_whisper_line(
    db_session, call_sessionmaker, settings, providers, monkeypatch
):
    """doc 03 §2: the coordinator hears who is on the line before they take it."""
    from app.providers.registry import get_telephony_provider

    clinic = await _clinic(db_session)
    # The registry's own fake — the same instance the driver will reach for.
    telephony = get_telephony_provider(settings)
    assert isinstance(telephony, FakeTelephonyProvider)
    monkeypatch.setattr(settings, "coordinator_phone", "+919000000001")

    record, _ = await _drive(call_sessionmaker, settings, intent=Intent.HUMAN, script=[speech()])

    assert record.handed_off
    assert record.end_reason == "handoff"
    assert telephony.last_transfer is not None
    assert telephony.last_transfer.to == "+919000000001"
    assert clinic["patient"].name in telephony.last_transfer.whisper
    assert "asked for a person" in telephony.last_transfer.whisper


async def test_a_handoff_without_a_coordinator_number_does_not_crash_the_call(
    db_session, call_sessionmaker, settings, providers
):
    """An unset COORDINATOR_PHONE is a misconfiguration; dropping the caller's
    websocket over it would make it a patient-facing outage."""
    await _clinic(db_session)
    assert not settings.coordinator_phone

    record, _ = await _drive(call_sessionmaker, settings, intent=Intent.HUMAN, script=[speech()])

    assert record.handed_off
    assert record.whisper


# -- unknown caller ------------------------------------------------------------


async def test_an_unknown_caller_is_handed_over_before_anything_is_booked(
    db_session, call_sessionmaker, settings, providers
):
    await _clinic(db_session)

    transport = FakeTransport()
    client = FakeExotelClient(transport)

    async def run_driver():
        return await reception.handle_receptionist_call(
            transport,
            sessionmaker=call_sessionmaker,
            settings=settings,
            tts=FakeTTSProvider(),
            stt=FakeSTTProvider(script=["appointment"]),
            receptionist=ScriptedReceptionist(Intent.BOOK),
        )

    driver = asyncio.create_task(run_driver())
    await client.start(cli="+919999000111", lang="hi")
    await client.drain()
    await client.hangup()
    record = await driver

    assert record.handed_off
    assert record.booked_appointment_id is None
    assert "Unknown caller" in record.whisper
