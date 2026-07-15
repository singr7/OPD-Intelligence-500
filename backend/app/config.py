"""Application settings, loaded from environment (12-factor)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # `fake` logs the OTP and never sends an SMS. Real impls land in S3.
    sms_provider: str = "fake"
    # Lets the dev/test kiosk log in without reading logs. Never set outside local.
    otp_debug_echo: bool = False

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
        if self.otp_debug_echo:
            problems.append("OTP_DEBUG_ECHO must be off outside local")
        if self.sms_provider == "fake":
            problems.append("SMS_PROVIDER is still 'fake'")
        if problems:
            raise RuntimeError(f"unsafe config for env={self.env}: {'; '.join(problems)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
