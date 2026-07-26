"""The ladder, and the clock it runs on (doc 03 §9).

Quiet hours are the third of S17's acceptance criteria and the first half of this
file. The rest is the ladder's one non-obvious rule: it advances on **silence**,
not on a failed send — a WhatsApp message Meta accepted and a patient never
opened looks the same to us as one she is about to answer, so the next rung waits
for the answer window before it is tried.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import queue as q
from app.checkins import delivery as d
from app.checkins.window import is_quiet, next_sendable, send_time_on
from app.config import Settings
from app.models.content import Checkin, CheckinPlan
from app.models.enums import Channel, CheckinPlanStatus, CheckinState, Lang
from app.providers.messaging import FakeMessagingProvider
from app.providers.sms import FakeSMSProvider
from app.providers.telephony import FakeTelephonyProvider
from app.scheduling import hospital_tz
from app.whatsapp.conversation import Conversation, InMemoryConversationStore

# 06:30 UTC is 12:00 in Alwar — the middle of a working day on any clock.
NOON = datetime(2026, 7, 27, 6, 30, tzinfo=UTC)


def local(hour: int, *, day: int = 27) -> datetime:
    """An instant at `hour` o'clock, hospital-local."""
    return datetime(2026, 7, day, hour, 0, tzinfo=hospital_tz())


# =============================================================================
# 1. quiet hours — S17 AC, third item
# =============================================================================


@pytest.mark.parametrize("hour", [21, 22, 23, 0, 3, 7])
def test_the_night_is_quiet(hour: int, settings: Settings) -> None:
    assert is_quiet(local(hour), settings=settings)


@pytest.mark.parametrize("hour", [8, 9, 12, 17, 20])
def test_the_day_is_not(hour: int, settings: Settings) -> None:
    assert not is_quiet(local(hour), settings=settings)


def test_a_moment_in_the_evening_half_defers_to_the_next_morning(
    settings: Settings,
) -> None:
    moved = next_sendable(local(22, day=27), settings=settings)
    assert moved.astimezone(hospital_tz()) == local(8, day=28)


def test_a_moment_in_the_small_hours_defers_to_the_same_morning(
    settings: Settings,
) -> None:
    moved = next_sendable(local(3, day=28), settings=settings)
    assert moved.astimezone(hospital_tz()) == local(8, day=28)


def test_a_moment_in_the_day_is_left_exactly_alone(settings: Settings) -> None:
    noon = local(12)
    assert next_sendable(noon, settings=settings) == noon.astimezone(UTC)


def test_quiet_hours_defer_and_never_drop(settings: Settings) -> None:
    """A D+2 that lands at 23:00 is asked at 08:00 on D+3. Skipping the rung
    would lose the one clinical signal the day was for."""
    assert next_sendable(local(23), settings=settings) > local(23).astimezone(UTC)


def test_a_checkin_is_scheduled_at_the_send_hour_not_at_midnight(
    settings: Settings,
) -> None:
    due = send_time_on(datetime(2026, 7, 29, 22, 0, tzinfo=UTC), settings=settings)
    assert due.astimezone(hospital_tz()).hour == settings.checkin_send_hour


# =============================================================================
# 2. the rungs, in order
# =============================================================================


def test_the_ladder_is_the_docs_ladder() -> None:
    assert d.next_rung(Channel.WHATSAPP) is Channel.PHONE
    assert d.next_rung(Channel.PHONE) is Channel.SMS
    assert d.next_rung(Channel.SMS) is None


async def _pending_checkin(
    session: AsyncSession,
    *,
    channel: Channel = Channel.WHATSAPP,
    question_set: str = "gi_platinum",
    due_at: datetime | None = None,
    status: CheckinPlanStatus = CheckinPlanStatus.ACTIVE,
):
    from app.checkins import protocols as pb

    clinic = await f.build_clinic(session)
    visit = f.make_visit(
        clinic["patient"], clinic["department"], date=q.today(), channel=Channel.KIOSK
    )
    session.add(visit)
    await session.flush()
    plan = CheckinPlan(
        patient_id=clinic["patient"].id,
        visit_id=visit.id,
        protocol_key="platinum",
        treatment_at=NOON - timedelta(days=2),
        lang=Lang.HI,
        schedule=[],
        status=status,
    )
    session.add(plan)
    await session.flush()

    qset = pb.get_bank().question_set(question_set)
    due = due_at or NOON
    checkin = Checkin(
        plan_id=plan.id,
        due_at=due,
        day_offset=2,
        question_set=question_set,
        asked=[question.to_json() for question in qset.questions],
        message="नमस्ते। कुछ सवाल हैं।",
        lang=Lang.HI,
        channel=channel,
        state=CheckinState.PENDING,
        next_attempt_at=due,
    )
    session.add(checkin)
    await session.flush()
    return clinic, plan, checkin


def _providers(monkeypatch):
    """Swap all three vendors for fakes and hand them back."""
    messaging = FakeMessagingProvider()
    sms = FakeSMSProvider()
    telephony = FakeTelephonyProvider()
    monkeypatch.setattr(d, "get_messaging_provider", lambda s=None: messaging)
    monkeypatch.setattr(d, "get_sms_provider", lambda s=None: sms)
    monkeypatch.setattr(d, "get_telephony_provider", lambda s=None: telephony)
    return messaging, sms, telephony


async def test_the_first_rung_out_of_window_sends_the_registered_template(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """A check-in is days after the patient last messaged us, by definition."""
    messaging, _, _ = _providers(monkeypatch)
    _, _, checkin = await _pending_checkin(session)

    sent = await d.send_due(session, now=NOON, settings=settings)

    assert [c.id for c in sent] == [checkin.id]
    assert messaging.sent[0].template_name == "checkin_due"
    assert checkin.state is CheckinState.SENT
    assert checkin.attempts == 1
    assert checkin.delivery[-1]["detail"] == "template (out of window)"


async def test_inside_the_window_the_patient_gets_the_question_itself(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, _, _ = _providers(monkeypatch)
    clinic, _, checkin = await _pending_checkin(session)
    conversations = InMemoryConversationStore()
    conversation = Conversation(wa_id=clinic["patient"].phone)
    conversation.mark_inbound(now=NOON - timedelta(hours=1))
    await conversations.save(conversation)

    await d.send_due(session, now=NOON, conversations=conversations, settings=settings)

    message = messaging.sent[0]
    assert message.template_name is None
    assert "नमस्ते। कुछ सवाल हैं।" in message.text
    # The first question of gi_platinum is a number — no buttons to press.
    assert "कितनी बार उल्टी" in message.text
    resumed = await conversations.get(clinic["patient"].phone)
    assert resumed is not None and resumed.checkin_id == checkin.id


async def test_a_refused_send_drops_to_the_next_rung_at_once(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """Waiting six hours for a message that was never sent helps nobody."""
    messaging, _, _ = _providers(monkeypatch)
    messaging.fail_with = RuntimeError("meta is down")
    _, _, checkin = await _pending_checkin(session)

    await d.send_due(session, now=NOON, settings=settings)

    assert checkin.channel is Channel.PHONE
    assert checkin.state is CheckinState.PENDING
    assert checkin.next_attempt_at == NOON
    assert checkin.delivery[-1]["status"] == "failed"


async def test_a_successful_send_waits_for_an_answer_before_the_next_rung(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    _providers(monkeypatch)
    _, _, checkin = await _pending_checkin(session)

    await d.send_due(session, now=NOON, settings=settings)

    assert checkin.channel is Channel.PHONE
    assert checkin.next_attempt_at == NOON + d.ANSWER_WINDOW


async def test_the_voice_rung_refuses_to_dial_into_an_applet_that_does_not_exist(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """She answers a hospital call and hears silence — worse than no call."""
    _, _, telephony = _providers(monkeypatch)
    monkeypatch.setattr(settings, "exotel_checkin_applet_url", "")
    _, _, checkin = await _pending_checkin(session, channel=Channel.PHONE)

    await d.send_due(session, now=NOON, settings=settings)

    assert telephony.placed == []
    assert checkin.channel is Channel.SMS
    assert "no check-in voice applet" in checkin.delivery[-1]["detail"]


async def test_the_voice_rung_dials_when_an_applet_is_configured(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    _, _, telephony = _providers(monkeypatch)
    monkeypatch.setattr(settings, "exotel_checkin_applet_url", "https://gw/exotel/checkin")
    clinic, _, checkin = await _pending_checkin(session, channel=Channel.PHONE)

    await d.send_due(session, now=NOON, settings=settings)

    assert len(telephony.placed) == 1
    assert telephony.placed[0].to == clinic["patient"].phone
    assert telephony.placed[0].reference == str(checkin.id)


async def test_the_sms_rung_is_a_nudge_and_asks_no_clinical_question(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """Structured answers over a DLT-templated gateway is not a thing that works;
    what this rung buys is a human knowing to ring her."""
    _, sms, _ = _providers(monkeypatch)
    _, _, checkin = await _pending_checkin(session, channel=Channel.SMS)

    await d.send_due(session, now=NOON, settings=settings)

    assert len(sms.sent) == 1
    assert "उल्टी" not in sms.sent[0].body
    assert "WhatsApp" in sms.sent[0].body


async def test_the_bottom_of_the_ladder_with_no_answer_expires_with_no_grade(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """ "We could not reach her" is not "she said she is fine"."""
    _providers(monkeypatch)
    _, _, checkin = await _pending_checkin(session, channel=Channel.SMS)
    await d.send_due(session, now=NOON, settings=settings)
    assert checkin.state is CheckinState.SENT

    await d.send_due(session, now=NOON + d.ANSWER_WINDOW, settings=settings)

    assert checkin.state is CheckinState.EXPIRED
    assert checkin.grade is None
    assert checkin.next_attempt_at is None


# =============================================================================
# 3. what the scheduler will and will not pick up
# =============================================================================


async def test_a_checkin_due_in_the_night_is_deferred_without_burning_a_rung(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """S17 AC, third item, at the point it actually bites."""
    messaging, _, _ = _providers(monkeypatch)
    night = local(22).astimezone(UTC)
    _, _, checkin = await _pending_checkin(session, due_at=night)

    sent = await d.send_due(session, now=night, settings=settings)

    assert sent == []
    assert messaging.sent == []
    assert checkin.attempts == 0
    assert checkin.channel is Channel.WHATSAPP
    assert checkin.state is CheckinState.PENDING
    assert checkin.next_attempt_at is not None
    assert checkin.next_attempt_at.astimezone(hospital_tz()) == local(8, day=28)


async def test_the_deferred_checkin_goes_out_in_the_morning(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, _, _ = _providers(monkeypatch)
    night = local(22).astimezone(UTC)
    _, _, checkin = await _pending_checkin(session, due_at=night)
    await d.send_due(session, now=night, settings=settings)

    await d.send_due(session, now=local(8, day=28).astimezone(UTC), settings=settings)

    assert len(messaging.sent) == 1
    assert checkin.state is CheckinState.SENT


async def test_a_plan_still_in_draft_messages_nobody(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, _, _ = _providers(monkeypatch)
    await _pending_checkin(session, status=CheckinPlanStatus.DRAFT)

    assert await d.send_due(session, now=NOON, settings=settings) == []
    assert messaging.sent == []


async def test_a_cancelled_plan_messages_nobody(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, _, _ = _providers(monkeypatch)
    await _pending_checkin(session, status=CheckinPlanStatus.CANCELLED)

    assert await d.send_due(session, now=NOON, settings=settings) == []
    assert messaging.sent == []


async def test_an_already_answered_checkin_is_never_sent_again(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, _, _ = _providers(monkeypatch)
    _, _, checkin = await _pending_checkin(session)
    checkin.state = CheckinState.ANSWERED
    checkin.next_attempt_at = None
    await session.flush()

    assert await d.send_due(session, now=NOON, settings=settings) == []
    assert messaging.sent == []


async def test_the_flag_off_stops_everything(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The switch a box being restored or replayed is turned off with."""
    messaging, _, _ = _providers(monkeypatch)
    monkeypatch.setattr(settings, "checkins_enabled", False)
    await _pending_checkin(session)

    assert await d.send_due(session, now=NOON, settings=settings) == []
    assert messaging.sent == []


async def test_a_checkin_not_yet_due_waits(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, _, _ = _providers(monkeypatch)
    await _pending_checkin(session, due_at=NOON + timedelta(days=1))

    assert await d.send_due(session, now=NOON, settings=settings) == []
    assert messaging.sent == []
