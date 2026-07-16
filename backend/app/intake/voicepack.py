"""V3 pre-recorded audio (doc 03 §1a) — the voice a kiosk plays with no AI.

> "V3 tier: identical UX but all prompts are pre-recorded human voice files per
> language, tree walked deterministically." — doc 03 §1a

Tier V3 speaks with recordings, not a live model, so it works offline and costs
nothing per turn. Each tree node names its clip in `node.audio[lang]`
(`app.trees.schema`); this module resolves that name to actual audio.

## Nothing is recorded yet — TTS covers the gap

Every authored node currently has an empty `audio` map (STATE.md → Stubs &
fakes): the real human recordings are S21's voice-artist session and the pack
format/generation is S7. Until then, `resolve` falls back to synthesising the
node's text with the TTS provider — the same words, a machine voice. That keeps
V3 runnable end to end today and gives S7/S21 a single seam (`VoicePack`) to drop
real clips into without touching the engine.

The manifest is a plain mapping so S7 can build it from a downloaded pack and S18
can manage it; a resolver that reached into the filesystem itself would not be
mockable and would tie the engine to a disk layout that has not been designed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import Lang
from app.providers import AudioClip, ProviderError, Speech, TTSProvider
from app.trees.schema import Node


@dataclass(frozen=True, slots=True)
class VoicePack:
    """Available pre-recorded clips, keyed by (clip name, language).

    `clip_name` is what a node's `audio[lang]` holds. Empty is valid and is the
    pilot's actual state — everything falls through to TTS.
    """

    clips: dict[tuple[str, str], AudioClip] = field(default_factory=dict)

    def get(self, clip_name: str, lang: Lang | str) -> AudioClip | None:
        return self.clips.get((clip_name, str(lang)))

    def has(self, clip_name: str, lang: Lang | str) -> bool:
        return (clip_name, str(lang)) in self.clips


EMPTY_PACK = VoicePack()


async def resolve(
    node: Node,
    lang: Lang | str,
    *,
    voicepack: VoicePack = EMPTY_PACK,
    tts: TTSProvider | None = None,
    sample_rate: int = 8000,
) -> Speech | None:
    """The audio to play for a node on V3: pre-recorded if we have it, else TTS.

    Returns None only when there is no recording *and* no TTS provider (a truly
    offline kiosk with an empty pack) — the caller then shows text and relies on
    the kiosk's own Web Speech, per doc 03 §1a. A TTS outage is not fatal here:
    V3's promise is that it keeps working when the AI is down.
    """
    clip_name = node.audio_clip(lang)
    if clip_name and (clip := voicepack.get(clip_name, lang)) is not None:
        return Speech(audio=clip, provider="voicepack", voice=clip_name)

    if tts is None:
        return None
    text = node.ask(lang)
    if not text.strip():
        return None
    try:
        return await tts.synthesize(text, str(lang), sample_rate=sample_rate)
    except ProviderError:
        # Offline / TTS down: V3 still walks; the kiosk falls back to on-screen
        # text + Web Speech. Better a silent question than a failed intake.
        return None
