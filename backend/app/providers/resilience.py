"""Retry and circuit breaking for provider calls (doc 02 §9).

The point of these is the tier ladder, not heroics. When Gemini Live is failing,
what saves the intake is downgrading to V2 within a second or two (doc 02 §2) —
not a provider layer that keeps retrying while a patient stands at the kiosk
listening to silence. So: few attempts, short backoff, and a breaker that trips
fast and tells the caller "use the next tier" rather than making it wait to find
out.

`ProviderUnavailable` is that signal. Everything above the provider layer treats
it as "this provider is out, fall back" — `with_fallback()` chains on it, and
S5's intake engine downgrades tier on it.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Base for provider failures."""


class ProviderUnavailable(ProviderError):
    """This provider cannot serve the call — fall back or downgrade tier.

    Raised on exhausted retries, an open circuit, or a timeout. Distinct from a
    caller error (a malformed request is a bug, not an outage, and retrying it
    just burns the budget), which impls raise as `ProviderBadRequest`.
    """


class ProviderBadRequest(ProviderError):
    """The request was rejected on its merits (bad number, unregistered template).

    Never retried, never trips the breaker: the provider is healthy and the next
    provider in the chain would reject it identically.
    """


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with jitter, deliberately shallow.

    Defaults give at most ~0.35s of added latency before we give up and let the
    tier ladder do its job. Batch paths (summaries, Celery check-ins) are the
    ones that should raise `attempts`; live audio should not.
    """

    attempts: int = 3
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 2.0
    # Full jitter (AWS's "Exponential Backoff and Jitter"): with 60-80 kiosk and
    # phone sessions retrying a recovering provider, unjittered backoff syncs
    # them into a thundering herd that re-kills it.
    jitter: bool = True

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before `attempt` (1-based; attempt 1 never waits)."""
        if attempt <= 1:
            return 0.0
        raw = min(self.base_delay_seconds * (2 ** (attempt - 2)), self.max_delay_seconds)
        return random.uniform(0, raw) if self.jitter else raw


class BreakerState(StrEnum):
    CLOSED = "closed"  # healthy, calls pass
    OPEN = "open"  # failing, calls rejected immediately
    HALF_OPEN = "half_open"  # probing recovery with a single call


class CircuitBreaker:
    """Per-provider breaker.

    Opens after `failure_threshold` consecutive failures, rejects for
    `reset_after_seconds`, then lets exactly one probe through. A success closes
    it; a failure re-opens it for another interval.

    Consecutive failures, not a rate: at pilot volume a rate window is mostly
    noise, and "the last N calls all failed" is both easier to reason about at
    3am and harder to argue with.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_after_seconds: float = 30.0,
    ) -> None:
        self._threshold = failure_threshold
        self._reset_after = timedelta(seconds=reset_after_seconds)
        self._consecutive_failures = 0
        self._opened_at: datetime | None = None
        self._half_open_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if datetime.now(UTC) - self._opened_at >= self._reset_after:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def check(self, provider: str) -> None:
        """Raise `ProviderUnavailable` if the circuit is not accepting calls."""
        state = self.state
        if state is BreakerState.OPEN:
            raise ProviderUnavailable(f"{provider}: circuit open")
        if state is BreakerState.HALF_OPEN:
            if self._half_open_in_flight:
                # One probe at a time; the rest fall back rather than pile onto a
                # provider that is probably still down.
                raise ProviderUnavailable(f"{provider}: circuit half-open, probe in flight")
            self._half_open_in_flight = True

    def record_success(self) -> None:
        if self._opened_at is not None:
            logger.info("circuit closed after successful probe")
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_in_flight = False

    def record_failure(self, provider: str) -> None:
        self._half_open_in_flight = False
        self._consecutive_failures += 1
        if self._opened_at is not None:
            # A failed half-open probe restarts the clock.
            self._opened_at = datetime.now(UTC)
            return
        if self._consecutive_failures >= self._threshold:
            self._opened_at = datetime.now(UTC)
            logger.warning(
                "circuit opened for %s after %d consecutive failures",
                provider,
                self._consecutive_failures,
            )

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_in_flight = False
