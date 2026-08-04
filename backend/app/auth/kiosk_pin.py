"""The coordinator's numeric PIN for the kiosk's staff strip.

The kiosk stands in a public corridor. A coordinator working it needs to unlock
the staff strip dozens of times a session, on a touchscreen, without a keyboard,
often with a patient watching — so the secret has to be a few digits, and a few
digits is a weak secret. Everything here is arranged around that fact.

**A PIN never buys a staff session.** `verify_pin` mints a token of type
`kiosk_staff`, not `access`. `require_kiosk_staff` is the only guard that accepts
it, and the only routes behind that guard are the kiosk's own assign/identity
verbs. If a PIN minted an ordinary access token, four digits typed in front of a
queue would open the coordinator console, the patient card and the audit surface
from anywhere on the network. The narrow token is the whole point; do not widen
it because a future screen finds it convenient.

The other two defences are the attempt cap and the short TTL. A four-digit PIN
falls to ten thousand tries, which is minutes of scripting; the cap makes it
hours per user and leaves a visible lockout behind.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.hashing import hash_secret, verify_secret
from app.auth.tokens import ISSUER, IssuedToken, TokenError, _encode
from app.config import Settings
from app.models.enums import Role
from app.models.org import User

#: Roles that may hold a kiosk PIN at all. A doctor does not work the strip, and
#: giving a clinical login a corridor-typed secret is how one gets shared.
PIN_ROLES = frozenset({Role.COORDINATOR, Role.ADMIN})

MIN_PIN_LENGTH = 4
MAX_PIN_LENGTH = 8
MAX_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)

#: Long enough to work a stretch of arrivals without re-typing, short enough that
#: a walk-away with the strip open closes itself. The client also relocks on idle;
#: this is the backstop for when it doesn't.
TOKEN_TTL = timedelta(minutes=30)


class PinError(Exception):
    """The PIN was refused. The message is safe to show a coordinator."""


class PinLocked(PinError):
    """Too many wrong tries. Distinct so the route can answer 429, not 401."""


def normalize_pin(pin: str) -> str:
    """Digits only, length-checked.

    Refuses non-digits rather than stripping them: a coordinator who typed a
    letter has made a mistake worth telling them about, and silently discarding
    characters would make two different PINs compare equal.
    """
    pin = (pin or "").strip()
    if not pin.isdigit():
        raise PinError("a PIN is digits only")
    if not MIN_PIN_LENGTH <= len(pin) <= MAX_PIN_LENGTH:
        raise PinError(f"a PIN is {MIN_PIN_LENGTH}-{MAX_PIN_LENGTH} digits")
    return pin


def _trivial(pin: str) -> bool:
    """Reject the PINs everyone picks. `1234`, `0000`, `1111`, `4321`.

    Not security theatre: with a five-try cap, the attacker's whole budget is the
    top handful of choices, so refusing them at set-time is most of the defence.
    """
    if len(set(pin)) == 1:
        return True
    ascending = "0123456789"
    return pin in ascending or pin in ascending[::-1]


async def set_pin(session: AsyncSession, *, user: User, pin: str) -> None:
    """Give a coordinator a kiosk PIN, or replace the one they have."""
    if user.role not in PIN_ROLES:
        raise PinError("only a coordinator or an admin can hold a kiosk PIN")
    pin = normalize_pin(pin)
    if _trivial(pin):
        raise PinError("that PIN is too easy to guess — avoid repeats and runs")
    user.kiosk_pin_hash = hash_secret(pin)
    user.kiosk_pin_attempts = 0
    user.kiosk_pin_locked_until = None
    await session.flush()


async def clear_pin(session: AsyncSession, *, user: User) -> None:
    """Take the kiosk away from a user. Their staff login is untouched."""
    user.kiosk_pin_hash = None
    user.kiosk_pin_attempts = 0
    user.kiosk_pin_locked_until = None
    await session.flush()


def create_kiosk_staff_token(
    *,
    user: User,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedToken:
    """A narrow, short-lived token good only for the kiosk's staff verbs."""
    now = now or datetime.now(UTC)
    expires_at = now + TOKEN_TTL
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(user.id),
        "kind": "user",
        "role": user.role.value,
        "name": user.name,
        "hospital_id": str(user.hospital_id) if user.hospital_id else None,
        # Not "access". `current_principal` decodes with expected_type="access"
        # and will refuse this token for every ordinary staff route.
        "type": "kiosk_staff",
        "iss": ISSUER,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return IssuedToken(_encode(claims, settings), jti, expires_at)


def decode_kiosk_staff_token(token: str, settings: Settings) -> dict:
    """Decode and insist on the narrow type."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("type") != "kiosk_staff":
        raise TokenError("not a kiosk staff token")
    if claims.get("kind") != "user":
        raise TokenError("not a staff token")
    return claims


async def verify_pin(
    session: AsyncSession,
    *,
    user: User,
    pin: str,
    settings: Settings,
    now: datetime | None = None,
) -> IssuedToken:
    """Check a PIN and mint the narrow token. Raises on every failure path."""
    now = now or datetime.now(UTC)

    if user.kiosk_pin_hash is None:
        raise PinError("this login has no kiosk PIN")
    if not user.can_login:
        raise PinError("this login is not active")
    if user.kiosk_pin_locked_until is not None and user.kiosk_pin_locked_until > now:
        raise PinLocked("too many wrong PINs — try again shortly")

    # A lockout that has expired is cleared before the attempt, so the count
    # starts again rather than tripping on the first miss after a wait.
    if user.kiosk_pin_locked_until is not None:
        user.kiosk_pin_locked_until = None
        user.kiosk_pin_attempts = 0

    if not verify_secret(pin, user.kiosk_pin_hash):
        user.kiosk_pin_attempts += 1
        if user.kiosk_pin_attempts >= MAX_ATTEMPTS:
            user.kiosk_pin_locked_until = now + LOCKOUT
        await session.flush()
        raise PinError("that PIN was not recognised")

    user.kiosk_pin_attempts = 0
    user.kiosk_pin_locked_until = None
    await session.flush()
    return create_kiosk_staff_token(user=user, settings=settings, now=now)
