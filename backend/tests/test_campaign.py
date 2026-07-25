"""The D-1 outbound intake campaign (doc 01 §4.2, doc 03 §1b).

The AC is "campaign dry-run produces correct call list", so the first block is
about *who is not called* — a dry run that lists everybody is easy and useless.
The rest walks the retry ladder rung by rung with the fake telephony provider,
because the ladder spans processes in production and can only be tested here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app import campaign, scheduling
from app.models.enums import Channel, OutboundCallState
from app.providers.telephony import CallHandle, CallState
from tests.factories import build_clinic, make_slot

pytestmark = pytest.mark.asyncio


def _evening(days_ahead: int = 0) -> datetime:
    """18:00 in the hospital's timezone — when the campaign actually runs."""
    local = (datetime.now(UTC) + timedelta(days=days_ahead)).astimezone(scheduling.hospital_tz())
    return local.replace(hour=18, minute=0, second=0, microsecond=0).astimezone(UTC)


async def _clinic_with_appointment(session, *, days_ahead: int = 1, patient=None):
    clinic = await build_clinic(session)
    slot_at = (datetime.now(UTC) + timedelta(days=days_ahead)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    slot = make_slot(clinic["doctor"], slot_at, capacity=5)
    session.add(slot)
    await session.flush()
    appointment = await scheduling.book(
        session, patient=patient or clinic["patient"], slot_id=slot.id, source=Channel.KIOSK
    )
    return clinic, slot, appointment


def _for_date(appointment):
    return appointment.slot_at.astimezone(scheduling.hospital_tz()).date()


# -- the dry run ---------------------------------------------------------------


async def test_the_dry_run_lists_tomorrows_patients(session, providers):
    clinic, _, appointment = await _clinic_with_appointment(session)

    plan = await campaign.plan_campaign(session, for_date=_for_date(appointment))

    assert [t.appointment_id for t in plan.targets] == [appointment.id]
    target = plan.targets[0]
    assert target.phone == clinic["patient"].phone
    assert target.lang is clinic["patient"].lang
    assert clinic["patient"].name in plan.report()


async def test_the_dry_run_writes_nothing(session, providers):
    _, _, appointment = await _clinic_with_appointment(session)

    await campaign.launch_campaign(session, for_date=_for_date(appointment), dry_run=True)

    assert await campaign.due_calls(session) == []


async def test_a_cancelled_appointment_is_not_called(session, providers):
    _, _, appointment = await _clinic_with_appointment(session)
    await scheduling.cancel(session, appointment=appointment)

    plan = await campaign.plan_campaign(session, for_date=_for_date(appointment))

    assert plan.targets == []


async def test_a_patient_without_a_phone_is_skipped_with_a_reason(session, providers):
    clinic, _, appointment = await _clinic_with_appointment(session)
    clinic["patient"].phone = ""
    await session.flush()

    plan = await campaign.plan_campaign(session, for_date=_for_date(appointment))

    assert plan.targets == []
    assert plan.skipped == [("no phone number", appointment.id)]


async def test_two_appointments_tomorrow_are_one_call(session, providers):
    clinic, slot, first = await _clinic_with_appointment(session)
    second_slot = make_slot(clinic["doctor"], slot.starts_at + timedelta(hours=2), capacity=5)
    session.add(second_slot)
    await session.flush()
    second = await scheduling.book(
        session, patient=clinic["patient"], slot_id=second_slot.id, source=Channel.KIOSK
    )

    plan = await campaign.plan_campaign(session, for_date=_for_date(first))

    assert len(plan.targets) == 1
    assert ("patient already on the list", second.id) in plan.skipped


async def test_relaunching_does_not_queue_a_patient_twice(session, providers):
    _, _, appointment = await _clinic_with_appointment(session)
    for_date = _for_date(appointment)

    await campaign.launch_campaign(session, for_date=for_date, dry_run=False)
    second = await campaign.launch_campaign(session, for_date=for_date, dry_run=False)

    assert second.targets == []
    assert len(await campaign.due_calls(session)) == 1


# -- dialling ------------------------------------------------------------------


async def test_dialling_places_a_call_per_due_row(session, providers, settings):
    from app.providers.registry import get_telephony_provider

    _, _, appointment = await _clinic_with_appointment(session)
    await campaign.launch_campaign(
        session, for_date=_for_date(appointment), dry_run=False, now=_evening()
    )

    [call] = await campaign.dial_due_calls(session, now=_evening())

    telephony = get_telephony_provider()
    assert len(telephony.placed) == 1
    assert telephony.last.to == call.to_phone
    # The reference is how the callback finds this row again, minutes later.
    assert telephony.last.reference == str(call.id)
    assert call.state is OutboundCallState.DIALING
    assert call.attempts == 1


async def test_nothing_is_dialled_at_midnight(session, providers):
    from app.providers.registry import get_telephony_provider

    _, _, appointment = await _clinic_with_appointment(session)
    await campaign.launch_campaign(
        session, for_date=_for_date(appointment), dry_run=False, now=_evening()
    )

    local_midnight = (
        datetime.now(UTC)
        .astimezone(scheduling.hospital_tz())
        .replace(hour=23, minute=30, second=0, microsecond=0)
    )
    dialled = await campaign.dial_due_calls(session, now=local_midnight.astimezone(UTC))

    assert dialled == []
    assert get_telephony_provider().placed == []


# -- the retry ladder ----------------------------------------------------------


async def test_a_completed_call_settles_the_row_and_meters_the_minutes(
    session, providers, meter, seeded_prices
):
    from app.providers.registry import get_telephony_provider

    _, _, appointment = await _clinic_with_appointment(session)
    await campaign.launch_campaign(
        session, for_date=_for_date(appointment), dry_run=False, now=_evening()
    )
    [call] = await campaign.dial_due_calls(session, now=_evening())

    settled = await campaign.record_call_result(
        session,
        handle=CallHandle(
            provider=get_telephony_provider().name,
            call_sid=call.last_call_sid,
            state=CallState.COMPLETED,
            duration_seconds=Decimal(180),
        ),
        reference=str(call.id),
    )

    assert settled.state is OutboundCallState.COMPLETED
    assert settled.next_attempt_at is None
    await meter.flush()  # the minutes must have reached usage_events


async def test_a_no_answer_goes_back_on_the_ladder(session, providers):
    _, _, appointment = await _clinic_with_appointment(session)
    await campaign.launch_campaign(
        session, for_date=_for_date(appointment), dry_run=False, now=_evening()
    )
    now = _evening()
    [call] = await campaign.dial_due_calls(session, now=now)

    settled = await campaign.record_call_result(
        session,
        handle=CallHandle(provider="fake", call_sid=call.last_call_sid, state=CallState.NO_ANSWER),
        reference=str(call.id),
        now=now,
    )

    assert settled.state is OutboundCallState.PENDING
    assert settled.attempts == 1
    assert settled.next_attempt_at == now + timedelta(minutes=campaign.RETRY_AFTER_MINUTES)
    # …and not before then.
    assert await campaign.due_calls(session, now=now + timedelta(minutes=5)) == []
    assert len(await campaign.due_calls(session, now=settled.next_attempt_at)) == 1


async def test_two_unanswered_attempts_exhaust_the_ladder(session, providers):
    _, _, appointment = await _clinic_with_appointment(session)
    await campaign.launch_campaign(
        session, for_date=_for_date(appointment), dry_run=False, now=_evening()
    )
    now = _evening()

    for _ in range(campaign.MAX_ATTEMPTS):
        [call] = await campaign.dial_due_calls(session, now=now)
        await campaign.record_call_result(
            session,
            handle=CallHandle(
                provider="fake", call_sid=call.last_call_sid, state=CallState.NO_ANSWER
            ),
            reference=str(call.id),
            now=now,
        )
        now = call.next_attempt_at or now

    assert call.state is OutboundCallState.FAILED
    assert call.attempts == campaign.MAX_ATTEMPTS
    assert await campaign.due_calls(session, now=now + timedelta(hours=6)) == []


async def test_a_refused_dial_counts_as_an_attempt(session, providers):
    from app.providers.base import ProviderUnavailable
    from app.providers.registry import get_telephony_provider

    _, _, appointment = await _clinic_with_appointment(session)
    await campaign.launch_campaign(
        session, for_date=_for_date(appointment), dry_run=False, now=_evening()
    )
    get_telephony_provider().fail_with = ProviderUnavailable("exotel down")

    dialled = await campaign.dial_due_calls(session, now=_evening())

    assert dialled == []
    [call] = await campaign.due_calls(session, now=_evening() + timedelta(hours=1))
    assert call.attempts == 1
    assert call.outcome == "dial_failed"


# -- the last rung -------------------------------------------------------------


async def test_the_exhausted_ladder_falls_back_to_whatsapp_once(session, providers):
    from app.providers.registry import get_messaging_provider

    _, _, appointment = await _clinic_with_appointment(session)
    await campaign.launch_campaign(
        session, for_date=_for_date(appointment), dry_run=False, now=_evening()
    )
    now = _evening()
    for _ in range(campaign.MAX_ATTEMPTS):
        [call] = await campaign.dial_due_calls(session, now=now)
        await campaign.record_call_result(
            session,
            handle=CallHandle(provider="fake", call_sid=call.last_call_sid, state=CallState.BUSY),
            reference=str(call.id),
            now=now,
        )
        now = call.next_attempt_at or now

    sent = await campaign.send_call_fallbacks(session)

    assert [c.id for c in sent] == [call.id]
    assert call.state is OutboundCallState.FALLBACK_SENT
    messaging = get_messaging_provider()
    assert len(messaging.sent) == 1
    assert messaging.sent[0].template_name == "intake_call_missed"

    # Running the job again messages nobody a second time.
    assert await campaign.send_call_fallbacks(session) == []
    assert len(messaging.sent) == 1


async def test_a_callback_for_an_unknown_call_is_ignored_not_an_error(session, providers):
    result = await campaign.record_call_result(
        session,
        handle=CallHandle(provider="fake", call_sid="not-ours", state=CallState.COMPLETED),
        reference="not-a-uuid",
    )
    assert result is None
