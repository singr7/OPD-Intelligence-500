# HANDOFF — after Session S14 (Telephony voice gateway (Exotel) part 1: pipeline)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out. Run it by hand:
> `gh workflow run ci.yml`. **`make test` locally is the only gate right now** — and
> S13 added `make lang-qa` (also a CI step and a pytest test).
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on.**
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults. Unchanged by S14.

**Repo state:** **`main`** — S14 built on it. `make test` green: backend **840**, voice-gw
**15** (was 1), web typecheck+lint+**48** conformance. `make lang-qa` clean across [en,hi,mr,te].
**No migration in S14** — phone intakes reuse the existing `patients/visits/intakes/usage_events`
tables (`Channel.PHONE` already existed). Postgres on host port **5433**; voice-gw on **8090**.
**The mainline sequence resumes at S15.**

⚠️ `make lint` is still **failing on the same 11 pre-existing unformatted files** (none S14's —
the new code is `ruff format`-clean). Not in `make test`. Worth one `ruff format .` commit.

**One paragraph:** S14 turned `voice-gw` from a bare health route into the **phone channel
adapter over the shared `IntakeEngine`**, in-process (doc 02 §5; no network hop in the turn
loop). The Exotel Voicebot websocket (`gw/exotel.py` codec, `gw/call.py` driver, `WS
/exotel/voicebot`) bridges phone audio to the same engine the kiosk and WhatsApp use — V1
Gemini Live and V2 STT↔TTS both proven, with barge-in, DTMF fallback, a consent line, an
8-minute cap, partial-save-on-hangup, per-minute metering and per-intake cost. A `channel=phone`
usage row now flows straight into the S18E admin dashboard with **no dashboard change** — proven
live: a real WS call persisted a phone `Intake` with cost and the dashboard's phone-filtered
breakdown returns it. The one engine change is additive: `IntakeEngine.run(turn_source=)` for
streaming turns; the fixed-`turns` path (kiosk, all existing callers) is byte-for-byte unchanged.

## Next session — S15 (Telephony part 2: inbound appointments + outbound campaigns)
- Objective: slot inventory + booking APIs (constraint-safe); inbound AI receptionist intents
  (route the caller's spoken chief complaint to a department/tree — S14 deferred this); human
  handoff with whisper summary; D-1 outbound intake campaign (Celery beat, retry ladder,
  WhatsApp fallback); confirmations WhatsApp+SMS.
- **Load:** doc 03 §2, doc 01 §4.2/4.4.
- **AC:** fake-client books/reschedules/cancels against real slots; double-booking test fails
  safely; campaign dry-run produces correct call list.
- **S14 gives S15 its dialer:** `voice-gw`'s `handle_call` + `PhoneCallRecord` are the call
  runtime; S15 wires the **Exotel status-callback webhook → `TelephonyProvider.record_call_completed`**
  (the per-call duration metering the provider stub already models) and reconciles it against the
  in-memory `PhoneCallStore` (make it Redis-backed here).
- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make test    # expect 840 backend / 15 voice-gw green
make lang-qa                                           # expect clean across [en,hi,mr,te]
```

## Watch out for (S14 fragile edges)
- **voice-gw now bundles `backend/`** via a **root build context** (`docker-compose` →
  `voice-gw.build.context: .`, `dockerfile: voice-gw/Dockerfile`; a root `.dockerignore` keeps
  it lean). The repo tree is mirrored under `/app` (backend at `/app/backend`) so `app.tiers`
  config (`/app/config`) and `app.trees.bank` (`/app/seeds`) resolve — **both are mounted** in
  compose. A fresh box needs `make seed` for the tree bank, same as api.
- **`make test-voicegw` runs on the *backend* venv** (voice-gw shares the engine) via
  `PYTHONPATH`. A stale `voice-gw/.venv` is no longer used for tests.
- **Barge-in / DTMF are channel-side energy heuristics, not engine signals** — `SILENCE_PEAK`
  ends an utterance, `UNCLEAR_PEAK`×2 triggers the keypad. They stand in for real VAD / STT
  confidence and want tuning on real telephony audio (S13 harness).
- **The streaming turn-source is additive** — `run(turns=…)` is unchanged; only `run(turn_source=…)`
  is new. Do not "simplify" by removing the fixed-`turns` path; kiosk/tests depend on it.
- **`finalize_cost` needs `intake.visit` eager-loaded** in a fresh session (async lazy loads
  raise `MissingGreenlet`); the driver `refresh`es it — mirror that in any new voice-gw DB path.

## Decisions needed from the human
- **mr/te still need a native + clinical review before a patient reads them** (S21). S14 adds the
  **phone consent line + DTMF prompts** to that pile — currently romanized placeholders.
- A **live Exotel number + creds** to smoke the real vendor bridge on the box (fakes only so far).

## Owed on omen (before adaptive / mr-te / the phone path face real use)
- **Live Exotel smoke** — point a real Exotel Voicebot applet at `wss://…/exotel/voicebot`, take
  one call each on V1 and V2, confirm audio both ways, barge-in, and a phone intake on the board
  + the cost dashboard. Cheapest proof of the S14 headline on real telephony. *(new)*
- **Phone-on-GPU contention** — phone's `[v_oss, v2, v3]` ladder shares the kiosk's local-GPU
  `max_oss_sessions: 12` pool. Watch admission shedding under real concurrent load (S-OSS.2). *(new)*
- **Admin console visual pass** (S18E) — walk the six tabs, publish a tree edit, confirm it
  changes a live kiosk intake. Carried.
- **Telugu kiosk render** — తెలుగు glyphs (not tofu), ≥1.6 line-height at 200% (doc 04 §4). Carried from S13.
- **Adaptive on** — flags to `1`, mark 1–2 live-tree nodes `adaptive: true`, re-seed, rebuild
  api+web. Rollback = flags to `0` + rebuild. Carried.
- **Doctor console + consult note on-box** (`/doctor`, `+915550001001`) — real-Qwen3 dictation
  `_was_said` pass still owed; `make eval-dictation` wants the same session. Carried.

## Backlog additions (S14)
- **Speech→department routing for inbound calls** (doc 03 §2) — S15 (the receptionist intents).
- **Redis-backed `PhoneCallStore`** — pairs with the S15 status-callback webhook.
- **Real Exotel `TelephonyProvider` impl + status callback** → `record_call_completed` — S15.
- **V1 continuous caller-audio streaming** into a live Gemini Live session (the fake scripts turns
  from the opening kick today) — with the real realtime vendor impl.
- **Surface STT confidence to the channel** so DTMF fallback keys off it instead of the energy proxy.
- **Tune VAD/DTMF thresholds** on real Alwar-accented telephony audio (S13 harness).
- Carried, unchanged: `make lint` red on 11 pre-existing files; mr/te unreviewed (S21); Telugu
  never seen rendered; admin console never seen on a screen (S18E); tier-mix what-if (S18-late).
