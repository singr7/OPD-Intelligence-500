"""Config guards: a non-local environment must not boot with dev-only settings.

These are cheap tests for an expensive mistake — dev defaults reaching the pilot
box would mean forgeable JWTs, OTP codes echoed in API responses, and a fake SMS
provider silently swallowing every login code.
"""

from __future__ import annotations

import pytest

from app.config import Settings


def _prod(**overrides: object) -> Settings:
    """A production config with nothing dev-only left in it.

    Every provider is named explicitly because S3 made *all* of them
    production-checked, not just SMS: a fake LLM on the box is an intake that
    answers itself, and a fake telephony provider is a D-1 campaign that calls
    nobody. `realtime` is the exception — see below.
    """
    base = {
        "env": "production",
        "jwt_secret": "a-real-secret-of-at-least-32-characters",
        "otp_debug_echo": False,
        "sms_provider": "msg91",
        "llm_provider": "gemini",
        "stt_provider": "sarvam",
        "tts_provider": "sarvam",
        # Not a real vendor yet: the Gemini Live impl lands in S5/S14, so today
        # a production box genuinely cannot run tier V1. This value is what will
        # fail `assert_production_safe` at S19 deploy time if that is still true
        # — which is the correct outcome, not a gap in the test.
        "realtime_provider": "gemini-live",
        "messaging_provider": "meta",
        "telephony_provider": "exotel",
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_local_env_tolerates_dev_defaults() -> None:
    Settings(env="local").assert_production_safe()  # must not raise


def test_production_rejects_the_default_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _prod(jwt_secret=Settings.model_fields["jwt_secret"].default).assert_production_safe()


def test_production_rejects_a_short_jwt_secret() -> None:
    with pytest.raises(RuntimeError, match="at least 32"):
        _prod(jwt_secret="too-short").assert_production_safe()


def test_production_rejects_otp_debug_echo() -> None:
    """Echoing the OTP in the response would hand every account to any caller."""
    with pytest.raises(RuntimeError, match="OTP_DEBUG_ECHO"):
        _prod(otp_debug_echo=True).assert_production_safe()


def test_production_rejects_the_fake_sms_provider() -> None:
    with pytest.raises(RuntimeError, match="SMS_PROVIDER"):
        _prod(sms_provider="fake").assert_production_safe()


@pytest.mark.parametrize(
    "setting",
    ["llm_provider", "stt_provider", "tts_provider", "messaging_provider", "telephony_provider"],
)
def test_production_rejects_any_fake_provider(setting: str) -> None:
    """S3 extended the fake check from SMS to every provider. A fake outside
    local is not a degraded mode — it is an intake that answers itself."""
    with pytest.raises(RuntimeError, match=setting.upper()):
        _prod(**{setting: "fake"}).assert_production_safe()


def test_a_safe_production_config_passes() -> None:
    _prod().assert_production_safe()  # must not raise


def test_every_problem_is_reported_at_once() -> None:
    """Fixing one misconfiguration only to hit the next is a bad night."""
    with pytest.raises(RuntimeError) as exc:
        _prod(
            jwt_secret=Settings.model_fields["jwt_secret"].default,
            otp_debug_echo=True,
            sms_provider="fake",
        ).assert_production_safe()

    message = str(exc.value)
    assert "JWT_SECRET" in message
    assert "OTP_DEBUG_ECHO" in message
    assert "SMS_PROVIDER" in message
