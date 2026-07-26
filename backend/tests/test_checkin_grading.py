"""Answers in, a colour out, and who gets woken (doc 03 §9).

The second half of S17's acceptance criterion lives here: *a simulated D+2 red
answer escalates*. The escalation path is asserted end to end against the SMS
fake — a red check-in reaches the doctor who signed the note and the coordinator,
and lands at the top of the nurse queue whether or not the message went out.

The rest is the boundary the session is really about. `grade` is a pure function
of the answers and the questions as they were **asked**, with no model in it; the
free-text assist can put a sentence in front of a nurse and cannot do anything
else, and there are tests here for each thing it cannot do.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import queue as q
from app.checkins import grading as g
from app.checkins import protocols as pb
from app.models.content import Checkin, CheckinPlan
from app.models.enums import (
    Channel,
    CheckinGrade,
    CheckinPlanStatus,
    CheckinState,
    Lang,
)
from app.providers.llm import FakeLLMProvider, FakeLLMScript
from app.providers.sms import FakeSMSProvider


def make_checkin(question_set: str = "myelosuppression", **overrides: Any) -> Checkin:
    """A check-in carrying the frozen questions, with no database behind it.

    Most of this file needs no session: grading is pure, and saying so in the
    fixture is part of the point.
    """
    qset = pb.get_bank().question_set(question_set)
    return Checkin(
        plan_id=f.new_uuid(),
        due_at=datetime.now(UTC),
        day_offset=7,
        question_set=question_set,
        asked=[question.to_json() for question in qset.questions],
        message="…",
        lang=Lang.HI,
        channel=Channel.WHATSAPP,
        state=CheckinState.SENT,
        **overrides,
    )


def graded(question_set: str, responses: dict[str, Any]) -> g.Grading:
    return g.grade(make_checkin(question_set, responses=responses))


# =============================================================================
# 1. the grade is the rules, and only the rules
# =============================================================================


def test_nothing_alarming_is_green_by_absence_not_by_a_rule() -> None:
    result = graded(
        "myelosuppression",
        {"ck.myelo.fever": "no", "ck.myelo.mouth": "none", "ck.myelo.bleeding": "no"},
    )
    assert result.grade is CheckinGrade.GREEN
    assert result.reasons == ()
    assert not result.needs_review


def test_a_fever_after_chemotherapy_is_red() -> None:
    result = graded("myelosuppression", {"ck.myelo.fever": "yes"})
    assert result.grade is CheckinGrade.RED
    assert "neutropenic sepsis" in result.reasons[0].reason


def test_a_measured_temperature_is_red_even_if_she_said_no_to_fever() -> None:
    """38.4 on a thermometer is a fact; "do you have a fever" is an opinion."""
    result = graded("myelosuppression", {"ck.myelo.fever": "no", "ck.myelo.temp": 38.4})
    assert result.grade is CheckinGrade.RED
    assert [r.id for r in result.reasons] == ["myelo.temp"]


def test_a_mouth_ulcer_alone_is_amber_and_waits_for_a_nurse() -> None:
    result = graded("myelosuppression", {"ck.myelo.fever": "no", "ck.myelo.mouth": "severe"})
    assert result.grade is CheckinGrade.AMBER
    assert result.needs_review


def test_red_wins_over_amber_and_both_reasons_survive() -> None:
    """A nurse triaging needs the whole picture, not just the worst line."""
    result = graded("myelosuppression", {"ck.myelo.fever": "yes", "ck.myelo.mouth": "severe"})
    assert result.grade is CheckinGrade.RED
    assert {r.id for r in result.reasons} == {"myelo.fever", "myelo.mucositis"}


def test_an_unanswered_question_earns_no_grade() -> None:
    """A flag has to be earned by something the patient actually said — the S4
    rule, unchanged. A check-in nobody answered is not green *and* not red."""
    assert graded("myelosuppression", {}).grade is CheckinGrade.GREEN
    assert graded("myelosuppression", {}).reasons == ()


def test_grading_is_recomputed_not_accumulated() -> None:
    """A corrected answer removes the grade it caused (S5's rule for red flags)."""
    checkin = make_checkin("myelosuppression", responses={"ck.myelo.fever": "yes"})
    g.apply_grade(checkin, g.grade(checkin))
    assert checkin.grade is CheckinGrade.RED

    checkin.responses = {"ck.myelo.fever": "no"}
    g.apply_grade(checkin, g.grade(checkin))

    assert checkin.grade is CheckinGrade.GREEN
    assert checkin.grade_reasons == []


def test_every_reason_says_where_it_came_from() -> None:
    result = graded("myelosuppression", {"ck.myelo.fever": "yes"})
    assert all(r.source == "rule" for r in result.reasons)


def test_a_question_set_retired_from_the_bank_grades_amber_by_hand() -> None:
    """The answers are still real; nobody is escalated on rules we cannot read."""
    checkin = make_checkin("myelosuppression", responses={"ck.myelo.fever": "yes"})
    checkin.question_set = "a_set_that_was_removed"

    result = g.grade(checkin)

    assert result.grade is CheckinGrade.AMBER
    assert result.reasons[0].source == "system"


# =============================================================================
# 2. answers are checked against the questions as they were asked
# =============================================================================


def test_an_option_that_was_not_offered_is_refused() -> None:
    checkin = make_checkin()
    with pytest.raises(g.AnswerError, match="not one of"):
        g.record_answer(checkin, question_id="ck.myelo.fever", raw="maybe")


def test_a_number_outside_the_bounds_is_refused_not_clamped() -> None:
    """102 into a Celsius question is a patient reading Fahrenheit. Clamping it
    to 43 would invent a red flag; refusing it asks her again."""
    checkin = make_checkin()
    with pytest.raises(g.AnswerError, match="above 43"):
        g.record_answer(checkin, question_id="ck.myelo.temp", raw=102)


def test_a_question_that_was_not_asked_cannot_be_answered() -> None:
    checkin = make_checkin()
    with pytest.raises(g.AnswerError, match="was not asked"):
        g.record_answer(checkin, question_id="ck.gi.vomit", raw=3)


def test_the_frozen_snapshot_is_what_is_validated_against_not_the_live_bank() -> None:
    """The bank may be re-authored between the message going out and the answer
    coming back. A "2" means what it meant when she read the question."""
    checkin = make_checkin()
    checkin.asked = [
        {
            "id": "ck.myelo.temp",
            "type": "number",
            "min": 34,
            "max": 39,
            "prompt": {"en": "Temperature?"},
        }
    ]
    with pytest.raises(g.AnswerError, match="above 39"):
        g.record_answer(checkin, question_id="ck.myelo.temp", raw=41)


def test_the_grading_rules_are_frozen_too_so_a_published_bank_cannot_re_grade() -> None:
    """The sibling of the snapshot rule above, and S18-late is why it exists.

    A grade is recomputed on every answer and every correction. Once an admin can
    publish a new bank from a console, rules read live would silently re-decide
    answers a patient already gave — so a check-in carries the rules it will be
    graded by, and the bank is consulted only for rows written before that.
    """
    checkin = make_checkin(responses={"ck.myelo.fever": "yes"})
    assert g.grade(checkin).grade is CheckinGrade.RED  # the bank's rule, unfrozen

    # The same answers, graded by the rules as they stood when she was asked.
    checkin.grading_rules = [
        {
            "id": "ck.frozen.fever",
            "grade": "amber",
            "reason": "Fever reported — as the protocol read that week",
            "when": {"op": "eq", "node": "ck.myelo.fever", "value": "yes"},
        }
    ]
    graded_frozen = g.grade(checkin)
    assert graded_frozen.grade is CheckinGrade.AMBER
    assert [r.id for r in graded_frozen.reasons] == ["ck.frozen.fever"]


def test_a_frozen_rule_the_validator_rejects_grades_amber_by_hand() -> None:
    """Never green. "We cannot read the rules" and "she is fine" stay different
    facts — the same distinction an expired check-in draws."""
    checkin = make_checkin(responses={"ck.myelo.fever": "yes"})
    checkin.grading_rules = [
        # A rule over a question that was never asked: `rules.validate` refuses it.
        {
            "id": "bad",
            "grade": "red",
            "reason": "…",
            "when": {"op": "eq", "node": "nope", "value": "yes"},
        }
    ]
    result = g.grade(checkin)
    assert result.grade is CheckinGrade.AMBER
    assert result.reasons[0].source == "system"
    assert "grade by hand" in result.reasons[0].reason


def test_a_frozen_rule_over_free_text_is_refused_like_any_other() -> None:
    """The snapshot is not a way around the boundary: rules still cannot match
    ASR output, so "no blood in my stool" cannot fire a bleeding grade."""
    checkin = make_checkin("palliative_comfort")
    free_text = next(q["id"] for q in checkin.asked if q["type"] == "free_voice")
    checkin.responses = {free_text: "some bleeding"}
    checkin.grading_rules = [
        {
            "id": "bad",
            "grade": "red",
            "reason": "…",
            "when": {"op": "eq", "node": free_text, "value": "some bleeding"},
        }
    ]
    assert g.grade(checkin).grade is CheckinGrade.AMBER  # by hand, not red


def test_unanswered_lists_what_is_still_outstanding_in_order() -> None:
    checkin = make_checkin(responses={"ck.myelo.fever": "no"})
    assert [q["id"] for q in g.unanswered(checkin)] == [
        "ck.myelo.temp",
        "ck.myelo.mouth",
        "ck.myelo.bleeding",
    ]


# =============================================================================
# 3. the free-text assist: one thing it may do, several it may not
# =============================================================================


def _assist(payload: dict[str, Any] | str, monkeypatch) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(
        g, "llm_chain", lambda settings=None: [FakeLLMProvider(script=[FakeLLMScript(text=text)])]
    )


async def test_a_concerning_sentence_raises_green_to_amber(monkeypatch) -> None:
    _assist({"concerning": True, "summary": "cannot get out of bed"}, monkeypatch)
    checkin = make_checkin(
        "palliative_comfort",
        responses={"ck.pall.pain": 2, "ck.pall.other": "उठ नहीं पा रही हूँ"},
    )

    result = await g.grade_with_assist(checkin)

    assert result.grade is CheckinGrade.AMBER
    assert [r.source for r in result.reasons] == ["llm"]
    assert "cannot get out of bed" in result.reasons[0].reason


async def test_the_assist_can_never_produce_a_red(monkeypatch) -> None:
    """A deliberate narrowing of doc 03 §9. A red rings a doctor's phone; making
    that depend on how the transcriber heard an accent is how a system gets
    ignored. An amber is a nurse reading the sentence herself."""
    _assist({"concerning": True, "summary": "coughing blood", "grade": "red"}, monkeypatch)
    checkin = make_checkin(
        "palliative_comfort", responses={"ck.pall.pain": 1, "ck.pall.other": "…"}
    )

    result = await g.grade_with_assist(checkin)

    assert result.grade is CheckinGrade.AMBER


async def test_the_assist_can_never_lower_a_grade_the_rules_set(monkeypatch) -> None:
    _assist({"concerning": False, "summary": "she is fine"}, monkeypatch)
    checkin = make_checkin(
        "palliative_comfort",
        responses={"ck.pall.pain": 9, "ck.pall.other": "सब ठीक है"},
    )

    result = await g.grade_with_assist(checkin)

    assert result.grade is CheckinGrade.RED
    assert [r.id for r in result.reasons] == ["pall.pain_severe", "pall.pain"]
    assert all(r.source == "rule" for r in result.reasons)


async def test_a_red_never_pays_for_a_model_call(monkeypatch) -> None:
    """Already the worst it can be — there is nothing for the assist to add."""
    provider = FakeLLMProvider(script=[FakeLLMScript(text='{"concerning": true}')])
    monkeypatch.setattr(g, "llm_chain", lambda settings=None: [provider])
    checkin = make_checkin(
        "palliative_comfort", responses={"ck.pall.pain": 10, "ck.pall.other": "बहुत दर्द"}
    )

    await g.grade_with_assist(checkin)

    assert provider.calls == []


async def test_a_dead_model_leaves_the_rules_grade_standing(monkeypatch) -> None:
    dead = FakeLLMProvider()
    dead.fail_with = RuntimeError("no model today")
    monkeypatch.setattr(g, "llm_chain", lambda settings=None: [dead])
    checkin = make_checkin(
        "palliative_comfort",
        responses={"ck.pall.pain": 6, "ck.pall.other": "कुछ और भी है"},
    )

    result = await g.grade_with_assist(checkin)

    assert result.grade is CheckinGrade.AMBER
    assert [r.source for r in result.reasons] == ["rule"]


async def test_a_malformed_assist_reply_changes_nothing(monkeypatch) -> None:
    _assist("I think she sounds unwell, honestly", monkeypatch)
    checkin = make_checkin(
        "palliative_comfort", responses={"ck.pall.pain": 1, "ck.pall.other": "ठीक हूँ"}
    )

    result = await g.grade_with_assist(checkin)

    assert result.grade is CheckinGrade.GREEN


async def test_the_assist_is_never_asked_about_a_structured_answer(monkeypatch) -> None:
    """It reads free_voice text and nothing else — a "yes" to a fever question is
    the rules' business."""
    provider = FakeLLMProvider(script=[FakeLLMScript(text='{"concerning": true}')])
    monkeypatch.setattr(g, "llm_chain", lambda settings=None: [provider])
    checkin = make_checkin("myelosuppression", responses={"ck.myelo.mouth": "severe"})

    await g.grade_with_assist(checkin)

    assert provider.calls == []


# =============================================================================
# 4. escalation — the S17 AC's second half
# =============================================================================


async def _plan_with_checkin(
    session: AsyncSession, *, question_set: str = "gi_platinum", day_offset: int = 2
):
    """A real plan, a real signed note behind it, and one pending check-in."""
    clinic = await f.build_clinic(session)
    visit = f.make_visit(
        clinic["patient"], clinic["department"], date=q.today(), channel=Channel.KIOSK
    )
    session.add(visit)
    await session.flush()
    dictation = f.make_dictation(visit, clinic["doctor"])
    dictation.signed_by = clinic["doctor"].id
    session.add(dictation)
    await session.flush()

    plan = CheckinPlan(
        patient_id=clinic["patient"].id,
        visit_id=visit.id,
        dictation_id=dictation.id,
        protocol_key="platinum",
        treatment_at=datetime.now(UTC) - timedelta(days=day_offset),
        lang=Lang.HI,
        schedule=[],
        status=CheckinPlanStatus.ACTIVE,
    )
    session.add(plan)
    await session.flush()

    qset = pb.get_bank().question_set(question_set)
    checkin = Checkin(
        plan_id=plan.id,
        due_at=datetime.now(UTC),
        day_offset=day_offset,
        question_set=question_set,
        asked=[question.to_json() for question in qset.questions],
        message="…",
        lang=Lang.HI,
        channel=Channel.WHATSAPP,
        state=CheckinState.SENT,
    )
    session.add(checkin)
    await session.flush()
    return clinic, plan, checkin


async def test_a_red_d2_answer_escalates_to_the_doctor_who_signed(
    session: AsyncSession, settings, monkeypatch
) -> None:
    """S17 AC, second half: a simulated D+2 red answer escalates."""
    sms = FakeSMSProvider()
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: sms)
    monkeypatch.setattr(settings, "coordinator_phone", "+915550000099")
    clinic, _, checkin = await _plan_with_checkin(session)

    grading = await g.submit(
        session,
        checkin=checkin,
        answers={"ck.gi.vomit": 7, "ck.gi.intake": "almost_nothing"},
        settings=settings,
    )

    assert grading.grade is CheckinGrade.RED
    assert checkin.state is CheckinState.ANSWERED
    assert checkin.escalated_at is not None
    assert checkin.escalated_to == clinic["doctor"].user_id
    assert checkin.next_attempt_at is None
    recipients = [message.to for message in sms.sent]
    assert clinic["doctor"].phone in recipients
    assert "+915550000099" in recipients
    assert "RED" in sms.sent[0].body


async def test_the_alert_carries_the_rule_that_fired_and_a_number_to_ring(
    session: AsyncSession, settings, monkeypatch
) -> None:
    sms = FakeSMSProvider()
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: sms)
    clinic, _, checkin = await _plan_with_checkin(session)

    await g.submit(session, checkin=checkin, answers={"ck.gi.urine": "yes"}, settings=settings)

    body = sms.sent[0].body
    assert "renal injury" in body
    assert clinic["patient"].phone in body
    assert "day 2" in body


async def test_an_amber_wakes_nobody_and_waits_on_the_queue(
    session: AsyncSession, settings, monkeypatch
) -> None:
    sms = FakeSMSProvider()
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: sms)
    _, _, checkin = await _plan_with_checkin(session)

    grading = await g.submit(
        session, checkin=checkin, answers={"ck.gi.vomit": 3}, settings=settings
    )

    assert grading.grade is CheckinGrade.AMBER
    assert sms.sent == []
    assert checkin.escalated_at is None
    assert [row[0].id for row in await g.review_queue(session)] == [checkin.id]


async def test_an_alert_that_cannot_be_sent_still_escalates(
    session: AsyncSession, settings, monkeypatch
) -> None:
    """The nurse queue is the backstop; a vendor outage must not lose both."""

    class DeadSms(FakeSMSProvider):
        async def _send(self, *args, **kwargs):  # type: ignore[override]
            from app.providers.base import ProviderError

            raise ProviderError("msg91 is down")

    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: DeadSms())
    _, _, checkin = await _plan_with_checkin(session)

    grading = await g.submit(
        session, checkin=checkin, answers={"ck.gi.urine": "yes"}, settings=settings
    )

    assert grading.grade is CheckinGrade.RED
    assert checkin.escalated_at is not None
    assert checkin.delivery[-1]["status"] == "failed"
    assert [row[0].id for row in await g.review_queue(session)] == [checkin.id]


async def test_a_red_answer_ends_the_checkin_on_the_spot(
    session: AsyncSession, settings, monkeypatch
) -> None:
    """She has said something that needs a phone call today. Asking her three
    more questions before escalating would be this session's point, missed —
    the nurse who rings her can ask the rest."""
    sms = FakeSMSProvider()
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: sms)
    _, _, checkin = await _plan_with_checkin(session)

    grading, finished = await g.answer_one(
        session, checkin=checkin, question_id="ck.gi.vomit", raw=9, settings=settings
    )

    assert finished
    assert grading.grade is CheckinGrade.RED
    assert checkin.state is CheckinState.ANSWERED
    assert checkin.escalated_at is not None
    assert sms.sent
    # The unasked questions stay unasked, and stay visible as such.
    assert [q["id"] for q in g.unanswered(checkin)] == [
        "ck.gi.intake",
        "ck.gi.urine",
        "ck.gi.tingling",
    ]


async def test_an_amber_answer_keeps_asking(session: AsyncSession, settings, monkeypatch) -> None:
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: FakeSMSProvider())
    _, _, checkin = await _plan_with_checkin(session)

    grading, finished = await g.answer_one(
        session, checkin=checkin, question_id="ck.gi.vomit", raw=3, settings=settings
    )

    assert not finished
    assert grading.grade is CheckinGrade.AMBER
    assert checkin.state is CheckinState.SENT
    assert checkin.escalated_at is None


async def test_the_last_answer_finishes_and_grades_with_the_assist(
    session: AsyncSession, settings, monkeypatch
) -> None:
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: FakeSMSProvider())
    _, _, checkin = await _plan_with_checkin(session)
    for question_id, value in (
        ("ck.gi.vomit", 0),
        ("ck.gi.intake", "normal"),
        ("ck.gi.urine", "no"),
    ):
        _, finished = await g.answer_one(
            session, checkin=checkin, question_id=question_id, raw=value, settings=settings
        )
        assert not finished

    grading, finished = await g.answer_one(
        session, checkin=checkin, question_id="ck.gi.tingling", raw="none", settings=settings
    )

    assert finished
    assert grading.grade is CheckinGrade.GREEN
    assert checkin.state is CheckinState.ANSWERED
    assert checkin.answered_at is not None


# =============================================================================
# 5. the nurse review queue
# =============================================================================


async def test_the_queue_puts_red_above_amber_however_old(
    session: AsyncSession, settings, monkeypatch
) -> None:
    """Sorted by time alone, a fever waits behind three sore mouths."""
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: FakeSMSProvider())
    _, _, amber = await _plan_with_checkin(session)
    await g.submit(session, checkin=amber, answers={"ck.gi.vomit": 3}, settings=settings)
    amber.answered_at = datetime.now(UTC) - timedelta(hours=6)

    _, _, red = await _plan_with_checkin(session)
    await g.submit(session, checkin=red, answers={"ck.gi.urine": "yes"}, settings=settings)
    await session.flush()

    assert [row[0].id for row in await g.review_queue(session)] == [red.id, amber.id]


async def test_a_green_checkin_never_reaches_the_queue(
    session: AsyncSession, settings, monkeypatch
) -> None:
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: FakeSMSProvider())
    _, _, checkin = await _plan_with_checkin(session)

    await g.submit(
        session,
        checkin=checkin,
        answers={"ck.gi.vomit": 0, "ck.gi.intake": "normal", "ck.gi.urine": "no"},
        settings=settings,
    )

    assert await g.review_queue(session) == []


async def test_resolving_clears_the_queue_and_keeps_the_grade(
    session: AsyncSession, settings, monkeypatch
) -> None:
    """What the patient said stays true after a nurse has dealt with it."""
    monkeypatch.setattr(g, "get_sms_provider", lambda s=None: FakeSMSProvider())
    clinic, _, checkin = await _plan_with_checkin(session)
    await g.submit(session, checkin=checkin, answers={"ck.gi.vomit": 3}, settings=settings)

    await g.resolve(
        session, checkin=checkin, user_id=clinic["user"].id, note="Rang her, antiemetic changed."
    )

    assert await g.review_queue(session) == []
    assert checkin.grade is CheckinGrade.AMBER
    assert checkin.resolved_by == clinic["user"].id
    assert checkin.resolution_note.startswith("Rang her")
