"""Phone-OTP challenge/verify (doc 02 §2, doc 03 §5 doctor login).

Threat model for a 6-digit code — the code space is small, so the protections are
structural rather than cryptographic:

- **Short TTL** (5 min) and **single use**: a consumed challenge is dead.
- **Attempt cap** per challenge (5), counted on the *challenge*, not the request.
- **Resend cooldown**: stops an attacker minting fresh challenges to widen the
  window, and stops SMS-pumping the user's bill.
- **Only the newest open challenge verifies**: without this, N outstanding codes
  would multiply an attacker's guessing odds by N.
- **No user enumeration**: requesting an OTP for an unknown or deactivated phone
  returns the same response as a real one, and simply sends nothing.

Verification is deliberately *not* rate-limited by IP here — that belongs at the
edge and lands with the rate limiting in S20.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.hashing import hash_secret, verify_secret
from app.config import Settings
from app.models.auth import OtpCode
from app.models.enums import OtpPurpose
from app.models.org import User
from app.providers.sms import SmsMessage, SMSProvider


class OtpError(Exception):
    """Base for OTP failures that are safe to surface to the caller."""


class OtpRateLimited(OtpError):
    pass


class OtpInvalid(OtpError):
    """Wrong, expired, exhausted, or already-consumed code.

    One error type on purpose: telling a caller *which* of those it was is free
    intelligence for someone guessing.
    """


@dataclass(frozen=True)
class OtpChallenge:
    phone: str
    expires_at: datetime
    # Populated only when OTP_DEBUG_ECHO is on (local/test). Never in production —
    # `Settings.assert_production_safe` refuses to boot with it enabled.
    debug_code: str | None = None


def generate_code(length: int) -> str:
    """Cryptographically random, zero-padded, fixed-length."""
    upper = 10**length
    return str(secrets.randbelow(upper)).zfill(length)


async def _find_user(session: AsyncSession, phone: str) -> User | None:
    result = await session.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    return user if user and user.can_login else None


async def _newest_open_challenge(
    session: AsyncSession, phone: str, now: datetime
) -> OtpCode | None:
    """The one open challenge for `phone`, if any.

    `request_otp` retires prior challenges as it issues a new one, so at most one
    is ever open and this needs no ordering to pick a winner. That matters: rows
    created in the same transaction share `created_at` (Postgres `now()` is the
    *transaction* timestamp), so `ORDER BY created_at DESC LIMIT 1` could return
    an older code — quietly breaking the single-outstanding-code guarantee.
    `.limit(1)` is belt-and-braces; the invariant is maintained on write.
    """
    result = await session.execute(
        select(OtpCode)
        .where(
            OtpCode.phone == phone,
            OtpCode.purpose == OtpPurpose.LOGIN,
            OtpCode.consumed_at.is_(None),
            OtpCode.expires_at > now,
        )
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _retire_open_challenges(session: AsyncSession, phone: str, now: datetime) -> None:
    """Consume every outstanding challenge for `phone`.

    Issuing a new code kills the old one. Without this, each resend would leave
    another live code in play and multiply an attacker's guessing odds by the
    number of outstanding codes.
    """
    await session.execute(
        update(OtpCode)
        .where(
            OtpCode.phone == phone,
            OtpCode.purpose == OtpPurpose.LOGIN,
            OtpCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )


async def _in_cooldown(
    session: AsyncSession, phone: str, settings: Settings, now: datetime
) -> bool:
    cutoff = now - timedelta(seconds=settings.otp_resend_cooldown_seconds)
    result = await session.execute(
        select(OtpCode.id).where(OtpCode.phone == phone, OtpCode.created_at > cutoff).limit(1)
    )
    return result.first() is not None


async def request_otp(
    session: AsyncSession,
    *,
    phone: str,
    settings: Settings,
    sms: SMSProvider,
    now: datetime | None = None,
) -> OtpChallenge:
    """Issue and send a login OTP.

    Returns an identical-looking challenge whether or not the phone belongs to a
    user — the caller cannot use this endpoint to discover who is registered.
    """
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.otp_ttl_seconds)

    if await _in_cooldown(session, phone, settings, now):
        raise OtpRateLimited(f"wait {settings.otp_resend_cooldown_seconds}s between OTP requests")

    user = await _find_user(session, phone)
    if user is None:
        # Same shape, same timing-ish, no row, no SMS.
        return OtpChallenge(phone=phone, expires_at=expires_at)

    await _retire_open_challenges(session, phone, now)

    code = generate_code(settings.otp_length)
    session.add(
        OtpCode(
            phone=phone,
            code_hash=hash_secret(code),
            purpose=OtpPurpose.LOGIN,
            expires_at=expires_at,
        )
    )
    await session.flush()

    await sms.send(
        SmsMessage(
            to=phone,
            body=f"{code} is your OPD login code. Valid {settings.otp_ttl_seconds // 60} minutes.",
            template_key="otp_login",
        )
    )

    return OtpChallenge(
        phone=phone,
        expires_at=expires_at,
        debug_code=code if settings.otp_debug_echo else None,
    )


async def verify_otp(
    session: AsyncSession,
    *,
    phone: str,
    code: str,
    settings: Settings,
    now: datetime | None = None,
) -> User:
    """Consume the newest open challenge for `phone` and return its user.

    Raises `OtpInvalid` for every failure mode.
    """
    now = now or datetime.now(UTC)

    challenge = await _newest_open_challenge(session, phone, now)
    if challenge is None:
        raise OtpInvalid("no valid code outstanding")

    if challenge.attempts >= settings.otp_max_attempts:
        # Burn it: an exhausted challenge must not stay guessable.
        challenge.consumed_at = now
        raise OtpInvalid("too many attempts")

    challenge.attempts += 1

    if not verify_secret(code, challenge.code_hash):
        raise OtpInvalid("incorrect code")

    user = await _find_user(session, phone)
    if user is None:
        # Deactivated between request and verify.
        challenge.consumed_at = now
        raise OtpInvalid("no valid code outstanding")

    challenge.consumed_at = now
    user.last_login_at = now
    await session.flush()
    return user
