"""Signing a note drafts the right plan, and the model cannot move it (doc 03 §9).

The first half of S17's acceptance criterion: *sign a fixture dictation → correct
plan drafted*. "Correct" is two separate claims and they are tested separately —
the right **regimen family** (chosen from the drugs and the doctor's own words,
never by a model) and the right **days and question sets** (copied from the
protocol, never read back from the model's reply).

The adversarial half is the important one. Everything here that hands the model a
reply hands it a *bad* one: a plan with an extra day, a missing rung, a channel
the patient cannot use, prose instead of JSON. In every case the assertion is the
same — the schedule is still the protocol's.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import dictation as dic
from app import queue as q
from app.checkins import plan as cp
from app.checkins import protocols as pb
from app.models.content import Checkin, CheckinPlan
from app.models.enums import Channel, CheckinPlanStatus, CheckinState, DictationStatus, Lang
from app.providers.llm import FakeLLMProvider, FakeLLMScript

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "checkin_dictations.json").read_text(encoding="utf-8")
)
CASES: list[dict[str, Any]] = FIXTURES["cases"]
CASE_IDS = [case["id"] for case in CASES]


def mapping_of(case_id: str) -> dic.DictationMapping:
    case = next(c for c in CASES if c["id"] == case_id)
    return dic.DictationMapping.parse(case["mapping"])


async def signed_dictation(session: AsyncSession, case_id: str) -> tuple[dict[str, Any], Any]:
    """A clinic with a dictation whose fields are the fixture's mapping, signed.

    Goes through `dic.sign` rather than setting the status by hand, because the
    thing under test is that signing is what drafts the plan.
    """
    case = next(c for c in CASES if c["id"] == case_id)
    clinic = await f.build_clinic(session)
    visit = f.make_visit(
        clinic["patient"], clinic["department"], date=q.today(), channel=Channel.KIOSK
    )
    session.add(visit)
    await session.flush()

    dictation = f.make_dictation(visit, clinic["doctor"])
    dictation.structured = {
        **dic.empty_structured(),
        "mapped": case["mapping"],
        "fields": case["mapping"],
    }
    session.add(dictation)
    await session.flush()
    signed = await dic.sign(session, dictation=dictation, doctor=clinic["doctor"])
    return clinic, signed


async def plan_for(session: AsyncSession, dictation_id) -> CheckinPlan | None:
    return await session.scalar(select(CheckinPlan).where(CheckinPlan.dictation_id == dictation_id))


# =============================================================================
# 1. the family is chosen from the note, deterministically
# =============================================================================


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_fixture_notes_choose_the_family_they_should(case: dict[str, Any]) -> None:
    chosen = cp.choose_protocol(dic.DictationMapping.parse(case["mapping"]))
    assert (chosen.key if chosen else None) == case["expect_protocol"]


def test_a_brand_name_matches_by_formulary_class_not_by_a_keyword_list() -> None:
    """The bank lists "carboplatin"; the doctor dictated "Kemocarb". The match
    comes from `app.formulary`'s class for that brand, which is why the protocol
    bank does not have to carry 617 dictatable names of its own."""
    mapping = dic.DictationMapping.parse(
        {"diagnosis": "Ca ovary", "meds": [{"name": "Inj Kemocarb 450"}]}
    )
    chosen = cp.choose_protocol(mapping)
    assert chosen is not None and chosen.key == "platinum"


def test_a_doublet_takes_the_family_whose_signal_is_least_reversible() -> None:
    """Carboplatin + paclitaxel matches two families. Taxane wins on precedence,
    and both are recorded so the doctor approving can see the choice."""
    mapping = dic.DictationMapping.parse(
        {
            "diagnosis": "Ca ovary",
            "meds": [{"name": "Inj Carboplatin 450"}, {"name": "Inj Paclitaxel 260"}],
        }
    )
    assert [p.key for p in cp.matching_protocols(mapping)] == ["taxane", "platinum"]


def test_the_haystack_is_the_doctors_note_not_the_transcript() -> None:
    """ "My daughter had an operation last year" is in the transcript and must not
    put this patient on a wound-care protocol. Only structured fields are read."""
    mapping = dic.DictationMapping.parse(
        {"diagnosis": "Hypothyroidism", "meds": [{"name": "Tab Thyronorm 50"}]}
    )
    assert cp.choose_protocol(mapping) is None


async def test_a_note_that_starts_no_treatment_gets_no_plan(session: AsyncSession) -> None:
    """Not every consult starts a follow-up. An empty plan to approve trains a
    doctor to tap through the ones that matter."""
    _, signed = await signed_dictation(session, "routine-review-no-treatment")
    assert await plan_for(session, signed.id) is None


# =============================================================================
# 2. signing drafts the plan
# =============================================================================


async def test_signing_a_platinum_note_drafts_the_platinum_plan(session: AsyncSession) -> None:
    """S17 AC, first half."""
    _, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)

    assert plan is not None
    assert plan.protocol_key == "platinum"
    assert plan.status is CheckinPlanStatus.DRAFT
    assert [rung["day_offset"] for rung in plan.schedule] == [2, 7, 14]
    assert [rung["question_set"] for rung in plan.schedule] == [
        "gi_platinum",
        "myelosuppression",
        "myelosuppression",
    ]


async def test_the_plan_is_anchored_on_the_treatment_date_the_doctor_dictated(
    session: AsyncSession,
) -> None:
    _, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)

    assert plan is not None
    assert plan.treatment_at is not None
    assert plan.treatment_at.date().isoformat() == "2026-07-26"
    first = datetime.fromisoformat(plan.schedule[0]["due_at"])
    assert (first.date() - plan.treatment_at.date()).days == 2


async def test_the_next_cycle_is_the_doctors_own_date_not_the_protocols_arithmetic(
    session: AsyncSession,
) -> None:
    _, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)

    assert plan is not None and plan.next_cycle_at is not None
    assert plan.next_cycle_at.date().isoformat() == "2026-08-16"


def test_a_regimen_with_no_cycles_never_invents_a_next_one() -> None:
    bank = pb.get_bank()
    anchor = datetime(2026, 7, 26, tzinfo=UTC)
    mapping = mapping_of("post-op-day-1")
    assert cp.next_cycle_at(mapping, protocol=bank.protocol("post_op"), anchor=anchor) is None


def test_an_unreadable_treatment_date_falls_back_to_the_signature() -> None:
    """ "14 tareekh" is not a date this code guesses at."""
    mapping = dic.DictationMapping.parse(
        {"treatment_events": [{"regimen": "AC-T", "date": "next Tuesday"}]}
    )
    signed_at = datetime(2026, 7, 26, 9, 30, tzinfo=UTC)
    assert cp.treatment_anchor(mapping, signed_at=signed_at) == signed_at


async def test_drafting_never_costs_a_signature(session: AsyncSession, monkeypatch) -> None:
    """A follow-up is next week; the patient is in the room now."""

    def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the protocol bank is on fire")

    monkeypatch.setattr(cp, "_draft", _explode)
    _, signed = await signed_dictation(session, "carboplatin-day-1")

    assert signed.status is DictationStatus.SIGNED
    assert await plan_for(session, signed.id) is None


async def test_drafting_is_idempotent_per_dictation(session: AsyncSession) -> None:
    clinic, signed = await signed_dictation(session, "carboplatin-day-1")
    first = await plan_for(session, signed.id)
    again = await cp.draft_from_dictation(session, dictation=signed, doctor=clinic["doctor"])

    assert first is not None and again is not None
    assert again.id == first.id
    count = await session.scalar(
        select(CheckinPlan)
        .where(CheckinPlan.dictation_id == signed.id)
        .with_only_columns(CheckinPlan.id)
    )
    assert count is not None


# =============================================================================
# 3. the model writes wording, and only wording
# =============================================================================


def _draft_for(case_id: str, lang: Lang = Lang.HI) -> list[cp.DraftRung]:
    bank = pb.get_bank()
    mapping = mapping_of(case_id)
    protocol = cp.choose_protocol(mapping)
    assert protocol is not None
    return cp.draft_schedule(
        protocol=protocol,
        anchor=datetime(2026, 7, 26, 6, 0, tzinfo=UTC),
        lang=lang,
        channels=cp.LADDER,
        bank=bank,
    )


def test_the_plain_message_is_never_english_at_a_hindi_patient() -> None:
    rungs = _draft_for("carboplatin-day-1", lang=Lang.HI)
    assert all(rung.message for rung in rungs)
    assert any("ऀ" <= c <= "ॿ" for c in rungs[0].message)


def test_personalisation_replaces_the_message_and_nothing_else() -> None:
    rungs = _draft_for("carboplatin-day-1")
    before = [(r.day_offset, r.question_set, r.due_at) for r in rungs]
    payload = {
        "schedule": [
            {
                "day_offset": r.day_offset,
                "question_set": r.question_set,
                "message": f"नमस्ते। कल की उल्टी के बारे में — दिन {r.day_offset}।",
            }
            for r in rungs
        ]
    }

    applied, complaints = cp.apply_personalisation(rungs, payload, channels=cp.LADDER)

    assert applied == 3 and complaints == []
    assert [(r.day_offset, r.question_set, r.due_at) for r in rungs] == before
    assert all("कल की उल्टी" in r.message for r in rungs)


def test_a_model_that_adds_a_day_does_not_get_one() -> None:
    rungs = _draft_for("carboplatin-day-1")
    payload = {
        "schedule": [
            {"day_offset": 2, "question_set": "gi_platinum", "message": "ठीक है"},
            {"day_offset": 21, "question_set": "gi_platinum", "message": "और एक दिन"},
        ]
    }

    cp.apply_personalisation(rungs, payload, channels=cp.LADDER)

    assert [r.day_offset for r in rungs] == [2, 7, 14]


def test_a_model_that_drops_a_day_leaves_it_plain_not_missing() -> None:
    rungs = _draft_for("carboplatin-day-1")
    plain = rungs[2].message
    payload = {"schedule": [{"day_offset": 2, "question_set": "gi_platinum", "message": "ठीक है"}]}

    applied, complaints = cp.apply_personalisation(rungs, payload, channels=cp.LADDER)

    assert applied == 1
    assert len(rungs) == 3
    assert rungs[2].message == plain
    assert any("day 14" in c for c in complaints)


def test_a_model_that_swaps_the_question_set_is_ignored() -> None:
    rungs = _draft_for("carboplatin-day-1")
    payload = {
        "schedule": [
            {"day_offset": 2, "question_set": "palliative_comfort", "message": "आराम कैसा है?"}
        ]
    }

    applied, _ = cp.apply_personalisation(rungs, payload, channels=cp.LADDER)

    assert applied == 0
    assert rungs[0].question_set == "gi_platinum"
    assert "आराम कैसा है?" not in rungs[0].message


def test_a_channel_the_patient_cannot_use_is_refused() -> None:
    """A patient reachable only by SMS stays on SMS, however confidently the
    model proposes WhatsApp."""
    bank = pb.get_bank()
    protocol = cp.choose_protocol(mapping_of("carboplatin-day-1"))
    assert protocol is not None
    rungs = cp.draft_schedule(
        protocol=protocol,
        anchor=datetime(2026, 7, 26, 6, 0, tzinfo=UTC),
        lang=Lang.HI,
        channels=(Channel.SMS,),
        bank=bank,
    )
    assert rungs[0].channel is Channel.SMS
    payload = {
        "schedule": [
            {
                "day_offset": 2,
                "question_set": "gi_platinum",
                "channel": "whatsapp",
                "message": "ठीक है",
            }
        ]
    }

    _, complaints = cp.apply_personalisation(rungs, payload, channels=(Channel.SMS,))

    assert rungs[0].channel is Channel.SMS
    assert any("unreachable channel" in c for c in complaints)


@pytest.mark.parametrize("payload", ["not an object", {"schedule": "a sentence"}, {}, None])
def test_a_malformed_personalisation_is_no_personalisation(payload: Any) -> None:
    rungs = _draft_for("carboplatin-day-1")
    plain = [r.message for r in rungs]

    applied, complaints = cp.apply_personalisation(rungs, payload, channels=cp.LADDER)

    assert applied == 0 and complaints
    assert [r.message for r in rungs] == plain


async def test_a_dead_model_still_produces_a_plan(session: AsyncSession, monkeypatch) -> None:
    dead = FakeLLMProvider()
    dead.fail_with = RuntimeError("no model today")
    monkeypatch.setattr(cp, "llm_chain", lambda settings=None: [dead])

    _, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)

    assert plan is not None
    assert len(plan.schedule) == 3
    assert plan.personalisation["applied"] == 0
    assert plan.personalisation["error"]


async def test_a_personalised_plan_records_what_the_model_did(
    session: AsyncSession, monkeypatch
) -> None:
    reply = FakeLLMScript(
        text=json.dumps(
            {
                "protocol_key": "platinum",
                "schedule": [
                    {
                        "day_offset": day,
                        "question_set": qset,
                        "channel": "whatsapp",
                        "message": f"नमस्ते। उल्टी के बारे में पूछना है (दिन {day})।",
                        "why": "note mentions nausea",
                    }
                    for day, qset in (
                        (2, "gi_platinum"),
                        (7, "myelosuppression"),
                        (14, "myelosuppression"),
                    )
                ],
                "notes_for_doctor": "Asked about nausea by name at D+2.",
            }
        )
    )
    monkeypatch.setattr(cp, "llm_chain", lambda settings=None: [FakeLLMProvider(script=[reply])])

    _, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)

    assert plan is not None
    assert plan.personalisation["applied"] == 3
    assert plan.personalisation["notes_for_doctor"].startswith("Asked about nausea")
    assert plan.personalisation["matched_protocols"] == ["platinum"]
    assert all("उल्टी" in rung["message"] for rung in plan.schedule)


# =============================================================================
# 4. approval is what makes it real
# =============================================================================


async def test_a_drafted_plan_has_no_checkins_until_a_doctor_approves(
    session: AsyncSession,
) -> None:
    _, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)
    assert plan is not None

    existing = await session.scalars(select(Checkin).where(Checkin.plan_id == plan.id))
    assert list(existing) == []


async def test_approving_materialises_the_checkins_with_the_questions_frozen(
    session: AsyncSession,
) -> None:
    clinic, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)
    assert plan is not None

    created = await cp.approve(session, plan=plan, doctor=clinic["doctor"])

    assert plan.status is CheckinPlanStatus.ACTIVE
    assert plan.approved_by == clinic["doctor"].id
    assert [c.day_offset for c in created] == [2, 7, 14]
    assert [c.question_set for c in created] == [
        "gi_platinum",
        "myelosuppression",
        "myelosuppression",
    ]
    assert all(c.state is CheckinState.PENDING for c in created)
    # The snapshot, not a reference: the bank may be re-authored next month.
    first = created[0]
    assert [q["id"] for q in first.asked] == list(
        pb.get_bank().question_set("gi_platinum").question_ids
    )
    assert first.asked[0]["prompt"]["hi"]


async def test_a_plan_approved_late_is_due_now_not_skipped(session: AsyncSession) -> None:
    """The questions are still worth asking on the day someone finally taps."""
    clinic, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)
    assert plan is not None
    plan.schedule = [
        dict(rung, due_at=(datetime.now(UTC) - timedelta(days=30)).isoformat())
        for rung in plan.schedule
    ]
    now = datetime.now(UTC)

    created = await cp.approve(session, plan=plan, doctor=clinic["doctor"], now=now)

    assert all(c.next_attempt_at == now for c in created)


async def test_a_plan_can_only_be_approved_once(session: AsyncSession) -> None:
    clinic, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)
    assert plan is not None
    await cp.approve(session, plan=plan, doctor=clinic["doctor"])

    with pytest.raises(cp.PlanError):
        await cp.approve(session, plan=plan, doctor=clinic["doctor"])


async def test_cancelling_stops_the_unsent_and_keeps_what_was_answered(
    session: AsyncSession,
) -> None:
    clinic, signed = await signed_dictation(session, "carboplatin-day-1")
    plan = await plan_for(session, signed.id)
    assert plan is not None
    created = await cp.approve(session, plan=plan, doctor=clinic["doctor"])
    created[0].state = CheckinState.ANSWERED
    created[0].responses = {"ck.gi.vomit": 1}
    await session.flush()

    await cp.cancel(session, plan=plan)

    assert plan.status is CheckinPlanStatus.CANCELLED
    assert created[0].state is CheckinState.ANSWERED
    assert created[0].responses == {"ck.gi.vomit": 1}
    assert [c.state for c in created[1:]] == [CheckinState.CANCELLED, CheckinState.CANCELLED]
    assert all(c.next_attempt_at is None for c in created[1:])
