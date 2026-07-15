"""Usage metering and pricing (doc 02 §4/§8).

`usage_events` is the raw feed behind the cost dashboard (S18) and the cost-guard
(S3). Costs are stored *and* recomputable: `unit_cost_ref` points at the
`price_book` row used, so when a vendor changes pricing, history can be re-priced
rather than silently reinterpreted.

Money is `Numeric(12, 4)`, never float — per-event costs are fractions of a rupee
and they get summed into invoices that must reconcile exactly (S18 AC).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey, enum_type
from app.models.enums import Channel, IntakeTier, PriceUnit, UsagePurpose


class PriceBook(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "price_book"
    __table_args__ = (
        UniqueConstraint("provider", "model", "unit", "effective_from", name="uq_price_book_entry"),
    )

    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    unit: Mapped[PriceUnit] = mapped_column(enum_type(PriceUnit, "price_unit"))
    price_inr: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    effective_from: Mapped[date] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(String(500))


class UsageEvent(Base, UUIDPrimaryKey):
    """One row per provider call/stream. Written async + batched by the metering
    decorator in S3 — this path must never block a patient-facing call."""

    __tablename__ = "usage_events"
    __table_args__ = (
        # The dashboard's hot query shape: bucket → group by provider/model/channel/tier.
        Index("ix_usage_events_minute_bucket_provider", "minute_bucket", "provider"),
        Index("ix_usage_events_intake_at", "intake_id", "at"),
    )

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Truncated to the minute on write, so per-minute rollups are a plain GROUP BY.
    minute_bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Deliberately un-FK'd: metering must never fail or block because a session
    # row was rolled back, and these are analytics dimensions, not relations.
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    intake_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    channel: Mapped[Channel | None] = mapped_column(enum_type(Channel, "channel"))
    tier: Mapped[IntakeTier | None] = mapped_column(enum_type(IntakeTier, "intake_tier"))
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(120))
    purpose: Mapped[UsagePurpose] = mapped_column(
        enum_type(UsagePurpose, "usage_purpose"), default=UsagePurpose.OTHER, index=True
    )

    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    audio_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    # Added in S3 alongside PriceUnit.CHAR: TTS vendors bill per character, so
    # without the quantity stored, TTS history could not be re-priced when a
    # vendor changes rates — which is the whole promise of `unit_cost_ref`.
    characters: Mapped[int] = mapped_column(Integer, default=0)

    unit_cost_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    computed_cost_inr: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
