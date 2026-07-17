# 08 — Addendum: Fully Open-Source Local Voice Tier ("V-OSS")

**Purpose.** A complete voice-to-voice pipeline with **zero paid APIs** — open-source STT, LLM, and TTS — running on your **24 GB GPU local server**, exposed to the platform as just another provider adapter (exactly like Gemini/OpenAI), selectable per channel via config. It can replace Gemini Flash for dialogue/summaries and replace all paid TTS, while meeting pilot concurrency (500 patients/day) with clean turn-taking (no VAD misfires, barge-in supported).

This tier slots into the existing ladder as a peer of V2:

```
V1  Gemini Live (premium cloud S2S)
V2  Cloud pipeline (Sarvam/Google STT → Gemini Flash/gpt-4o-mini → Sarvam TTS)
V-OSS  Local pipeline (Whisper → local LLM → local TTS on the 24GB GPU box)   ← this addendum
V3  Zero-AI (rule-based + pre-recorded packs)
```
Downgrade ladder becomes configurable per channel, e.g. `phone: [V-OSS, V2, V3]` or `kiosk: [V-OSS, V3]`. A hospital can run **entirely on V-OSS + V3** with no per-call AI cost.

---

## 1. Voicebox (voicebox.sh) — evaluation & role

**What it is:** an open-source (AGPL-style local-first) voice studio: zero-shot voice cloning from short samples, 7 switchable TTS engines (Qwen3-TTS, Qwen CustomVoice, Chatterbox Multilingual, Chatterbox Turbo, LuxTTS, Kokoro, HumeAI TADA), Whisper STT for dictation, small local LLMs, effects/timeline editing, and a REST API; runs on CUDA locally or as a remote inference server.

**Verdict — adopt it in three roles, but not as the live call server:**

1. **Voice-identity studio (adopt, high value).** Record ~2–5 min of a warm human "Dhara" voice per language (hi, mr, te, en) with a voice artist once; clone in Voicebox; from then on **every** system voice — V3 pre-recorded packs, V-OSS live TTS, reminder audio, app audio clips — is generated locally from the same cloned identity. One consistent voice across every channel, zero recurring TTS cost, re-generate any prompt in minutes when trees change. This replaces the "recording script sheets for a voice artist" plan in S21 with a one-time recording + clone workflow.
2. **TTS engine bake-off harness (adopt).** Its engine-switchable UI + API is the fastest way to A/B Qwen3-TTS vs Chatterbox Multilingual vs Kokoro on real Hindi/Marathi/Telugu prompt sets before committing the production engine.
3. **Live serving for calls (do not adopt as-is).** It is a studio/desktop-first app; its request path is not designed for 10+ concurrent low-latency *streaming* sessions with per-session isolation. Instead, **serve the winning engine from the bake-off directly** behind a proper streaming server (below). Same models, production-grade path. Keep Voicebox's remote-API mode as an acceptable low-concurrency fallback host (e.g., after-hours check-in calls).

## 2. Production V-OSS pipeline (the prescription)

**Orchestrator: Pipecat** (open source, BSD) — one pipeline instance per call session, connected to our existing `voice-gw` WebSocket transport (Exotel/kiosk/app audio in and out).

**Turn-taking (the "no VAD issues" requirement), solved explicitly:**
- **Silero VAD** (open source) for frame-level speech detection — per-session instance, never shared state, so concurrency cannot cross-trigger.
- **Semantic endpointing** on top (Pipecat smart-turn model, open weights): end-of-utterance decided by *what* was said + prosody, not silence length alone — critical for elderly Hindi/Marathi/Telugu speakers whose natural mid-sentence pauses (1–2s) would cause premature cut-offs with naive VAD. Silence-only fallback threshold set high (1.2s) behind it.
- **Barge-in:** VAD event during TTS playback → cancel TTS stream within one frame (~30ms), flush queue, start STT. Tested per session under load (S-OSS.2 AC).
- Echo safety on telephony: Exotel leg is naturally duplex-separated; kiosk uses browser AEC + we duck mic sensitivity during playback.

**Components on the 24 GB GPU (VRAM budget):**

| Component | Choice (primary) | Serving | VRAM | Notes / alternates |
|---|---|---|---|---|
| STT | **Whisper large-v3-turbo, int8** via **faster-whisper** worker pool (3 workers, batched) | gRPC/WS micro-service | ~3.5 GB | Eval alternate: AI4Bharat **IndicConformer** for hi/mr/te accuracy; keep both behind `STTProvider` |
| Dialogue + summaries LLM | **Qwen3-8B-Instruct AWQ (4-bit)** via **vLLM** (OpenAI-compatible endpoint, continuous batching, function calling → our existing tool contract) | vLLM | ~7 GB + ~4 GB KV cache | Alternate if quality demands: Gemma-3-12B-it int4 (tighter fit). Same prompts from `prompts/` — they are vendor-neutral by design |
| TTS | Winner of bake-off among **Qwen3-TTS / Chatterbox Multilingual / IndicF5 / AI4Bharat Indic Parler-TTS** (all open), cloned Dhara voice, sentence-chunk streaming with crossfade | dedicated streaming TTS service (2 workers) | ~4–5 GB | **Kokoro-82M** kept loaded as ultra-fast English/fallback voice (~0.5 GB, RTF≈0.1) |
| VAD + endpointing | Silero VAD + smart-turn | CPU + <0.5 GB | ~0.5 GB | Per-session, stateless across sessions |
| Headroom | — | — | **~4–5 GB free** | Burst KV cache, model swaps, safety margin |

**Latency budget (turn, p90):** STT finalize ≤0.9s (streaming partials) + LLM first-token ≤0.6s / full short turn ≤1.4s (vLLM, ≤120 output tokens) + TTS first-audio ≤0.7s (first sentence chunk) → **first audio back ≤3.0s, target 2.5s** — comparable to V2, with pre-recorded Dhara fillers masking anything longer.

## 3. Concurrency engineering for 500 patients/day

Load math: ~150–180 voice sessions/day, peak hour ≈ 25–30 sessions/hr ≈ **8–12 concurrent calls** peak. Engineering targets **12 concurrent, tested to 16**:

- **vLLM continuous batching**: 12 concurrent short dialogue turns is trivial for an 8B AWQ model (aggregate <2k tok/s needed vs ~10k+ available).
- **STT pool**: 3 faster-whisper workers × batched streaming ≈ 12–15 real-time streams; audio chunks queued per session, 200ms max queue delay before admission control triggers.
- **TTS is the choke point**: production engine must show **RTF ≤0.35 per stream** on the bake-off bench so 2 workers sustain 12 streams; sentence-chunking keeps memory flat; Kokoro absorbs overflow for English.
- **Admission control** in voice-gw: hard cap `MAX_OSS_SESSIONS` (start 12); session #13 is transparently routed to the next tier in that channel's ladder (V2 if keys configured, else V3). No queuing patients on a GPU.
- **Per-session isolation**: one Pipecat pipeline per call — a crash/cancel affects only that call; supervisor restarts pipeline pool.
- Everything still emits **usage_events** (tokens, audio-seconds, latency) with `provider=local`, priced from a `price_book` "local" row (₹0 marginal or an amortized ₹/min you set) — so the §11 dashboard shows true cost-per-intake comparisons of V-OSS vs V1/V2, which is the data you'll use to tune the tier mix.

## 4. Deployment (local GPU server)

- Box: your 24 GB GPU server (e.g., RTX 4090/A5000-class), Ubuntu 22.04+, NVIDIA driver 550+, `nvidia-container-toolkit`.
- Everything ships as a **`docker-compose.gpu.yml` profile**: `vllm`, `stt`, `tts`, `pipecat-runner`, `node-exporter`+`dcgm-exporter` (GPU metrics → the same Grafana). Models pre-pulled to a `/models` volume; cold start <3 min; `make gpu-up`, `make gpu-bench`.
- **Connectivity:** WireGuard tunnel between EC2 and the GPU box (GPU box dials out — works behind hospital NAT, no inbound ports). voice-gw reaches local providers at `10.8.0.2:*`; health-checked; tunnel down = provider unhealthy = automatic tier failover. If the GPU box sits **in the hospital**, kiosk audio can short-path to it on LAN — kiosk voice then survives even an internet outage (a genuine upgrade to the Downtime Protocol).
- Ops: same runbook style — model updates are compose-tag bumps; nightly `gpu-bench` regression (RTF, latency) posted to Grafana.

## 5. Adapter spec (config-only switching, like Gemini/OpenAI)

New package `backend/app/providers/local_oss/` implementing the **existing interfaces** — no feature code changes anywhere:

- `LocalLLMProvider(LLMProvider)` → vLLM OpenAI-compatible endpoint (base_url config; function calling passthrough).
- `LocalSTTProvider(STTProvider)` / `LocalTTSProvider(TTSProvider)` → streaming gRPC/WS clients.
- `LocalPipelineVoiceProvider(RealtimeVoiceProvider)` → wraps a Pipecat session end-to-end and speaks the same tool contract (`get_next_node / save_answer / check_red_flags / finish_and_summarize`) as Gemini Live — so to the IntakeEngine, V-OSS *is* just another realtime voice provider.
- `VoiceboxTTSProvider(TTSProvider)` → Voicebox REST API adapter (batch generation of V3 packs + low-concurrency fallback host).

```yaml
# config/tiers.yaml (example)
channels:
  phone:  {ladder: [v_oss, v2, v3]}
  kiosk:  {ladder: [v_oss, v3]}
  whatsapp_voice: {ladder: [v2, v_oss, v3]}
providers:
  v_oss:
    llm:  {kind: local_vllm, base_url: http://10.8.0.2:8000/v1, model: qwen3-8b-awq}
    stt:  {kind: local_whisper, url: ws://10.8.0.2:8010}
    tts:  {kind: local_tts, url: ws://10.8.0.2:8020, voice: dhara_hi_v1}
    realtime: {kind: local_pipecat, max_sessions: 12}
```

## 6. Build-plan additions (insert as a parallel track after S5; no cloud keys required for any of it)

**S-OSS.1 — GPU stack bring-up + open-model bake-off**
- Load: this doc §1–2, docs 02 §5/§9.
- Build: `docker-compose.gpu.yml` (vllm+stt+tts skeleton); Voicebox install on the GPU box; **bake-off harness**: 60-prompt set per language (from real tree prompts) × {Qwen3-TTS, Chatterbox Multilingual, IndicF5, Indic Parler-TTS, Kokoro} scoring RTF, first-audio latency, and a human MOS sheet; Whisper-turbo vs IndicConformer WER bench on 30 recorded utterances per language; Qwen3-8B function-calling smoke against the tool contract; pick + record decisions in the session log.
- AC: bench report committed (`benchmarks/oss-voice/`); chosen TTS shows RTF ≤0.35 for hi/mr/te; STT WER report per language; vLLM passes the shared tool-contract test suite.

**S-OSS.2 — Pipecat pipeline, adapter, concurrency & turn-taking proof**
- Load: this doc §2–5, docs 02 §5.
- Build: Pipecat per-session pipeline (Silero VAD + smart-turn endpointing + barge-in); the four adapters in `providers/local_oss/` incl. usage-event metering with `provider=local`; WireGuard compose + health/failover wiring; admission control in voice-gw; `make gpu-bench` (synthetic concurrent callers replaying recorded audio).
- AC: **12 concurrent synthetic calls** complete full intakes with p90 first-audio ≤3.0s and **zero premature end-of-turn cuts** on a pause-heavy Hindi test set (≥1.5s mid-sentence pauses); barge-in interrupts within 100ms under full load; session #13 transparently lands on the fallback tier; tunnel-kill mid-call downgrades without data loss; dashboard shows V-OSS cost-per-intake.

**S-OSS.3 (folds into S21) — Dhara voice cloning + V3 pack generation**
- Record the human voice samples, clone in Voicebox, regenerate all V3 pre-recorded packs (4 languages) via `VoiceboxTTSProvider` batch mode, and set the live V-OSS TTS to the cloned voice.
- AC: every audible prompt in the system is the same Dhara voice; pack regeneration is one command.

## 7. Honest constraints to carry into evaluation

- Open Indic TTS is the quality risk: Marathi/Telugu naturalness varies by engine — this is exactly why S-OSS.1 is a measured bake-off, not a default. If te/mr quality fails MOS, run those languages on V2 TTS only (per-language TTS routing is one config line) while keeping local STT+LLM.
- Whisper on telephony-band (8kHz) Indic audio needs the resample+denoise front-end (included in the STT service) and should be benched against IndicConformer specifically on phone-quality audio.
- One GPU = one failure domain: the ladder (→V2/V3) is the HA story; don't buy a second GPU for the pilot.
- 8B-class local LLM is fine for tree-driven intake turns (the tool contract constrains it) and summaries; keep dictation→Rx mapping on the strongest configured model until S-OSS bench proves the local one on your Hinglish fixtures — patient-safety-adjacent extraction gets the higher bar.