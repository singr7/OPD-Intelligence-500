"""Analytics service tests (S18) — the dashboard's numbers, proven against the feed.

The load-bearing test is `test_reconciles_to_usage_events_exactly`: on a seeded
replay day, every rolled-up total the dashboard shows must equal the plain sum of
the `usage_events` rows that produced it — the S18 AC, and the property that makes
the dashboard trustworthy rather than merely plausible. The others pin the shapes
(filters, what-if hand-calc, unit economics splits) the routes lean on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app import analytics
from app.models.clinical import ClinicalNote
from app.models.enums import Channel, IntakeTier, NoteStatus, UsagePurpose, VisitStatus
from app.models.metering import UsageEvent
from tests.factories import build_clinic, make_intake, make_visit

pytestmark = pytest.mark.asyncio

# A fixed replay day, well clear of "now", so windowing never straddles midnight.
REPLAY = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def _event(
    *,
    at: datetime,
    provider: str,
    cost: str,
    channel: Channel | None = None,
    tier: IntakeTier | None = None,
    purpose: UsagePurpose = UsagePurpose.INTAKE_TURN,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    audio: str = "0",
    session_id: str | None = None,
    intake_id=None,
) -> UsageEvent:
    return UsageEvent(
        at=at,
        minute_bucket=at.replace(second=0, microsecond=0),
        channel=channel,
        tier=tier,
        provider=provider,
        model=model,
        purpose=purpose,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        audio_seconds=Decimal(audio),
        computed_cost_inr=Decimal(cost),
        session_id=session_id,
        intake_id=intake_id,
    )


async def _seed_replay_day(session) -> Decimal:
    """A small but dimensionally-diverse replay day. Returns total spend."""
    events = [
        _event(
            at=REPLAY,
            provider="gemini",
            model="flash",
            channel=Channel.KIOSK,
            tier=IntakeTier.CONVERSATIONAL,
            purpose=UsagePurpose.INTAKE_TURN,
            tokens_in=1000,
            tokens_out=200,
            cost="0.5000",
            session_id="s1",
        ),
        _event(
            at=REPLAY + timedelta(minutes=1),
            provider="gemini",
            model="flash",
            channel=Channel.KIOSK,
            tier=IntakeTier.CONVERSATIONAL,
            purpose=UsagePurpose.SUMMARY,
            tokens_in=500,
            cost="0.2500",
            session_id="s1",
        ),
        _event(
            at=REPLAY + timedelta(minutes=2),
            provider="sarvam",
            model="bulbul",
            channel=Channel.WHATSAPP,
            tier=IntakeTier.RULE_BASED,
            purpose=UsagePurpose.INTAKE_TURN,
            audio="30",
            cost="0.1200",
            session_id="s2",
        ),
        _event(
            at=REPLAY + timedelta(minutes=3),
            provider="openai",
            model="gpt-4o-mini",
            channel=Channel.WHATSAPP,
            tier=IntakeTier.RULE_BASED,
            purpose=UsagePurpose.DICTATION,
            tokens_in=800,
            tokens_out=400,
            cost="0.9900",
            session_id="s3",
        ),
        # Telephony (S14): a per-minute audio row from the voice gateway. The
        # dashboard's five dimensions already carry `channel=phone`, so it reconciles
        # with no dashboard change — the free instrument the S18E handoff promised.
        _event(
            at=REPLAY + timedelta(minutes=4),
            provider="exotel",
            model="voicebot",
            channel=Channel.PHONE,
            tier=IntakeTier.CONVERSATIONAL,
            purpose=UsagePurpose.INTAKE_TURN,
            audio="60",
            cost="0.7500",
            session_id="s4",
        ),
    ]
    session.add_all(events)
    await session.flush()
    return sum((e.computed_cost_inr for e in events), Decimal("0")).quantize(analytics.CENT)


async def test_reconciles_to_usage_events_exactly(session) -> None:
    """The S18 AC: rolled-up totals == the raw sum, to the paisa."""
    total = await _seed_replay_day(session)
    start, end = REPLAY - timedelta(minutes=1), REPLAY + timedelta(hours=1)

    series = await analytics.time_series(session, start=start, end=end)
    series_total = sum((p.cost_inr for p in series), Decimal("0")).quantize(analytics.CENT)

    rows = await analytics.breakdown(session, start=start, end=end)
    breakdown_total = sum((r.cost_inr for r in rows), Decimal("0")).quantize(analytics.CENT)

    assert series_total == total
    assert breakdown_total == total
    # % of spend is a partition — it sums to 100 (within rounding).
    assert abs(sum(r.pct_of_spend for r in rows) - 100.0) < 0.01


async def test_filters_narrow_every_dimension(session) -> None:
    await _seed_replay_day(session)
    start, end = REPLAY - timedelta(minutes=1), REPLAY + timedelta(hours=1)

    kiosk = await analytics.breakdown(
        session, start=start, end=end, filters=analytics.Filters(channel=Channel.KIOSK)
    )
    assert sum((r.cost_inr for r in kiosk), Decimal("0")) == Decimal("0.7500")  # 0.50 + 0.25

    dictation = await analytics.breakdown(
        session, start=start, end=end, filters=analytics.Filters(purpose=UsagePurpose.DICTATION)
    )
    assert [r.provider for r in dictation] == ["openai"]


async def test_what_if_matches_a_hand_calculation(session) -> None:
    """Zero out gemini and the delta is exactly minus gemini's contribution."""
    await _seed_replay_day(session)
    start, end = REPLAY - timedelta(minutes=1), REPLAY + timedelta(hours=1)

    result = await analytics.what_if(
        session,
        start=start,
        end=end,
        overrides=[analytics.PriceOverride(provider="gemini", factor=Decimal("0"))],
    )
    # baseline = 0.50 + 0.25 (gemini) + 0.12 (sarvam) + 0.99 (openai) + 0.75 (exotel).
    # gemini contributed 0.50 + 0.25 = 0.75; removing it leaves 0.12 + 0.99 + 0.75 = 1.86.
    assert result.baseline_inr == Decimal("2.6100")
    assert result.adjusted_inr == Decimal("1.8600")
    assert result.delta_inr == Decimal("-0.7500")


async def test_unit_economics_splits_by_channel_and_tier(session) -> None:
    clinic = await build_clinic(session)
    # Two completed kiosk/V1 intakes with known finalized cost; median is the mean.
    for cost in ("0.4000", "0.6000"):
        visit = make_visit(
            clinic["patient"], clinic["department"], channel=Channel.KIOSK, status=VisitStatus.DONE
        )
        session.add(visit)
        await session.flush()
        intake = make_intake(
            visit, tier=IntakeTier.CONVERSATIONAL, completed_at=REPLAY, cost_inr=Decimal(cost)
        )
        session.add(intake)
    await session.flush()

    ue = await analytics.unit_economics(
        session, start=REPLAY - timedelta(hours=1), end=REPLAY + timedelta(hours=1)
    )
    kiosk_v1 = [
        u
        for u in ue.per_completed_intake
        if u.channel is Channel.KIOSK and u.tier is IntakeTier.CONVERSATIONAL
    ]
    assert len(kiosk_v1) == 1
    assert kiosk_v1[0].count == 2
    assert kiosk_v1[0].median_inr == Decimal("0.5000")  # median of 0.40, 0.60
    assert ue.overall_per_intake.count == 2


async def test_live_strip_reads_the_trailing_minute(session) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            _event(
                at=now - timedelta(seconds=20),
                provider="gemini",
                tier=IntakeTier.CONVERSATIONAL,
                tokens_in=100,
                tokens_out=50,
                cost="0.1000",
                session_id="live1",
            ),
            _event(
                at=now - timedelta(seconds=40),
                provider="gemini",
                tier=IntakeTier.CONVERSATIONAL,
                tokens_in=100,
                tokens_out=50,
                cost="0.1000",
                session_id="live2",
            ),
        ]
    )
    await session.flush()

    strip = await analytics.live_strip(session, now=now)
    assert strip.tokens_per_min == 300  # (100+50) * 2
    assert strip.inr_per_min == Decimal("0.2000")
    assert strip.active_sessions_by_tier == {"conversational": 2}


# -- tier-mix what-if ---------------------------------------------------------


async def _completed(session, clinic, *, channel: Channel, tier: IntakeTier, costs: list[str]):
    for cost in costs:
        visit = make_visit(clinic["patient"], clinic["department"], channel=channel)
        session.add(visit)
        await session.flush()
        session.add(make_intake(visit, tier=tier, completed_at=REPLAY, cost_inr=Decimal(cost)))
    await session.flush()


async def test_tier_mix_matches_a_hand_calculation(session) -> None:
    """Doc 03 §11: "if phone intake ran V2 instead of V1: −₹X/day".

    Both sides are medians this hospital actually booked, so the answer is
    `intakes × (to_median − from_median)` and nothing else.
    """
    clinic = await build_clinic(session)
    # Three phone V1 intakes (median ₹4.00) and two phone V2 (median ₹1.50).
    await _completed(
        session,
        clinic,
        channel=Channel.PHONE,
        tier=IntakeTier.CONVERSATIONAL,
        costs=["2.0000", "4.0000", "9.0000"],
    )
    await _completed(
        session,
        clinic,
        channel=Channel.PHONE,
        tier=IntakeTier.RULE_BASED,
        costs=["1.0000", "2.0000"],
    )

    mix = await analytics.tier_mix(
        session,
        start=REPLAY - timedelta(hours=1),
        end=REPLAY + timedelta(hours=1),
        channel=Channel.PHONE,
        from_tier=IntakeTier.CONVERSATIONAL,
        to_tier=IntakeTier.RULE_BASED,
    )

    assert mix.basis == "observed"
    assert mix.intakes == 3
    assert mix.from_median_inr == Decimal("4.0000")
    assert mix.to_median_inr == Decimal("1.5000")
    # By hand: 3 × 4.00 = 12.00 today, 3 × 1.50 = 4.50 on V2, so −₹7.50.
    assert mix.baseline_inr == Decimal("12.00")
    assert mix.adjusted_inr == Decimal("4.50")
    assert mix.delta_inr == Decimal("-7.50")


async def test_tier_mix_refuses_rather_than_modelling_an_unobserved_tier(session) -> None:
    """No phone intake has ever run V2 → no number, and the reason why.

    The alternative is pricing phone V2 off the kiosk's V2 intakes, which are a
    different unit of work; a confident wrong number here would be an operator
    switching a channel's tier on the strength of it.
    """
    clinic = await build_clinic(session)
    await _completed(
        session,
        clinic,
        channel=Channel.PHONE,
        tier=IntakeTier.CONVERSATIONAL,
        costs=["4.0000"],
    )
    await _completed(
        session,
        clinic,
        channel=Channel.KIOSK,
        tier=IntakeTier.RULE_BASED,
        costs=["0.2000"],
    )

    mix = await analytics.tier_mix(
        session,
        start=REPLAY - timedelta(hours=1),
        end=REPLAY + timedelta(hours=1),
        channel=Channel.PHONE,
        from_tier=IntakeTier.CONVERSATIONAL,
        to_tier=IntakeTier.RULE_BASED,
    )

    assert mix.to_median_inr is None
    assert "no measured cost to re-price against" in mix.basis
    # Unknown means unchanged, never zero: the panel shows no saving, not a saving.
    assert mix.delta_inr == Decimal("0.00")


# =============================================================================
# Ambient note tags (M4)
# =============================================================================


async def _note(session, clinic, *, status, tags: dict, at: datetime | None = None):
    """One clinical note carrying the given tags, in the given state."""
    visit = make_visit(clinic["patient"], clinic["department"], channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    note = ClinicalNote(
        visit_id=visit.id,
        doctor_id=clinic["doctor"].id,
        transcript="…",
        structured={
            "version": 1,
            "mapped": None,
            "fields": {
                "subjective": "",
                "objective": "",
                "assessment": "tolerating",
                "plan_narrative": "",
                "tags": tags,
            },
            "edits": [],
        },
        status=status,
    )
    session.add(note)
    await session.flush()
    if at is not None:
        note.created_at = at
        await session.flush()
    return note


def _week() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(days=7), now + timedelta(days=1)


async def test_note_tags_count_symptoms_problems_and_follow_up_debt(session) -> None:
    """The query that makes the S/O/A/P mapping worth having (plan §3.2)."""
    clinic = await build_clinic(session)
    await _note(
        session,
        clinic,
        status=NoteStatus.CONFIRMED,
        tags={
            "problems": ["carcinoma breast"],
            "symptoms": [{"name": "mucositis", "grade_mentioned": "1"}],
            "followups": ["CBC before next cycle"],
        },
    )
    await _note(
        session,
        clinic,
        status=NoteStatus.CONFIRMED,
        tags={
            "problems": ["carcinoma breast"],
            "symptoms": [{"name": "mucositis", "grade_mentioned": None}, {"name": "fatigue"}],
            "followups": [],
        },
    )

    lo, hi = _week()
    tags = await analytics.note_tags(session, start=lo, end=hi)

    assert tags.notes_counted == 2
    assert tags.drafts_excluded == 0
    assert tags.problems[0].label == "carcinoma breast"
    assert tags.problems[0].notes == 2
    mucositis = next(s for s in tags.symptoms if s.label == "mucositis")
    assert mucositis.notes == 2
    # Only one doctor said a grade out loud. The field counts that, not grading.
    assert mucositis.with_grade == 1
    fatigue = next(s for s in tags.symptoms if s.label == "fatigue")
    assert (fatigue.notes, fatigue.with_grade) == (1, 0)
    assert [f.label for f in tags.followups] == ["CBC before next cycle"]


async def test_a_draft_note_is_not_counted(session) -> None:
    """A draft is a machine reading nobody has checked. Counting one would put a
    model's guess into a clinic-level number with no doctor in between."""
    clinic = await build_clinic(session)
    await _note(
        session,
        clinic,
        status=NoteStatus.DRAFT,
        tags={"problems": ["carcinoma lung"], "symptoms": [], "followups": []},
    )
    await _note(
        session,
        clinic,
        status=NoteStatus.CONFIRMED,
        tags={"problems": ["carcinoma breast"], "symptoms": [], "followups": []},
    )

    lo, hi = _week()
    tags = await analytics.note_tags(session, start=lo, end=hi)

    assert tags.notes_counted == 1
    # Reported rather than hidden: a period where most notes were never confirmed
    # is a fact about the workflow, and it is what would otherwise make these
    # counts quietly unrepresentative.
    assert tags.drafts_excluded == 1
    assert [p.label for p in tags.problems] == ["carcinoma breast"]


async def test_one_note_counts_a_tag_once_however_often_it_repeats(session) -> None:
    """A doctor who says "mucositis" three times in one observation still has one
    patient with mucositis."""
    clinic = await build_clinic(session)
    await _note(
        session,
        clinic,
        status=NoteStatus.CONFIRMED,
        tags={
            "problems": ["carcinoma breast", "carcinoma breast"],
            "symptoms": [{"name": "mucositis"}, {"name": "Mucositis", "grade_mentioned": "1"}],
            "followups": ["CBC", "CBC"],
        },
    )

    lo, hi = _week()
    tags = await analytics.note_tags(session, start=lo, end=hi)

    assert tags.problems[0].notes == 1
    assert tags.followups[0].notes == 1
    # Case-folded to one row, displayed in the casing it first arrived in — and a
    # grade said anywhere in the note counts for the note.
    assert [(s.label, s.notes, s.with_grade) for s in tags.symptoms] == [("mucositis", 1, 1)]


async def test_note_tags_outside_the_window_are_not_counted(session) -> None:
    clinic = await build_clinic(session)
    await _note(
        session,
        clinic,
        status=NoteStatus.CONFIRMED,
        tags={"problems": ["last month's problem"], "symptoms": [], "followups": []},
        at=datetime.now(UTC) - timedelta(days=40),
    )

    lo, hi = _week()
    tags = await analytics.note_tags(session, start=lo, end=hi)

    assert tags.notes_counted == 0
    assert tags.problems == []


async def test_a_note_with_no_tags_at_all_does_not_break_the_count(session) -> None:
    """A doctor can confirm a note whose mapping failed and which they typed prose
    into. It counts as a note and contributes no tags."""
    clinic = await build_clinic(session)
    await _note(session, clinic, status=NoteStatus.CONFIRMED, tags={})

    lo, hi = _week()
    tags = await analytics.note_tags(session, start=lo, end=hi)

    assert tags.notes_counted == 1
    assert tags.problems == [] and tags.symptoms == [] and tags.followups == []
