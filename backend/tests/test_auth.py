"""Auth: OTP challenge/verify, JWT lifecycle, and RBAC.

The security-relevant behaviours (attempt cap, single use, no user enumeration,
refresh rotation, role changes taking effect) get tests of their own — those are
the properties that make a 6-digit code safe enough to be a login.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.otp import OtpInvalid, OtpRateLimited, request_otp, verify_otp
from app.auth.rbac import Principal, require_roles
from app.auth.tokens import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.config import Settings
from app.models.auth import OtpCode, RefreshToken
from app.models.enums import Role
from app.providers.sms import FakeSMSProvider
from tests import factories as f

# --- OTP service -------------------------------------------------------------


async def test_request_otp_sends_sms_and_stores_only_a_hash(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    user = f.make_user(role=Role.DOCTOR)
    session.add(user)
    await session.flush()

    challenge = await request_otp(session, phone=user.phone, settings=settings, sms=sms)

    assert sms.last is not None
    assert sms.last.to == user.phone
    assert sms.last.template_key == "otp_login"
    assert challenge.debug_code is not None
    assert challenge.debug_code in sms.last.body

    stored = (
        await session.execute(select(OtpCode).where(OtpCode.phone == user.phone))
    ).scalar_one()
    assert stored.code_hash != challenge.debug_code
    assert challenge.debug_code not in stored.code_hash


async def test_verify_otp_returns_the_user_and_consumes_the_code(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    user = f.make_user(role=Role.DOCTOR)
    session.add(user)
    await session.flush()

    challenge = await request_otp(session, phone=user.phone, settings=settings, sms=sms)
    verified = await verify_otp(
        session, phone=user.phone, code=challenge.debug_code, settings=settings
    )

    assert verified.id == user.id
    assert verified.last_login_at is not None

    stored = (
        await session.execute(select(OtpCode).where(OtpCode.phone == user.phone))
    ).scalar_one()
    assert stored.consumed_at is not None


async def test_a_code_cannot_be_used_twice(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    user = f.make_user()
    session.add(user)
    await session.flush()

    challenge = await request_otp(session, phone=user.phone, settings=settings, sms=sms)
    await verify_otp(session, phone=user.phone, code=challenge.debug_code, settings=settings)

    with pytest.raises(OtpInvalid):
        await verify_otp(session, phone=user.phone, code=challenge.debug_code, settings=settings)


async def test_wrong_code_is_rejected(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    user = f.make_user()
    session.add(user)
    await session.flush()
    await request_otp(session, phone=user.phone, settings=settings, sms=sms)

    with pytest.raises(OtpInvalid):
        await verify_otp(session, phone=user.phone, code="000000", settings=settings)


async def test_expired_code_is_rejected(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    user = f.make_user()
    session.add(user)
    await session.flush()

    challenge = await request_otp(session, phone=user.phone, settings=settings, sms=sms)

    later = datetime.now(UTC) + timedelta(seconds=settings.otp_ttl_seconds + 1)
    with pytest.raises(OtpInvalid):
        await verify_otp(
            session, phone=user.phone, code=challenge.debug_code, settings=settings, now=later
        )


async def test_attempt_cap_burns_the_challenge(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    """After max attempts the code is dead — not merely 'wrong this time'."""
    user = f.make_user()
    session.add(user)
    await session.flush()

    challenge = await request_otp(session, phone=user.phone, settings=settings, sms=sms)

    for _ in range(settings.otp_max_attempts):
        with pytest.raises(OtpInvalid):
            await verify_otp(session, phone=user.phone, code="000000", settings=settings)

    # Even the *correct* code no longer works.
    with pytest.raises(OtpInvalid):
        await verify_otp(session, phone=user.phone, code=challenge.debug_code, settings=settings)

    stored = (
        await session.execute(select(OtpCode).where(OtpCode.phone == user.phone))
    ).scalar_one()
    assert stored.consumed_at is not None


async def test_requesting_a_new_code_retires_the_previous_one(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    """Only one code is ever live: N outstanding codes would multiply an
    attacker's guessing odds by N.

    Enforced on write, not by `ORDER BY created_at DESC`: rows written in one
    transaction share a `created_at` (Postgres `now()` is the transaction
    timestamp), so ordering alone could hand back the older code.
    """
    user = f.make_user()
    session.add(user)
    await session.flush()

    first = await request_otp(session, phone=user.phone, settings=settings, sms=sms)
    second = await request_otp(session, phone=user.phone, settings=settings, sms=sms)
    assert first.debug_code != second.debug_code

    # Exactly one open challenge exists, regardless of timestamp ties.
    open_codes = (
        await session.execute(
            select(OtpCode).where(OtpCode.phone == user.phone, OtpCode.consumed_at.is_(None))
        )
    ).scalars()
    assert len(list(open_codes)) == 1

    with pytest.raises(OtpInvalid):
        await verify_otp(session, phone=user.phone, code=first.debug_code, settings=settings)

    verified = await verify_otp(
        session, phone=user.phone, code=second.debug_code, settings=settings
    )
    assert verified.id == user.id


async def test_unknown_phone_does_not_reveal_itself(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    """Same response shape as a real user, no row, no SMS."""
    challenge = await request_otp(session, phone="+915559999999", settings=settings, sms=sms)

    assert challenge.expires_at is not None
    assert challenge.debug_code is None
    assert sms.sent == []
    rows = (await session.execute(select(OtpCode).where(OtpCode.phone == "+915559999999"))).all()
    assert rows == []


async def test_deactivated_user_gets_no_code(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    user = f.make_user(active=False)
    session.add(user)
    await session.flush()

    await request_otp(session, phone=user.phone, settings=settings, sms=sms)
    assert sms.sent == []


async def test_resend_cooldown_is_enforced(
    session: AsyncSession, sms: FakeSMSProvider, settings: Settings
) -> None:
    cooling = settings.model_copy(update={"otp_resend_cooldown_seconds": 30})
    user = f.make_user()
    session.add(user)
    await session.flush()

    await request_otp(session, phone=user.phone, settings=cooling, sms=sms)
    with pytest.raises(OtpRateLimited):
        await request_otp(session, phone=user.phone, settings=cooling, sms=sms)


# --- Tokens ------------------------------------------------------------------


def test_access_token_carries_role_claims(settings: Settings) -> None:
    user_id = f.new_uuid()
    issued = create_access_token(
        user_id=user_id, role=Role.DOCTOR, name="Dr. Anil Gupta", settings=settings
    )

    claims = decode_token(issued.token, settings, expected_type="access")
    assert claims["sub"] == str(user_id)
    assert claims["role"] == "doctor"
    assert claims["type"] == "access"


def test_a_refresh_token_is_not_an_access_token(settings: Settings) -> None:
    """Same signature, very different lifetime — the type claim must be checked."""
    issued = create_refresh_token(user_id=f.new_uuid(), settings=settings)

    with pytest.raises(TokenError):
        decode_token(issued.token, settings, expected_type="access")


def test_expired_token_is_rejected(settings: Settings) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    issued = create_access_token(
        user_id=f.new_uuid(), role=Role.NURSE, name="N", settings=settings, now=past
    )

    with pytest.raises(TokenError):
        decode_token(issued.token, settings)


def test_token_signed_with_another_secret_is_rejected(settings: Settings) -> None:
    issued = create_access_token(user_id=f.new_uuid(), role=Role.ADMIN, name="A", settings=settings)
    other = settings.model_copy(update={"jwt_secret": "a-different-secret"})

    with pytest.raises(TokenError):
        decode_token(issued.token, other)


def test_tampered_token_is_rejected(settings: Settings) -> None:
    issued = create_access_token(
        user_id=f.new_uuid(), role=Role.PATIENT, name="P", settings=settings
    )
    header, payload, signature = issued.token.split(".")
    forged = f"{header}.{payload}x.{signature}"

    with pytest.raises(TokenError):
        decode_token(forged, settings)


# --- HTTP flow ---------------------------------------------------------------


async def _login(client: AsyncClient, phone: str) -> dict[str, str]:
    requested = await client.post("/auth/otp/request", json={"phone": phone})
    code = requested.json()["debug_code"]
    verified = await client.post("/auth/otp/verify", json={"phone": phone, "code": code})
    assert verified.status_code == 200, verified.text
    return verified.json()


async def test_full_login_flow_over_http(client: AsyncClient, session: AsyncSession) -> None:
    user = f.make_user(role=Role.DOCTOR, name="Dr. Kavita Rao")
    session.add(user)
    await session.flush()

    tokens = await _login(client, user.phone)
    assert tokens["token_type"] == "bearer"

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "Dr. Kavita Rao"
    assert me.json()["role"] == "doctor"


async def test_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_rejects_garbage_token(client: AsyncClient) -> None:
    response = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_otp_request_is_the_same_for_unknown_numbers(client: AsyncClient) -> None:
    """The HTTP surface must not leak who is registered either."""
    response = await client.post("/auth/otp/request", json={"phone": "+915559999999"})
    assert response.status_code == 200
    assert response.json()["sent"] is True


async def test_wrong_code_over_http_is_401(client: AsyncClient, session: AsyncSession) -> None:
    user = f.make_user()
    session.add(user)
    await session.flush()
    await client.post("/auth/otp/request", json={"phone": user.phone})

    response = await client.post("/auth/otp/verify", json={"phone": user.phone, "code": "000000"})
    assert response.status_code == 401


async def test_otp_attempt_cap(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """The attempt counter must survive the failed request's rollback.

    `get_session` rolls back on exception; the verify route commits the
    increment before raising. Without that, every wrong guess would roll back its
    own increment and the cap would never bite — an unlimited oracle on a
    6-digit code.
    """
    user = f.make_user()
    session.add(user)
    await session.flush()

    requested = await client.post("/auth/otp/request", json={"phone": user.phone})
    code = requested.json()["debug_code"]

    for _ in range(settings.otp_max_attempts):
        await client.post("/auth/otp/verify", json={"phone": user.phone, "code": "000000"})

    stored = (
        await session.execute(select(OtpCode).where(OtpCode.phone == user.phone))
    ).scalar_one()
    assert stored.attempts >= settings.otp_max_attempts, (
        "attempts were rolled back — the cap is not enforced"
    )

    response = await client.post("/auth/otp/verify", json={"phone": user.phone, "code": code})
    assert response.status_code == 401


async def test_refresh_rotates_and_revokes_the_old_token(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = f.make_user(role=Role.NURSE)
    session.add(user)
    await session.flush()
    tokens = await _login(client, user.phone)

    refreshed = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]

    # The old one is spent: replaying it is how a theft shows up.
    replayed = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replayed.status_code == 401


async def test_logout_revokes_the_refresh_token(client: AsyncClient, session: AsyncSession) -> None:
    user = f.make_user()
    session.add(user)
    await session.flush()
    tokens = await _login(client, user.phone)

    assert (
        await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 204

    after = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert after.status_code == 401


async def test_logout_is_idempotent_and_quiet(client: AsyncClient, session: AsyncSession) -> None:
    user = f.make_user()
    session.add(user)
    await session.flush()
    tokens = await _login(client, user.phone)

    for _ in range(2):
        response = await client.post(
            "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 204

    unknown = await client.post("/auth/logout", json={"refresh_token": "not-a-token"})
    assert unknown.status_code == 204


async def test_refresh_token_is_stored_as_a_revocable_handle(
    client: AsyncClient, session: AsyncSession
) -> None:
    user = f.make_user()
    session.add(user)
    await session.flush()
    await _login(client, user.phone)

    stored = (
        await session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id))
    ).scalar_one()
    assert stored.revoked_at is None
    assert stored.jti


async def test_deactivated_user_cannot_use_a_live_token(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A still-valid JWT must stop working the moment access is revoked."""
    user = f.make_user(role=Role.DOCTOR)
    session.add(user)
    await session.flush()
    tokens = await _login(client, user.phone)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert (await client.get("/auth/me", headers=headers)).status_code == 200

    user.active = False
    await session.flush()

    assert (await client.get("/auth/me", headers=headers)).status_code == 401


async def test_role_change_invalidates_an_old_tokens_claim(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A token minted as admin must not stay admin after a demotion."""
    user = f.make_user(role=Role.ADMIN)
    session.add(user)
    await session.flush()
    tokens = await _login(client, user.phone)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    user.role = Role.NURSE
    await session.flush()

    assert (await client.get("/auth/me", headers=headers)).status_code == 401


# --- RBAC --------------------------------------------------------------------


async def test_require_roles_admits_and_refuses(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> None:
    from app.config import get_settings
    from app.db import get_session
    from app.main import create_app

    app: FastAPI = create_app(settings)

    @app.get("/doctors-only")
    async def doctors_only(
        principal: Principal = Depends(require_roles(Role.DOCTOR)),
    ) -> dict[str, str]:
        return {"role": principal.role.value}

    async def _session_override():
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings

    doctor = f.make_user(role=Role.DOCTOR)
    coordinator = f.make_user(role=Role.COORDINATOR)
    session.add_all([doctor, coordinator])
    await session.flush()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        doctor_tokens = await _login(client, doctor.phone)
        allowed = await client.get(
            "/doctors-only",
            headers={"Authorization": f"Bearer {doctor_tokens['access_token']}"},
        )
        assert allowed.status_code == 200
        assert allowed.json() == {"role": "doctor"}

        coordinator_tokens = await _login(client, coordinator.phone)
        refused = await client.get(
            "/doctors-only",
            headers={"Authorization": f"Bearer {coordinator_tokens['access_token']}"},
        )
        # 403 not 404: authenticated, just not permitted.
        assert refused.status_code == 403

        anonymous = await client.get("/doctors-only")
        assert anonymous.status_code == 401


def test_require_roles_rejects_an_empty_grant() -> None:
    """`require_roles()` with no roles would admit nobody — likelier a bug than intent."""
    with pytest.raises(ValueError):
        require_roles()
