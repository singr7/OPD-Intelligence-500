"""Cost guard (doc 02 §8) — daily budget caps that downgrade the tier ladder.

> "**Cost-guard**: daily budget caps per channel/tier; approaching cap (80%) →
> alert; breach → automatic tier downgrade (V1→V2→V3) with banner in coordinator
> console. Guard rules are config." — doc 02 §8

The insight this subsystem rests on (doc 02 §2): **cost and resilience use the
same ladder**. When Gemini Live dies we drop V1→V2→V3; when the budget runs out
we do exactly the same thing, for a different reason. So the guard does not need
to block calls or fail requests — it flips a flag, the intake engine reads the
flag, and patients keep getting seen on a cheaper tier. Nobody is turned away
because of a budget. That is the whole design goal: **degrade, never deny.**

## Skeleton, per doc 06 S3

What is here: budget config, spend-so-far from `usage_events`, the 80%/100%
thresholds, the verdict, and the override store the engine reads. What is not:
the engine that obeys it (S5), the coordinator banner (S8), and the admin
editor for the caps (S18). The seam is `TierOverrideStore` — S5 reads it, this
writes it.

## Why the override is stored, not computed per call

Three processes (api, worker, voice-gw) make provider calls, and an intake
already in flight must not flap between tiers because two of them summed
`usage_events` a second apart. The guard evaluates on a schedule, writes one
answer, and everyone reads it. It is also why the store is Redis in production:
a per-process flag would mean voice-gw happily running V1 while api thinks the
budget is blown.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Channel, IntakeTier
from app.models.metering import UsageEvent

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

#: The downgrade ladder (doc 02 §2). Paper is not on it: the guard degrades a
#: patient to a cheaper *automated* tier, and dropping the OPD to paper is a
#: downtime decision a human makes, never a budget one.
LADDER: tuple[IntakeTier, ...] = (
    IntakeTier.CONVERSATIONAL,
    IntakeTier.RULE_BASED,
    IntakeTier.PRERECORDED,
)


def downgrade(tier: IntakeTier) -> IntakeTier:
    """One rung down; V3 is the floor (it costs nothing and works offline)."""
    try:
        index = LADDER.index(tier)
    except ValueError:
        return IntakeTier.PRERECORDED
    return LADDER[min(index + 1, len(LADDER) - 1)]


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the guard concluded about one channel, this evaluation."""

    channel: Channel
    spent_inr: Decimal
    budget_inr: Decimal | None
    tier: IntakeTier | None  # the forced tier, None = no override
    breached: bool
    approaching: bool

    @property
    def fraction(self) -> float | None:
        if not self.budget_inr:
            return None
        return float(self.spent_inr / self.budget_inr)


class TierOverrideStore(Protocol):
    """Where the flag lives. The seam between this guard and S5's intake engine."""

    async def get(self, channel: Channel) -> IntakeTier | None: ...

    async def set(self, channel: Channel, tier: IntakeTier, ttl_seconds: int) -> None: ...

    async def clear(self, channel: Channel) -> None: ...


class InMemoryTierOverrideStore:
    """Single-process store, for tests and `make dev` without Redis.

    Wrong for production by design — three processes would each keep their own
    idea of the tier. `build_override_store` picks Redis outside local.
    """

    def __init__(self) -> None:
        self._tiers: dict[Channel, tuple[IntakeTier, datetime]] = {}

    async def get(self, channel: Channel) -> IntakeTier | None:
        entry = self._tiers.get(channel)
        if entry is None:
            return None
        tier, expires_at = entry
        if datetime.now(UTC) >= expires_at:
            del self._tiers[channel]
            return None
        return tier

    async def set(self, channel: Channel, tier: IntakeTier, ttl_seconds: int) -> None:
        self._tiers[channel] = (tier, datetime.now(UTC) + timedelta(seconds=ttl_seconds))

    async def clear(self, channel: Channel) -> None:
        self._tiers.pop(channel, None)


class RedisTierOverrideStore:
    """Redis-backed store — the one production uses.

    TTL is the safety property: if the guard process dies right after tripping a
    downgrade, the override expires on its own rather than pinning the whole OPD
    to V3 until someone notices.
    """

    KEY = "costguard:tier:{channel}"

    def __init__(self, redis) -> None:  # redis.asyncio.Redis
        self._redis = redis

    async def get(self, channel: Channel) -> IntakeTier | None:
        raw = await self._redis.get(self.KEY.format(channel=channel.value))
        if raw is None:
            return None
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            return IntakeTier(value)
        except ValueError:
            # Someone hand-edited the key, or an old value survived a rename.
            # Ignoring it means we serve the configured tier, which is safe.
            logger.warning("ignoring unparseable tier override %r for %s", value, channel)
            return None

    async def set(self, channel: Channel, tier: IntakeTier, ttl_seconds: int) -> None:
        await self._redis.set(self.KEY.format(channel=channel.value), tier.value, ex=ttl_seconds)

    async def clear(self, channel: Channel) -> None:
        await self._redis.delete(self.KEY.format(channel=channel.value))


class CostGuard:
    """Evaluates spend against budgets and writes the tier override."""

    def __init__(
        self,
        session_factory: SessionFactory,
        store: TierOverrideStore,
        *,
        budgets: dict[str, Decimal],
        alert_fraction: float = 0.8,
        override_ttl_seconds: int = 900,
        timezone: str = "Asia/Kolkata",
        enabled: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._store = store
        self._budgets = {Channel(k): Decimal(v) for k, v in budgets.items()}
        self._alert_fraction = alert_fraction
        self._ttl = override_ttl_seconds
        self._tz = ZoneInfo(timezone)
        self._enabled = enabled

    def day_start(self, now: datetime | None = None) -> datetime:
        """Midnight IST, as UTC. Budgets are per OPD day, not per UTC day — a
        UTC rollover at 05:30 IST would reset the cap in the middle of the
        morning rush, which is precisely when it matters."""
        local = (now or datetime.now(UTC)).astimezone(self._tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)

    async def spend_today(self, session: AsyncSession, channel: Channel) -> Decimal:
        total = await session.scalar(
            select(func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0)).where(
                UsageEvent.channel == channel,
                UsageEvent.at >= self.day_start(),
            )
        )
        return Decimal(total or 0)

    async def evaluate_channel(self, session: AsyncSession, channel: Channel) -> Verdict:
        budget = self._budgets.get(channel)
        spent = await self.spend_today(session, channel)

        if not self._enabled or budget is None or budget <= 0:
            # Uncapped channel: report the spend, force nothing.
            return Verdict(
                channel=channel,
                spent_inr=spent,
                budget_inr=budget,
                tier=None,
                breached=False,
                approaching=False,
            )

        fraction = spent / budget
        breached = fraction >= 1
        approaching = not breached and float(fraction) >= self._alert_fraction

        # One rung per evaluation, down from wherever the channel already is —
        # which is what makes doc 02 §8's "V1→V2→V3" actually happen. Computing
        # it from the top of the ladder instead would pin the channel at V2
        # forever: still spending, still breaching, never reaching the free tier.
        # The first rupee over does not jump a patient straight to V3.
        current = await self._store.get(channel)
        return Verdict(
            channel=channel,
            spent_inr=spent,
            budget_inr=budget,
            tier=downgrade(current or IntakeTier.CONVERSATIONAL) if breached else None,
            breached=breached,
            approaching=approaching,
        )

    async def evaluate(self) -> list[Verdict]:
        """Evaluate every budgeted channel and apply the overrides.

        Called on a schedule (S17 gives beat real work; until then, on demand
        and from tests). Returns the verdicts so a caller can raise the alert —
        the banner (S8) and the alert channel (S19) are not this module's job.
        """
        verdicts: list[Verdict] = []
        async with self._session_factory() as session:
            for channel in self._budgets:
                verdict = await self.evaluate_channel(session, channel)
                verdicts.append(verdict)
                await self._apply(verdict)
        return verdicts

    async def _apply(self, verdict: Verdict) -> None:
        if verdict.breached and verdict.tier is not None:
            current = await self._store.get(verdict.channel)
            # Ratchet: never *raise* a tier that a previous breach already
            # lowered. Recovery happens when the day rolls over or an admin
            # clears it, not because one evaluation read a smaller number.
            if current is not None and LADDER.index(current) >= LADDER.index(verdict.tier):
                return
            await self._store.set(verdict.channel, verdict.tier, self._ttl)
            logger.warning(
                "cost guard: %s breached ₹%s of ₹%s — forcing tier %s",
                verdict.channel.value,
                verdict.spent_inr,
                verdict.budget_inr,
                verdict.tier.value,
            )
        elif verdict.approaching:
            logger.warning(
                "cost guard: %s at %.0f%% of ₹%s",
                verdict.channel.value,
                (verdict.fraction or 0) * 100,
                verdict.budget_inr,
            )

    async def effective_tier(self, channel: Channel, configured: IntakeTier) -> IntakeTier:
        """The tier a session should actually run at. **This is the flag S5 reads.**

        Returns the lower of what config asks for and what the guard forces —
        never the higher: a guard that could promote a channel above its
        configured tier would be a cost bug wearing a safety hat.
        """
        override = await self._store.get(channel)
        if override is None:
            return configured
        if configured not in LADDER or override not in LADDER:
            # Paper is off-ladder: a human chose it (downtime), and the guard
            # does not argue with a human about a budget.
            return configured
        return max(configured, override, key=LADDER.index)

    async def clear(self, channel: Channel) -> None:
        """Admin "resume normal service" (S18)."""
        await self._store.clear(channel)


_guard: CostGuard | None = None


def set_guard(guard: CostGuard | None) -> None:
    global _guard
    _guard = guard


def get_guard() -> CostGuard | None:
    return _guard


def build_override_store(settings) -> TierOverrideStore:
    """Redis outside local; in-memory for tests and single-process dev.

    Local dev gets the in-memory store even though compose runs Redis: it keeps
    `pytest` from needing a broker, and a developer's cost guard has nothing to
    coordinate with.
    """
    if settings.is_local:
        return InMemoryTierOverrideStore()
    from redis.asyncio import Redis

    return RedisTierOverrideStore(Redis.from_url(settings.redis_url))


async def guard_lifespan(guard: CostGuard) -> AsyncIterator[None]:  # pragma: no cover
    set_guard(guard)
    try:
        yield
    finally:
        set_guard(None)
