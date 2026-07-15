"""The `Provider` base every external dependency inherits (doc 02 §9).

Doc 02 §9 says a provider without usage metering fails review. Rather than trust
review, this base makes metering the only way to make a call: concrete impls
implement private `_verbs` and never reach the recorder themselves. The public
verb goes through `_invoke()`, which times the call, meters it, updates health,
applies retry, and consults the circuit breaker — in that order, for every
provider, for free.

So a new provider is: subclass, set `kind`/`name`, implement the private verb,
report what it used on the `MeterCall`. Forgetting to meter is not reachable;
the worst case is a zero-usage event, which `tests/test_providers_metering.py`
catches by asserting cost > 0 for every fake.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import ClassVar

from app.models.enums import UsagePurpose
from app.providers.metering import MeterCall, UsageDelta, UsageDraft, current_context, record
from app.providers.resilience import (
    BreakerState,
    CircuitBreaker,
    ProviderBadRequest,
    ProviderError,
    ProviderUnavailable,
    RetryPolicy,
)

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    """What `/providers/health` reports (doc 02 §9 / S3 AC).

    Deliberately in-memory and per-process: this is "is this provider working
    right now", which is what the tier ladder and the coordinator's banner need.
    Historical provider health is a Grafana question (S19), not this endpoint's.
    """

    kind: str
    name: str
    calls: int = 0
    failures: int = 0
    last_ok_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    breaker: BreakerState = BreakerState.CLOSED
    configured: bool = True

    @property
    def status(self) -> str:
        """`ok` · `degraded` · `down` · `unconfigured`.

        A provider that has never been called reports `ok`, not `unknown`: at
        boot every provider is uncalled, and a board full of "unknown" trains
        people to ignore the endpoint. `degraded` means it has failed since it
        last succeeded but the breaker still lets calls through.
        """
        if not self.configured:
            return "unconfigured"
        if self.breaker is BreakerState.OPEN:
            return "down"
        if self.last_error_at is None:
            return "ok"
        if self.last_ok_at is None or self.last_error_at > self.last_ok_at:
            return "degraded"
        return "ok"

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "breaker": self.breaker.value,
            "calls": self.calls,
            "failures": self.failures,
            "last_ok_at": self.last_ok_at.isoformat() if self.last_ok_at else None,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "last_error": self.last_error,
        }


class Provider(ABC):
    """Base for every provider interface.

    Subclasses set `kind` (the interface: sms, llm, stt, …) and `name` (the
    vendor: msg91, gemini-flash, fake). Both are wire values — they land in
    `usage_events.provider` and the health endpoint — so renaming one breaks the
    price book and the dashboard's history. Treat them as data, not labels.
    """

    kind: ClassVar[str] = "provider"
    name: ClassVar[str] = "abstract"

    #: Wall-clock ceiling per attempt. Audio paths override this down; the
    #: default is sized for an HTTP vendor API, not a websocket stream.
    timeout_seconds: ClassVar[float] = 10.0

    def __init__(
        self,
        *,
        retry: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        configured: bool = True,
    ) -> None:
        self.retry = retry or RetryPolicy()
        self.breaker = breaker or CircuitBreaker()
        self.health = ProviderHealth(kind=self.kind, name=self.name, configured=configured)

    # -- the one way to call a vendor -----------------------------------------

    async def _invoke[T](
        self,
        purpose: UsagePurpose,
        fn: Callable[[MeterCall], Awaitable[T]],
        *,
        model: str | None = None,
        timeout: float | None = None,
    ) -> T:
        """Run one vendor call: breaker → retry → timeout → meter → health.

        `fn` receives a `MeterCall` and reports what it used on it. Every attempt
        meters separately, including failed ones: a vendor that 500s after
        burning input tokens still bills for them, and a cost dashboard that only
        counts successes would quietly understate a bad day.
        """
        last_error: Exception | None = None

        for attempt in range(1, self.retry.attempts + 1):
            delay = self.retry.delay_for(attempt)
            if delay:
                await asyncio.sleep(delay)

            # Raises ProviderUnavailable when the circuit says don't bother.
            self.breaker.check(self.name)

            call = MeterCall(model=model)
            started_at = datetime.now(UTC)
            clock = perf_counter()
            try:
                result = await asyncio.wait_for(fn(call), timeout=timeout or self.timeout_seconds)
            except ProviderBadRequest as exc:
                # Our fault, not theirs: meter it, don't retry, don't trip.
                self._finish(call, purpose, started_at, clock, ok=False)
                self.health.calls += 1
                raise exc
            except (TimeoutError, asyncio.CancelledError) as exc:
                self._finish(call, purpose, started_at, clock, ok=False)
                self._record_failure(f"timeout after {timeout or self.timeout_seconds}s")
                last_error = (
                    ProviderUnavailable(f"{self.name}: timeout")
                    if isinstance(exc, TimeoutError)
                    else exc
                )
                if isinstance(exc, asyncio.CancelledError):
                    # Shutdown/caller cancellation is not a provider fault.
                    raise
            except Exception as exc:
                self._finish(call, purpose, started_at, clock, ok=False)
                self._record_failure(f"{type(exc).__name__}: {exc}")
                last_error = exc
            else:
                self._finish(call, purpose, started_at, clock, ok=True)
                self.health.calls += 1
                self.health.last_ok_at = datetime.now(UTC)
                self.breaker.record_success()
                self.health.breaker = self.breaker.state
                return result

        raise ProviderUnavailable(
            f"{self.name}: {self.retry.attempts} attempts failed: {last_error}"
        ) from last_error

    def _finish(
        self,
        call: MeterCall,
        purpose: UsagePurpose,
        started_at: datetime,
        clock: float,
        *,
        ok: bool,
    ) -> None:
        """Meter one attempt. Never raises — `record` swallows, and a metering
        failure must not turn a working call into a failed one."""
        record(
            UsageDraft(
                at=started_at,
                provider=self.name,
                model=call.model,
                purpose=purpose,
                usage=call.usage,
                context=current_context(),
                latency_ms=int((perf_counter() - clock) * 1000),
                ok=ok,
            )
        )

    def _meter_stream(
        self,
        purpose: UsagePurpose,
        usage: UsageDelta,
        *,
        model: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Meter a slice of a long-lived stream (realtime voice, a phone call).

        `_invoke` assumes request/response; a Gemini Live session or an Exotel
        call is neither — it runs for minutes and bills continuously. Streaming
        providers call this periodically (per doc 02 §5: "per-minute audio
        metering") rather than once at hangup, so the cost-guard can act on a
        call that is still in progress. Waiting for the call to end means the
        budget is already spent by the time we notice.
        """
        record(
            UsageDraft(
                at=datetime.now(UTC),
                provider=self.name,
                model=model,
                purpose=purpose,
                usage=usage,
                context=current_context(),
                latency_ms=latency_ms,
                ok=True,
            )
        )

    def _record_failure(self, message: str) -> None:
        self.health.calls += 1
        self.health.failures += 1
        self.health.last_error_at = datetime.now(UTC)
        self.health.last_error = message
        self.breaker.record_failure(self.name)
        self.health.breaker = self.breaker.state
        logger.warning("provider %s/%s failed: %s", self.kind, self.name, message)


async def with_fallback[T](
    providers: list[Provider],
    call: Callable[[Provider], Awaitable[T]],
) -> T:
    """First provider that answers wins (doc 02 §2: Sarvam→Google, Gemini→OpenAI).

    Only `ProviderUnavailable` moves to the next one. A `ProviderBadRequest`
    propagates immediately: if Sarvam rejected the request as malformed, Google
    will too, and trying it twice just doubles the cost of a bug.
    """
    if not providers:
        raise ProviderUnavailable("no providers configured")

    last: Exception | None = None
    for provider in providers:
        try:
            return await call(provider)
        except ProviderUnavailable as exc:
            logger.info("provider %s unavailable, falling back: %s", provider.name, exc)
            last = exc
    raise ProviderUnavailable(
        f"all providers exhausted: {[p.name for p in providers]}: {last}"
    ) from last


__all__ = [
    "Provider",
    "ProviderBadRequest",
    "ProviderError",
    "ProviderHealth",
    "ProviderUnavailable",
    "with_fallback",
]
