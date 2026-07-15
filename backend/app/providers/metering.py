"""Usage metering (doc 02 §8, §9).

Every provider call emits a priced `usage_events` row. Doc 02 §9 is blunt about
it — "a provider implementation without metering fails review" — so metering is
not something an implementation opts into here. `Provider._meter()` in `base.py`
wraps the call, and the concrete impls only report *what* they used; they cannot
forget to record it, because they never call the recorder.

Three properties this path must hold, in priority order:

1. **It never breaks a patient-facing call.** `record()` is sync, non-blocking,
   and swallows everything. A metering bug must not drop an intake mid-sentence.
   If the buffer is full we drop rows and count the drops, because back-pressure
   on a live phone call is worse than a gap in the cost dashboard.
2. **It never blocks one.** Pricing needs a DB read and writing needs a DB round
   trip, so both happen on a background drain task, off the call path.
3. **It reconciles exactly.** Money is `Decimal` end to end (S18 reconciles the
   dashboard against these rows to the paisa), and `unit_cost_ref` records which
   `price_book` row priced it.

The context (which intake/session/channel/tier a call belongs to) rides a
contextvar rather than being threaded through every provider signature: the
provider layer sits under the intake engine, WhatsApp webhooks and Celery tasks
alike, and none of those want to pass an intake_id into a TTS call by hand.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Channel, IntakeTier, UsagePurpose
from app.models.metering import UsageEvent
from app.providers.pricing import PriceBookCache, Priced

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True, slots=True)
class UsageDelta:
    """What one provider call consumed. Reported by the impl, priced by the meter.

    `messages` is the count for `msg`-priced providers (SMS/WhatsApp); one call
    is normally one message, so `usage_events` has no quantity column — the row
    itself is the unit.
    """

    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    audio_seconds: Decimal = Decimal("0")
    characters: int = 0
    messages: int = 0

    def __add__(self, other: UsageDelta) -> UsageDelta:
        """Streaming calls accumulate deltas across chunks before recording once."""
        return UsageDelta(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            audio_seconds=self.audio_seconds + other.audio_seconds,
            characters=self.characters + other.characters,
            messages=self.messages + other.messages,
        )


@dataclass(frozen=True, slots=True)
class UsageContext:
    """The dimensions a call is attributed to. Empty is valid — a boot-time health
    probe belongs to no intake, and the dashboard filters those out by channel."""

    session_id: str | None = None
    intake_id: uuid.UUID | None = None
    visit_id: uuid.UUID | None = None
    channel: Channel | None = None
    tier: IntakeTier | None = None


_context: ContextVar[UsageContext] = ContextVar("usage_context", default=UsageContext())


def current_context() -> UsageContext:
    return _context.get()


@contextmanager
def usage_scope(
    *,
    session_id: str | None = None,
    intake_id: uuid.UUID | None = None,
    visit_id: uuid.UUID | None = None,
    channel: Channel | None = None,
    tier: IntakeTier | None = None,
):
    """Attribute every provider call in this block to an intake/session.

    Fields merge onto the enclosing scope, so an intake can open a scope with the
    intake_id and channel once, and a mid-session tier downgrade can nest a scope
    that only overrides `tier`. Contextvars are task-local: concurrent intakes on
    the same event loop do not see each other's scope.
    """
    base = _context.get()
    merged = UsageContext(
        session_id=session_id if session_id is not None else base.session_id,
        intake_id=intake_id if intake_id is not None else base.intake_id,
        visit_id=visit_id if visit_id is not None else base.visit_id,
        channel=channel if channel is not None else base.channel,
        tier=tier if tier is not None else base.tier,
    )
    token = _context.set(merged)
    try:
        yield merged
    finally:
        _context.reset(token)


@dataclass(slots=True)
class UsageDraft:
    """An unpriced event, as handed to the meter."""

    at: datetime
    provider: str
    model: str | None
    purpose: UsagePurpose
    usage: UsageDelta
    context: UsageContext
    latency_ms: int | None = None
    ok: bool = True


class UsageMeter:
    """Buffers usage drafts and drains them to `usage_events`, priced, in batches.

    Not a Celery task: these arrive at hundreds per minute during peak OPD and
    each one is a few hundred bytes. A queue plus a batched INSERT is cheaper
    than the broker round trip, and losing a bounded buffer on process kill is an
    acceptable trade for never touching the call path (doc 02 §8: "async,
    batched, never blocks the call path").
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        prices: PriceBookCache,
        *,
        max_buffer: int = 10_000,
        batch_size: int = 200,
        flush_interval_seconds: float = 2.0,
    ) -> None:
        self._session_factory = session_factory
        self._prices = prices
        self._buffer: deque[UsageDraft] = deque(maxlen=max_buffer)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.dropped = 0
        self.recorded = 0

    # -- call path (must never raise, must never await) ------------------------

    def record(self, draft: UsageDraft) -> None:
        """Buffer one event. Safe to call from anywhere; failures are swallowed.

        `deque(maxlen=...)` silently discards the *oldest* draft when full. That
        is deliberate: under a drain stall the newest events are the ones the
        cost-guard needs, and an unbounded buffer would trade a cost gap for an
        OOM that takes the OPD down.
        """
        try:
            if len(self._buffer) == self._buffer.maxlen:
                self.dropped += 1
                if self.dropped % 100 == 1:
                    logger.warning("usage meter buffer full; dropped=%d", self.dropped)
            self._buffer.append(draft)
        except Exception:  # pragma: no cover - defensive; deque.append does not raise
            logger.exception("usage meter record failed; event dropped")

    # -- drain path -----------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._stopping.clear()
            self._task = asyncio.create_task(self._run(), name="usage-meter-drain")

    async def stop(self) -> None:
        """Stop draining, flushing what is buffered so a clean shutdown keeps its costs."""
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self.flush()

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._flush_interval)
            except TimeoutError:
                pass
            try:
                await self.flush()
            except Exception:
                # A DB blip must not kill the drain task; the next tick retries.
                logger.exception("usage meter flush failed; retrying next tick")

    async def flush(self) -> int:
        """Price and write everything buffered. Returns rows written.

        Tests call this directly instead of waiting on the drain task, which is
        why it is public and idempotent on an empty buffer.
        """
        if not self._buffer:
            return 0

        drafts: list[UsageDraft] = []
        while self._buffer and len(drafts) < self._batch_size:
            drafts.append(self._buffer.popleft())

        try:
            async with self._session_factory() as session:
                rows = [await self._to_row(session, draft) for draft in drafts]
                session.add_all(rows)
                await session.commit()
        except Exception:
            # Put them back (at the front, preserving order) for the next tick.
            self._buffer.extendleft(reversed(drafts))
            raise

        self.recorded += len(rows)
        return len(rows)

    async def _to_row(self, session: AsyncSession, draft: UsageDraft) -> UsageEvent:
        priced: Priced = await self._prices.price(
            session,
            provider=draft.provider,
            model=draft.model,
            at=draft.at,
            usage=draft.usage,
        )
        return UsageEvent(
            at=draft.at,
            # Truncated on write so the S18 per-minute rollup is a plain GROUP BY.
            minute_bucket=draft.at.replace(second=0, microsecond=0),
            session_id=draft.context.session_id,
            intake_id=draft.context.intake_id,
            visit_id=draft.context.visit_id,
            channel=draft.context.channel,
            tier=draft.context.tier,
            provider=draft.provider,
            model=draft.model,
            purpose=draft.purpose,
            tokens_in=draft.usage.tokens_in,
            tokens_out=draft.usage.tokens_out,
            cached_tokens=draft.usage.cached_tokens,
            audio_seconds=draft.usage.audio_seconds,
            characters=draft.usage.characters,
            unit_cost_ref=priced.unit_cost_ref,
            computed_cost_inr=priced.cost_inr,
            latency_ms=draft.latency_ms,
        )


# -- process-wide meter --------------------------------------------------------

_meter: UsageMeter | None = None


def set_meter(meter: UsageMeter | None) -> None:
    """Install the process meter. The app factory does this at startup; tests
    install one bound to their rolled-back session."""
    global _meter
    _meter = meter


def get_meter() -> UsageMeter | None:
    return _meter


def record(draft: UsageDraft) -> None:
    """Record against the process meter, or drop if none is installed.

    Dropping is right for the no-meter case: `python -m app.seed` and one-shot
    scripts have no drain task, and refusing to run them over a cost row would
    be the metering tail wagging the dog. Anything serving traffic installs one.
    """
    meter = _meter
    if meter is None:
        return
    meter.record(draft)


@dataclass(slots=True)
class MeterCall:
    """Handle yielded by `Provider._meter()`. The impl sets `.usage` on it.

    Left at zero usage, the event still records — a call that cost nothing (a
    cache hit, a fake) is data, and a silent gap looks identical to a bug.
    """

    usage: UsageDelta = UsageDelta()
    model: str | None = None

    def add(self, delta: UsageDelta) -> None:
        self.usage = self.usage + delta


def draft_from(
    call: MeterCall,
    *,
    provider: str,
    purpose: UsagePurpose,
    started_at: datetime,
    latency_ms: int,
    ok: bool,
) -> UsageDraft:
    return UsageDraft(
        at=started_at,
        provider=provider,
        model=call.model,
        purpose=purpose,
        usage=call.usage,
        context=current_context(),
        latency_ms=latency_ms,
        ok=ok,
    )


async def drain_forever(meter: UsageMeter) -> AsyncIterator[None]:  # pragma: no cover
    """Lifespan helper: start the drain, stop it (flushing) on shutdown."""
    await meter.start()
    try:
        yield
    finally:
        await meter.stop()


def _now() -> datetime:
    return datetime.now(UTC)


def blank_draft(provider: str, purpose: UsagePurpose) -> UsageDraft:
    """Used by tests and health probes that need a draft without a call."""
    return UsageDraft(
        at=_now(),
        provider=provider,
        model=None,
        purpose=purpose,
        usage=UsageDelta(),
        context=current_context(),
    )


__all__ = [
    "MeterCall",
    "UsageContext",
    "UsageDelta",
    "UsageDraft",
    "UsageMeter",
    "blank_draft",
    "current_context",
    "draft_from",
    "get_meter",
    "record",
    "replace",
    "set_meter",
    "usage_scope",
]
