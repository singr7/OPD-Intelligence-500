"""Vendor credentials entered in the console (S-GL.1, doc 12 §4/§7).

The point of the feature is that the day the Exotel number arrives, opening the
phone channel is not a deploy. The point of *these tests* is that buying that
convenience did not quietly cost the three things it could have cost:

- a credential readable back out of the API,
- a console able to write settings that are not credentials,
- a plaintext secret in a database dump.

Plus the operational half: `.env` stays the floor, a rotated key says so rather
than looking like "never configured", and a provider already built with the old
token is rebuilt rather than kept.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import admin as admin_svc
from app.config import Settings
from app.models.content import ProviderSecret
from app.providers import runtime
from app.providers.profiles import resolve_profile, snapshot_profile
from app.providers.registry import get_messaging_provider, reset_providers
from app.providers.secrets import SecretUnreadable, decrypt, encrypt, key_id
from tests import factories as f

pytestmark = pytest.mark.asyncio

META = "messaging:meta"
EXOTEL = "telephony:exotel"
OPENAI = "vendor:openai"

META_VALUES = {
    "meta_whatsapp_token": "EAA-a-real-looking-token",
    "meta_phone_number_id": "1234567890",
    "meta_verify_token": "hook-secret",
    "meta_app_secret": "app-secret",
}


@pytest_asyncio.fixture(autouse=True)
async def _fresh_overlay():
    """The overlay caches for ten seconds by design; a test must not read the
    previous test's credentials out of that cache."""
    runtime.invalidate()
    yield
    runtime.invalidate()


# -- encryption ---------------------------------------------------------------


async def test_a_credential_round_trips_through_encryption(settings: Settings):
    ciphertext, kid = encrypt(META_VALUES, settings)
    assert decrypt(ciphertext, kid, settings) == META_VALUES


async def test_the_ciphertext_does_not_contain_the_secret(settings: Settings):
    """The whole reason this is not a plaintext column: a database dump must not
    be a set of live vendor credentials."""
    ciphertext, _ = encrypt(META_VALUES, settings)
    assert "EAA-a-real-looking-token" not in ciphertext
    assert "hook-secret" not in ciphertext


async def test_a_rotated_key_reports_itself_rather_than_failing_vaguely(settings: Settings):
    """ "These are here and unreadable" is a different problem from "these were
    never entered", and it has a different fix."""
    ciphertext, kid = encrypt(META_VALUES, settings)
    rotated = settings.model_copy(update={"jwt_secret": "a-different-secret-padded-to-32-chars!"})
    with pytest.raises(SecretUnreadable, match="must be entered again"):
        decrypt(ciphertext, kid, rotated)


async def test_an_explicit_secrets_key_decouples_from_the_jwt_secret(settings: Settings):
    from cryptography.fernet import Fernet

    explicit = settings.model_copy(update={"secrets_key": Fernet.generate_key().decode()})
    ciphertext, kid = encrypt(META_VALUES, explicit)

    # Rotating the JWT secret no longer touches the credentials.
    rotated = explicit.model_copy(update={"jwt_secret": "another-secret-padded-to-32-chars-ok!"})
    assert decrypt(ciphertext, kid, rotated) == META_VALUES
    assert key_id(explicit) != key_id(settings)


# -- the allow-list -----------------------------------------------------------


async def test_only_this_vendors_fields_are_stored(session: AsyncSession, settings: Settings):
    """A console may supply a vendor's credentials. It may not repoint the
    database, turn on OTP echo, or select a different vendor."""
    await admin_svc.save_provider_credentials(
        session,
        provider=META,
        values={
            **META_VALUES,
            "database_url": "postgresql://attacker/evil",
            "otp_debug_echo": "true",
            "messaging_provider": "fake",
            "exotel_token": "not-metas-to-set",
        },
        settings=settings,
    )
    row = (
        await session.execute(select(ProviderSecret).where(ProviderSecret.provider == META))
    ).scalar_one()
    stored = decrypt(row.secret, row.key_id, settings)
    assert set(stored) == set(META_VALUES)


async def test_an_unknown_provider_is_refused(session: AsyncSession, settings: Settings):
    with pytest.raises(runtime.UnknownProviderSecret):
        await admin_svc.save_provider_credentials(
            session, provider="messaging:telegram", values={"token": "x"}, settings=settings
        )


async def test_a_save_with_nothing_recognisable_is_refused(
    session: AsyncSession, settings: Settings
):
    with pytest.raises(admin_svc.AdminError, match="no recognised credential fields"):
        await admin_svc.save_provider_credentials(
            session, provider=META, values={"nonsense": "x"}, settings=settings
        )


# -- the overlay --------------------------------------------------------------


async def test_a_stored_credential_overlays_the_environment(
    session: AsyncSession, settings: Settings
):
    base = settings.model_copy(update={"meta_whatsapp_token": "from-env"})
    assert (await runtime.effective_settings(session, base)).meta_whatsapp_token == "from-env"

    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=base
    )
    effective = await runtime.effective_settings(session, base)
    assert effective.meta_whatsapp_token == META_VALUES["meta_whatsapp_token"]
    assert effective.meta_phone_number_id == META_VALUES["meta_phone_number_id"]


async def test_one_encrypted_openai_key_is_shared_by_all_voice_components(
    session: AsyncSession, settings: Settings
):
    await admin_svc.save_provider_credentials(
        session,
        provider=OPENAI,
        values={"openai_api_key": "one-shared-key"},
        settings=settings,
    )

    effective = await runtime.effective_settings(session, settings)
    rows = (
        (await session.execute(select(ProviderSecret).where(ProviderSecret.provider == OPENAI)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert effective.openai_api_key == "one-shared-key"
    assert "one-shared-key" not in rows[0].secret


async def test_clearing_a_credential_returns_the_box_to_env(
    session: AsyncSession, settings: Settings
):
    """`.env` is the floor, exactly as the seed files are for trees and protocols."""
    base = settings.model_copy(update={"meta_whatsapp_token": "from-env"})
    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=base
    )
    await admin_svc.clear_provider_credentials(session, provider=META)
    assert (await runtime.effective_settings(session, base)).meta_whatsapp_token == "from-env"


async def test_clearing_hard_deletes_rather_than_soft_deletes(
    session: AsyncSession, settings: Settings
):
    """A soft-deleted secret is a live vendor credential still sitting in the
    database after somebody decided it should not be."""
    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=settings
    )
    await admin_svc.clear_provider_credentials(session, provider=META)
    rows = (await session.execute(select(ProviderSecret))).scalars().all()
    assert rows == []


async def test_a_partial_re_entry_does_not_blank_the_other_fields(
    session: AsyncSession, settings: Settings
):
    """The form cannot show what it holds, so an admin re-typing one field must
    not silently wipe the other three."""
    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=settings
    )
    await admin_svc.save_provider_credentials(
        session, provider=META, values={"meta_whatsapp_token": "rotated"}, settings=settings
    )
    effective = await runtime.effective_settings(session, settings)
    assert effective.meta_whatsapp_token == "rotated"
    assert effective.meta_phone_number_id == META_VALUES["meta_phone_number_id"]


async def test_an_unreadable_row_does_not_take_the_other_vendor_down(
    session: AsyncSession, settings: Settings
):
    ciphertext, _ = encrypt({"meta_whatsapp_token": "x"}, settings)
    session.add(ProviderSecret(provider=META, secret=ciphertext, key_id="a-stale-key-id"))
    await admin_svc.save_provider_credentials(
        session,
        provider=EXOTEL,
        values={"exotel_sid": "sid", "exotel_api_key": "key", "exotel_token": "tok"},
        settings=settings,
    )
    effective = await runtime.effective_settings(session, settings)
    assert effective.exotel_sid == "sid"


# -- status: what the console is told ------------------------------------------


async def test_status_reports_configured_source_and_missing_fields(
    session: AsyncSession, settings: Settings
):
    before = {s.provider: s for s in await admin_svc.provider_credentials(session, settings)}
    assert not before[META].configured
    assert before[META].source == "unset"
    assert set(before[META].missing) == {"meta_whatsapp_token", "meta_phone_number_id"}

    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=settings
    )
    after = {s.provider: s for s in await admin_svc.provider_credentials(session, settings)}
    assert after[META].configured
    assert after[META].source == "console"
    assert after[META].missing == []


async def test_a_credential_set_in_env_reads_as_configured_from_env(
    session: AsyncSession, settings: Settings
):
    env = settings.model_copy(
        update={"meta_whatsapp_token": "env-token", "meta_phone_number_id": "999"}
    )
    status = {s.provider: s for s in await admin_svc.provider_credentials(session, env)}
    assert status[META].configured
    assert status[META].source == "env"


async def test_an_incomplete_credential_set_is_not_configured(
    session: AsyncSession, settings: Settings
):
    """Half a credential set must not make a channel look ready and then fail on
    the first message."""
    await admin_svc.save_provider_credentials(
        session, provider=META, values={"meta_whatsapp_token": "tok"}, settings=settings
    )
    status = {s.provider: s for s in await admin_svc.provider_credentials(session, settings)}
    assert not status[META].configured
    assert status[META].missing == ["meta_phone_number_id"]


async def test_a_rotated_key_shows_as_unreadable_not_as_unset(
    session: AsyncSession, settings: Settings
):
    ciphertext, _ = encrypt(META_VALUES, settings)
    session.add(ProviderSecret(provider=META, secret=ciphertext, key_id="written-by-another-key"))
    await session.flush()
    status = {s.provider: s for s in await admin_svc.provider_credentials(session, settings)}
    assert status[META].unreadable
    assert not status[META].configured


# -- the HTTP surface ---------------------------------------------------------


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, session: AsyncSession, settings: Settings) -> dict:
    from app.auth.tokens import create_access_token
    from app.models.enums import Role

    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    user = f.make_user(hospital, role=Role.ADMIN)
    session.add(user)
    await session.flush()
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        hospital_id=user.hospital_id,
        settings=settings,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def test_no_route_ever_returns_a_credential(
    client: AsyncClient, session: AsyncSession, settings: Settings, admin_headers: dict
):
    """The rule the whole design hangs off: write-only over the wire. Asserted
    against the serialised response rather than field by field, so a field added
    later cannot quietly open a read path."""
    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=settings
    )

    response = await client.get("/admin/providers/credentials", headers=admin_headers)
    assert response.status_code == 200
    body = json.dumps(response.json())
    for secret in META_VALUES.values():
        assert secret not in body

    written = await client.put(
        f"/admin/providers/{META}/credentials",
        json={"values": {"meta_whatsapp_token": "another-token"}},
        headers=admin_headers,
    )
    assert written.status_code == 200
    assert "another-token" not in json.dumps(written.json())


async def test_the_status_endpoint_names_the_fields_a_vendor_takes(
    client: AsyncClient, admin_headers: dict
):
    body = (await client.get("/admin/providers/credentials", headers=admin_headers)).json()
    meta = next(row for row in body if row["provider"] == META)
    assert "meta_whatsapp_token" in meta["fields"]
    assert meta["derived_key"] is True, "no SECRETS_KEY in tests — the console must say so"


async def test_a_test_against_unconfigured_credentials_says_so_rather_than_failing(
    client: AsyncClient, admin_headers: dict
):
    """ "Not configured" and "the vendor rejected us" are different answers, and
    only one of them means retype the token."""
    result = (await client.post(f"/admin/providers/{META}/test", headers=admin_headers)).json()
    assert result["ok"] is False
    assert "not configured" in result["detail"]
    assert "meta_phone_number_id" in result["detail"]


async def test_the_test_button_reports_the_vendors_own_error(
    client: AsyncClient, session: AsyncSession, settings: Settings, admin_headers: dict, monkeypatch
):
    """The entire value of the button. "Meta: Session has expired" is actionable;
    "connection failed" costs an afternoon of retyping a correct token."""
    from app.providers import probe as probe_mod

    async def rejecting(kind: str, vendor: str, _settings):
        raise RuntimeError("Meta: Error validating access token: Session has expired")

    monkeypatch.setattr(probe_mod, "probe", rejecting)
    monkeypatch.setattr(admin_svc, "probe", rejecting)

    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=settings
    )
    result = (await client.post(f"/admin/providers/{META}/test", headers=admin_headers)).json()
    assert result["ok"] is False
    assert result["detail"] == "Meta: Error validating access token: Session has expired"

    # And it is remembered, so the console can show it later without re-testing.
    status = {s.provider: s for s in await admin_svc.provider_credentials(session, settings)}
    assert status[META].last_test["detail"] == result["detail"]


async def test_a_successful_test_is_recorded_on_the_row(
    client: AsyncClient, session: AsyncSession, settings: Settings, admin_headers: dict, monkeypatch
):
    async def accepting(kind: str, vendor: str, _settings):
        return "Reached WhatsApp number +91 98765 43210 (Alwar Cancer Centre)."

    monkeypatch.setattr(admin_svc, "probe", accepting)
    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=settings
    )
    result = (await client.post(f"/admin/providers/{META}/test", headers=admin_headers)).json()
    assert result["ok"] is True
    assert "Alwar Cancer Centre" in result["detail"]


async def test_each_cloud_voice_component_has_a_separate_test_result(
    client: AsyncClient,
    session: AsyncSession,
    settings: Settings,
    admin_headers: dict,
    monkeypatch,
):
    async def accepting(component: str, vendor: str, _settings):
        return f"{vendor} {component} model accepted"

    monkeypatch.setattr(admin_svc, "probe_voice_component", accepting)
    await admin_svc.save_provider_credentials(
        session,
        provider=OPENAI,
        values={"openai_api_key": "write-only"},
        settings=settings,
    )

    for component in ("stt", "llm", "tts"):
        response = await client.post(
            f"/admin/providers/{OPENAI}/test?component={component}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True

    status = {row.provider: row for row in await admin_svc.provider_credentials(session, settings)}[
        OPENAI
    ]
    assert set(status.last_test["components"]) == {"stt", "llm", "tts"}


async def test_vendor_error_cannot_echo_a_cloud_key_through_the_api(
    client: AsyncClient,
    session: AsyncSession,
    settings: Settings,
    admin_headers: dict,
    monkeypatch,
):
    secret = "must-never-come-back"

    async def rejecting(component: str, vendor: str, _settings):
        raise RuntimeError(f"{vendor} rejected API key {secret}")

    monkeypatch.setattr(admin_svc, "probe_voice_component", rejecting)
    await admin_svc.save_provider_credentials(
        session,
        provider=OPENAI,
        values={"openai_api_key": secret},
        settings=settings,
    )
    response = await client.post(
        f"/admin/providers/{OPENAI}/test?component=llm", headers=admin_headers
    )

    assert secret not in response.text
    assert "[redacted]" in response.json()["detail"]


# -- no restart ---------------------------------------------------------------


async def test_changed_credentials_rebuild_the_provider_rather_than_reusing_it(
    session: AsyncSession, settings: Settings, providers: None
):
    """The "no restart" claim, at the one place it could quietly fail: a cached
    provider instance still holding the old token."""
    reset_providers()
    meta_settings = settings.model_copy(
        update={
            "messaging_provider": "meta",
            "meta_whatsapp_token": "first",
            "meta_phone_number_id": "1",
        }
    )
    first = get_messaging_provider(meta_settings)

    await admin_svc.save_provider_credentials(
        session,
        provider=META,
        values={"meta_whatsapp_token": "second", "meta_phone_number_id": "2"},
        settings=meta_settings,
    )
    effective = await runtime.effective_settings(session, meta_settings)
    second = get_messaging_provider(effective)

    assert second is not first, "the provider still holds the credentials that were replaced"


async def test_changed_shared_key_rebuilds_all_profile_components(
    session: AsyncSession, settings: Settings, providers: None
):
    reset_providers()
    snapshot = snapshot_profile("openai_cloud", settings)
    await admin_svc.save_provider_credentials(
        session,
        provider=OPENAI,
        values={"openai_api_key": "first"},
        settings=settings,
    )
    first = resolve_profile(snapshot, await runtime.effective_settings(session, settings))

    await admin_svc.save_provider_credentials(
        session,
        provider=OPENAI,
        values={"openai_api_key": "second"},
        settings=settings,
    )
    second = resolve_profile(snapshot, await runtime.effective_settings(session, settings))

    assert all(
        before is not after
        for before, after in zip(
            (*first.stt, *first.llm, *first.tts),
            (*second.stt, *second.llm, *second.tts),
            strict=True,
        )
    )


async def test_the_overlay_cache_is_dropped_when_credentials_are_saved(
    session: AsyncSession, settings: Settings
):
    """An admin presses "test" immediately after "save"; the test must not read a
    ten-second-old overlay from before the save."""
    await runtime.overlay(session, settings)  # warm the cache with nothing in it
    await admin_svc.save_provider_credentials(
        session, provider=META, values=META_VALUES, settings=settings
    )
    assert (await runtime.overlay(session, settings))["meta_whatsapp_token"] == (
        META_VALUES["meta_whatsapp_token"]
    )
