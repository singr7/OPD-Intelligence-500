# SESSION-14 — Telephony voice gateway (Exotel) part 1: pipeline

**Date:** 2026-07-25 · **Scope ref:** docs/06-BUILD-PLAN.md → S14

## Acceptance criteria checklist
- [x] AC1 — Fake-client e2e intake completes on **both V1 and V2** paths
      (`test_call_v1.py`, `test_call_v2.py`).
- [x] AC2 — **Latency measured** per turn, asserted under budget: V1 <1.5s, V2 <3.5s p90
      (`test_v1_turn_latency_within_budget`, `test_v2_turn_latency_within_budget`).
- [x] AC3 — **Barge-in** works: caller speech during playback flushes it and sends `clear`
      (`test_barge_in.py`).
- [x] AC4 — **DTMF fallback** for yes/no after two unclear tries (`test_dtmf.py`).
- [x] AC5 — **Consent line** at call start, recorded (`test_v2_consent_is_played_and_recorded`;
      also on `Patient.consent_given_at/consent_audio_url` when a DB is present).
- [x] AC6 — **Partial save on hangup** (`test_v2_hangup_saves_a_partial_intake`).
- [x] AC7 — **Call cost recorded per intake**: proven live — a real WS call persisted a phone
      `Intake` with `cost_inr` and metered `channel=phone` usage_events (see Tests & evidence).

## What was built
- **`voice-gw` is now the phone channel adapter over the shared `IntakeEngine`**, in-process
  (doc 02 §5; no network hop in the turn loop). The image bundles `backend/` via a root build
  context; it stays a separate container for crash isolation (doc 05 §3).
- **`gw/exotel.py`** — the Exotel Voicebot WS codec: `connected/start/media/dtmf/mark/stop`
  in, `media/clear/mark` out, 8 kHz 16-bit PCM. A tiny `ExotelTransport` protocol so the real
  socket and the fake client share one wire format.
- **`gw/call.py`** — the call driver: consent → tier/lang/tree resolve → phone `Intake` rows
  (patient by CLI) → `engine.run(turn_source, on_audio)`. Owns the channel's jobs: a playback
  pump with **barge-in** (`clear`), utterance detection via a silence-energy VAD stand-in,
  **DTMF fallback** after two unclear utterances, the **8-minute cap**, **partial save on
  hangup**, per-minute metering scope, `finalize_cost`, and a persisted `PhoneCallRecord`.
- **`gw/engine.py`** — stands up one `IntakeEngine` + `UsageMeter` + `CostGuard` on the FastAPI
  lifespan, mirroring `backend/app/main.py`.
- **`gw/main.py`** — `WS /exotel/voicebot` runs one call; `_WebSocketTransport` adapts Starlette.
- **`gw/fake_exotel.py`** — the AC instrument: an in-memory client that speaks the wire protocol,
  replays caller utterances, captures assistant audio, and measures per-turn latency.
- **Engine streaming turn-source (additive)** — `IntakeEngine.run(..., turn_source=)` +
  `TurnSource`/`_Turns` in `backend/app/intake/engine.py`. A live phone call has no turns up
  front; the source yields one per detected utterance. A fixed `turns` sequence is adapted to
  the same shape, so the kiosk and all existing callers are unchanged.
- **Analytics loop-back** — a `channel=phone` row in the seeded replay day
  (`backend/tests/test_analytics.py`); the S18E dashboard reconciles it with no dashboard change.

## Decisions made
- **Engine runs in-process inside voice-gw** (not a proxy hop to api) — best for the latency
  ACs and faithful to doc 02 §5's channel-adapter model. voice-gw now depends on the backend
  package + Postgres/Redis; still its own process.
- **The streaming turn-source is an additive engine change, not a fork.** `run()` accepts either
  `turns` (existing) or `turn_source` (new); one loop serves both. Do not re-introduce a
  separate per-turn loop in voice-gw — there is one engine.
- **Barge-in and the DTMF trigger are channel-side audio signals, not engine signals.** The
  engine's STT confidence is not visible to the channel, so the driver reads its own audio
  energy: near-silence ends an utterance; a non-silent-but-low-energy utterance is "unclear",
  and two of them trigger the keypad. This is a tunable stand-in for real VAD / STT confidence.
- **Money stays a wire string / `Decimal`.** No rupee arithmetic in voice-gw.

## Deviations from spec
- The real Exotel **vendor** bridge is validated against the **fake replay harness**, not a live
  Exotel number — exactly the AC ("fake-client e2e"). A live-number smoke is owed to a box/creds
  session (HANDOFF).
- **Routing from the caller's spoken chief complaint is not done here** — the applet/campaign
  passes a `tree` custom parameter (default `general_medicine_routing`). Speech→department
  routing is the inbound receptionist's job (S15, doc 03 §2).
- **DTMF trigger uses a channel-side energy proxy** for "STT confidence <0.5", because STT runs
  inside the engine turn and its confidence is not surfaced to the channel. Surfacing it is a
  small future refinement; the observable behaviour (two bad tries → keypad → digit accepted) holds.
- mr/te consent + keypad prompts are **romanized placeholders** (native-script + clinical review
  is the standing S21 item for all mr/te copy).

## Tests & evidence
- `make test` — **green**: backend **840**, voice-gw **1 → 15**, web typecheck + lint + 48
  conformance. `make lang-qa` clean across [en, hi, mr, te].
- New: `voice-gw/tests/test_exotel_protocol.py`, `test_call_v1.py`, `test_call_v2.py`,
  `test_barge_in.py`, `test_dtmf.py` (+ `conftest.py`, `gw/fake_exotel.py`). `make test-voicegw`
  now runs on the backend venv (voice-gw shares the engine).
- **Live-stack proof:** rebuilt the `voice-gw` container (root context) → boots healthy → drove a
  real `ws://localhost:8090/exotel/voicebot` call → persisted a phone `Intake` (`completed_at`
  set, `cost_inr=0.0293`) and metered `channel=phone` usage_events. The admin dashboard's
  `GET /admin/analytics/breakdown?channel=phone` returns them — the free instrument the S18E
  handoff promised, with no dashboard change.

## Known gaps / stubs introduced (mirrored to STATE.md)
- Real Exotel vendor WS + status-callback (`record_call_completed`) is still stubbed — S15.
- V1 continuous caller-audio streaming into a live Gemini Live session: the fake realtime
  scripts turns from the opening kick, so `_pump_v1` consumes only the opening. Real streaming
  input is the real-vendor path.
- The `PhoneCallStore` is in-memory (single-process pilot); a Redis-backed store lands with the
  S15 status-callback webhook.
- DTMF/VAD thresholds (`SILENCE_PEAK`, `UNCLEAR_PEAK`) are starting points — tune against the
  S13 language QA harness on real Alwar-accented telephony audio.

## Commits
(to be filled at commit time)
