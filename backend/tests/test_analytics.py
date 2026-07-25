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
from app.models.enums import Channel, IntakeTier, UsagePurpose, VisitStatus
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
