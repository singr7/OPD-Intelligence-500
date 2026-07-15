"""Registry, config-only swapping, and `/providers/health` (doc 02 §9).

Two S3 acceptance criteria live here: **provider swap is config-only** and
**`providers/health` reports each provider**.

The first is the one worth being pedantic about. Doc 02 §9 justifies the whole
provider layer on the claim that swapping a vendor is cheap; the pilot is
currently *relying* on that claim to defer the MSG91-vs-Exotel decision. A test
that only checked "the registry returns something" would let that rot quietly.
"""

from __future__ import annotations

import pytest

from app.config import PROVIDER_SETTINGS, Settings
from app.providers.llm import GeminiFlashProvider, OpenAIProvider
from app.providers.registry import (
    UnknownProvider,
    all_providers,
    get_llm_provider,
    get_sms_provider,
    llm_chain,
    reset_providers,
    stt_chain,
)
from app.providers.sms import ExotelSMSProvider, FakeSMSProvider, Msg91SMSProvider
from app.providers.stt import GoogleSTTProvider, SarvamSTTProvider

pytestmark = pytest.mark.usefixtures("providers")


def _settings(**overrides) -> Settings:
    base = dict(env="test", jwt_secret="x" * 40)
    return Settings(**(base | overrides))


# -- the config-only promise ---------------------------------------------------


def test_swapping_the_sms_vendor_is_config_only():
    """The pilot's open decision (MSG91 vs Exotel SMS) must stay a config change.

    Same call site, same interface, three different vendors — chosen by one env
    var. This is the test that keeps the decision deferrable.
    """
    for name, expected in [
        ("fake", FakeSMSProvider),
        ("msg91", Msg91SMSProvider),
        ("exotel", ExotelSMSProvider),
    ]:
        reset_providers()
        provider = get_sms_provider(_settings(sms_provider=name, msg91_key="k", exotel_sid="s"))
        assert isinstance(provider, expected)
        assert provider.kind == "sms"


def test_swapping_the_llm_vendor_is_config_only():
    for name, expected in [("gemini", GeminiFlashProvider), ("openai", OpenAIProvider)]:
        reset_providers()
        assert isinstance(get_llm_provider(_settings(llm_provider=name)), expected)


def test_unknown_provider_name_raises_instead_of_falling_back_to_the_fake():
    """A typo'd SMS_PROVIDER that silently became the fake would be an OTP that
    never arrives and a login nobody can debug. Fail at boot instead."""
    with pytest.raises(UnknownProvider, match="msg9l"):
        get_sms_provider(_settings(sms_provider="msg9l"))  # l, not 1


def test_realtime_gemini_live_refuses_rather_than_pretending():
    """The Live impl lands in S5/S14. Config naming it today would promise a tier
    that cannot run — better a boot error than a silent V1 that never connects."""
    with pytest.raises(UnknownProvider, match="S5/S14"):
        get_llm_provider(_settings())  # warm the registry
        from app.providers.registry import get_realtime_provider

        get_realtime_provider(_settings(realtime_provider="gemini-live"))


def test_providers_are_singletons_per_vendor():
    """Health and breaker state live on the instance; handing out a fresh one per
    call would reset the breaker on every request and never trip."""
    settings = _settings()
    assert get_sms_provider(settings) is get_sms_provider(settings)


def test_fallback_chain_keeps_separate_instances():
    """Primary and fallback must not share a breaker, or the fallback's outage
    reads as the primary's and takes both down."""
    chain = llm_chain(_settings(llm_provider="gemini", llm_fallback_provider="openai"))
    assert [type(p) for p in chain] == [GeminiFlashProvider, OpenAIProvider]
    assert chain[0].breaker is not chain[1].breaker


def test_stt_chain_is_sarvam_then_google():
    """Doc 02 §2's chain, as config."""
    chain = stt_chain(_settings(stt_provider="sarvam", stt_fallback_provider="google"))
    assert [type(p) for p in chain] == [SarvamSTTProvider, GoogleSTTProvider]


def test_chain_without_a_fallback_is_just_the_primary():
    assert len(llm_chain(_settings(llm_provider="gemini"))) == 1


def test_fallback_equal_to_primary_is_not_duplicated():
    """Retrying the same dead provider twice is not a fallback."""
    assert len(llm_chain(_settings(llm_provider="openai", llm_fallback_provider="openai"))) == 1


def test_all_providers_covers_every_interface_and_its_fallbacks():
    providers = all_providers(
        _settings(
            llm_provider="gemini",
            llm_fallback_provider="openai",
            stt_provider="sarvam",
            stt_fallback_provider="google",
        )
    )
    kinds = [p.kind for p in providers]
    assert set(kinds) == {"sms", "llm", "stt", "tts", "realtime", "messaging", "telephony"}
    assert kinds.count("llm") == 2  # primary + fallback
    assert kinds.count("stt") == 2


# -- production safety ---------------------------------------------------------


def test_production_refuses_to_boot_on_any_fake_provider():
    """A fake outside local is not a degraded mode — it is an OTP that never
    sends and an intake that answers itself."""
    settings = Settings(env="prod", jwt_secret="x" * 40, sms_provider="msg91")
    with pytest.raises(RuntimeError) as exc:
        settings.assert_production_safe()

    problems = str(exc.value)
    assert "SMS_PROVIDER" not in problems  # this one was set to a real vendor
    assert "LLM_PROVIDER is still 'fake'" in problems
    assert "TELEPHONY_PROVIDER is still 'fake'" in problems


def test_every_provider_setting_is_checked_for_production_safety():
    """`PROVIDER_SETTINGS` is walked by `assert_production_safe`, so a new
    interface cannot ship to the box still pointing at its fake just because
    someone forgot to add a check."""
    settings = Settings(env="prod", jwt_secret="x" * 40)
    with pytest.raises(RuntimeError) as exc:
        settings.assert_production_safe()

    for name in PROVIDER_SETTINGS:
        assert name.upper() in str(exc.value), f"{name} is not production-checked"


def test_local_env_is_allowed_to_use_fakes():
    Settings(env="local").assert_production_safe()  # must not raise


# -- /providers/health ---------------------------------------------------------


async def test_health_reports_every_provider(client):
    """S3 AC: "providers/health reports each provider"."""
    response = await client.get("/providers/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert {p["kind"] for p in body["providers"]} == {
        "sms",
        "llm",
        "stt",
        "tts",
        "realtime",
        "messaging",
        "telephony",
    }
    assert body["unpriced"] == []


async def test_health_reports_a_degraded_provider(client, settings, meter):
    """What the coordinator's banner (S8) and an on-call human actually need:
    not "the process is up" but "can we still run an intake"."""
    from app.providers.llm import LLMRequest
    from app.providers.resilience import ProviderUnavailable, RetryPolicy

    llm = get_llm_provider(settings)
    llm.retry = RetryPolicy(attempts=1)
    llm.fail_with = RuntimeError("gemini is down")
    with pytest.raises(ProviderUnavailable):
        await llm.complete(LLMRequest(prompt="x"))

    body = (await client.get("/providers/health")).json()
    entry = next(p for p in body["providers"] if p["kind"] == "llm")

    assert entry["status"] == "degraded"
    assert entry["failures"] == 1
    assert body["status"] == "degraded"  # worst provider wins


async def test_health_never_leaks_credentials(client, settings):
    """Unauthenticated endpoint. It reports vendor names and health, and must
    stay boring enough to deserve that."""
    body = (await client.get("/providers/health")).text
    for secret in ("api_key", "authkey", "Bearer", settings.jwt_secret):
        assert secret not in body


async def test_health_surfaces_unpriced_usage(client, session, meter, prices):
    """An unpriced provider reports ₹0 forever, which the cost-guard reads as
    "budget to spare". That silence is exactly what this field breaks."""
    from app.providers.llm import FakeLLMProvider, LLMRequest

    class _UnpricedProvider(FakeLLMProvider):
        name = "vendor-nobody-priced"

    await _UnpricedProvider().complete(LLMRequest(prompt="x"))
    await meter.flush()

    body = (await client.get("/providers/health")).json()
    assert any(u["provider"] == "vendor-nobody-priced" for u in body["unpriced"])
