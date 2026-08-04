"""The coordinator's kiosk PIN (`app.auth.kiosk_pin`).

The point of these tests is the blast radius. A PIN is a few digits typed in a
corridor; what matters is that it opens the staff strip and nothing else, that
guessing it is expensive, and that revoking it takes effect immediately rather
than when a token happens to expire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app.auth import kiosk_pin as kp
from app.auth.tokens import TokenError, decode_token
from app.config import Settings
from app.models.enums import Role

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def coordinator(session: AsyncSession):
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    user = f.make_user(hospital, role=Role.COORDINATOR)
    session.add(user)
    await session.flush()
    return user


# -- setting a PIN ------------------------------------------------------------


async def test_a_pin_is_stored_hashed_never_in_the_clear(session, coordinator):
    await kp.set_pin(session, user=coordinator, pin="4718")
    assert coordinator.kiosk_pin_hash is not None
    assert "4718" not in coordinator.kiosk_pin_hash


@pytest.mark.parametrize("bad", ["123", "123456789", "12a4", "", "  ", "abcd"])
async def test_a_pin_must_be_four_to_eight_digits(session, coordinator, bad):
    with pytest.raises(kp.PinError):
        await kp.set_pin(session, user=coordinator, pin=bad)


@pytest.mark.parametrize("guessable", ["1234", "0000", "1111", "4321", "12345678"])
async def test_the_pins_everyone_picks_are_refused(session, coordinator, guessable):
    """With a five-try cap, the attacker's whole budget is the top few choices."""
    with pytest.raises(kp.PinError):
        await kp.set_pin(session, user=coordinator, pin=guessable)


async def test_a_doctor_cannot_hold_a_kiosk_pin(session):
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    doctor_user = f.make_user(hospital, role=Role.DOCTOR)
    session.add(doctor_user)
    await session.flush()

    with pytest.raises(kp.PinError):
        await kp.set_pin(session, user=doctor_user, pin="4718")


# -- verifying ----------------------------------------------------------------


async def test_the_right_pin_mints_a_token(session, coordinator, settings: Settings):
    await kp.set_pin(session, user=coordinator, pin="4718")
    issued = await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)
    claims = kp.decode_kiosk_staff_token(issued.token, settings)
    assert claims["sub"] == str(coordinator.id)


async def test_the_token_is_not_a_staff_access_token(session, coordinator, settings: Settings):
    """The whole design: four digits must not open the coordinator console."""
    await kp.set_pin(session, user=coordinator, pin="4718")
    issued = await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)

    with pytest.raises(TokenError):
        decode_token(issued.token, settings, expected_type="access")


async def test_a_staff_access_token_does_not_open_the_strip(
    session, coordinator, settings: Settings
):
    """And the reverse, so the two token types cannot be substituted either way."""
    from app.auth.tokens import create_access_token

    issued = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role,
        name=coordinator.name,
        settings=settings,
        hospital_id=coordinator.hospital_id,
    )
    with pytest.raises(TokenError):
        kp.decode_kiosk_staff_token(issued.token, settings)


async def test_a_wrong_pin_is_refused_and_counted(session, coordinator, settings: Settings):
    await kp.set_pin(session, user=coordinator, pin="4718")

    with pytest.raises(kp.PinError):
        await kp.verify_pin(session, user=coordinator, pin="9999", settings=settings)
    assert coordinator.kiosk_pin_attempts == 1


async def test_the_attempt_cap_locks_the_pin_out(session, coordinator, settings: Settings):
    await kp.set_pin(session, user=coordinator, pin="4718")

    for _ in range(kp.MAX_ATTEMPTS):
        with pytest.raises(kp.PinError):
            await kp.verify_pin(session, user=coordinator, pin="9999", settings=settings)

    # Even the correct PIN is refused while the lockout stands, and with a
    # distinct error so the route can answer 429 rather than 401.
    with pytest.raises(kp.PinLocked):
        await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)


async def test_the_lockout_expires_and_the_count_restarts(session, coordinator, settings: Settings):
    await kp.set_pin(session, user=coordinator, pin="4718")
    coordinator.kiosk_pin_attempts = kp.MAX_ATTEMPTS
    coordinator.kiosk_pin_locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    issued = await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)
    assert issued.token
    assert coordinator.kiosk_pin_attempts == 0
    assert coordinator.kiosk_pin_locked_until is None


async def test_a_good_pin_clears_the_count(session, coordinator, settings: Settings):
    await kp.set_pin(session, user=coordinator, pin="4718")
    with pytest.raises(kp.PinError):
        await kp.verify_pin(session, user=coordinator, pin="9999", settings=settings)

    await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)
    assert coordinator.kiosk_pin_attempts == 0


async def test_a_user_with_no_pin_cannot_unlock(session, coordinator, settings: Settings):
    with pytest.raises(kp.PinError):
        await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)


async def test_clearing_the_pin_takes_the_kiosk_away(session, coordinator, settings: Settings):
    await kp.set_pin(session, user=coordinator, pin="4718")
    await kp.clear_pin(session, user=coordinator)

    assert coordinator.kiosk_pin_hash is None
    with pytest.raises(kp.PinError):
        await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)


async def test_a_deactivated_coordinator_cannot_unlock(session, coordinator, settings: Settings):
    await kp.set_pin(session, user=coordinator, pin="4718")
    coordinator.active = False
    await session.flush()

    with pytest.raises(kp.PinError):
        await kp.verify_pin(session, user=coordinator, pin="4718", settings=settings)


# -- seeding ------------------------------------------------------------------


async def test_seeding_never_clobbers_a_rotated_pin(session, coordinator, monkeypatch):
    """`make seed` runs on every deploy. Overwriting would silently hand the
    corridor back a value printed in this repository."""
    from app.seed import _seed_kiosk_pin

    await kp.set_pin(session, user=coordinator, pin="8261")
    rotated = coordinator.kiosk_pin_hash

    await _seed_kiosk_pin(session, coordinator, "4729")

    assert coordinator.kiosk_pin_hash == rotated


async def test_seeding_a_pin_is_refused_outside_local(session, coordinator, settings, monkeypatch):
    """The seeded PIN is committed and world-readable, so a box that a real
    patient could walk up to must not be given it."""
    import app.seed as seed_mod

    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(seed_mod, "get_settings", lambda: settings)

    await seed_mod._seed_kiosk_pin(session, coordinator, "4729")

    assert coordinator.kiosk_pin_hash is None


async def test_seeding_gives_a_fresh_coordinator_the_pin(session, coordinator):
    from app.seed import _seed_kiosk_pin

    await _seed_kiosk_pin(session, coordinator, "4729")
    assert coordinator.kiosk_pin_hash is not None
