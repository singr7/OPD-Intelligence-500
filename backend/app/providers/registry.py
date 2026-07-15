"""Provider selection, driven entirely by config (doc 02 §9: "provider swap is config-only").

This module is where that promise is kept or broken. Every provider is built
here, from `Settings`, and handed out as a singleton; feature code asks for an
interface and never learns which vendor answered. Swapping MSG91 for Exotel, or
Gemini for OpenAI, is an env var and a restart — no code change.

Two rules worth keeping:

- **Unknown provider name → raise.** Never fall back to the fake. A typo'd
  `SMS_PROVIDER` that silently becomes the fake is an OTP that never arrives and
  a login nobody can debug. Fail at boot, loudly, where it is cheap.
- **Fallback chains are config too** (doc 02 §2: Sarvam→Google, Gemini→OpenAI).
  `llm_chain()` returns the list; `base.with_fallback` walks it.
"""

from __future__ import annotations

import logging

from fastapi import Depends

from app.config import Settings, get_settings
from app.providers.base import Provider
from app.providers.llm import FakeLLMProvider, GeminiFlashProvider, LLMProvider, OpenAIProvider
from app.providers.messaging import (
    FakeMessagingProvider,
    MessagingProvider,
    MetaWhatsAppProvider,
)
from app.providers.realtime import FakeRealtimeProvider, RealtimeVoiceProvider
from app.providers.sms import (
    ExotelSMSProvider,
    FakeSMSProvider,
    Msg91SMSProvider,
    SMSProvider,
)
from app.providers.stt import FakeSTTProvider, GoogleSTTProvider, SarvamSTTProvider, STTProvider
from app.providers.telephony import (
    ExotelTelephonyProvider,
    FakeTelephonyProvider,
    TelephonyProvider,
)
from app.providers.tts import FakeTTSProvider, GoogleTTSProvider, SarvamTTSProvider, TTSProvider

logger = logging.getLogger(__name__)


class UnknownProvider(ValueError):
    """Config names a provider that does not exist. Raised at build time."""


# -- builders: one per interface, each a pure function of Settings -------------


def _build_sms(name: str, settings: Settings) -> SMSProvider:
    match name:
        case "fake":
            return FakeSMSProvider(log_body=settings.otp_debug_echo)
        case "msg91":
            return Msg91SMSProvider(
                auth_key=settings.msg91_key,
                sender_id=settings.msg91_sender_id,
                template_ids=settings.msg91_template_ids,
            )
        case "exotel":
            return ExotelSMSProvider(
                sid=settings.exotel_sid,
                api_key=settings.exotel_api_key,
                api_token=settings.exotel_token,
                sender_id=settings.exotel_sms_sender_id,
                subdomain=settings.exotel_subdomain,
                dlt_entity_id=settings.exotel_dlt_entity_id,
                dlt_template_ids=settings.exotel_dlt_template_ids,
            )
    raise UnknownProvider(f"SMS_PROVIDER={name!r}; expected fake|msg91|exotel")


def _build_llm(name: str, settings: Settings) -> LLMProvider:
    match name:
        case "fake":
            return FakeLLMProvider()
        case "gemini":
            return GeminiFlashProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)
        case "openai":
            return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)
    raise UnknownProvider(f"LLM_PROVIDER={name!r}; expected fake|gemini|openai")


def _build_stt(name: str, settings: Settings) -> STTProvider:
    match name:
        case "fake":
            return FakeSTTProvider()
        case "sarvam":
            return SarvamSTTProvider(
                api_key=settings.sarvam_api_key, model=settings.sarvam_stt_model
            )
        case "google":
            return GoogleSTTProvider(api_key=settings.google_api_key)
    raise UnknownProvider(f"STT_PROVIDER={name!r}; expected fake|sarvam|google")


def _build_tts(name: str, settings: Settings) -> TTSProvider:
    match name:
        case "fake":
            return FakeTTSProvider()
        case "sarvam":
            return SarvamTTSProvider(
                api_key=settings.sarvam_api_key,
                model=settings.sarvam_tts_model,
                voice=settings.sarvam_tts_voice,
            )
        case "google":
            return GoogleTTSProvider(
                api_key=settings.google_api_key, voice=settings.google_tts_voice or None
            )
    raise UnknownProvider(f"TTS_PROVIDER={name!r}; expected fake|sarvam|google")


def _build_realtime(name: str, settings: Settings) -> RealtimeVoiceProvider:
    match name:
        case "fake":
            return FakeRealtimeProvider()
        case "gemini-live":
            # The Live session manager is S5's build and the audio bridge S14's
            # (see app/providers/realtime.py). Naming it in config today would
            # promise a tier that cannot run, so refuse rather than pretend.
            raise UnknownProvider(
                "REALTIME_PROVIDER=gemini-live is not implemented yet (S5/S14); use 'fake'"
            )
    raise UnknownProvider(f"REALTIME_PROVIDER={name!r}; expected fake|gemini-live")


def _build_messaging(name: str, settings: Settings) -> MessagingProvider:
    match name:
        case "fake":
            return FakeMessagingProvider()
        case "meta":
            return MetaWhatsAppProvider(
                access_token=settings.meta_whatsapp_token,
                phone_number_id=settings.meta_phone_number_id,
            )
    raise UnknownProvider(f"MESSAGING_PROVIDER={name!r}; expected fake|meta")


def _build_telephony(name: str, settings: Settings) -> TelephonyProvider:
    match name:
        case "fake":
            return FakeTelephonyProvider()
        case "exotel":
            return ExotelTelephonyProvider(
                sid=settings.exotel_sid,
                api_key=settings.exotel_api_key,
                api_token=settings.exotel_token,
                caller_id=settings.exotel_caller_id,
                subdomain=settings.exotel_subdomain,
            )
    raise UnknownProvider(f"TELEPHONY_PROVIDER={name!r}; expected fake|exotel")


_BUILDERS = {
    "sms": (_build_sms, "sms_provider"),
    "llm": (_build_llm, "llm_provider"),
    "stt": (_build_stt, "stt_provider"),
    "tts": (_build_tts, "tts_provider"),
    "realtime": (_build_realtime, "realtime_provider"),
    "messaging": (_build_messaging, "messaging_provider"),
    "telephony": (_build_telephony, "telephony_provider"),
}

_FALLBACK_SETTING = {
    "llm": "llm_fallback_provider",
    "stt": "stt_fallback_provider",
    "tts": "tts_fallback_provider",
}

# Cached per (kind, vendor): a fallback chain wants two live instances of the
# same interface, and each must keep its own breaker and health — one shared
# instance would report the fallback's outage as the primary's.
_instances: dict[tuple[str, str], Provider] = {}


def _get(kind: str, settings: Settings | None = None, *, name: str | None = None) -> Provider:
    settings = settings or get_settings()
    build, setting = _BUILDERS[kind]
    chosen = name or getattr(settings, setting)
    key = (kind, chosen)
    if key not in _instances:
        _instances[key] = build(chosen, settings)
        logger.info("provider %s -> %s", kind, chosen)
    return _instances[key]


def _fallback_name(kind: str, settings: Settings) -> str:
    """The configured fallback, unless it is the primary (then there is none)."""
    fallback = getattr(settings, _FALLBACK_SETTING.get(kind, ""), "")
    primary = getattr(settings, _BUILDERS[kind][1])
    return fallback if fallback and fallback != primary else ""


def _chain(kind: str, settings: Settings | None = None) -> list[Provider]:
    settings = settings or get_settings()
    providers = [_get(kind, settings)]
    if fallback := _fallback_name(kind, settings):
        providers.append(_get(kind, settings, name=fallback))
    return providers


# -- public accessors ----------------------------------------------------------


def get_sms_provider(settings: Settings | None = None) -> SMSProvider:
    return _get("sms", settings)  # type: ignore[return-value]


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    return _get("llm", settings)  # type: ignore[return-value]


def get_stt_provider(settings: Settings | None = None) -> STTProvider:
    return _get("stt", settings)  # type: ignore[return-value]


def get_tts_provider(settings: Settings | None = None) -> TTSProvider:
    return _get("tts", settings)  # type: ignore[return-value]


def get_realtime_provider(settings: Settings | None = None) -> RealtimeVoiceProvider:
    return _get("realtime", settings)  # type: ignore[return-value]


def get_messaging_provider(settings: Settings | None = None) -> MessagingProvider:
    return _get("messaging", settings)  # type: ignore[return-value]


def get_telephony_provider(settings: Settings | None = None) -> TelephonyProvider:
    return _get("telephony", settings)  # type: ignore[return-value]


def llm_chain(settings: Settings | None = None) -> list[LLMProvider]:
    """Gemini Flash → OpenAI (doc 02 §2). Pass to `with_fallback`."""
    return _chain("llm", settings)  # type: ignore[return-value]


def stt_chain(settings: Settings | None = None) -> list[STTProvider]:
    """Sarvam → Google (doc 02 §2)."""
    return _chain("stt", settings)  # type: ignore[return-value]


def tts_chain(settings: Settings | None = None) -> list[TTSProvider]:
    """Sarvam → Google (doc 02 §2)."""
    return _chain("tts", settings)  # type: ignore[return-value]


def all_providers(settings: Settings | None = None) -> list[Provider]:
    """Every configured provider, primaries and fallbacks — what `/providers/health` walks.

    Builds any that are not built yet, so the endpoint reports the full
    configured surface rather than only what happens to have been used since boot.
    """
    settings = settings or get_settings()
    providers: list[Provider] = []
    for kind in _BUILDERS:
        providers.append(_get(kind, settings))
        if fallback := _fallback_name(kind, settings):
            providers.append(_get(kind, settings, name=fallback))
    return providers


# -- FastAPI dependencies ------------------------------------------------------
#
# Routes must depend on these, never on `get_*_provider` directly: FastAPI
# inspects a dependency's signature, sees `settings: Settings` (a pydantic model)
# and tries to parse it out of the request body — every call 422s.


def sms_provider_dependency(settings: Settings = Depends(get_settings)) -> SMSProvider:
    return get_sms_provider(settings)


def llm_provider_dependency(settings: Settings = Depends(get_settings)) -> LLMProvider:
    return get_llm_provider(settings)


def stt_provider_dependency(settings: Settings = Depends(get_settings)) -> STTProvider:
    return get_stt_provider(settings)


def tts_provider_dependency(settings: Settings = Depends(get_settings)) -> TTSProvider:
    return get_tts_provider(settings)


def realtime_provider_dependency(
    settings: Settings = Depends(get_settings),
) -> RealtimeVoiceProvider:
    return get_realtime_provider(settings)


def messaging_provider_dependency(settings: Settings = Depends(get_settings)) -> MessagingProvider:
    return get_messaging_provider(settings)


def telephony_provider_dependency(settings: Settings = Depends(get_settings)) -> TelephonyProvider:
    return get_telephony_provider(settings)


def reset_providers() -> None:
    """Drop cached providers. Test fixtures use this for isolation between tests."""
    _instances.clear()


def install(kind: str, provider: Provider, *, name: str | None = None) -> None:
    """Force a specific instance in — for fixtures that need a handle on the fake."""
    _instances[(kind, name or provider.name)] = provider
