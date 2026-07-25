"""Auth-flow tables: OTP challenges and refresh tokens.

Neither stores a secret in the clear. OTP codes and refresh tokens are hashed
the same way passwords would be, so a database leak does not hand an attacker a
working login. Both tables are consumed by `app.auth`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey, enum_type
from app.models.enums import OtpPurpose


class OtpCode(Base, UUIDPrimaryKey, TimestampMixin):
    """A single OTP challenge. Rows are kept after use (consumed/expired) so the
    rate limiter and any abuse investigation have a history; a Celery job prunes
    them in S17."""

    __tablename__ = "otp_codes"
    __table_args__ = (Index("ix_otp_codes_phone_created_at", "phone", "created_at"),)

    phone: Mapped[str] = mapped_column(String(20), index=True)
    # Argon2 of the code — never the code itself.
    code_hash: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[OtpPurpose] = mapped_column(
        enum_type(OtpPurpose, "otp_purpose"), default=OtpPurpose.LOGIN
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    def is_open(self, now: datetime) -> bool:
        return self.consumed_at is None and self.expires_at > now


class RefreshToken(Base, UUIDPrimaryKey, TimestampMixin):
    """Server-side handle on an issued refresh token so a session can actually be
    revoked. Without this table, logout is cosmetic until the JWT expires.

    A session belongs to **either** a staff `User` **or** a `Patient` (S16 — the
    Android app logs patients and caregivers in with the same OTP flow). The
    CHECK makes "both" and "neither" unrepresentable rather than merely wrong:
    a refresh row that named both would let a rotation mint the wrong audience's
    access token.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) <> (patient_id IS NULL)",
            name="subject_exactly_one",
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"), index=True)
    #: The phone that verified the OTP. Patient sessions only, and the reason
    #: rotation can tell "the patient herself" from "her linked caregiver"
    #: without trusting a claim in the token being rotated.
    subject_phone: Mapped[str | None] = mapped_column(String(20))
    # The JWT's `jti`; the token itself is never stored.
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and self.expires_at > now
