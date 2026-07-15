"""Provider layer (doc 02 §9) — every external dependency behind an interface.

The hard rule: feature code never imports a vendor SDK, and every provider
wrapper meters usage. Both are structural rather than reviewed — `base.Provider`
makes metering the only way to make a call, and `registry` is the only place a
vendor is named.

    from app.providers import get_llm_provider, usage_scope

    with usage_scope(intake_id=intake.id, channel=Channel.KIOSK, tier=tier):
        result = await get_llm_provider().complete(request)   # metered, priced

Layout:
  base        Provider, health, fallback chains
  metering    usage_events recording (async, batched) + the attribution scope
  pricing     price_book lookup + cost computation
  resilience  retry, circuit breaker, the ProviderUnavailable signal
  costguard   daily budget caps -> tier downgrade
  registry    config -> instances (the "swap is config-only" promise)
  sms · llm · stt · tts · realtime · messaging · telephony · audio
"""

from app.providers.audio import AudioClip, bcp47
from app.providers.base import (
    Provider,
    ProviderBadRequest,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
    with_fallback,
)
from app.providers.costguard import CostGuard, Verdict, downgrade, get_guard
from app.providers.llm import (
    FakeLLMProvider,
    LLMProvider,
    LLMRequest,
    LLMResult,
    ToolCall,
)
from app.providers.messaging import (
    Button,
    FakeMessagingProvider,
    MessagingProvider,
    OutboundMessage,
)
from app.providers.metering import (
    UsageDelta,
    UsageMeter,
    current_context,
    usage_scope,
)
from app.providers.pricing import PriceBookCache, get_price_book
from app.providers.realtime import (
    FakeRealtimeProvider,
    RealtimeConfig,
    RealtimeEvent,
    RealtimeSession,
    RealtimeVoiceProvider,
)
from app.providers.registry import (
    all_providers,
    get_llm_provider,
    get_messaging_provider,
    get_realtime_provider,
    get_sms_provider,
    get_stt_provider,
    get_telephony_provider,
    get_tts_provider,
    llm_chain,
    reset_providers,
    stt_chain,
    tts_chain,
)
from app.providers.resilience import CircuitBreaker, RetryPolicy
from app.providers.sms import (
    ExotelSMSProvider,
    FakeSMSProvider,
    Msg91SMSProvider,
    SmsMessage,
    SMSProvider,
    SmsResult,
    SmsSendError,
)
from app.providers.stt import FakeSTTProvider, STTProvider, Transcript
from app.providers.telephony import (
    CallHandle,
    CallRequest,
    CallState,
    FakeTelephonyProvider,
    TelephonyProvider,
)
from app.providers.tts import FakeTTSProvider, Speech, TTSProvider

__all__ = [
    "AudioClip",
    "Button",
    "CallHandle",
    "CallRequest",
    "CallState",
    "CircuitBreaker",
    "CostGuard",
    "ExotelSMSProvider",
    "FakeLLMProvider",
    "FakeMessagingProvider",
    "FakeRealtimeProvider",
    "FakeSMSProvider",
    "FakeSTTProvider",
    "FakeTTSProvider",
    "FakeTelephonyProvider",
    "LLMProvider",
    "LLMRequest",
    "LLMResult",
    "MessagingProvider",
    "Msg91SMSProvider",
    "OutboundMessage",
    "PriceBookCache",
    "Provider",
    "ProviderBadRequest",
    "ProviderError",
    "ProviderHealth",
    "ProviderUnavailable",
    "RealtimeConfig",
    "RealtimeEvent",
    "RealtimeSession",
    "RealtimeVoiceProvider",
    "RetryPolicy",
    "SMSProvider",
    "SmsMessage",
    "SmsResult",
    "SmsSendError",
    "Speech",
    "STTProvider",
    "TTSProvider",
    "TelephonyProvider",
    "ToolCall",
    "Transcript",
    "UsageDelta",
    "UsageMeter",
    "Verdict",
    "all_providers",
    "bcp47",
    "current_context",
    "downgrade",
    "get_guard",
    "get_llm_provider",
    "get_messaging_provider",
    "get_price_book",
    "get_realtime_provider",
    "get_sms_provider",
    "get_stt_provider",
    "get_telephony_provider",
    "get_tts_provider",
    "llm_chain",
    "reset_providers",
    "stt_chain",
    "tts_chain",
    "usage_scope",
    "with_fallback",
]
