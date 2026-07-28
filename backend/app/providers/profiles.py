"""Typed kiosk voice profiles and their immutable per-intake snapshot.

The profile name is operator configuration; the resolved provider/model tuple is
session data.  Keeping those separate is what makes a publish affect new
intakes without moving a patient who is already mid-question to another vendor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from app.providers.llm import LLMProvider
    from app.providers.stt import STTProvider
    from app.providers.tts import TTSProvider


class VoiceProfileName(StrEnum):
    LOCAL_OSS = "local_oss"
    OPENAI_CLOUD = "openai_cloud"
    SARVAM_CLOUD = "sarvam_cloud"


class VoiceProfileError(ValueError):
    """An unknown or internally inconsistent profile selection."""


@dataclass(frozen=True, slots=True)
class VoiceComponent:
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class VoiceProfileSnapshot:
    name: VoiceProfileName
    stt: VoiceComponent
    llm: VoiceComponent
    tts: VoiceComponent

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> VoiceProfileSnapshot:
        try:
            return cls(
                name=VoiceProfileName(data["name"]),
                stt=VoiceComponent(**data["stt"]),
                llm=VoiceComponent(**data["llm"]),
                tts=VoiceComponent(**data["tts"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VoiceProfileError("invalid snapshotted kiosk voice profile") from exc


@dataclass(frozen=True, slots=True)
class VoiceProviderTrio:
    """The only provider assembly feature code receives for a kiosk profile."""

    profile: VoiceProfileSnapshot
    stt: tuple[STTProvider, ...]
    llm: tuple[LLMProvider, ...]
    tts: tuple[TTSProvider, ...]


def profile_name(value: str | VoiceProfileName) -> VoiceProfileName:
    try:
        return VoiceProfileName(value)
    except ValueError as exc:
        raise VoiceProfileError(
            f"unknown kiosk voice profile {value!r}; "
            f"expected one of {[profile.value for profile in VoiceProfileName]}"
        ) from exc


def snapshot_profile(value: str | VoiceProfileName, settings: Settings) -> VoiceProfileSnapshot:
    """Resolve a selected name to exact providers/models once, at intake start."""
    selected = profile_name(value)
    if selected is VoiceProfileName.LOCAL_OSS:
        tts_provider = settings.kiosk_local_tts_provider
        if tts_provider not in {"local_tts", "voicebox"}:
            raise VoiceProfileError("KIOSK_LOCAL_TTS_PROVIDER must be local_tts or voicebox")
        tts_model = (
            settings.local_tts_model or settings.local_tts_voice
            if tts_provider == "local_tts"
            else settings.voicebox_voice
        )
        return VoiceProfileSnapshot(
            name=selected,
            stt=VoiceComponent("local_whisper", settings.local_stt_model),
            llm=VoiceComponent("local_vllm", settings.local_vllm_model),
            tts=VoiceComponent(tts_provider, tts_model),
        )
    if selected is VoiceProfileName.OPENAI_CLOUD:
        return VoiceProfileSnapshot(
            name=selected,
            stt=VoiceComponent("openai", settings.openai_stt_model),
            llm=VoiceComponent("openai", settings.openai_model),
            tts=VoiceComponent("openai", settings.openai_tts_model),
        )
    return VoiceProfileSnapshot(
        name=selected,
        stt=VoiceComponent("sarvam", settings.sarvam_stt_model),
        llm=VoiceComponent("sarvam", settings.sarvam_llm_model),
        tts=VoiceComponent("sarvam", settings.sarvam_tts_model),
    )


def resolve_profile(
    snapshot: VoiceProfileSnapshot, settings: Settings | None = None
) -> VoiceProviderTrio:
    """Resolve one immutable snapshot to its approved same-profile trio.

    Each tuple is a fallback chain. Today each approved profile has one exact
    provider per interface, so exhaustion returns to deterministic taps. Adding a
    fallback later requires adding it here to the same profile explicitly; the
    registry's generic OpenAI/Sarvam/Google fallbacks are never consulted.
    """
    from app.providers.registry import get_profile_component

    if settings is None:
        from app.providers.runtime import cached_effective_settings

        settings = cached_effective_settings(get_settings())
    return VoiceProviderTrio(
        profile=snapshot,
        stt=(get_profile_component("stt", snapshot.stt, settings),),  # type: ignore[arg-type]
        llm=(get_profile_component("llm", snapshot.llm, settings),),  # type: ignore[arg-type]
        tts=(get_profile_component("tts", snapshot.tts, settings),),  # type: ignore[arg-type]
    )
