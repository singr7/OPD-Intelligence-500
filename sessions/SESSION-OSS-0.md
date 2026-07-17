# SESSION-OSS-0 — V-OSS adapter & tier-ladder software layer

**Date:** 2026-07-17 · **Scope ref:** docs/06-BUILD-PLAN.md → Parallel track V-OSS (S-OSS.0); doc 08

Context: the user added **doc 08** (fully open-source local voice tier, "V-OSS") and asked to
evaluate where it fits, fold it into the build plan, and build the next session. Evaluation found a
hardware split the addendum glossed over — the bake-off (S-OSS.1) and the realtime concurrency proof
(S-OSS.2) need the physical 24 GB GPU box, which this dev machine is not. So V-OSS was split, and the
**software half** — buildable and testable here with no GPU and no cloud keys — was built as
**S-OSS.0**. The user chose this over continuing S7.

## Acceptance criteria checklist
- [x] Swapping to local providers is **config-only** (`LLM_PROVIDER=local_vllm`,
  `STT_PROVIDER=local_whisper`, `TTS_PROVIDER=local_tts|voicebox`) — same call sites, local impls.
- [x] Every local adapter emits a **priced** `usage_event` with `provider=local-*` (doc 02 §9 is not
  waived for a free provider; amortized `local-*` price rows).
- [x] `REALTIME_PROVIDER=local-pipecat` **honestly refuses** to build until S-OSS.2 (needs GPU +
  Pipecat), mirroring `gemini-live`.
- [x] Admission control: admits up to `MAX_OSS_SESSIONS`, routes overflow (session #cap+1) to the
  next tier without queuing, and **releases the seat on crash** (per-session isolation).
- [x] `config/tiers.yaml` loads + validates; malformed ladders (unknown channel/tier, empty ladder,
  negative cap) are boot errors.
- [x] `make test` green — 492 → **515** backend tests; `ruff check .` + `ruff format --check .` clean.

## What was built
- `backend/app/providers/local_oss/` — new package:
  - `llm.py` `LocalLLMProvider` — vLLM's OpenAI-compatible endpoint; **reuses the whole OpenAI wire**
    (added a tiny `_auth_headers()` hook to `OpenAIProvider`), keyless, `provider=local-vllm`.
  - `stt.py` `LocalSTTProvider` — Whisper via OpenAI-audio-compatible `/v1/audio/transcriptions`;
    `confidence=None` (honest, like Sarvam); meters audio even on rejection.
  - `tts.py` `LocalTTSProvider` (local `/tts`) + `VoiceboxTTSProvider` (Voicebox `/api/tts`, batch
    V3-pack + fallback host); both bill per character, speak the cloned `dhara_hi_v1` voice.
  - `admission.py` `AdmissionController` — per-profile concurrency cap; `slot()` yields
    `admitted: bool`, never queues; frees seats in `finally`.
- `config/tiers.yaml` + `backend/app/tiers.py` — per-channel tier ladder (labels `v1/v_oss/v2/v3`)
  + `admission.max_oss_sessions`; validated loader; builds a capped `AdmissionController`.
- `backend/app/providers/registry.py` — `local_vllm` / `local_whisper` / `local_tts` / `voicebox`
  builders; `local-pipecat` refuses with an S-OSS.2 pointer. `config.py` — the `local_*` / `voicebox`
  settings block. `.env.example` updated.
- `seeds/price_book.json` — amortized `local-vllm` / `local-whisper` / `local-tts` / `voicebox` rows
  (wildcard model, non-zero so the S18 dashboard shows a true V-OSS cost).
- `backend/tests/test_providers_local_oss.py` — 23 tests (adapters via `httpx.MockTransport`,
  config-only swap, metering, admission, tiers config).
- Docs: doc 06 gains the V-OSS parallel track (S-OSS.0/.1/.2/.3 with the hardware split); doc 08
  renamed `08-LOCAL-OSS-VOICE.md` (was `08-local-oss-voice.md ` with a trailing space).

## Decisions made
- **V-OSS is provider adapters, not a new `IntakeTier` enum value.** Doc 08 §5's intent: "to the
  IntakeEngine, V-OSS is just another provider." The ladder `[v_oss, v2, v3]` is expressed
  operationally by provider fallback chains (local primary + cloud `*_FALLBACK_PROVIDER`) plus the
  existing V2→V3 tier downgrade — zero engine surgery. `config/tiers.yaml` labels are for the
  S-OSS.2 voice-gw routing, not a parallel tier machine.
- **Split V-OSS by hardware dependency.** S-OSS.0 (this) is pure software/integration, green here.
  S-OSS.1/.2 (bake-off, Pipecat realtime, 12-concurrent proof) are GPU-gated and deferred. Recorded
  in doc 06 and STATE.
- **`LocalLLMProvider` subclasses `OpenAIProvider`** rather than duplicating the wire — vLLM *is*
  OpenAI-compatible. The only edit to shared code is the `_auth_headers()` hook so the local, keyless
  path drops the bearer.
- **Local providers are "configured" from a base URL, not a key** — a keyless local box is normal,
  not a misconfiguration; no URL ⇒ reports `unconfigured` on `/providers/health` (same as a keyless
  vendor), not a build failure.
- **`local-*` price rows are amortized placeholders**, non-zero on purpose (doc 08 §3): a flat ₹0
  would read to the cost-guard as infinite budget and hide V-OSS's real GPU cost from S18.

## Deviations from spec
- Doc 08 §6 folded the adapters + admission into "S-OSS.2"; this session extracted them as **S-OSS.0**
  so they can land without the GPU. Doc 06 records the new numbering. No behavioural deviation.
- Fixed two pre-existing lint stragglers unrelated to this work (a stray `import Path` in
  `test_tree_bank.py`, a format-only reflow of one alembic migration) — local ruff 0.15 (pin `>=0.7`,
  CI installs unpinned) now flags what an older ruff didn't, so CI would fail regardless. Format-only;
  no SQL/semantics changed.

## Tests & evidence
- `make test` (backend): **515 passed** (492 + 23). `ruff check .` clean; `ruff format --check .` clean.
- App boot smoke: `import app.main`, `get_tier_config()`, `all_providers()` all import clean (no
  circular import from the new package wiring).
- New tests: `backend/tests/test_providers_local_oss.py` (23).

## Known gaps / stubs introduced (mirrored into STATE.md → Stubs & fakes)
- No live GPU server has answered — adapters tested through `httpx.MockTransport` only; first real
  bring-up is S-OSS.1.
- `LocalPipelineVoiceProvider` (V-OSS realtime) does not exist; `local-pipecat` refuses until S-OSS.2.
- `config/tiers.yaml` `ladder_for()` is loaded/validated but **not wired into the engine/voice-gw**
  yet — that routing (and gating the live session on admission) is S-OSS.2.
- `AdmissionController` count is per-process/in-memory — a second voice-gw replica needs a Redis
  counter (S-OSS.2), same shape as the cost-guard override store.
- `local-*` price rates are amortized placeholders, admin-editable in S18.

## Commits
- 8740201 — S-OSS.0: session close — V-OSS open-source local voice tier, software half; 515 tests green
