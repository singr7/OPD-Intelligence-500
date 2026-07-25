"""Auth routes: OTP request/verify, refresh, logout, whoami.

Login is phone-OTP for everyone (doc 02 §2: doctors via phone OTP; the staff
username+TOTP option is spec'd but not part of S2).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import patient_app
from app.auth.otp import OtpInvalid, OtpRateLimited, check_code, request_otp, verify_otp
from app.auth.rbac import PatientPrincipal, Principal, current_patient, current_principal
from app.auth.tokens import (
    TokenError,
    create_access_token,
    create_patient_access_token,
    create_refresh_token,
    decode_token,
)
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


class ProfileOut(BaseModel):
    """One care file the phone that just logged in may open (S16)."""

    patient_id: str
    name: str
    via: str
    relation: str | None = None


class PatientTokenPair(TokenPair):
    #: Whose file this token opens, and every other file this phone may switch to.
    patient_id: str
    via: str
    profiles: list[ProfileOut]


class SwitchIn(BaseModel):
    patient_id: uuid.UUID


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


async def _issue_patient_pair(
    session: AsyncSession,
    profile: patient_app.Profile,
    phone: str,
    settings: Settings,
    request: Request,
    profiles: list[patient_app.Profile] | None = None,
) -> PatientTokenPair:
    """Mint a session for one care file, opened by one phone (S16).

    The refresh row records **which patient** and **which phone**, not the `via`
    the client was told: a rotation an hour later re-resolves the footing from
    `caregiver_links`, so a caregiver whose access was revoked in the meantime
    cannot refresh her way back in.
    """
    patient = profile.patient
    access = create_patient_access_token(
        patient_id=patient.id,
        name=patient.name,
        hospital_id=patient.hospital_id,
        via=profile.via,
        actor_phone=phone,
        settings=settings,
    )
    refresh = create_refresh_token(user_id=patient.id, settings=settings, kind="patient")

    session.add(
        RefreshToken(
            patient_id=patient.id,
            subject_phone=phone,
            jti=refresh.jti,
            expires_at=refresh.expires_at,
            user_agent=request.headers.get("user-agent", "")[:255] or None,
        )
    )
    await session.flush()

    listed = profiles if profiles is not None else [profile]
    return PatientTokenPair(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_at=access.expires_at,
        patient_id=str(patient.id),
        via=profile.via,
        profiles=[
            ProfileOut(
                patient_id=str(p.patient.id),
                name=p.patient.name,
                via=p.via,
                relation=p.relation,
            )
            for p in listed
        ],
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


@router.post("/refresh", response_model=TokenPair | PatientTokenPair)
async def refresh_tokens(
    payload: RefreshIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    """Rotate a refresh token — staff or patient app, told apart by the row.

    Which audience this is comes from `refresh_tokens`, never from the token's
    own `kind` claim. The row is the thing a `CHECK` constrains and an admin can
    revoke; the claim is just what the holder is carrying.
    """
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

    if stored.patient_id is not None:
        # Re-resolve the footing rather than trusting the expired session's: a
        # revoked caregiver link must not survive one more rotation.
        profile = await patient_app.profile_for(
            session, phone=stored.subject_phone or "", patient_id=stored.patient_id
        )
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
            )
        stored.revoked_at = now
        profiles = await patient_app.profiles_for_phone(session, stored.subject_phone or "")
        return await _issue_patient_pair(
            session, profile, stored.subject_phone or "", settings, request, profiles
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


# -- the patient app's login (S16, doc 03 §1c.7) -------------------------------


@router.post("/patient/otp/request", response_model=OtpRequestOut)
async def patient_otp_request(
    payload: OtpRequestIn,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    sms: SMSProvider = Depends(sms_provider_dependency),
) -> OtpRequestOut:
    """Send a login code to a patient's or caregiver's phone.

    Separate from the staff endpoint above only in *who counts as a recipient* —
    the code's TTL, cooldown, single-outstanding rule and attempt cap are the
    same `otp_codes` machinery. Like the staff one it never reveals whether the
    number is registered.
    """
    known = bool(await patient_app.profiles_for_phone(session, payload.phone))
    try:
        challenge = await request_otp(
            session, phone=payload.phone, settings=settings, sms=sms, known=known
        )
    except OtpRateLimited as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc

    return OtpRequestOut(
        sent=True, expires_at=challenge.expires_at, debug_code=challenge.debug_code
    )


@router.post("/patient/otp/verify", response_model=PatientTokenPair)
async def patient_otp_verify(
    payload: OtpVerifyIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PatientTokenPair:
    """Verify the code and open a session on the first file this phone may see.

    A phone that reaches several files (a shared handset, a son who is caregiver
    to both parents) gets all of them listed in `profiles` and one token for the
    first — her own file when she has one. The app switches with
    `POST /auth/patient/switch`; it never holds two sessions at once, because two
    open cancer files on one screen is how the wrong one gets shown.
    """

    async def resolve() -> list[patient_app.Profile] | None:
        found = await patient_app.profiles_for_phone(session, payload.phone)
        return found or None

    try:
        profiles = await check_code(
            session,
            phone=payload.phone,
            code=payload.code,
            settings=settings,
            resolve=resolve,
        )
    except OtpInvalid as exc:
        # Commit the attempt increment before raising — same reason as the staff
        # verify above: `get_session` rolls back on exception and the attempt cap
        # would never bite.
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    return await _issue_patient_pair(
        session, profiles[0], payload.phone, settings, request, profiles
    )


@router.post("/patient/switch", response_model=PatientTokenPair)
async def patient_switch(
    payload: SwitchIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    principal: PatientPrincipal = Depends(current_patient),
) -> PatientTokenPair:
    """Open a different file this same phone is entitled to.

    Entitlement is re-resolved from the database against the *token's* phone, so
    switching is not an id-guessing game: an id the phone may not open is a 403,
    and no OTP was spent to find that out.
    """
    profile = await patient_app.profile_for(
        session, phone=principal.actor_phone, patient_id=payload.patient_id
    )
    if profile is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not your file")

    profiles = await patient_app.profiles_for_phone(session, principal.actor_phone)
    return await _issue_patient_pair(
        session, profile, principal.actor_phone, settings, request, profiles
    )


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
