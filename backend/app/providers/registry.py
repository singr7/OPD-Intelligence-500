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

import hashlib
import logging
from typing import TYPE_CHECKING

from fastapi import Depends

from app.config import Settings, get_settings
from app.providers.base import Provider
from app.providers.llm import (
    FakeLLMProvider,
    GeminiFlashProvider,
    LLMProvider,
    OpenAIProvider,
    SarvamLLMProvider,
)
from app.providers.local_oss import (
    LocalLLMProvider,
    LocalSTTProvider,
    LocalTTSProvider,
    VoiceboxTTSProvider,
)
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
from app.providers.stt import (
    FakeSTTProvider,
    GoogleSTTProvider,
    OpenAISTTProvider,
    SarvamSTTProvider,
    STTProvider,
)
from app.providers.telephony import (
    ExotelTelephonyProvider,
    FakeTelephonyProvider,
    TelephonyProvider,
)
from app.providers.tts import (
    FakeTTSProvider,
    GoogleTTSProvider,
    OpenAITTSProvider,
    SarvamTTSProvider,
    TTSProvider,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.providers.profiles import VoiceComponent


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
        case "sarvam":
            return SarvamLLMProvider(
                api_key=settings.sarvam_api_key, model=settings.sarvam_llm_model
            )
        case "local_vllm":
            return LocalLLMProvider(
                base_url=settings.local_vllm_base_url,
                model=settings.local_vllm_model,
                api_key=settings.local_vllm_api_key,
            )
    raise UnknownProvider(f"LLM_PROVIDER={name!r}; expected fake|gemini|openai|sarvam|local_vllm")


def _build_stt(name: str, settings: Settings) -> STTProvider:
    match name:
        case "fake":
            return FakeSTTProvider()
        case "sarvam":
            return SarvamSTTProvider(
                api_key=settings.sarvam_api_key, model=settings.sarvam_stt_model
            )
        case "openai":
            return OpenAISTTProvider(
                api_key=settings.openai_api_key, model=settings.openai_stt_model
            )
        case "google":
            return GoogleSTTProvider(api_key=settings.google_api_key)
        case "local_whisper":
            return LocalSTTProvider(base_url=settings.local_stt_url, model=settings.local_stt_model)
    raise UnknownProvider(
        f"STT_PROVIDER={name!r}; expected fake|openai|sarvam|google|local_whisper"
    )


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
        case "openai":
            return OpenAITTSProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_tts_model,
                voice=settings.openai_tts_voice,
            )
        case "google":
            return GoogleTTSProvider(
                api_key=settings.google_api_key, voice=settings.google_tts_voice or None
            )
        case "local_tts":
            return LocalTTSProvider(
                base_url=settings.local_tts_url,
                model=settings.local_tts_model or None,
                voice=settings.local_tts_voice or None,
            )
        case "voicebox":
            return VoiceboxTTSProvider(
                base_url=settings.voicebox_url, voice=settings.voicebox_voice or None
            )
    raise UnknownProvider(
        f"TTS_PROVIDER={name!r}; expected fake|openai|sarvam|google|local_tts|voicebox"
    )


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
        case "local-pipecat":
            # LocalPipelineVoiceProvider — the Pipecat per-session pipeline with
            # Silero VAD + smart-turn endpointing (doc 08 §2) — needs the GPU box
            # and Pipecat serving. That is the S-OSS.2 GPU half; naming it here
            # today would promise a tier that cannot run, so refuse, exactly like
            # gemini-live. V-OSS voice runs as V2-with-local-providers until then.
            raise UnknownProvider(
                "REALTIME_PROVIDER=local-pipecat needs the GPU box + Pipecat (S-OSS.2, doc 08 §6); "
                "use local_vllm/local_whisper/local_tts for the V-OSS pipeline, or 'fake'"
            )
    raise UnknownProvider(f"REALTIME_PROVIDER={name!r}; expected fake|gemini-live|local-pipecat")


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

#: The credentials each cached instance was built from, so a console that changes
#: them (S-GL.1) gets a rebuilt provider rather than one still holding the old
#: token. Fingerprints, not values — this dict must not become a second place
#: credentials live.
_fingerprints: dict[tuple[str, str], str] = {}

#: Keys a fixture pinned with `install()`. Their fingerprints are never checked:
#: a test that handed us a specific fake must keep getting that fake.
_pinned: set[tuple[str, str]] = set()

# Profile-bound providers are keyed by the exact snapshotted model as well as
# interface and vendor. A later config publish may change the model for new
# sessions; an intake already in flight must keep the instance built for its
# snapshot.
_profile_instances: dict[tuple[str, str, str, str], Provider] = {}


def _fingerprint(kind: str, name: str, settings: Settings) -> str:
    """A short digest of the credential fields this provider is built from.

    Only the fields the runtime overlay may write (`app.providers.runtime`), so an
    unrelated settings change does not churn every provider in the process.
    """
    from app.providers.runtime import CREDENTIAL_FIELDS

    fields = CREDENTIAL_FIELDS.get(f"{kind}:{name}")
    if not fields:
        return ""
    joined = "\x00".join(str(getattr(settings, field, "")) for field in fields)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _get(kind: str, settings: Settings | None = None, *, name: str | None = None) -> Provider:
    settings = settings or get_settings()
    build, setting = _BUILDERS[kind]
    chosen = name or getattr(settings, setting)
    key = (kind, chosen)
    fingerprint = "" if key in _pinned else _fingerprint(kind, chosen, settings)
    if key not in _instances or _fingerprints.get(key, "") != fingerprint:
        if key in _instances:
            logger.info("provider %s -> %s rebuilt: credentials changed", kind, chosen)
        else:
            logger.info("provider %s -> %s", kind, chosen)
        _instances[key] = build(chosen, settings)
        _fingerprints[key] = fingerprint
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

_PROFILE_MODEL_FIELDS: dict[tuple[str, str], str] = {
    ("llm", "local_vllm"): "local_vllm_model",
    ("llm", "openai"): "openai_model",
    ("llm", "sarvam"): "sarvam_llm_model",
    ("stt", "local_whisper"): "local_stt_model",
    ("stt", "openai"): "openai_stt_model",
    ("stt", "sarvam"): "sarvam_stt_model",
    ("tts", "local_tts"): "local_tts_model",
    ("tts", "voicebox"): "voicebox_voice",
    ("tts", "openai"): "openai_tts_model",
    ("tts", "sarvam"): "sarvam_tts_model",
}


def get_profile_component(
    kind: str, component: VoiceComponent, settings: Settings | None = None
) -> Provider:
    """Build one component from an immutable profile snapshot.

    This is deliberately separate from the process-wide primary/fallback chain:
    those settings may change after an intake starts. The exact model participates
    in the cache key, and no generic cross-vendor fallback is appended.
    """
    settings = settings or get_settings()
    field = _PROFILE_MODEL_FIELDS.get((kind, component.provider))
    if field is None:
        raise UnknownProvider(
            f"voice profile {kind} component {component.provider!r} is not approved"
        )
    if not component.model:
        raise UnknownProvider(f"voice profile {kind} model must not be empty")

    exact = settings.model_copy(update={field: component.model})
    fingerprint = _fingerprint(kind, component.provider, exact)
    key = (kind, component.provider, component.model, fingerprint)
    if key not in _profile_instances:
        build, _ = _BUILDERS[kind]
        _profile_instances[key] = build(component.provider, exact)
    return _profile_instances[key]


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
    _fingerprints.clear()
    _pinned.clear()
    _profile_instances.clear()


def install(kind: str, provider: Provider, *, name: str | None = None) -> None:
    """Force a specific instance in — for fixtures that need a handle on the fake."""
    key = (kind, name or provider.name)
    _instances[key] = provider
    _fingerprints[key] = ""
    _pinned.add(key)
