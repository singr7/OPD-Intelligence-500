"""Cost & usage analytics over `usage_events` (doc 03 §11, S18).

Every number the admin dashboard shows is derived here, and every one of them is
**traceable to `usage_events` or a domain table** — the S18 AC is "no
hand-maintained figures" and "dashboard numbers reconcile to usage_events
exactly on a seeded replay day". So this module only ever *reads* and *sums*; it
never stores a rollup that could drift from the raw feed.

## Why this is built before its channels exist (S18-early)

Pulled ahead of S14/S15/S17. The queries group by the *dimensions* on
`usage_events` — `channel`, `tier`, `purpose`, `provider`, `model` — not by a
hard-coded list of channels. When telephony (S14), campaigns (S15) and check-ins
(S17) start emitting rows, they appear as new values under the existing filters
with **no change here**. The one additive step per channel is extending the
seeded replay day so the reconciliation test covers it (see
`tests/test_analytics.py`).

## Money is Decimal, end to end

`computed_cost_inr` is `Numeric(12,4)` and these sums land in an invoice
reconciliation view. Every rupee that leaves this module is a `Decimal`; the wire
layer (`app/routes/admin.py`) stringifies them, so JSON's float never touches a
cost.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import ClinicalNote, Dictation, Intake, Visit
from app.models.enums import Channel, DictationStatus, IntakeTier, NoteStatus, UsagePurpose
from app.models.metering import PriceBook, UsageEvent

# The cost column's scale — every rupee sum quantizes to this, so a reconciliation
# subtracts to an exact zero rather than to 1e-15.
CENT = Decimal("0.0001")


class Granularity(StrEnum):
    """Time-series bucket size. `minute` is the raw grain `usage_events` stores
    (`minute_bucket`); `day` truncates it in IST so a day lines up with an OPD
    day, not a UTC one (the same boundary the cost guard uses)."""

    MINUTE = "minute"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class Filters:
    """The dashboard's cross-cutting filter set (doc 03 §11).

    Every field is optional; a `None` means "don't filter on this dimension".
    Deliberately the five dimensions that live on `usage_events`, so a new
    channel/tier/purpose/model needs no new field — it just becomes a value the
    UI can pass.
    """

    channel: Channel | None = None
    tier: IntakeTier | None = None
    purpose: UsagePurpose | None = None
    model: str | None = None
    provider: str | None = None

    def apply(self, stmt):
        if self.channel is not None:
            stmt = stmt.where(UsageEvent.channel == self.channel)
        if self.tier is not None:
            stmt = stmt.where(UsageEvent.tier == self.tier)
        if self.purpose is not None:
            stmt = stmt.where(UsageEvent.purpose == self.purpose)
        if self.model is not None:
            stmt = stmt.where(UsageEvent.model == self.model)
        if self.provider is not None:
            stmt = stmt.where(UsageEvent.provider == self.provider)
        return stmt


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    at: datetime
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    audio_seconds: Decimal
    cost_inr: Decimal


@dataclass(frozen=True, slots=True)
class BreakdownRow:
    provider: str
    model: str | None
    purpose: UsagePurpose
    tokens_in: int
    tokens_out: int
    audio_seconds: Decimal
    calls: int
    cost_inr: Decimal
    pct_of_spend: float


@dataclass(frozen=True, slots=True)
class UnitCost:
    """One ₹-per-thing cell, split by a dimension pair (e.g. channel × tier)."""

    channel: Channel | None
    tier: IntakeTier | None
    count: int
    median_inr: Decimal | None
    p90_inr: Decimal | None


@dataclass(frozen=True, slots=True)
class UnitEconomics:
    per_completed_intake: list[UnitCost]
    per_abandoned_intake: UnitCost
    per_dictation: UnitCost
    overall_per_intake: UnitCost


@dataclass(frozen=True, slots=True)
class Anomaly:
    kind: str
    detail: str
    value: Decimal


@dataclass(frozen=True, slots=True)
class FunnelRow:
    channel: Channel
    started: int
    completed: int
    confirmed: int
    median_duration_s: float | None


@dataclass(frozen=True, slots=True)
class OpsMetrics:
    funnel: list[FunnelRow]
    tier_downgrades: int
    intakes_by_lang: dict[str, int]


@dataclass(frozen=True, slots=True)
class TagCount:
    """One tag and how many confirmed notes carried it."""

    label: str
    notes: int


@dataclass(frozen=True, slots=True)
class SymptomCount:
    """One symptom, and how often a doctor said a grade for it out loud.

    `with_grade` is not "how many were graded" — nothing in this system grades.
    It counts the notes where the doctor spoke a grade, which is why the field
    on the note is called `grade_mentioned`. Surfaced separately because the two
    numbers answer different questions: how often the symptom comes up, and how
    often anyone was specific about it.
    """

    label: str
    notes: int
    with_grade: int


@dataclass(frozen=True, slots=True)
class NoteTags:
    """What the ambient notes of a period were about (M4, plan §3.2).

    Every count here is over **confirmed** notes. Drafts are excluded because a
    draft is a machine reading nobody has looked at, and counting one would put
    a model's guess into a clinic-level number with no human in between.
    `drafts_excluded` is reported rather than hidden: a period where most notes
    were never confirmed is a fact about the workflow, and it is exactly the
    thing that would otherwise make these counts quietly unrepresentative.
    """

    notes_counted: int
    drafts_excluded: int
    problems: list[TagCount]
    symptoms: list[SymptomCount]
    followups: list[TagCount]


# -- windowing ----------------------------------------------------------------


def _window(stmt, start: datetime, end: datetime):
    """Half-open `[start, end)` on `at`. Half-open so adjacent windows never
    double-count the row that lands exactly on the boundary."""
    return stmt.where(UsageEvent.at >= start, UsageEvent.at < end)


# -- time series --------------------------------------------------------------


async def time_series(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    granularity: Granularity = Granularity.MINUTE,
    filters: Filters = Filters(),
    tz: str = "Asia/Kolkata",
) -> list[SeriesPoint]:
    """Bucketed tokens/audio/₹ over a window (doc 03 §11 Tab 1 time series).

    `minute` reads `minute_bucket` straight — it is pre-truncated on write, so
    this is a plain GROUP BY on an indexed column. `day` truncates the bucket in
    the OPD timezone.
    """
    if granularity is Granularity.MINUTE:
        bucket = UsageEvent.minute_bucket
    else:
        # date_trunc in IST, then back to UTC so the label is an instant. A day
        # boundary at 00:00 IST, not 05:30 IST, is what an operator expects.
        bucket = func.date_trunc("day", func.timezone(tz, UsageEvent.minute_bucket))

    stmt = (
        select(
            bucket.label("bucket"),
            func.coalesce(func.sum(UsageEvent.tokens_in), 0),
            func.coalesce(func.sum(UsageEvent.tokens_out), 0),
            func.coalesce(func.sum(UsageEvent.cached_tokens), 0),
            func.coalesce(func.sum(UsageEvent.audio_seconds), 0),
            func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0),
        )
        .group_by(bucket)
        .order_by(bucket)
    )
    stmt = filters.apply(_window(stmt, start, end))

    rows = (await session.execute(stmt)).all()
    return [
        SeriesPoint(
            at=_as_utc(r[0]),
            tokens_in=int(r[1]),
            tokens_out=int(r[2]),
            cached_tokens=int(r[3]),
            audio_seconds=Decimal(r[4]),
            cost_inr=Decimal(r[5]).quantize(CENT),
        )
        for r in rows
    ]


# -- breakdown table ----------------------------------------------------------


async def breakdown(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    filters: Filters = Filters(),
) -> list[BreakdownRow]:
    """provider → model → purpose, with tokens, audio, calls, ₹ and % of spend
    (doc 03 §11 Tab 1 breakdown table). Ordered by spend, biggest first."""
    grp = (UsageEvent.provider, UsageEvent.model, UsageEvent.purpose)
    stmt = (
        select(
            *grp,
            func.coalesce(func.sum(UsageEvent.tokens_in), 0),
            func.coalesce(func.sum(UsageEvent.tokens_out), 0),
            func.coalesce(func.sum(UsageEvent.audio_seconds), 0),
            func.count(),
            func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0),
        )
        .group_by(*grp)
        .order_by(func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0).desc())
    )
    stmt = filters.apply(_window(stmt, start, end))

    rows = (await session.execute(stmt)).all()
    total = sum((Decimal(r[7]) for r in rows), Decimal("0"))
    out: list[BreakdownRow] = []
    for r in rows:
        cost = Decimal(r[7]).quantize(CENT)
        out.append(
            BreakdownRow(
                provider=r[0],
                model=r[1],
                purpose=r[2],
                tokens_in=int(r[3]),
                tokens_out=int(r[4]),
                audio_seconds=Decimal(r[5]),
                calls=int(r[6]),
                cost_inr=cost,
                pct_of_spend=float(cost / total * 100) if total > 0 else 0.0,
            )
        )
    return out


# -- unit economics -----------------------------------------------------------

_P50 = func.percentile_cont(0.5)
_P90 = func.percentile_cont(0.9)


async def unit_economics(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
) -> UnitEconomics:
    """₹-per-thing cards (doc 03 §11 Tab 1 unit economics).

    - **per completed intake**: median + p90 of `Intake.cost_inr`, split by
      channel (from the visit) and tier — the finalized attribution S5 wrote on
      completion, which itself is the sum of that intake's `usage_events`.
    - **per abandoned intake**: intakes with no `completed_at` carry no
      finalized `cost_inr`, so their cost is summed live from `usage_events`.
    - **per dictation**: signed dictations over the window's `dictation` spend.

    Completion time (`completed_at`) is the window key for intakes so a card
    counts an intake in the window it *finished*, matching how its cost was
    booked.
    """
    per_completed = await _per_completed_intake(session, start=start, end=end)
    overall = await _overall_per_intake(session, start=start, end=end)
    abandoned = await _per_abandoned_intake(session, start=start, end=end)
    dictation = await _per_dictation(session, start=start, end=end)
    return UnitEconomics(
        per_completed_intake=per_completed,
        per_abandoned_intake=abandoned,
        per_dictation=dictation,
        overall_per_intake=overall,
    )


async def _per_completed_intake(
    session: AsyncSession, *, start: datetime, end: datetime
) -> list[UnitCost]:
    stmt = (
        select(
            Visit.channel,
            Intake.tier,
            func.count(),
            _P50.within_group(Intake.cost_inr.asc()),
            _P90.within_group(Intake.cost_inr.asc()),
        )
        .join(Visit, Visit.id == Intake.visit_id)
        .where(
            Intake.completed_at.is_not(None),
            Intake.completed_at >= start,
            Intake.completed_at < end,
            Intake.cost_inr.is_not(None),
        )
        .group_by(Visit.channel, Intake.tier)
        .order_by(Visit.channel, Intake.tier)
    )
    rows = (await session.execute(stmt)).all()
    return [
        UnitCost(
            channel=r[0],
            tier=r[1],
            count=int(r[2]),
            median_inr=_q(r[3]),
            p90_inr=_q(r[4]),
        )
        for r in rows
    ]


async def _overall_per_intake(session: AsyncSession, *, start: datetime, end: datetime) -> UnitCost:
    stmt = select(
        func.count(),
        _P50.within_group(Intake.cost_inr.asc()),
        _P90.within_group(Intake.cost_inr.asc()),
    ).where(
        Intake.completed_at.is_not(None),
        Intake.completed_at >= start,
        Intake.completed_at < end,
        Intake.cost_inr.is_not(None),
    )
    r = (await session.execute(stmt)).one()
    return UnitCost(channel=None, tier=None, count=int(r[0]), median_inr=_q(r[1]), p90_inr=_q(r[2]))


async def _per_abandoned_intake(
    session: AsyncSession, *, start: datetime, end: datetime
) -> UnitCost:
    """Cost of intakes that started in the window but never completed.

    No `completed_at` means no finalized `cost_inr`, so the cost is the live sum
    of the intake's `usage_events`. Abandonment is keyed on `created_at` (when it
    started) since that is the only timestamp an unfinished intake has.
    """
    per_intake = (
        select(
            Intake.id.label("iid"),
            func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0).label("c"),
        )
        .join(UsageEvent, UsageEvent.intake_id == Intake.id)
        .where(
            Intake.completed_at.is_(None),
            Intake.created_at >= start,
            Intake.created_at < end,
        )
        .group_by(Intake.id)
        .subquery()
    )
    stmt = select(
        func.count(),
        _P50.within_group(cast(per_intake.c.c, Numeric(12, 4)).asc()),
        _P90.within_group(cast(per_intake.c.c, Numeric(12, 4)).asc()),
    )
    r = (await session.execute(stmt)).one()
    return UnitCost(channel=None, tier=None, count=int(r[0]), median_inr=_q(r[1]), p90_inr=_q(r[2]))


async def _per_dictation(session: AsyncSession, *, start: datetime, end: datetime) -> UnitCost:
    signed = await session.scalar(
        select(func.count()).where(
            Dictation.status == DictationStatus.SIGNED,
            Dictation.signed_at >= start,
            Dictation.signed_at < end,
        )
    )
    spend = await session.scalar(
        _window(
            select(func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0)).where(
                UsageEvent.purpose == UsagePurpose.DICTATION
            ),
            start,
            end,
        )
    )
    count = int(signed or 0)
    avg = (Decimal(spend or 0) / count).quantize(CENT) if count else None
    # A single average, not a distribution: dictation cost is per-signed-note and
    # the median here would need the per-note attribution the intake path has and
    # the dictation path does not yet (backlog: attribute dictation usage_events
    # to a dictation_id the way intake_id works).
    return UnitCost(channel=None, tier=None, count=count, median_inr=avg, p90_inr=avg)


# -- what-if ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceOverride:
    """A multiplier on a slice of the price book, for the what-if panel.

    Matches any `usage_events` row whose provider/model equals the set fields
    (a `None` field matches everything). `factor` scales the rupees that slice
    contributed — 0.0 removes it, 1.5 raises it 50%.
    """

    provider: str | None = None
    model: str | None = None
    factor: Decimal = Decimal("1")

    def matches(self, provider: str, model: str | None) -> bool:
        if self.provider is not None and self.provider != provider:
            return False
        if self.model is not None and self.model != model:
            return False
        return True


@dataclass(frozen=True, slots=True)
class WhatIf:
    baseline_inr: Decimal
    adjusted_inr: Decimal

    @property
    def delta_inr(self) -> Decimal:
        return (self.adjusted_inr - self.baseline_inr).quantize(CENT)


async def what_if(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    overrides: list[PriceOverride],
) -> WhatIf:
    """Recompute a window's spend under an edited price book (doc 03 §11 what-if).

    Reconcilable by hand: baseline is the plain sum of `computed_cost_inr`;
    adjusted re-scales each (provider, model) group's contribution by its
    override factor and re-sums. Because it works off the *stored* per-row cost,
    not a re-priced quantity, the delta is exactly `Σ cost·(factor−1)` over the
    matched groups — which is what the test asserts by hand.

    Tier-mix what-if ("if phone ran V2 not V1") is the other half of doc 03
    §11's panel and a different recompute — `tier_mix`, below.
    """
    stmt = _window(
        select(
            UsageEvent.provider,
            UsageEvent.model,
            func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0),
        ),
        start,
        end,
    ).group_by(UsageEvent.provider, UsageEvent.model)
    rows = (await session.execute(stmt)).all()

    baseline = Decimal("0")
    adjusted = Decimal("0")
    for provider, model, cost in rows:
        cost = Decimal(cost)
        baseline += cost
        factor = Decimal("1")
        for ov in overrides:
            if ov.matches(provider, model):
                factor = ov.factor  # last matching override wins, most-specific last by convention
        adjusted += cost * factor
    return WhatIf(baseline_inr=baseline.quantize(CENT), adjusted_inr=adjusted.quantize(CENT))


# -- tier-mix what-if ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TierMix:
    """ "If phone intake had run V2 instead of V1, what would the week have cost?"
    (doc 03 §11 what-if, second half).

    Deliberately **measured, not modelled**: both sides of the comparison are
    medians of `Intake.cost_inr` this hospital actually booked, on this channel,
    at each tier. So the answer is arithmetic anyone can check —
    `intakes × (to_median − from_median)` — rather than the output of a cost
    model with assumptions nobody wrote down.

    Which is also why it refuses. With no completed intakes on the target tier
    for that channel there is nothing to price against, and `basis` says so
    instead of the panel showing a confident number derived from the other
    channels' shapes. A phone V1 minute and a kiosk V1 turn are not the same
    unit of work, and averaging them would make the panel worse than absent.
    """

    channel: Channel
    from_tier: IntakeTier
    to_tier: IntakeTier
    intakes: int
    from_median_inr: Decimal | None
    to_median_inr: Decimal | None
    #: "observed", or the reason there is no answer.
    basis: str

    @property
    def baseline_inr(self) -> Decimal:
        if self.from_median_inr is None:
            return Decimal("0.00")
        return (self.from_median_inr * self.intakes).quantize(CENT)

    @property
    def adjusted_inr(self) -> Decimal:
        if self.to_median_inr is None:
            return self.baseline_inr
        return (self.to_median_inr * self.intakes).quantize(CENT)

    @property
    def delta_inr(self) -> Decimal:
        return (self.adjusted_inr - self.baseline_inr).quantize(CENT)


async def tier_mix(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    channel: Channel,
    from_tier: IntakeTier,
    to_tier: IntakeTier,
) -> TierMix:
    """Re-price one channel's completed intakes at another tier's observed median."""
    observed = {
        (u.channel, u.tier): u
        for u in await _per_completed_intake(session, start=start, end=end)
        if u.channel is not None and u.tier is not None
    }
    source = observed.get((channel, from_tier))
    target = observed.get((channel, to_tier))

    if source is None or source.median_inr is None:
        return TierMix(
            channel=channel,
            from_tier=from_tier,
            to_tier=to_tier,
            intakes=0,
            from_median_inr=None,
            to_median_inr=target.median_inr if target else None,
            basis=f"no completed {channel.value} intakes ran on {from_tier.value} in this window",
        )
    if target is None or target.median_inr is None:
        return TierMix(
            channel=channel,
            from_tier=from_tier,
            to_tier=to_tier,
            intakes=source.count,
            from_median_inr=source.median_inr,
            to_median_inr=None,
            basis=(
                f"no completed {channel.value} intakes ran on {to_tier.value} in this window, "
                "so there is no measured cost to re-price against"
            ),
        )

    return TierMix(
        channel=channel,
        from_tier=from_tier,
        to_tier=to_tier,
        intakes=source.count,
        from_median_inr=source.median_inr,
        to_median_inr=target.median_inr,
        basis="observed",
    )


# -- live strip ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveStrip:
    tokens_per_min: int
    inr_per_min: Decimal
    active_sessions_by_tier: dict[str, int]
    at: datetime


async def live_strip(session: AsyncSession, *, now: datetime | None = None) -> LiveStrip:
    """Right-now rates (doc 03 §11 Tab 1 live strip).

    A one-minute trailing window: tokens and ₹ over the last 60s *are* the
    per-minute rate. Active sessions are the distinct `session_id`s seen in the
    last two minutes, grouped by tier — a session mid-turn may have gone quiet
    for a few seconds, so a 60s window would under-count it.
    """
    now = now or datetime.now(UTC)
    minute_ago = now - timedelta(minutes=1)
    two_min_ago = now - timedelta(minutes=2)

    rate = (
        await session.execute(
            _window(
                select(
                    func.coalesce(func.sum(UsageEvent.tokens_in + UsageEvent.tokens_out), 0),
                    func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0),
                ),
                minute_ago,
                now,
            )
        )
    ).one()

    active = (
        await session.execute(
            _window(
                select(UsageEvent.tier, func.count(func.distinct(UsageEvent.session_id))).where(
                    UsageEvent.session_id.is_not(None)
                ),
                two_min_ago,
                now,
            ).group_by(UsageEvent.tier)
        )
    ).all()

    return LiveStrip(
        tokens_per_min=int(rate[0]),
        inr_per_min=Decimal(rate[1]).quantize(CENT),
        active_sessions_by_tier={(t.value if t else "unknown"): int(n) for t, n in active},
        at=now,
    )


# -- anomalies ----------------------------------------------------------------


async def anomalies(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    runaway_intake_inr: Decimal = Decimal("50"),
) -> list[Anomaly]:
    """Cheap red flags (doc 03 §11 anomaly flags).

    - **cost/intake spike**: today's median ₹/completed-intake vs the trailing
      7-day median; >2× fires.
    - **runaway session**: any single intake whose finalized cost clears a
      threshold — a stuck loop burning tokens.

    Latency degradation (the third spec flag) needs the provider-health series
    S19 wires into Grafana; not computed here.
    """
    now = now or datetime.now(UTC)
    today = _day_start(now)
    week_ago = today - timedelta(days=7)
    out: list[Anomaly] = []

    today_median = await session.scalar(
        select(_P50.within_group(Intake.cost_inr.asc())).where(
            Intake.completed_at >= today,
            Intake.cost_inr.is_not(None),
        )
    )
    base_median = await session.scalar(
        select(_P50.within_group(Intake.cost_inr.asc())).where(
            Intake.completed_at >= week_ago,
            Intake.completed_at < today,
            Intake.cost_inr.is_not(None),
        )
    )
    if today_median and base_median and Decimal(today_median) > Decimal(base_median) * 2:
        out.append(
            Anomaly(
                kind="cost_per_intake_spike",
                detail=(
                    f"median ₹/intake today {_q(today_median)} is over 2× the "
                    f"7-day median {_q(base_median)}"
                ),
                value=_q(today_median) or Decimal("0"),
            )
        )

    runaway = (
        await session.execute(
            select(Intake.id, Intake.cost_inr)
            .where(Intake.completed_at >= today, Intake.cost_inr > runaway_intake_inr)
            .order_by(Intake.cost_inr.desc())
        )
    ).all()
    for iid, cost in runaway:
        out.append(
            Anomaly(
                kind="runaway_session",
                detail=f"intake {iid} cost ₹{_q(cost)}, over the ₹{runaway_intake_inr} threshold",
                value=_q(cost) or Decimal("0"),
            )
        )
    return out


# -- ops (Tab 2) --------------------------------------------------------------


async def ops_metrics(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
) -> OpsMetrics:
    """Intake & operations metrics (doc 03 §11 Tab 2), from the domain tables.

    Built from what the schema records today: the intake funnel (started →
    completed → confirmed) per channel with median duration, tier-downgrade count
    (intakes running below the channel's ceiling tier — a proxy until S8's
    downgrade events land their own rows), and intake volume by language.
    Node-level abandonment ("where in the tree people quit") wants the per-node
    answer timestamps and is the tree-improvement report deferred with the S18
    tree editor.
    """
    funnel = await _funnel(session, start=start, end=end)

    downgrades = await session.scalar(
        select(func.count())
        .select_from(Intake)
        .where(
            Intake.created_at >= start,
            Intake.created_at < end,
            # V1 is the ceiling; anything running below it on a channel that could
            # do V1 is a downgrade. A coarse proxy — real events come with S8.
            Intake.tier.in_([IntakeTier.RULE_BASED, IntakeTier.PRERECORDED]),
        )
    )

    by_lang = (
        await session.execute(
            select(Intake.lang, func.count())
            .where(Intake.created_at >= start, Intake.created_at < end)
            .group_by(Intake.lang)
        )
    ).all()

    return OpsMetrics(
        funnel=funnel,
        tier_downgrades=int(downgrades or 0),
        intakes_by_lang={lang.value: int(n) for lang, n in by_lang},
    )


async def _funnel(session: AsyncSession, *, start: datetime, end: datetime) -> list[FunnelRow]:
    duration_s = func.extract("epoch", Intake.completed_at - Intake.created_at)
    stmt = (
        select(
            Visit.channel,
            func.count(),
            func.count().filter(Intake.completed_at.is_not(None)),
            func.count().filter(Intake.confirmed_by_patient.is_(True)),
            _P50.within_group(duration_s.asc()).filter(Intake.completed_at.is_not(None)),
        )
        .join(Visit, Visit.id == Intake.visit_id)
        .where(Intake.created_at >= start, Intake.created_at < end)
        .group_by(Visit.channel)
        .order_by(Visit.channel)
    )
    rows = (await session.execute(stmt)).all()
    return [
        FunnelRow(
            channel=r[0],
            started=int(r[1]),
            completed=int(r[2]),
            confirmed=int(r[3]),
            median_duration_s=float(r[4]) if r[4] is not None else None,
        )
        for r in rows
    ]


# -- ambient note tags (M4) ---------------------------------------------------


async def note_tags(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    limit: int = 10,
) -> NoteTags:
    """What the clinic's ambient notes were about over a period (plan §3.2).

    This is the query that makes the S/O/A/P mapping worth having: four prose
    fields are a better note, but `tags` is the part a clinic can count. Symptom
    burden, follow-up debt and problem prevalence all fall out of one pass.

    Three things about how it counts, each a decision rather than a detail:

    * **Confirmed notes only.** A draft is a machine reading nobody has checked;
      counting one would put a model's guess into a clinic-level number with no
      doctor in between. The excluded count is returned so a caller can say how
      much was left out rather than silently reporting a partial picture.
    * **One note counts a tag once.** A doctor who says "mucositis" three times
      in one observation has one patient with mucositis, and the model may or may
      not repeat the tag. Deduplicating per note makes the number mean "notes
      mentioning this", which is a claim the data supports.
    * **Tags are compared lowercased and stripped, and reported in the casing
      they were first seen in.** These are model-suggested free text: "Mucositis"
      and "mucositis" are the same symptom and must not be two rows, but
      normalising further (stemming, synonyms) would be this module deciding that
      "oral mucositis" and "mucositis" are the same thing, which is a clinical
      judgement it has no business making. It is a real limit on these numbers,
      and it is why every surface showing them says model-assisted.

    Read in Python rather than as a JSONB aggregate: the shape is nested three
    deep, the row count is one per confirmed note per period, and a readable pass
    here is worth more than a clever query nobody can modify. If this ever runs
    over a year of a 500-patient/day clinic it wants a rollup table, not a
    smarter select.
    """
    rows = (
        await session.scalars(
            select(ClinicalNote).where(
                ClinicalNote.created_at >= start,
                ClinicalNote.created_at < end,
                ClinicalNote.deleted_at.is_(None),
            )
        )
    ).all()

    confirmed = [n for n in rows if n.status is NoteStatus.CONFIRMED]

    problems: Counter[str] = Counter()
    followups: Counter[str] = Counter()
    symptoms: Counter[str] = Counter()
    graded: Counter[str] = Counter()
    #: First-seen casing per normalised key, so the display reads like a doctor
    #: wrote it rather than like a database key.
    labels: dict[str, str] = {}

    def _key(raw: Any) -> str | None:
        """Normalised counting key, remembering the casing it first arrived in."""
        text = str(raw or "").strip()
        if not text:
            return None
        key = text.lower()
        labels.setdefault(key, text)
        return key

    for note in confirmed:
        fields = (note.structured or {}).get("fields") or {}
        tags = fields.get("tags") if isinstance(fields, dict) else None
        if not isinstance(tags, dict):
            continue

        # One note is one vote per tag, hence the sets.
        problems.update({k for p in tags.get("problems") or [] if (k := _key(p))})
        followups.update({k for f in tags.get("followups") or [] if (k := _key(f))})

        #: symptom key -> did the doctor say a grade for it anywhere in this note
        in_this_note: dict[str, bool] = {}
        for row in tags.get("symptoms") or []:
            if not isinstance(row, dict) or (key := _key(row.get("name"))) is None:
                continue
            in_this_note[key] = in_this_note.get(key, False) or bool(row.get("grade_mentioned"))
        # `.keys()`, not the dict: `Counter.update` on a mapping adds its
        # *values*, which would score an ungraded symptom as zero.
        symptoms.update(in_this_note.keys())
        graded.update({k for k, had_grade in in_this_note.items() if had_grade})

    return NoteTags(
        notes_counted=len(confirmed),
        drafts_excluded=len(rows) - len(confirmed),
        problems=[TagCount(label=labels[k], notes=n) for k, n in problems.most_common(limit)],
        symptoms=[
            SymptomCount(label=labels[k], notes=n, with_grade=graded[k])
            for k, n in symptoms.most_common(limit)
        ],
        followups=[TagCount(label=labels[k], notes=n) for k, n in followups.most_common(limit)],
    )


# -- price book (editor read side) --------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceRow:
    id: uuid.UUID
    provider: str
    model: str
    unit: str
    price_inr: Decimal
    effective_from: date
    notes: str | None


async def price_rows(session: AsyncSession) -> list[PriceRow]:
    """The whole price book, newest-effective first — the editor's list view."""
    rows = (
        (
            await session.execute(
                select(PriceBook).order_by(
                    PriceBook.provider,
                    PriceBook.model,
                    PriceBook.unit,
                    PriceBook.effective_from.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        PriceRow(
            id=r.id,
            provider=r.provider,
            model=r.model,
            unit=r.unit.value,
            price_inr=r.price_inr,
            effective_from=r.effective_from,
            notes=r.notes,
        )
        for r in rows
    ]


# -- helpers ------------------------------------------------------------------


def _q(value) -> Decimal | None:
    return Decimal(value).quantize(CENT) if value is not None else None


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _day_start(now: datetime, tz: str = "Asia/Kolkata") -> datetime:
    from zoneinfo import ZoneInfo

    local = now.astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
