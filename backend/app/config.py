"""Application settings, loaded from environment (12-factor)."""

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Every provider interface, and the setting that picks its implementation.
#: `assert_production_safe` walks this, so a new provider kind cannot be
#: deployed still pointing at its fake just because someone forgot a check.
PROVIDER_SETTINGS: tuple[str, ...] = (
    "sms_provider",
    "llm_provider",
    "stt_provider",
    "tts_provider",
    "realtime_provider",
    "messaging_provider",
    "telephony_provider",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    log_level: str = "info"

    database_url: str = "postgresql+asyncpg://opd:opd_local_dev@postgres:5432/opd"
    redis_url: str = "redis://redis:6379/0"

    # --- Auth (S2) -----------------------------------------------------------
    # Local-only default. S19 injects a real secret on the box; `assert_production_safe`
    # below refuses to boot a non-local env that is still using this value.
    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 7

    # --- OTP (S2) ------------------------------------------------------------
    otp_ttl_seconds: int = 300
    otp_length: int = 6
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 30
    # Lets the dev/test kiosk log in without reading logs. Never set outside local.
    otp_debug_echo: bool = False

    # --- Provider selection (S3) ---------------------------------------------
    # Every one of these is a config-only swap (doc 02 §9). `fake` is the
    # deterministic in-process impl; anything else names a vendor.
    sms_provider: str = "fake"  # fake | msg91 | exotel
    llm_provider: str = "fake"  # fake | gemini | openai
    stt_provider: str = "fake"  # fake | sarvam | google
    tts_provider: str = "fake"  # fake | sarvam | google
    realtime_provider: str = "fake"  # fake | gemini-live (S5/S14)
    messaging_provider: str = "fake"  # fake | meta
    telephony_provider: str = "fake"  # fake | exotel

    # Second choice per doc 02 §2's fallback chains. Empty = no fallback, and the
    # tier ladder handles the outage instead.
    llm_fallback_provider: str = ""  # openai
    stt_fallback_provider: str = ""  # google
    tts_fallback_provider: str = ""  # google

    # --- Provider credentials (S3) -------------------------------------------
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_live_model: str = "gemini-live-2.5-flash-preview"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saarika:v2.5"
    sarvam_tts_model: str = "bulbul:v2"
    sarvam_tts_voice: str = "anushka"
    google_api_key: str = ""
    google_tts_voice: str = ""

    # MSG91 — one of the two SMS options (see app/providers/sms.py).
    msg91_key: str = ""
    msg91_sender_id: str = "OPDALW"
    # DLT template ids, keyed by our template_key. JSON in the env:
    #   MSG91_TEMPLATE_IDS='{"otp_login": "64f1..."}'
    msg91_template_ids: dict[str, str] = {}

    # Exotel — telephony always; SMS only if it wins the SMS decision.
    exotel_sid: str = ""
    exotel_api_key: str = ""
    exotel_token: str = ""
    exotel_subdomain: str = "api.exotel.com"
    exotel_caller_id: str = ""
    exotel_sms_sender_id: str = "OPDALW"
    exotel_dlt_entity_id: str = ""
    exotel_dlt_template_ids: dict[str, str] = {}

    # Meta WhatsApp Cloud API.
    meta_whatsapp_token: str = ""
    meta_phone_number_id: str = ""

    # --- Cost guard (S3, doc 02 §8) ------------------------------------------
    cost_guard_enabled: bool = True
    # Daily spend cap in rupees, per channel. JSON in the env:
    #   DAILY_BUDGET_INR='{"phone": "2000", "kiosk": "500"}'
    # A channel with no cap is uncapped — deliberate, so a missing key never
    # silently throttles a channel nobody budgeted for.
    daily_budget_inr: dict[str, Decimal] = {}
    # Alert at 80% of cap (doc 02 §8), downgrade at 100%.
    cost_guard_alert_fraction: float = 0.8
    # How long a breach-driven downgrade sticks before re-evaluating. Budgets are
    # daily, so this only bounds how fast we recover after an admin raises a cap.
    cost_guard_override_ttl_seconds: int = 900
    # Cost guard reads spend since local midnight; the OPD's day is IST.
    timezone: str = "Asia/Kolkata"

    @property
    def is_local(self) -> bool:
        return self.env in {"local", "test"}

    def assert_production_safe(self) -> None:
        """Fail fast rather than serve production traffic with dev-only secrets."""
        if self.is_local:
            return
        problems = []
        if self.jwt_secret == Settings.model_fields["jwt_secret"].default:
            problems.append("JWT_SECRET is still the dev default")
        # RFC 7518 §3.2: an HMAC key shorter than the hash output weakens HS256.
        if len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be at least 32 characters")
        if self.otp_debug_echo:
            problems.append("OTP_DEBUG_ECHO must be off outside local")
        # A fake provider outside local is not a degraded mode — it is an OTP
        # that never arrives, or an intake that answers itself. Refuse to boot.
        problems += [
            f"{name.upper()} is still 'fake'"
            for name in PROVIDER_SETTINGS
            if getattr(self, name) == "fake"
        ]
        if problems:
            raise RuntimeError(f"unsafe config for env={self.env}: {'; '.join(problems)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
