"""The inbound AI receptionist (doc 03 §2, doc 01 §4.4).

The AC is "fake-client books/reschedules/cancels against real slots", and these
tests are the service-layer half of it: a scripted LLM returns an intent, and the
call drives real inventory. The audio half is `voice-gw/tests/test_receptionist_call.py`.

The LLM is always the fake provider with a queued reply — a real model in a test
would make the suite measure the vendor's mood.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app import receptionist as rec
from app import scheduling
from app.models.enums import AppointmentStatus, Channel, Lang
from app.providers.llm import FakeLLMProvider, FakeLLMScript
from tests.factories import build_clinic, make_slot

pytestmark = pytest.mark.asyncio


def _llm(*intents: dict) -> list[FakeLLMProvider]:
    """A fake LLM that answers each classify_intent call with the next payload."""
    provider = FakeLLMProvider()
    provider.queue(*(FakeLLMScript(text=json.dumps(payload)) for payload in intents))
    return [provider]


def _at(days: int = 3, hour: int = 10) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


async def _clinic_with_slots(session, count: int = 3):
    clinic = await build_clinic(session)
    slots = [make_slot(clinic["doctor"], _at(days=d + 1)) for d in range(count)]
    session.add_all(slots)
    await session.flush()
    return clinic, slots


# -- the intent classifier -----------------------------------------------------


async def test_intent_is_read_from_the_model(providers):
    guess = await rec.classify_intent(
        "appointment chahiye",
        providers=_llm({"intent": "book", "confidence": 0.9, "reason": "wants a slot"}),
    )
    assert guess.intent is rec.Intent.BOOK
    assert not guess.needs_human


async def test_an_invented_intent_goes_to_a_human(providers):
    guess = await rec.classify_intent(
        "kuch bhi", providers=_llm({"intent": "TRANSFER_TO_WARD", "confidence": 0.99})
    )
    assert guess.intent is rec.Intent.HUMAN
    assert guess.needs_human
    assert not guess.from_model


async def test_low_confidence_goes_to_a_human(providers):
    guess = await rec.classify_intent(
        "hmm", providers=_llm({"intent": "book", "confidence": 0.3})
    )
    assert guess.intent is rec.Intent.BOOK
    assert guess.needs_human  # the intent is kept for the whisper; the human decides


async def test_a_dead_classifier_is_a_handoff_not_an_error(providers):
    from app.providers.base import ProviderUnavailable

    broken = FakeLLMProvider()
    broken.fail_with = ProviderUnavailable("gemini down")
    guess = await rec.classify_intent("appointment", providers=[broken])
    assert guess.intent is rec.Intent.HUMAN
    assert not guess.from_model


# -- booking on a call ---------------------------------------------------------


async def test_a_call_books_a_real_slot(session, providers, sms):
    clinic, slots = await _clinic_with_slots(session)
    receptionist = rec.Receptionist(
        providers=_llm({"intent": "book", "confidence": 0.95, "reason": "wants an appointment"})
    )

    state, greeting = await receptionist.open(session, cli=clinic["patient"].phone)
    assert clinic["patient"].name in greeting.text
    assert not greeting.handoff

    offered = await receptionist.handle(session, state, "mujhe dikhana hai")
    assert offered.expects_digit
    assert state.step is rec.Step.CHOOSING
    assert len(state.offers) == rec.OFFER_COUNT

    done = await receptionist.handle(session, state, "1")
    assert done.done
    assert state.booked_appointment_id is not None

    booked = await scheduling.upcoming_for_patient(session, patient_id=clinic["patient"].id)
    assert [a.id for a in booked] == [state.booked_appointment_id]
    assert booked[0].slot_id == slots[0].id
    assert booked[0].source is Channel.PHONE
    assert booked[0].status is AppointmentStatus.CONFIRMED
    await session.refresh(slots[0])
    assert slots[0].booked == 1


async def test_a_booking_sends_whatsapp_and_sms(session, providers, sms):
    """doc 03 §2's AC: "every booking generates WhatsApp+SMS"."""
    from app.providers.registry import get_messaging_provider

    clinic, _ = await _clinic_with_slots(session)
    receptionist = rec.Receptionist(providers=_llm({"intent": "book", "confidence": 0.9}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)
    await receptionist.handle(session, state, "appointment chahiye")
    await receptionist.handle(session, state, "1")

    assert sms.sent, "no SMS confirmation went out"
    assert get_messaging_provider().sent, "no WhatsApp confirmation went out"
    appointment = await session.get(
        type(await _one_appointment(session, clinic)), state.booked_appointment_id
    )
    channels = {entry["channel"] for entry in appointment.reminders}
    assert channels == {"whatsapp", "sms"}


async def _one_appointment(session, clinic):
    [appointment] = await scheduling.upcoming_for_patient(
        session, patient_id=clinic["patient"].id
    )
    return appointment


async def test_a_spoken_number_works_as_well_as_the_keypad(session, providers, sms):
    clinic, slots = await _clinic_with_slots(session)
    receptionist = rec.Receptionist(providers=_llm({"intent": "book", "confidence": 0.9}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)
    await receptionist.handle(session, state, "appointment")

    await receptionist.handle(session, state, "haan, doosra wala")

    booked = await scheduling.upcoming_for_patient(session, patient_id=clinic["patient"].id)
    assert booked[0].slot_id == slots[1].id


async def test_a_slot_taken_mid_call_is_re_offered_not_an_error(session, providers, sms):
    clinic, slots = await _clinic_with_slots(session)
    receptionist = rec.Receptionist(providers=_llm({"intent": "book", "confidence": 0.9}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)
    await receptionist.handle(session, state, "appointment")

    # Somebody else takes the slot we just read out.
    from tests.factories import make_patient

    other = make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    await scheduling.book(session, patient=other, slot_id=slots[0].id, source=Channel.WHATSAPP)

    reply = await receptionist.handle(session, state, "1")

    assert not reply.done
    assert reply.expects_digit
    assert rec.say("taken", Lang.HI) in reply.text
    assert slots[0].id not in {offer.slot_id for offer in state.offers}


# -- reschedule / cancel / status ----------------------------------------------


async def test_a_call_reschedules_an_existing_appointment(session, providers, sms):
    clinic, slots = await _clinic_with_slots(session)
    existing = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slots[0].id, source=Channel.KIOSK
    )

    receptionist = rec.Receptionist(
        providers=_llm({"intent": "reschedule", "confidence": 0.9, "when_hint": "agle hafte"})
    )
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)
    await receptionist.handle(session, state, "us din nahi aa paunga")
    # Offer 1 is now the second slot — the first is the one they already hold.
    reply = await receptionist.handle(session, state, "1")

    assert reply.done
    await session.refresh(existing)
    assert existing.status is AppointmentStatus.RESCHEDULED
    assert existing.slot_id == state.offers[0].slot_id
    await session.refresh(slots[0])
    assert slots[0].booked == 0  # the old seat was released


async def test_a_call_cancels_after_confirming_on_the_keypad(session, providers, sms):
    clinic, slots = await _clinic_with_slots(session)
    existing = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slots[0].id, source=Channel.KIOSK
    )

    receptionist = rec.Receptionist(providers=_llm({"intent": "cancel", "confidence": 0.95}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)
    asked = await receptionist.handle(session, state, "cancel kar do")
    assert asked.expects_digit
    assert state.step is rec.Step.CONFIRMING

    reply = await receptionist.handle(session, state, "1")

    assert reply.done
    await session.refresh(existing)
    assert existing.status is AppointmentStatus.CANCELLED
    assert existing.seat_no is None
    await session.refresh(slots[0])
    assert slots[0].booked == 0


async def test_pressing_two_keeps_the_appointment(session, providers, sms):
    clinic, slots = await _clinic_with_slots(session)
    existing = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slots[0].id, source=Channel.KIOSK
    )
    receptionist = rec.Receptionist(providers=_llm({"intent": "cancel", "confidence": 0.95}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)
    await receptionist.handle(session, state, "cancel")

    reply = await receptionist.handle(session, state, "2")

    assert reply.done
    await session.refresh(existing)
    assert existing.status is AppointmentStatus.BOOKED
    assert existing.seat_no == 1


async def test_status_reads_the_next_appointment_back(session, providers, sms):
    clinic, slots = await _clinic_with_slots(session)
    await scheduling.book(
        session, patient=clinic["patient"], slot_id=slots[0].id, source=Channel.KIOSK
    )
    receptionist = rec.Receptionist(providers=_llm({"intent": "status", "confidence": 0.9}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)

    reply = await receptionist.handle(session, state, "mera appointment kab hai")

    assert reply.done
    assert not reply.handoff
    assert clinic["doctor"].name in reply.text


# -- handoff -------------------------------------------------------------------


async def test_two_failed_turns_hand_off_with_a_whisper_summary(session, providers, sms):
    """doc 01 §4.4: "human handoff on 2 failed turns"."""
    clinic, _ = await _clinic_with_slots(session)
    receptionist = rec.Receptionist(providers=_llm({"intent": "book", "confidence": 0.9}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)
    await receptionist.handle(session, state, "appointment chahiye")

    first = await receptionist.handle(session, state, "kya bola aapne")
    assert not first.handoff  # one bad turn is a re-ask, not a transfer

    second = await receptionist.handle(session, state, "samajh nahi aaya")

    assert second.handoff
    assert second.done
    assert clinic["patient"].name in second.whisper
    assert "new appointment" in second.whisper


async def test_asking_for_a_person_transfers_immediately(session, providers, sms):
    clinic, _ = await _clinic_with_slots(session)
    receptionist = rec.Receptionist(
        providers=_llm({"intent": "human", "confidence": 0.99, "reason": "asked for staff"})
    )
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)

    reply = await receptionist.handle(session, state, "kisi se baat karao")

    assert reply.handoff
    assert "asked for a person" in reply.whisper


async def test_an_unknown_caller_is_handed_to_a_coordinator(session, providers, sms):
    await build_clinic(session)
    receptionist = rec.Receptionist(providers=_llm({"intent": "book", "confidence": 0.9}))

    state, reply = await receptionist.open(session, cli="+919999900001")

    assert reply.handoff
    assert state.patient_id is None
    assert "Unknown caller" in reply.whisper


async def test_a_reschedule_without_an_appointment_hands_off(session, providers, sms):
    clinic, _ = await _clinic_with_slots(session)
    receptionist = rec.Receptionist(providers=_llm({"intent": "reschedule", "confidence": 0.9}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)

    reply = await receptionist.handle(session, state, "badal dijiye")

    assert reply.handoff
    assert reply.text == rec.say("no_appointment", state.lang)


async def test_no_free_slots_hands_off_rather_than_inventing_one(session, providers, sms):
    clinic = await build_clinic(session)  # a clinic with no slots at all
    receptionist = rec.Receptionist(providers=_llm({"intent": "book", "confidence": 0.9}))
    state, _ = await receptionist.open(session, cli=clinic["patient"].phone)

    reply = await receptionist.handle(session, state, "appointment chahiye")

    assert reply.handoff
    assert reply.text == rec.say("no_slots", state.lang)


# -- the whisper summary -------------------------------------------------------


async def test_the_whisper_summary_names_the_appointment_being_moved(session):
    clinic, slots = await _clinic_with_slots(session)
    appointment = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slots[0].id, source=Channel.KIOSK
    )

    line = rec.whisper_summary(
        patient_name="Kamla Devi",
        intent=rec.Intent.RESCHEDULE,
        appointment=appointment,
        lang=Lang.HI,
    )

    assert line.startswith("Kamla Devi, wants to move ")
    assert "Speaking hi" in line
