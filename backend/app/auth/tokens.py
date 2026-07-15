"""JWT issue/verify (doc 02 §7: short-lived JWT + refresh, role claims).

Access tokens carry the role claim that RBAC reads, so they are kept short
(30 min default) — a role change or deactivation takes effect within that window
without a revocation lookup on every request. Refresh tokens carry a `jti`
backed by the `refresh_tokens` table, which is what makes logout real.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.config import Settings
from app.models.enums import Role

TokenType = Literal["access", "refresh"]

ISSUER = "opd-intelligence"


class TokenError(Exception):
    """Malformed, expired, or wrong-type token."""


@dataclass(frozen=True)
class IssuedToken:
    token: str
    jti: str
    expires_at: datetime


def _encode(claims: dict[str, Any], settings: Settings) -> str:
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    *,
    user_id: uuid.UUID,
    role: Role,
    name: str,
    settings: Settings,
    hospital_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> IssuedToken:
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(user_id),
        "role": role.value,
        "name": name,
        "hospital_id": str(hospital_id) if hospital_id else None,
        "type": "access",
        "iss": ISSUER,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return IssuedToken(_encode(claims, settings), jti, expires_at)


def create_refresh_token(
    *, user_id: uuid.UUID, settings: Settings, now: datetime | None = None
) -> IssuedToken:
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(days=settings.refresh_token_ttl_days)
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(user_id),
        "type": "refresh",
        "iss": ISSUER,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return IssuedToken(_encode(claims, settings), jti, expires_at)


def decode_token(
    token: str, settings: Settings, *, expected_type: TokenType | None = None
) -> dict[str, Any]:
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

    # Without this check an access token would be accepted where a refresh token
    # is expected (and vice versa) — same signature, very different lifetime.
    if expected_type and claims.get("type") != expected_type:
        raise TokenError(f"expected {expected_type} token, got {claims.get('type')!r}")

    return claims
