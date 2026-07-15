"""Cost computation against `price_book` (doc 02 §8).

Two properties the S18 dashboard depends on:

- **Costs are stored *and* recomputable.** Each event keeps the computed rupees
  *and* `unit_cost_ref` → the `price_book` row that produced them. When a vendor
  changes pricing mid-quarter, history re-prices from `(provider, model, at)`
  instead of being silently reinterpreted at today's rate.
- **Money is `Decimal`, never float.** These rows get summed into an invoice
  reconciliation view; binary floating point loses that argument by construction.

## Units and quanta

`price_book.unit` is a vendor-facing unit, and vendors quote tokens per million.
Storing INR-per-token in `Numeric(14, 6)` would round Gemini Flash input
(~₹0.0000063/token) to zero, so **token prices are per 1,000 tokens** — the
quantum is explicit in `UNIT_QUANTUM` and asserted in tests rather than left in
a comment for someone to rediscover during an invoice dispute.

Cached input tokens are priced at the `token_in` rate. Vendors discount them
(Gemini charges ~25% for cached context), so this *over*-estimates cost —
deliberately, because a cost-guard that under-reports is the failure that hurts.
A `token_cached` unit belongs with the S18 price-book editor; it is in the
backlog, not invented here.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PriceUnit
from app.models.metering import PriceBook

if TYPE_CHECKING:
    from app.providers.metering import UsageDelta

logger = logging.getLogger(__name__)

# Tokens and characters are priced per 1,000; everything else per single unit
# (one audio second, one call minute, one message).
UNIT_QUANTUM: dict[PriceUnit, Decimal] = {
    PriceUnit.TOKEN_IN: Decimal("1000"),
    PriceUnit.TOKEN_OUT: Decimal("1000"),
    PriceUnit.CHAR: Decimal("1000"),
    PriceUnit.AUDIO_SEC: Decimal("1"),
    PriceUnit.CALL_MIN: Decimal("60"),  # metered in seconds, priced per minute
    PriceUnit.MSG: Decimal("1"),
}

# Rounded to paisa-and-then-some: usage_events.computed_cost_inr is Numeric(12, 4).
COST_EXPONENT = Decimal("0.0001")

#: `price_book.model` for a flat per-provider rate (SMS, telephony, WhatsApp —
#: vendors that charge per message or per minute regardless of model). A literal
#: rather than NULL for two reasons: the column is NOT NULL, and Postgres treats
#: NULLs as distinct in a UNIQUE constraint, so NULL rows would let a duplicate
#: rate slip in behind `uq_price_book_entry` and silently double-bill.
WILDCARD_MODEL = "*"


@dataclass(frozen=True, slots=True)
class Priced:
    cost_inr: Decimal
    # The dominant price row — the one that contributed the most rupees. An LLM
    # call touches two rows (in + out); this points at whichever drove the cost,
    # and `(provider, model, at)` re-derives the rest.
    unit_cost_ref: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class _Entry:
    id: uuid.UUID
    price_inr: Decimal
    effective_from: datetime


def _quantities(usage: UsageDelta) -> dict[PriceUnit, Decimal]:
    """Map what was used onto the units a vendor charges for.

    Note `audio_seconds` is the quantity for *both* `audio_sec` and `call_min` —
    the same seconds, billed two different ways (Sarvam bills audio seconds,
    Exotel bills call minutes). `price()` picks exactly one of them; charging
    both would double-bill every voice minute.
    """
    return {
        PriceUnit.TOKEN_IN: Decimal(usage.tokens_in + usage.cached_tokens),
        PriceUnit.TOKEN_OUT: Decimal(usage.tokens_out),
        PriceUnit.CHAR: Decimal(usage.characters),
        PriceUnit.AUDIO_SEC: usage.audio_seconds,
        PriceUnit.CALL_MIN: usage.audio_seconds,
        PriceUnit.MSG: Decimal(usage.messages),
    }


class PriceBookCache:
    """In-memory `price_book`, refreshed on a TTL.

    The book is a few dozen rows read on every provider call and written only by
    an admin (S18), so it is cached rather than joined. The TTL is what bounds
    how long a price edit takes to take effect; `invalidate()` is the admin
    console's hook to make it immediate.
    """

    def __init__(self, *, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str, PriceUnit], list[_Entry]] = {}
        self._loaded_at: datetime | None = None
        # (provider, model, unit) combos with usage but no price row. Surfaced on
        # /providers/health: an unpriced provider reports ₹0 forever, which the
        # cost-guard would read as "plenty of budget left".
        self.unpriced: set[tuple[str, str | None, PriceUnit]] = set()

    def invalidate(self) -> None:
        self._loaded_at = None

    async def _ensure_loaded(self, session: AsyncSession) -> None:
        now = datetime.now(UTC)
        if self._loaded_at is not None and (now - self._loaded_at).total_seconds() < self._ttl:
            return

        rows = (await session.execute(select(PriceBook))).scalars().all()
        entries: dict[tuple[str, str, PriceUnit], list[_Entry]] = {}
        for row in rows:
            key = (row.provider, row.model, row.unit)
            entries.setdefault(key, []).append(
                _Entry(
                    id=row.id,
                    price_inr=row.price_inr,
                    effective_from=datetime.combine(row.effective_from, datetime.min.time(), UTC),
                )
            )
        for versions in entries.values():
            versions.sort(key=lambda e: e.effective_from, reverse=True)
        self._entries = entries
        self._loaded_at = now

    def _lookup(
        self, provider: str, model: str | None, unit: PriceUnit, at: datetime
    ) -> _Entry | None:
        """The price in force at `at` — the newest row not dated in the future.

        A model-specific row wins; failing that, the provider's `*` row. That
        ordering is what lets SMS and telephony price with one flat row while an
        LLM prices per model, without either knowing about the other.
        """
        keys = [(provider, model, unit)] if model else []
        keys.append((provider, WILDCARD_MODEL, unit))
        for key in keys:
            for entry in self._entries.get(key, ()):
                if entry.effective_from <= at:
                    return entry
        return None

    async def price(
        self,
        session: AsyncSession,
        *,
        provider: str,
        model: str | None,
        at: datetime,
        usage: UsageDelta,
    ) -> Priced:
        """Price one event. Never raises — an unpriced call still gets a row.

        A missing price is recorded as ₹0 and flagged (`unpriced`), because the
        usage itself is still true and losing the row would hide the gap.
        """
        try:
            await self._ensure_loaded(session)
        except Exception:
            logger.exception("price_book load failed; pricing this event at 0")
            return Priced(cost_inr=Decimal("0"), unit_cost_ref=None)

        total = Decimal("0")
        dominant: tuple[Decimal, uuid.UUID] | None = None

        quantities = _quantities(usage)
        # A provider bills audio by the second *or* by the minute, never both,
        # but the quantity for each is the same `audio_seconds`. Resolve to one
        # unit before charging: `call_min` wins where it exists (only telephony
        # prices that way), otherwise `audio_sec`. Without this, a provider with
        # rows for both units silently bills every voice minute twice.
        if usage.audio_seconds > 0:
            bills_per_minute = self._lookup(provider, model, PriceUnit.CALL_MIN, at) is not None
            quantities.pop(PriceUnit.AUDIO_SEC if bills_per_minute else PriceUnit.CALL_MIN)

        for unit, quantity in quantities.items():
            if quantity <= 0:
                continue
            entry = self._lookup(provider, model, unit, at)
            if entry is None:
                self.unpriced.add((provider, model, unit))
                continue
            component = (quantity / UNIT_QUANTUM[unit]) * entry.price_inr
            total += component
            if dominant is None or component > dominant[0]:
                dominant = (component, entry.id)

        return Priced(
            cost_inr=total.quantize(COST_EXPONENT),
            unit_cost_ref=dominant[1] if dominant else None,
        )


_prices = PriceBookCache()


def get_price_book() -> PriceBookCache:
    """Process-wide cache. One book, so one cache.

    The meter prices against this and `/providers/health` reports its `unpriced`
    set, so they must be the same object — a second cache would report an empty
    `unpriced` while the real one filled up.
    """
    return _prices


def set_price_book(prices: PriceBookCache) -> None:
    """Replace the process cache. Tests use this to scope a book to one test."""
    global _prices
    _prices = prices
