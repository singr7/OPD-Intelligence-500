"""Admission control — one GPU is a finite resource (doc 08 §3).

The pilot's peak is ~8–12 concurrent voice calls, and the box is engineered for
12 (tested to 16). The rule from doc 08 §3 is blunt and deliberate: **never queue
a patient on a GPU.** Session #13 does not wait for a slot — it is routed to the
next tier in that channel's ladder (V2 if cloud keys are configured, else V3), so
the patient is served *now* on a cheaper path rather than hearing dead air while
an overloaded GPU catches up.

This is a counting gate, not a semaphore that blocks: `slot()` either admits
(and reserves a seat until the call ends) or refuses immediately, and the caller
decides the fallback. That asymmetry is the whole point — blocking would
reintroduce the queue the doc forbids.

**Per-session isolation** (doc 08 §3): the seat is released in a `finally`, so a
crashed or cancelled call frees its slot rather than leaking it until the count
pins at the cap and every subsequent caller is pushed to fallback forever.

This module is the software seam. Wiring it in front of the live local realtime
session — and proving 12 concurrent callers with the GPU actually attached — is
S-OSS.2 (`LocalPipelineVoiceProvider`, doc 08 §6), which is why nothing here
touches the network: it is pure bookkeeping, testable without a GPU.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class AdmissionFull(Exception):
    """Raised by `reserve()` when a profile is at capacity. `slot()` never raises
    it — it yields `admitted=False` so the caller can fall through to the next
    tier without a try/except at every call site."""


class AdmissionController:
    """A per-profile concurrency cap. In-memory and per-process by design.

    Like the cost-guard's tier override, the *authoritative* cross-process count
    in production belongs in Redis (voice-gw is the one process that admits
    calls, so a single instance is correct for the pilot; a second voice-gw
    replica is when this graduates to a Redis counter — noted for S-OSS.2). A
    limit of 0 or a missing profile means "uncapped", never "always full": an
    unconfigured cap must not silently push every call to fallback.
    """

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self._limits = {k: v for k, v in (limits or {}).items() if v and v > 0}
        self._active: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def limit(self, profile: str) -> int | None:
        """The cap for a profile, or None if uncapped."""
        return self._limits.get(profile)

    def active(self, profile: str) -> int:
        return self._active[profile]

    async def reserve(self, profile: str) -> None:
        """Take a seat or raise `AdmissionFull`. Pair with `release`."""
        async with self._lock:
            limit = self._limits.get(profile)
            if limit is not None and self._active[profile] >= limit:
                raise AdmissionFull(f"{profile} at capacity ({limit})")
            self._active[profile] += 1

    async def release(self, profile: str) -> None:
        async with self._lock:
            if self._active[profile] > 0:
                self._active[profile] -= 1

    @asynccontextmanager
    async def slot(self, profile: str) -> AsyncIterator[bool]:
        """Reserve a seat for the duration of a call.

            async with admission.slot("v_oss") as admitted:
                if not admitted:
                    return await run_on_fallback_tier(...)
                return await run_v_oss(...)

        Yields True if a seat was free (held until the block exits, released even
        on error), False if the profile is at capacity — in which case *no* seat
        is held and the caller must route elsewhere.
        """
        try:
            await self.reserve(profile)
        except AdmissionFull:
            logger.info(
                "admission: %s full (%d/%s) — routing to fallback tier",
                profile,
                self._active[profile],
                self._limits.get(profile),
            )
            yield False
            return
        try:
            yield True
        finally:
            await self.release(profile)
