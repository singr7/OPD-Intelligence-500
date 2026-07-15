"""Provider layer (doc 02 §9) — every external dependency behind an interface.

S2 only needs `SMSProvider` (for OTP). S3 fills in the rest — realtime voice,
LLM, STT, TTS, messaging, telephony — plus usage metering, retries, and the
health registry, and turns `get_sms_provider` into a general registry.
"""

from app.providers.registry import get_sms_provider, reset_providers
from app.providers.sms import (
    FakeSMSProvider,
    SmsMessage,
    SMSProvider,
    SmsResult,
    SmsSendError,
)

__all__ = [
    "SMSProvider",
    "FakeSMSProvider",
    "SmsMessage",
    "SmsResult",
    "SmsSendError",
    "get_sms_provider",
    "reset_providers",
]
