# HANDOFF — after Session S-OSS.0 (V-OSS software layer)

**Repo state:** branch `main`, last code commit precedes the session-close commit that
follows this file. `make test` green (backend **515**, up from 492). `ruff check .` and
`ruff format --check .` clean. `make dev` brings up the stack; Postgres on host port
**5433**. Kiosk PWA live at `/kiosk` (S6). This was a **parallel-track** session (doc 08),
not the main line — S7 (kiosk offline) is still the main-line next.

**One paragraph:** The user added **doc 08** — a fully open-source local voice tier
("V-OSS": Whisper → local LLM → local TTS on a 24 GB GPU box, zero paid APIs). We folded
it into the build plan as a parallel track and built its **software half (S-OSS.0)**: the
GPU-independent part. `app/providers/local_oss/` adds four adapters onto the *existing*
provider interfaces — `LocalLLMProvider` (vLLM, reusing the OpenAI wire, keyless),
`LocalSTTProvider` (Whisper), `LocalTTSProvider` + `VoiceboxTTSProvider` — so a hospital
selects V-OSS with three env vars (`LLM_PROVIDER=local_vllm` etc.) and nothing else changes;
each meters `provider=local-*`, priced from amortized `local-*` rows. `config/tiers.yaml` +
`app/tiers.py` hold the per-channel tier ladder; `AdmissionController` is the
`MAX_OSS_SESSIONS` cap that routes overflow to the next tier instead of queuing on the GPU.
All tested through `httpx.MockTransport` — **no GPU, no cloud keys**. The **GPU half**
(S-OSS.1 bake-off, S-OSS.2 Pipecat realtime + 12-concurrent proof, S-OSS.3 Dhara cloning)
is deferred until the box is available; `REALTIME_PROVIDER=local-pipecat` honestly refuses
to build until then.

## Next session — pick one
**Main line: S7 — Kiosk part 2 (offline-first, T3 voices, printing).** Unchanged by this
session; see its brief in docs/06-BUILD-PLAN.md and the S6 notes (below). The kiosk still
dies without the API — service worker + Dexie, offline token blocks, Downtime Mode, ESC/POS
printing, voice-pack manifest.

**Parallel track (only when the GPU box is available): S-OSS.1** — `docker-compose.gpu.yml`,
Voicebox install, the TTS bake-off + Whisper/IndicConformer WER bench (doc 08 §6). S-OSS.0
already gave it the adapters, price rows and admission gate to plug into.

- Exact first commands (either path): `make dev` → `make migrate && make seed` → `make test`.

### (Carried from S6, still true for S7)
- Kiosk calls `kioskApi.*` (`web/app/(kiosk)/kiosk/_lib/api.ts`) synchronously; S7 puts a
  service worker + Dexie in front and walks the dispatcher client-side when offline.
- Token issuance is the offline crux — `app.kiosk.allocate_token` is a provisional `max+1`;
  S7's offline token *blocks* (server pre-allocates a range, kiosk consumes offline,
  reconciles) replace it. Build server allocation + kiosk consumption together.
- Add `/kiosk/stt` over `stt_chain` + MediaRecorder for the "trouble hearing?" toggle.

## Watch out for
- **V-OSS `ladder_for()` is loaded but not wired into the engine/voice-gw** — routing a
  channel down its ladder + gating the live local session on admission is S-OSS.2. Today the
  ladder is only operational via provider fallback chains + the V2→V3 tier downgrade.
- **`local-pipecat` refuses on purpose** — do not "fix" it to fall to the fake; it needs the
  GPU + Pipecat (S-OSS.2), same stance as `gemini-live`.
- **`local-*` price rows are amortized placeholders**, non-zero so the dashboard shows a real
  cost; not measured. Admin-editable in S18.
- **Docker died mid-session once in S6** (daemon stop → Postgres 5433 refused → api 500s that
  the browser mislabels "CORS"). If a kiosk fetch fails, `docker ps` before suspecting code.
- Pre-existing lint stragglers were fixed this session (local ruff 0.15 vs unpinned CI now
  flags what older ruff didn't); if CI ruff drifts again, expect more format-only nits.

## Decisions needed from the human
- None blocking. When the 24 GB GPU box is provisioned, that unblocks S-OSS.1 (its network,
  WireGuard tunnel and driver versions are doc 08 §4).

## Backlog additions
- S-OSS.2: wire `config/tiers.yaml` `ladder_for()` + `AdmissionController` into voice-gw;
  `LocalPipelineVoiceProvider` (Pipecat, Silero VAD + smart-turn); Redis-backed admission
  counter for multi-replica voice-gw.
- S18: replace amortized `local-*` price rows with measured GPU cost-per-unit.
