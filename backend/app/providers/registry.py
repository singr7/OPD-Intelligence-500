"""Provider selection, driven entirely by config (doc 02 §9: "provider swap is
config-only").

S3 generalises this into the full registry with health tracking and the
metering decorator; the shape here is deliberately the one it will grow into.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.sms import FakeSMSProvider, SMSProvider

_sms_provider: SMSProvider | None = None


def _build_sms_provider(settings: Settings) -> SMSProvider:
    if settings.sms_provider == "fake":
        return FakeSMSProvider(log_body=settings.otp_debug_echo)
    # MSG91 / Exotel SMS land in S3 (the pick is an open decision in HANDOFF).
    # Failing loudly beats silently falling back to the fake and dropping real OTPs.
    raise ValueError(f"unknown SMS_PROVIDER={settings.sms_provider!r}; only 'fake' exists until S3")


def get_sms_provider(settings: Settings | None = None) -> SMSProvider:
    """Process-wide singleton — the fake accumulates sends, so tests can inspect it."""
    global _sms_provider
    if _sms_provider is None:
        _sms_provider = _build_sms_provider(settings or get_settings())
    return _sms_provider


def reset_providers() -> None:
    """Drop cached providers. Test fixtures use this for isolation between tests."""
    global _sms_provider
    _sms_provider = None
