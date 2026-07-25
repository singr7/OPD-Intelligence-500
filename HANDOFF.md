# HANDOFF — after Session S15 (Telephony part 2: inbound appointments + outbound campaigns)

> **Operator's current priority (2026-07-22):** the pilot is **deployed live** on
> an on-prem RTX 4090 box with **STT + LLM + TTS all local** (kiosk voice-in via
> Whisper, routing/summaries via Qwen3, read-aloud via a Kokoro `/tts` container —
> zero cloud AI) at `https://opd.radpretation.ai`.
>
> **⚠️ CI is off (2026-07-23, operator).** `.github/workflows/ci.yml` is intact but
> its `push`/`pull_request` triggers are commented out. Run it by hand:
> `gh workflow run ci.yml`. **`make test` locally is the only gate** — plus `make lang-qa`.
>
> **🚩 Adaptive intake (S-ADAPT) is on `main` but NEVER PROVEN with its flags on.**
> `INTAKE_ADAPTIVE=0` / `NEXT_PUBLIC_KIOSK_ADAPTIVE=0` are the defaults. Unchanged by S15.

**Repo state:** **`main`**, last commit `S 15: session close`. `make test` green: backend
**907** (was 840), voice-gw **22** (was 15), web typecheck+lint+**48** conformance.
`make lang-qa` clean across [en,hi,mr,te]. **Migration `48da92857b2a`** (slot inventory,
appointment seats, outbound calls) — run `make migrate` before anything. Postgres on host
port **5433**; voice-gw on **8090**. **The mainline sequence resumes at S16.**

⚠️ `make lint` still fails on the **same pre-existing unformatted files** (none of S15's —
new code is `ruff format`-clean) plus one stale unused import in `tests/test_tree_bank.py`.
Not in `make test`. Still worth one `ruff format .` commit.

**One paragraph:** S15 gave the phone line something to *do* besides intake. `app/scheduling.py`
is slot inventory where **a seat is a row** — `UNIQUE(slot_id, seat_no)` plus
`CHECK(0 <= booked <= capacity)` make double-booking unrepresentable rather than merely
checked, and cancelling NULLs the seat to release it. `app/receptionist.py` is the inbound AI
receptionist: one distrusted model call picks the intent (book/reschedule/cancel/status/human)
and decides nothing else; slots are chosen by keypad digit; every failure — invented intent,
low confidence, dead provider, unknown caller, no free slots, two bad turns — ends on a
coordinator with a **whisper summary**. `voice-gw` grew a second socket
(`WS /exotel/receptionist`) running it over S14's transport, pump and VAD. Every booking fans
out to **WhatsApp + SMS** and can never be undone by a failed send. The **D-1 campaign** is
four idempotent beat jobs with its 2-attempt ladder in an `outbound_calls` row and a WhatsApp
last rung, off by default. Proven live on the local stack: a real websocket call booked a real
slot (`confirmed / phone / seat 1 of 3`), both confirmations recorded, and the dry run listed
exactly that patient for D-1.

## Next session — S16 (Android app)
- Objective: Kotlin/Compose app — OTP login, My Cancer Care File (offline), Talk-to-Dhara home
  intake (native speech), live queue position, medicine reminders (WorkManager, exact alarms),
  chemo calendar with audio clips, caregiver link; <15MB, minSdk 26.
- **Load:** doc 03 §1c, doc 04 §3.
- **AC:** instrumented tests for offline file + reminders; full home-intake flow on emulator;
  APK size check in CI.
- **What S15 gives it:** `/appointments` (slots, book, reschedule, cancel, a patient's upcoming
  list) is the appointment surface the app can call today — same rules as the phone line,
  because both go through `app.scheduling`. The app is a **new client of existing APIs**; resist
  adding app-only endpoints.
- **Start from `main`.** First commands:
```
make dev && make migrate && make seed && make slots   # 12 slot templates -> ~800 slots
make test                                             # expect 907 backend / 22 voice-gw green
make lang-qa                                          # expect clean across [en,hi,mr,te]
```

## Watch out for (S15 fragile edges)
- **The voice-gw test suite now needs Postgres.** `voice-gw/tests/conftest.py` grew
  `db_session` / `call_sessionmaker` fixtures (mirroring the backend's rolled-back-transaction
  pattern) because a receptionist call's whole job is writing an appointment. It also needed
  `asyncio_default_test_loop_scope = "session"` — without it, asyncpg raises "attached to a
  different loop".
- **Keypad meaning is now a parameter, not a constant.** `ExotelTurnSource(dtmf_answers=…)`:
  the intake line keeps `1=yes/2=no` (doc 03 §1b) and the appointment line passes `{}` so a
  digit stays a digit. Do not re-hardcode `DTMF_ANSWERS` inside the turn source.
- **`app.receptionist` is not the `IntakeEngine`** and must not be folded into it — no tree,
  no red flags, no clinical content. Likewise `gw/reception.py` is deliberately a second
  driver, not a flag on `gw/call.py`.
- **`campaign_enabled` off is load-bearing.** With it on, `assert_production_safe` also
  demands `EXOTEL_WEBHOOK_TOKEN` — an unauthenticated status callback lets anyone mark a
  patient's call complete and silently cancel their remaining retry.
- **Slots are materialised, not computed.** `make slots` (or the 02:30 beat job) must have run
  for the horizon, or the receptionist truthfully says it has no times and hands off. A fresh
  box needs `make seed && make slots`.

## Decisions needed from the human
- **A live Exotel number + creds** — now blocking *two* proofs: the S14 intake bridge and the
  S15 receptionist/campaign. Everything is fake-client-proven only.
- **Who is the coordinator on `COORDINATOR_PHONE`**, and does the Exotel console have a
  whisper applet to read the handoff line to them? The transfer is a second `connect`; the
  applet that speaks the whisper is console configuration, not code.
- **mr/te still need a native + clinical review before a patient reads them** (S21). S15 adds
  the receptionist phrasebook + appointment SMS/WhatsApp copy to that pile.

## Owed on omen (before adaptive / mr-te / the phone path face real use)
- **Live Exotel smoke, both applets** — point one Voicebot applet at `wss://…/exotel/voicebot`
  (intake) and one at `wss://…/exotel/receptionist` (appointments); take a call on each. *(S15
  widens the S14 item)*
- **Turn the campaign on for one evening, on real numbers** — `CAMPAIGN_ENABLED=true` plus the
  applet/callback URLs, then watch `outbound_calls` walk its ladder. *(new)*
- **Phone-on-GPU contention** — phone's `[v_oss, v2, v3]` ladder shares the kiosk's local-GPU
  `max_oss_sessions: 12` pool. Watch admission shedding under real concurrent load (S-OSS.2).
- **Admin console visual pass** (S18E) — walk the six tabs, publish a tree edit, confirm it
  changes a live kiosk intake. Carried.
- **Telugu kiosk render** — తెలుగు glyphs (not tofu), ≥1.6 line-height at 200% (doc 04 §4). Carried.
- **Adaptive on** — flags to `1`, mark 1–2 live-tree nodes `adaptive: true`, re-seed, rebuild
  api+web. Rollback = flags to `0` + rebuild. Carried.
- **Doctor console + consult note on-box** (`/doctor`, `+915550001001`) — real-Qwen3 dictation
  `_was_said` pass still owed; `make eval-dictation` wants the same session. Carried.

## Backlog additions (S15)
- **Appointment waitlist** — doc 03 §2's "cancellations release slots and notify waitlist" is
  half-built: the seat is released, nobody is notified. Needs a waitlist table (S18-late).
- **Language detection from the caller's greeting** (doc 01 §4.4) — the applet passes `lang`
  today, because detecting it needs a full STT round-trip before the first prompt.
- **Arrival/check-in turns an `Appointment` into a queue entry** — the doctor's day list is
  still walk-ins only (doc 03 §5 wants both), and doc 01 §4.2's "registration scans phone →
  token issued instantly" has no endpoint yet. Suggest S18-late or wherever registration lands.
- **Admin slot-template editor** — `SlotTemplate` now exists; the console still returns the
  `{deferred}` marker. S18-late.
- **Campaign observability** — no dashboard panel for reach/answer/fallback rates; the data is
  all in `outbound_calls`. Pairs with the S18E analytics work.
- Carried, unchanged: `make lint` red on pre-existing files; mr/te unreviewed (S21); Telugu
  never seen rendered; admin console never seen on a screen (S18E); tier-mix what-if (S18-late);
  V1 continuous caller-audio streaming into a live Gemini Live session; surface STT confidence
  to the channel instead of the energy proxy; tune VAD/DTMF thresholds on real Alwar telephony.
