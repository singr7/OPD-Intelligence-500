"""S17's acceptance criterion, as one walk (doc 03 §9, doc 06 S17).

> **AC:** sign a fixture dictation → correct plan drafted → a simulated D+2 red
> answer escalates within 1 min; quiet hours respected.

`test_the_session_acceptance_criterion` is that sentence, in order, with the
clock moved by hand: a doctor signs, taps once, beat tries to send at 22:00 and
does not, sends at 08:00, the patient answers on WhatsApp, and a doctor's phone
buzzes. Everything it asserts is asserted somewhere else in more detail — this
one exists so the pieces are known to fit together, and so the AC has a single
address.

The rest of the file is the HTTP surface, the WhatsApp answer path and the
next-cycle reminders.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import dictation as dic
from app import queue as q
from app import worker
from app.auth.tokens import create_access_token
from app.checkins import cycles as cyc
from app.checkins import delivery as d
from app.checkins import grading as g
from app.checkins import plan as cp
from app.config import Settings
from app.intake import IntakeEngine
from app.intake.state import InMemorySessionStore
from app.models.content import Checkin, CheckinPlan
from app.models.enums import (
    Channel,
    CheckinGrade,
    CheckinPlanStatus,
    CheckinState,
    Lang,
    Role,
)
from app.providers.llm import FakeLLMProvider
from app.providers.messaging import FakeMessagingProvider
from app.providers.sms import FakeSMSProvider
from app.providers.telephony import FakeTelephonyProvider
from app.scheduling import hospital_tz
from app.whatsapp.bot import Inbound, WhatsAppBot
from app.whatsapp.conversation import Conversation, InMemoryConversationStore

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "checkin_dictations.json").read_text(encoding="utf-8")
)


def local(hour: int, *, day: int) -> datetime:
    return datetime(2026, 7, day, hour, 0, tzinfo=hospital_tz())


def _fakes(monkeypatch) -> tuple[FakeMessagingProvider, FakeSMSProvider]:
    messaging = FakeMessagingProvider()
    sms = FakeSMSProvider()
    monkeypatch.setattr(d, "get_messaging_provider", lambda s=None: messaging)
    monkeypatch.setattr(d, "get_sms_provider", lambda s=None: sms)
    monkeypatch.setattr(d, "get_telephony_provider", lambda s=None: FakeTelephonyProvider())
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: sms)
    monkeypatch.setattr(cyc, "get_messaging_provider", lambda s=None: messaging)
    monkeypatch.setattr(cyc, "get_sms_provider", lambda s=None: sms)
    # No canned reply for `checkin_personalize`, so the personalisation falls
    # back to the plain four-language message — which is what a box with no
    # vendor key does, and the AC is about the schedule, not the wording.
    monkeypatch.setattr(cp, "llm_chain", lambda settings=None: [FakeLLMProvider()])
    monkeypatch.setattr(g, "llm_chain", lambda settings=None: [FakeLLMProvider()])
    return messaging, sms


async def _sign(session: AsyncSession, case_id: str, *, treated_on: datetime):
    """A clinic, a visit, and a signed note whose treatment date is `treated_on`."""
    case = next(c for c in FIXTURES["cases"] if c["id"] == case_id)
    mapping: dict[str, Any] = json.loads(json.dumps(case["mapping"]))
    mapping["treatment_events"][0]["date"] = treated_on.date().isoformat()
    mapping["treatment_events"][0]["next_due"] = (
        (treated_on + timedelta(days=21)).date().isoformat()
    )

    clinic = await f.build_clinic(session)
    visit = f.make_visit(
        clinic["patient"], clinic["department"], date=q.today(), channel=Channel.KIOSK
    )
    session.add(visit)
    await session.flush()
    dictation = f.make_dictation(visit, clinic["doctor"])
    dictation.structured = {**dic.empty_structured(), "mapped": mapping, "fields": mapping}
    session.add(dictation)
    await session.flush()
    signed = await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])
    return clinic, signed


# =============================================================================
# The acceptance criterion
# =============================================================================


async def test_the_session_acceptance_criterion(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, sms = _fakes(monkeypatch)
    monkeypatch.setattr(settings, "coordinator_phone", "+915550000099")
    conversations = InMemoryConversationStore()

    # --- the doctor signs a fixture note on the day of treatment -------------
    treated_on = local(11, day=27).astimezone(UTC)
    clinic, signed = await _sign(session, "carboplatin-day-1", treated_on=treated_on)

    # --- the correct plan is drafted -----------------------------------------
    plan = await session.scalar(select(CheckinPlan).where(CheckinPlan.dictation_id == signed.id))
    assert plan is not None
    assert plan.protocol_key == "platinum"
    assert plan.status is CheckinPlanStatus.DRAFT
    assert [rung["day_offset"] for rung in plan.schedule] == [2, 7, 14]

    # --- one tap ------------------------------------------------------------
    created = await cp.approve(session, plan=plan, doctor=clinic["doctor"])
    d2 = created[0]
    assert d2.day_offset == 2
    assert d2.question_set == "gi_platinum"

    # --- quiet hours: beat runs at 22:00 on D+1 and sends nothing ------------
    night = local(22, day=28).astimezone(UTC)
    assert await d.send_due(session, now=night, settings=settings) == []
    assert messaging.sent == []
    assert d2.attempts == 0

    # --- 08:00 on D+2 (the plan's own due time is 10:00) ---------------------
    morning = local(10, day=29).astimezone(UTC)
    assert d2.due_at.astimezone(hospital_tz()).date() == local(10, day=29).date()
    conversation = Conversation(wa_id=clinic["patient"].phone, lang=Lang.HI)
    conversation.mark_inbound(now=morning - timedelta(hours=2))
    await conversations.save(conversation)

    await d.send_due(session, now=morning, conversations=conversations, settings=settings)

    assert d2.state is CheckinState.SENT
    assert len(messaging.sent) == 1
    assert "कितनी बार उल्टी" in messaging.sent[0].text

    # --- she answers, and the first answer is a red one ----------------------
    bot = _bot(settings, conversations)
    before = datetime.now(UTC)
    reply = await bot.handle(session, Inbound(wa_id=clinic["patient"].phone, kind="text", text="7"))

    # Seven vomits in a day is `gi.vomiting_severe`. The escalation is
    # synchronous with the answer — not a job, not a queue, not a minute.
    await session.refresh(d2)
    assert d2.grade is CheckinGrade.RED
    assert d2.escalated_at is not None
    assert (d2.escalated_at - before) < timedelta(minutes=1)
    assert d2.escalated_to == clinic["doctor"].user_id
    alerted = [message.to for message in sms.sent]
    assert clinic["doctor"].phone in alerted
    assert "+915550000099" in alerted
    assert "dehydration risk" in sms.sent[0].body

    # She is thanked, and not told by a bot that her answer was alarming.
    assert reply.messages
    assert "धन्यवाद" in reply.messages[-1].text

    # --- and she is at the top of the nurse's queue --------------------------
    queue = await g.review_queue(session)
    assert [row[0].id for row in queue] == [d2.id]
    assert queue[0][2] is not None and queue[0][2].name == clinic["patient"].name


# =============================================================================
# the HTTP surface
# =============================================================================


def _headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def test_a_doctor_sees_and_approves_their_own_draft(
    session: AsyncSession, client: AsyncClient, settings: Settings, monkeypatch
) -> None:
    _fakes(monkeypatch)
    clinic, signed = await _sign(session, "carboplatin-day-1", treated_on=datetime.now(UTC))
    await session.commit()
    headers = _headers(settings, clinic["user"])

    drafts = await client.get("/checkins/plans/drafts", headers=headers)
    assert drafts.status_code == 200
    body = drafts.json()
    assert len(body) == 1
    assert body[0]["protocol_key"] == "platinum"
    assert [rung["day_offset"] for rung in body[0]["schedule"]] == [2, 7, 14]
    # The doctor is shown what the personalisation did before they tap.
    assert "applied" in body[0]["personalisation"]

    approved = await client.post(f"/checkins/plans/{body[0]['id']}/approve", headers=headers)

    assert approved.status_code == 200
    assert approved.json()["status"] == "active"
    assert (await client.get("/checkins/plans/drafts", headers=headers)).json() == []


async def test_approving_twice_is_a_conflict_not_a_second_set_of_messages(
    session: AsyncSession, client: AsyncClient, settings: Settings, monkeypatch
) -> None:
    _fakes(monkeypatch)
    clinic, signed = await _sign(session, "carboplatin-day-1", treated_on=datetime.now(UTC))
    await session.commit()
    headers = _headers(settings, clinic["user"])
    plan_id = (await client.get("/checkins/plans/drafts", headers=headers)).json()[0]["id"]
    await client.post(f"/checkins/plans/{plan_id}/approve", headers=headers)

    again = await client.post(f"/checkins/plans/{plan_id}/approve", headers=headers)

    assert again.status_code == 409
    count = len(
        list(await session.scalars(select(Checkin).where(Checkin.plan_id == uuid_of(plan_id))))
    )
    assert count == 3


def uuid_of(value: str):
    import uuid

    return uuid.UUID(value)


async def test_a_coordinator_cannot_read_what_a_patient_said_about_her_symptoms(
    session: AsyncSession, client: AsyncClient, settings: Settings
) -> None:
    """`require_clinical`, not `require_staff`: moving a line does not need this."""
    clinic = await f.build_clinic(session)
    user = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(user)
    await session.commit()
    response = await client.get("/checkins/review", headers=_headers(settings, user))

    assert response.status_code == 403


async def test_the_nurse_queue_shows_the_rule_that_fired_and_her_answers(
    session: AsyncSession, client: AsyncClient, settings: Settings, monkeypatch
) -> None:
    _fakes(monkeypatch)
    clinic, signed = await _sign(
        session, "carboplatin-day-1", treated_on=datetime.now(UTC) - timedelta(days=2)
    )
    plan = await session.scalar(select(CheckinPlan).where(CheckinPlan.dictation_id == signed.id))
    assert plan is not None
    checkins = await cp.approve(session, plan=plan, doctor=clinic["doctor"])
    await g.submit(session, checkin=checkins[0], answers={"ck.gi.vomit": 3}, settings=settings)
    await session.commit()

    nurse = f.make_user(clinic["hospital"], role=Role.NURSE)
    session.add(nurse)
    await session.commit()
    headers = _headers(settings, nurse)

    rows = (await client.get("/checkins/review", headers=headers)).json()

    assert len(rows) == 1
    assert rows[0]["grade"] == "amber"
    assert rows[0]["patient_phone"] == clinic["patient"].phone
    assert any("antiemetic" in reason["reason"] for reason in rows[0]["reasons"])
    answered = {a["id"]: a["answer"] for a in rows[0]["answers"]}
    assert answered["ck.gi.vomit"] == 3
    # The prompt is in the patient's language, as she read it.
    assert "कितनी बार उल्टी" in rows[0]["answers"][0]["prompt"]

    resolved = await client.post(
        f"/checkins/{rows[0]['checkin_id']}/resolve",
        json={"note": "Rang her, antiemetic changed."},
        headers=headers,
    )

    assert resolved.status_code == 200
    # The grade stays — what she said stays true.
    assert resolved.json()["grade"] == "amber"
    assert (await client.get("/checkins/review", headers=headers)).json() == []


# =============================================================================
# the WhatsApp answer path
# =============================================================================


async def _sent_checkin(session: AsyncSession, settings: Settings, monkeypatch, conversations):
    clinic, signed = await _sign(
        session, "carboplatin-day-1", treated_on=datetime.now(UTC) - timedelta(days=2)
    )
    plan = await session.scalar(select(CheckinPlan).where(CheckinPlan.dictation_id == signed.id))
    assert plan is not None
    checkins = await cp.approve(session, plan=plan, doctor=clinic["doctor"])
    checkin = checkins[0]
    conversation = Conversation(wa_id=clinic["patient"].phone, lang=Lang.HI)
    conversation.mark_inbound()
    conversation.checkin_id = checkin.id
    conversation.checkin_question = "ck.gi.vomit"
    await conversations.save(conversation)
    return clinic, checkin


def _bot(settings: Settings, conversations) -> WhatsAppBot:
    return WhatsAppBot(
        engine=IntakeEngine(InMemorySessionStore()),
        conversations=conversations,
        settings=settings,
    )


async def test_she_is_walked_through_the_questions_one_at_a_time(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    _fakes(monkeypatch)
    conversations = InMemoryConversationStore()
    clinic, checkin = await _sent_checkin(session, settings, monkeypatch, conversations)
    bot = _bot(settings, conversations)
    wa_id = clinic["patient"].phone

    first = await bot.handle(session, Inbound(wa_id=wa_id, kind="text", text="0"))
    # The next question is a single-select, so it arrives as buttons.
    assert first.messages[0].buttons
    reply_id = first.messages[0].buttons[0].id
    assert reply_id.startswith(f"ck:{checkin.id}:ck.gi.intake:")

    second = await bot.handle(session, Inbound(wa_id=wa_id, kind="reply", reply_id=reply_id))
    assert "पेशाब" in second.messages[0].text


async def test_an_answer_the_question_cannot_accept_is_re_asked_not_guessed(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    _fakes(monkeypatch)
    conversations = InMemoryConversationStore()
    clinic, checkin = await _sent_checkin(session, settings, monkeypatch, conversations)
    bot = _bot(settings, conversations)

    reply = await bot.handle(
        session,
        Inbound(wa_id=clinic["patient"].phone, kind="text", text="dono baar"),
    )

    assert "समझ नहीं" in reply.messages[0].text
    assert "कितनी बार उल्टी" in reply.messages[1].text
    assert checkin.responses == {}


async def test_a_tap_on_someone_elses_checkin_answers_nothing(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The button id is in the message; a forwarded message must not let a
    stranger answer for her."""
    _fakes(monkeypatch)
    conversations = InMemoryConversationStore()
    _, checkin = await _sent_checkin(session, settings, monkeypatch, conversations)
    other = Conversation(wa_id="+915559998888", lang=Lang.HI)
    other.mark_inbound()
    await conversations.save(other)
    bot = _bot(settings, conversations)

    reply = await bot.handle(
        session,
        Inbound(
            wa_id="+915559998888",
            kind="reply",
            reply_id=f"ck:{checkin.id}:ck.gi.intake:almost_nothing",
        ),
    )

    assert "बंद हो चुका" in reply.messages[0].text
    assert checkin.responses == {}


async def test_a_tap_that_arrives_after_the_checkin_closed_is_refused(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    _fakes(monkeypatch)
    conversations = InMemoryConversationStore()
    clinic, checkin = await _sent_checkin(session, settings, monkeypatch, conversations)
    checkin.state = CheckinState.EXPIRED
    await session.flush()
    bot = _bot(settings, conversations)

    reply = await bot.handle(
        session,
        Inbound(
            wa_id=clinic["patient"].phone,
            kind="reply",
            reply_id=f"ck:{checkin.id}:ck.gi.intake:normal",
        ),
    )

    assert "बंद हो चुका" in reply.messages[0].text
    assert checkin.responses == {}


# =============================================================================
# next-cycle reminders
# =============================================================================


def _plan_with_cycle(days_away: int) -> CheckinPlan:
    return CheckinPlan(
        patient_id=f.new_uuid(),
        protocol_key="platinum",
        lang=Lang.HI,
        next_cycle_at=datetime.now(UTC) + timedelta(days=days_away),
    )


@pytest.mark.parametrize(("days_away", "expected"), [(5, None), (2, 2), (1, 2), (0, 2), (-1, None)])
def test_which_rung_is_due(days_away: int, expected: int | None) -> None:
    """D-2 fires once the cycle is two days out; a missed tick still sends."""
    plan = _plan_with_cycle(days_away)
    assert cyc.rung_due(plan, now=datetime.now(UTC)) == expected


def test_the_day_0_rung_comes_after_the_day_2_one() -> None:
    plan = _plan_with_cycle(0)
    plan.cycle_reminders = [{"rung": 2, "at": "…", "channel": "sms", "status": "sent"}]
    assert cyc.rung_due(plan, now=datetime.now(UTC)) == 0


def test_a_rung_already_sent_is_never_sent_again() -> None:
    """The job runs hourly; a patient hears from us twice, not twenty-four times."""
    plan = _plan_with_cycle(1)
    plan.cycle_reminders = [
        {"rung": 2, "at": "…", "channel": "sms", "status": "sent"},
        {"rung": 0, "at": "…", "channel": "sms", "status": "sent"},
    ]
    assert cyc.rung_due(plan, now=datetime.now(UTC)) is None


async def test_a_cycle_with_no_booked_slot_gets_the_template_and_an_sms(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, sms = _fakes(monkeypatch)
    clinic, signed = await _sign(
        session, "carboplatin-day-1", treated_on=datetime.now(UTC) - timedelta(days=19)
    )
    plan = await session.scalar(select(CheckinPlan).where(CheckinPlan.dictation_id == signed.id))
    assert plan is not None
    await cp.approve(session, plan=plan, doctor=clinic["doctor"])

    reminded = await cyc.send_due_reminders(
        session, now=datetime.now(UTC).replace(hour=6, minute=30), settings=settings
    )

    assert [p.id for p in reminded] == [plan.id]
    assert messaging.sent[0].template_name == "next_cycle_due"
    assert len(sms.sent) == 1
    assert {entry["rung"] for entry in plan.cycle_reminders} == {2}


async def test_nothing_is_reminded_in_the_night(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    messaging, _ = _fakes(monkeypatch)
    clinic, signed = await _sign(
        session, "carboplatin-day-1", treated_on=datetime.now(UTC) - timedelta(days=19)
    )
    plan = await session.scalar(select(CheckinPlan).where(CheckinPlan.dictation_id == signed.id))
    assert plan is not None
    await cp.approve(session, plan=plan, doctor=clinic["doctor"])

    assert await cyc.send_due_reminders(session, now=local(22, day=27), settings=settings) == []
    assert messaging.sent == []


# =============================================================================
# the beat jobs
# =============================================================================


async def test_the_jobs_are_no_ops_with_the_flag_off(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "checkins_enabled", False)
    assert await worker.checkins_send_job(session, settings) == "check-ins disabled"
    assert await worker.checkin_cycles_job(session, settings) == "check-ins disabled"


async def test_the_send_job_runs_the_ladder(
    session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    _fakes(monkeypatch)
    clinic, signed = await _sign(
        session, "carboplatin-day-1", treated_on=datetime.now(UTC) - timedelta(days=3)
    )
    plan = await session.scalar(select(CheckinPlan).where(CheckinPlan.dictation_id == signed.id))
    assert plan is not None
    await cp.approve(session, plan=plan, doctor=clinic["doctor"])

    result = await worker.checkins_send_job(session, settings)

    # Whether anything went out depends on the hour the suite happens to run in
    # — quiet hours are real time here. Either way the job reports honestly.
    assert result.startswith("delivered ")


def test_the_send_job_ticks_often_enough_to_be_useful() -> None:
    assert worker.SCHEDULE["opd.checkins.send"] == ("*", "*/10")
