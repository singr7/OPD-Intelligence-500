"""Cost guard (doc 02 §8).

S3 AC: **cost-guard breach flips tier flag in config**.

The design claim being tested is doc 02 §2's — "cost and resilience use the same
ladder". A budget breach must degrade the channel to a cheaper tier, never block
a call and never turn a patient away. Every test here is ultimately checking that
the OPD keeps running when the money runs out.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.enums import Channel, IntakeTier, UsagePurpose
from app.models.metering import UsageEvent
from app.providers.costguard import (
    CostGuard,
    InMemoryTierOverrideStore,
    build_override_store,
    downgrade,
)


def _guard(session, budgets: dict[str, str], **kwargs) -> CostGuard:
    @asynccontextmanager
    async def factory():
        yield session

    return CostGuard(
        factory,
        kwargs.pop("store", InMemoryTierOverrideStore()),
        budgets={k: Decimal(v) for k, v in budgets.items()},
        **kwargs,
    )


async def _spend(session, channel: Channel, rupees: str, *, at: datetime | None = None) -> None:
    at = at or datetime.now(UTC)
    session.add(
        UsageEvent(
            at=at,
            minute_bucket=at.replace(second=0, microsecond=0),
            channel=channel,
            provider="fake-llm",
            purpose=UsagePurpose.INTAKE_TURN,
            computed_cost_inr=Decimal(rupees),
        )
    )
    await session.flush()


# -- the ladder ----------------------------------------------------------------


def test_downgrade_walks_the_ladder_and_stops_at_v3():
    """V3 is the floor: it costs nothing and works offline (doc 02 §2)."""
    assert downgrade(IntakeTier.CONVERSATIONAL) is IntakeTier.RULE_BASED
    assert downgrade(IntakeTier.RULE_BASED) is IntakeTier.PRERECORDED
    assert downgrade(IntakeTier.PRERECORDED) is IntakeTier.PRERECORDED


def test_paper_is_not_on_the_budget_ladder():
    """Dropping the OPD to paper is a downtime decision a human makes. A budget
    must never do it — that would be denying care to save rupees."""
    assert downgrade(IntakeTier.PAPER) is IntakeTier.PRERECORDED


# -- the headline AC -----------------------------------------------------------


async def test_breach_flips_the_tier_flag(session):
    """S3 AC. Spend past the cap, and the channel is forced down a tier."""
    guard = _guard(session, {"phone": "100"})
    await _spend(session, Channel.PHONE, "150")

    verdicts = await guard.evaluate()
    verdict = next(v for v in verdicts if v.channel is Channel.PHONE)

    assert verdict.breached
    assert verdict.tier is IntakeTier.RULE_BASED
    # The flag S5 reads: a session asking for V1 on phone now gets V2.
    assert await guard.effective_tier(Channel.PHONE, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.RULE_BASED
    )


async def test_under_budget_changes_nothing(session):
    guard = _guard(session, {"phone": "100"})
    await _spend(session, Channel.PHONE, "10")

    verdict = (await guard.evaluate())[0]
    assert not verdict.breached and not verdict.approaching
    assert await guard.effective_tier(Channel.PHONE, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.CONVERSATIONAL
    )


async def test_approaching_cap_alerts_without_downgrading(session):
    """80% is a warning to a human, not an action (doc 02 §8). Downgrading here
    would degrade every patient's experience over a budget we have not hit."""
    guard = _guard(session, {"phone": "100"}, alert_fraction=0.8)
    await _spend(session, Channel.PHONE, "85")

    verdict = (await guard.evaluate())[0]
    assert verdict.approaching and not verdict.breached
    assert verdict.tier is None
    assert await guard.effective_tier(Channel.PHONE, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.CONVERSATIONAL
    )


async def test_repeated_breaches_walk_the_ladder_down_to_v3(session):
    """Doc 02 §8: "breach → automatic tier downgrade (V1→V2→V3)".

    Each evaluation drops one rung from wherever the channel *is*, not from the
    top. Computing it from the top instead pins the channel at V2 forever: still
    spending, still breaching, never reaching the free tier — a cost guard that
    stops guarding precisely when it is needed most.
    """
    guard = _guard(session, {"kiosk": "10"})
    await _spend(session, Channel.KIOSK, "10000")

    await guard.evaluate()
    assert await guard.effective_tier(Channel.KIOSK, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.RULE_BASED
    )

    await guard.evaluate()
    assert await guard.effective_tier(Channel.KIOSK, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.PRERECORDED
    )


async def test_the_guard_degrades_but_never_denies(session):
    """The design's whole point: no patient is turned away because of a budget.
    Even wildly over cap, the floor is V3 — which still completes an intake,
    offline, for free — and never paper."""
    guard = _guard(session, {"kiosk": "10"})
    await _spend(session, Channel.KIOSK, "10000")

    for _ in range(5):
        await guard.evaluate()

    tier = await guard.effective_tier(Channel.KIOSK, IntakeTier.CONVERSATIONAL)
    assert tier is IntakeTier.PRERECORDED  # the floor, not paper, not blocked


# -- attribution and scoping ---------------------------------------------------


async def test_budgets_are_per_channel(session):
    """A WhatsApp bot burning its budget must not downgrade the kiosk."""
    guard = _guard(session, {"phone": "100", "kiosk": "100"})
    await _spend(session, Channel.PHONE, "150")

    await guard.evaluate()

    assert await guard.effective_tier(Channel.PHONE, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.RULE_BASED
    )
    assert await guard.effective_tier(Channel.KIOSK, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.CONVERSATIONAL
    )


async def test_an_unbudgeted_channel_is_uncapped_not_throttled(session):
    """A missing key must never silently throttle a channel nobody budgeted for."""
    guard = _guard(session, {"phone": "100"})
    await _spend(session, Channel.WHATSAPP, "99999")

    await guard.evaluate()
    assert await guard.effective_tier(Channel.WHATSAPP, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.CONVERSATIONAL
    )


async def test_spend_is_counted_from_local_midnight_not_utc(session):
    """The OPD's day is IST. A UTC rollover resets the cap at 05:30 IST — in the
    middle of the morning rush, which is exactly when the cap matters."""
    guard = _guard(session, {"phone": "100"}, timezone="Asia/Kolkata")

    day_start = guard.day_start()
    local = day_start.astimezone(guard._tz)
    assert (local.hour, local.minute) == (0, 0)

    # Yesterday's spend must not count against today's budget.
    await _spend(session, Channel.PHONE, "500", at=day_start - timedelta(minutes=5))
    await _spend(session, Channel.PHONE, "10", at=day_start + timedelta(minutes=5))

    assert await guard.spend_today(session, Channel.PHONE) == Decimal("10")


# -- the override store --------------------------------------------------------


async def test_the_guard_ratchets_and_does_not_undo_a_downgrade(session):
    """Recovery is a day rollover or an admin, not one evaluation reading a
    smaller number. Otherwise a session flaps tier mid-sentence."""
    store = InMemoryTierOverrideStore()
    guard = _guard(session, {"phone": "100"}, store=store)

    await store.set(Channel.PHONE, IntakeTier.PRERECORDED, 900)
    await _spend(session, Channel.PHONE, "150")
    await guard.evaluate()  # would force RULE_BASED, which is *higher* than V3

    assert await store.get(Channel.PHONE) is IntakeTier.PRERECORDED


async def test_effective_tier_never_promotes_above_config(session):
    """A guard that could raise a tier would be a cost bug wearing a safety hat."""
    store = InMemoryTierOverrideStore()
    guard = _guard(session, {"phone": "100"}, store=store)
    await store.set(Channel.PHONE, IntakeTier.CONVERSATIONAL, 900)

    assert await guard.effective_tier(Channel.PHONE, IntakeTier.PRERECORDED) is (
        IntakeTier.PRERECORDED
    )


async def test_override_expires_on_its_own(session):
    """If the guard process dies right after tripping, the TTL is what stops the
    whole OPD being pinned to V3 until someone notices."""
    store = InMemoryTierOverrideStore()
    await store.set(Channel.PHONE, IntakeTier.PRERECORDED, ttl_seconds=0)
    assert await store.get(Channel.PHONE) is None


async def test_admin_can_clear_an_override(session):
    """ "Resume normal service" (S18)."""
    store = InMemoryTierOverrideStore()
    guard = _guard(session, {"phone": "100"}, store=store)
    await _spend(session, Channel.PHONE, "150")
    await guard.evaluate()

    await guard.clear(Channel.PHONE)
    assert await guard.effective_tier(Channel.PHONE, IntakeTier.CONVERSATIONAL) is (
        IntakeTier.CONVERSATIONAL
    )


async def test_a_paper_session_ignores_the_guard(session):
    """Downtime is a human's call; the guard does not argue with it."""
    store = InMemoryTierOverrideStore()
    guard = _guard(session, {"phone": "100"}, store=store)
    await store.set(Channel.PHONE, IntakeTier.PRERECORDED, 900)

    assert await guard.effective_tier(Channel.PHONE, IntakeTier.PAPER) is IntakeTier.PAPER


async def test_disabled_guard_reports_spend_but_forces_nothing(session):
    guard = _guard(session, {"phone": "1"}, enabled=False)
    await _spend(session, Channel.PHONE, "500")

    verdict = (await guard.evaluate())[0]
    assert verdict.spent_inr == Decimal("500")
    assert not verdict.breached and verdict.tier is None


def test_local_dev_gets_the_in_memory_store():
    """Tests and single-process dev must not need a Redis broker."""
    from app.config import Settings

    assert isinstance(build_override_store(Settings(env="local")), InMemoryTierOverrideStore)
