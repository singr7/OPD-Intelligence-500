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

#: Which population a token speaks for. Staff (`users` row) and patients
#: (`patients` row) are different tables with different id spaces, so a token
#: that did not say which one it meant would let a patient id be looked up in
#: `users` — and one day match. Carried in the `kind` claim and checked by every
#: authentication dependency in `app.auth.rbac`.
SubjectKind = Literal["user", "patient"]

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
        "kind": "user",
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


def create_patient_access_token(
    *,
    patient_id: uuid.UUID,
    name: str,
    settings: Settings,
    hospital_id: uuid.UUID | None = None,
    via: Literal["self", "caregiver"] = "self",
    actor_phone: str,
    now: datetime | None = None,
) -> IssuedToken:
    """An access token for the patient app (S16).

    `sub` is the **patient** whose file this token opens — a caregiver's token
    names the patient, not the caregiver, so every route downstream scopes on one
    id and cannot accidentally serve the wrong file. Who is holding the phone is
    `via` + `actor_phone`, which is what the write guards and the audit trail
    read. The claims are never trusted alone: `app.auth.rbac.current_patient`
    re-checks the patient row and, for a caregiver, the live consent state.
    """
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_ttl_minutes)
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(patient_id),
        "kind": "patient",
        "role": Role.CAREGIVER.value if via == "caregiver" else Role.PATIENT.value,
        "via": via,
        "actor_phone": actor_phone,
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
    *,
    user_id: uuid.UUID,
    settings: Settings,
    kind: SubjectKind = "user",
    now: datetime | None = None,
) -> IssuedToken:
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(days=settings.refresh_token_ttl_days)
    jti = uuid.uuid4().hex
    claims = {
        "sub": str(user_id),
        "kind": kind,
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
