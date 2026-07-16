# SESSION-05 — Intake Engine (all tiers) + session state

**Date:** 2026-07-16 · **Scope ref:** docs/06-BUILD-PLAN.md → S5

## Acceptance criteria checklist
- [x] `IntakeEngine` exposing the shared tool contract — `app/intake/dispatch.py`
  runs get_next_node/save_answer/check_red_flags/finish_and_summarize as a thin
  dispatcher over one `Walk`; `engine.dispatcher(state)` is the entry.
- [x] **V1 Gemini Live session manager** — `engine._run_v1` bridges a
  `RealtimeSession`'s tool calls to the dispatcher, streams the model's audio out
  through the `on_audio` passthrough (the voice-gw hook, S14), closes on
  finish_and_summarize. Driven end-to-end against `FakeRealtimeProvider`.
- [x] **V2 pipeline loop** — `engine._turn_v2`: STT → dialogue LLM (same tool
  contract) → TTS, one patient turn per question, with a safety-net save if the
  model speaks but forgets to record.
- [x] **V3 deterministic** — `engine._turn_v3` walks the tree and plays
  pre-recorded audio via `app/intake/voicepack.py` (TTS fallback until S7/S21).
- [x] **Redis session state incl. active tier** — `app/intake/state.py`:
  `SessionState` + `RedisSessionStore`/`InMemorySessionStore`; stores the answers
  (`walk.to_json()`), not a cursor; carries `configured_tier` + `active_tier`.
- [x] **Automatic downgrade on provider failure OR cost-guard, preserving
  answers** — `_downgrade` / `_maybe_costguard_downgrade` lower the tier and
  rebuild the dispatcher from the stored answers. Tested: V1→V2 on connect
  failure, V2→V3 on a mid-session LLM kill, and a cost-guard-forced start tier.
- [x] **Summarizer → doc 03 §4 contract + read-back** — `app/intake/summary.py`:
  `LLMSummarizer` (V1/V2) validates the structured contract; `TemplateSummarizer`
  (V3, offline) needs no vendor; the engine falls back LLM→template. Red flags on
  both paths come from the rules, never the model.
- [x] **Per-intake cost finalised on completion** — `engine.finalize_cost` sums
  `usage_events` for the intake_id and writes `Intake.cost_inr` (+ answers,
  red_flags, transcript, summary). DB test reconciles to the paisa.
- [x] scripted e2e through V1/V2/V3 via fakes; mid-session kill downgrades without
  data loss; completed intake carries an accurate cost; summary matches schema.

## What was built
- `app/intake/state.py` — `SessionState`, `SessionStatus`, `SessionStore` protocol,
  in-memory + Redis stores, `build_session_store` (Redis outside local).
- `app/intake/dispatch.py` — `ToolDispatcher`, the four tools over a `Walk`,
  persisting the session after every mutation; `ToolError`.
- `app/intake/summary.py` — `IntakeSummary` (doc 03 §4) with `to_markdown`/
  `to_structured`, contract validation, `LLMSummarizer`, `TemplateSummarizer`.
- `app/intake/voicepack.py` — `VoicePack` manifest + `resolve` (pre-recorded →
  TTS → None), the S7/S21 seam.
- `app/intake/engine.py` — `IntakeEngine` (start_session, run, downgrade,
  finalize_cost, the three tier runners), `PatientTurn`.
- `prompts/intake/v1.md` — the V1/V2 dialogue driver system prompt.
- `tests/test_intake.py` — 20 tests (state, dispatcher, each tier, both
  downgrades, cost-guard, summarizer, DB cost reconciliation).

## Decisions made
- **The dispatcher is the single implementation of the tools; the three tiers
  differ only in how the tools get called.** V1 feeds tool *results* back into the
  Live session (`send_tool_result`); V2 mediates `get_next_node` by injecting the
  current question into the LLM prompt (the request/response `LLMProvider` has no
  tool-result message type — a real multi-step tool loop needs a contract change,
  deferred). Both still call save_answer/check_red_flags/finish through the same
  dispatcher, so the answers are identical by construction.
- **Position stays derived from the answers; a downgrade rebuilds the walk.** The
  session stores `walk.to_json()`, never a cursor (STATE.md invariant). Downgrade
  = lower the tier + build a fresh dispatcher from the stored answers.
- **V3 summarises deterministically, and V1/V2 fall back to the template if the
  LLM is down.** A completed intake never needs a network to produce its summary —
  degrade, never deny. Red flags are always the rule engine's, passed in.
- **`PatientTurn` carries both `audio` and `answer`** so one scripted intake
  survives a downgrade across modalities (V2 hears audio; V3 taps an answer).
- **The engine wires no HTTP/WS routes.** Channels are S6 (kiosk), S12 (WhatsApp)
  and S14 (telephony); S5 ships the service class and the passthrough seam.

## Deviations from spec
- None material. Doc 02 §5's "STT stream → LLM → TTS stream" is implemented as a
  turn pipeline rather than token streaming — streaming is a latency optimisation
  for S14's real-time path, not a contract change; the tool semantics are identical.

## Tests & evidence
- `make test`: backend **486 passed** (was 466; +20), voice-gw 1 passed, web
  unchanged (no UI this session). `ruff check`/`format` clean on all S5 files.
- New tests: `tests/test_intake.py` — the tier matrix, downgrades, cost.
- Screenshots: none (no UI in S5).

## Known gaps / stubs introduced (mirrored to STATE.md)
- **The Gemini Live impl is still not written** — `_run_v1` drives the
  `RealtimeVoiceProvider` interface and is proven against the fake; the real
  websocket session + the Exotel↔Live audio bridge are S14. `REALTIME_PROVIDER=
  gemini-live` still raises.
- **V2 is a turn pipeline, not token streaming**, and does not feed tool results
  back to the LLM within a turn (interface limit). Fine for kiosk/WhatsApp;
  S14 wants true streaming + a tool-result message on `LLMProvider`.
- **No node has real V3 audio** — `voicepack.resolve` falls back to TTS for every
  prompt. Real packs are S7 (format) / S21 (recordings).
- **The engine is not wired to any channel or route** — S6/S12/S14.
- **`text_en` is not filled during intake** — the doctor-screen English gloss per
  answer is left to the summariser; a per-answer translation pass is future work.

## Commits
- ed572b6 — S 05: add the intake engine — session state, tool dispatcher, tiers, summarizer
- 514f433 — S 05: test the intake engine across all three tiers, downgrade and cost
- (session-close commit follows this file)
