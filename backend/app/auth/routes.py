"""Auth routes: OTP request/verify, refresh, logout, whoami.

Login is phone-OTP for everyone (doc 02 §2: doctors via phone OTP; the staff
username+TOTP option is spec'd but not part of S2).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.otp import OtpInvalid, OtpRateLimited, request_otp, verify_otp
from app.auth.rbac import Principal, current_principal
from app.auth.tokens import TokenError, create_access_token, create_refresh_token, decode_token
from app.config import Settings, get_settings
from app.db import get_session
from app.models.auth import RefreshToken
from app.models.org import User
from app.providers.registry import sms_provider_dependency
from app.providers.sms import SMSProvider

router = APIRouter(prefix="/auth", tags=["auth"])


# --- Schemas -----------------------------------------------------------------


class OtpRequestIn(BaseModel):
    # E.164-ish. Kept loose here; canonicalisation lands with the real SMS
    # provider in S3, which is where the vendor's number format actually matters.
    phone: str = Field(min_length=8, max_length=20)


class OtpRequestOut(BaseModel):
    sent: bool
    expires_at: datetime
    # Present only when OTP_DEBUG_ECHO is on (local/test).
    debug_code: str | None = None


class OtpVerifyIn(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    code: str = Field(min_length=4, max_length=8)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime


class RefreshIn(BaseModel):
    refresh_token: str


class MeOut(BaseModel):
    id: str
    name: str
    role: str
    hospital_id: str | None


# --- Helpers -----------------------------------------------------------------


async def _issue_pair(
    session: AsyncSession, user: User, settings: Settings, request: Request
) -> TokenPair:
    access = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        hospital_id=user.hospital_id,
        settings=settings,
    )
    refresh = create_refresh_token(user_id=user.id, settings=settings)

    session.add(
        RefreshToken(
            user_id=user.id,
            jti=refresh.jti,
            expires_at=refresh.expires_at,
            user_agent=request.headers.get("user-agent", "")[:255] or None,
        )
    )
    await session.flush()

    return TokenPair(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_at=access.expires_at,
    )


# --- Routes ------------------------------------------------------------------


@router.post("/otp/request", response_model=OtpRequestOut)
async def otp_request(
    payload: OtpRequestIn,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    sms: SMSProvider = Depends(sms_provider_dependency),
) -> OtpRequestOut:
    try:
        challenge = await request_otp(session, phone=payload.phone, settings=settings, sms=sms)
    except OtpRateLimited as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

    # `sent: True` even for an unknown phone — see `app.auth.otp.request_otp`:
    # this endpoint must not reveal who is registered.
    return OtpRequestOut(
        sent=True, expires_at=challenge.expires_at, debug_code=challenge.debug_code
    )


@router.post("/otp/verify", response_model=TokenPair)
async def otp_verify(
    payload: OtpVerifyIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    try:
        user = await verify_otp(session, phone=payload.phone, code=payload.code, settings=settings)
    except OtpInvalid as exc:
        # Commit before raising. `verify_otp` increments the challenge's attempt
        # counter, and `get_session` rolls back on exception — without this, every
        # wrong guess would roll its own increment back and the attempt cap would
        # never bite. Covered by tests/test_auth.py::test_otp_attempt_cap.
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return await _issue_pair(session, user, settings, request)


@router.post("/refresh", response_model=TokenPair)
async def refresh_tokens(
    payload: RefreshIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token, settings, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        ) from exc

    now = datetime.now(UTC)
    result = await session.execute(select(RefreshToken).where(RefreshToken.jti == claims["jti"]))
    stored = result.scalar_one_or_none()
    # A signed-but-unknown jti means the row was pruned, or the token was minted
    # against a different database. Either way it is not a live session.
    if stored is None or not stored.is_active(now):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )

    user = await session.get(User, stored.user_id)
    if user is None or not user.can_login:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
        )

    # Rotate: one refresh token is good for exactly one refresh, so a stolen
    # token is usable at most once and the theft shows up as a failed refresh.
    stored.revoked_at = now
    return await _issue_pair(session, user, settings, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshIn,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    """Revoke a refresh token. Idempotent, and quiet about unknown tokens —
    logging out is not an oracle for whether a token was real."""
    try:
        claims = decode_token(payload.refresh_token, settings, expected_type="refresh")
    except TokenError:
        return

    result = await session.execute(select(RefreshToken).where(RefreshToken.jti == claims["jti"]))
    stored = result.scalar_one_or_none()
    if stored and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)


@router.get("/me", response_model=MeOut)
async def me(principal: Principal = Depends(current_principal)) -> MeOut:
    return MeOut(
        id=str(principal.id),
        name=principal.name,
        role=principal.role.value,
        hospital_id=str(principal.hospital_id) if principal.hospital_id else None,
    )
