"""V-OSS — the fully open-source local voice tier (doc 08).

A complete voice pipeline with **zero paid APIs** — open-source STT, LLM and TTS
served from the hospital's own 24 GB GPU box — exposed to the platform as *just
another provider adapter* (doc 08 §5). Nothing in the intake engine, the channels
or the tool contract changes: V-OSS is realised entirely as concrete impls of the
same `LLMProvider` / `STTProvider` / `TTSProvider` interfaces every vendor uses,
selected by config exactly like Gemini or Sarvam.

    LLM_PROVIDER=local_vllm  STT_PROVIDER=local_whisper  TTS_PROVIDER=local_tts

Because a channel's fallback chain is config too (`with_fallback`, doc 02 §2), the
doc 08 "ladder [v_oss, v2, v3]" is expressed without a new tier enum: put a local
provider first and a cloud one in `*_FALLBACK_PROVIDER`, and a GPU/tunnel outage
falls to the cloud automatically; the existing V2→V3 tier downgrade is the floor.

## What is here (the software half — no GPU required)

- `LocalLLMProvider`   — vLLM's OpenAI-compatible endpoint, reusing the OpenAI
  wire path. Function calling passes the *same* `INTAKE_TOOLS` contract through.
- `LocalSTTProvider`   — a faster-whisper / OpenAI-audio-compatible HTTP server.
- `LocalTTSProvider`   — a local streaming TTS server (the bake-off winner), the
  cloned "Dhara" voice.
- `VoiceboxTTSProvider`— Voicebox's REST API, for batch V3 pack generation and a
  low-concurrency fallback host (doc 08 §1).
- `AdmissionController`/`config/tiers.yaml` — the `MAX_OSS_SESSIONS` cap and the
  per-channel ladder (doc 08 §3), so session #13 lands on the next tier instead
  of queuing patients on one GPU.

Every adapter meters like any other provider — `provider=local-*`, priced from
the `local-*` rows in `price_book` — so the S18 dashboard shows a true
cost-per-intake comparison of V-OSS against V1/V2 (doc 08 §3).

## What is NOT here (the GPU half — S-OSS.2, doc 08 §6)

`LocalPipelineVoiceProvider` (the Pipecat per-session realtime pipeline with
Silero VAD + smart-turn endpointing) and the 12-concurrent-call proof need the
physical GPU box, so `REALTIME_PROVIDER=local-pipecat` deliberately refuses to
build until then — the same honesty `gemini-live` keeps (see `registry`).
"""

from app.providers.local_oss.admission import AdmissionController, AdmissionFull
from app.providers.local_oss.llm import LocalLLMProvider
from app.providers.local_oss.stt import LocalSTTProvider
from app.providers.local_oss.tts import LocalTTSProvider, VoiceboxTTSProvider

__all__ = [
    "AdmissionController",
    "AdmissionFull",
    "LocalLLMProvider",
    "LocalSTTProvider",
    "LocalTTSProvider",
    "VoiceboxTTSProvider",
]
