# SESSION-15 — Telephony part 2: inbound appointments + outbound campaigns

**Date:** 2026-07-25 · **Scope ref:** docs/06-BUILD-PLAN.md → S15

## Acceptance criteria checklist
- [x] AC1 — **Fake-client books / reschedules / cancels against real slots**
      (`voice-gw/tests/test_receptionist_call.py`: real wire protocol → real driver →
      real rows in Postgres; service-level twin in `backend/tests/test_receptionist.py`).
- [x] AC2 — **Double-booking fails safely**: `UNIQUE(slot_id, seat_no)` +
      `CHECK(0 <= booked <= capacity)` + a conditional seat claim; the second caller gets
      `SlotUnavailable` → a 409 on HTTP and a re-offer on the phone, never a 500
      (`test_scheduling.py`, `test_appointments_routes.py`).
- [x] AC3 — **Campaign dry-run produces the correct call list** — including who is *not*
      called and why (`test_campaign.py`); `make campaign-dryrun` prints it.
- [x] doc 03 §2 AC — **every booking generates WhatsApp + SMS** (`app/notify.py`, asserted
      in the receptionist and route tests; proven live in the `reminders` JSONB).
- [x] Inbound receptionist **intents** (book / reschedule / cancel / status / human), and
      **human handoff with a whisper summary** on 2 failed turns (doc 01 §4.4).
- [x] **D-1 campaign**: Celery beat, 2-attempt retry ladder, WhatsApp fallback.
- [x] S14's carry: **Exotel status callback → `record_call_completed`**, and a
      **Redis-backed `PhoneCallStore`**.

## What was built
- **`app/scheduling.py`** — slot inventory + constraint-safe booking. `SlotTemplate`
  (the clinic grid, admin-configured) → `generate_slots` materialises
  `AppointmentSlot` rows → `book`/`reschedule`/`cancel` move `Appointment` rows.
  A **seat is a row**: booking claims `seat_no` under `UNIQUE(slot_id, seat_no)`, and
  cancelling NULLs it (releasing the seat, keeping the history).
- **`app/receptionist.py`** — the inbound AI receptionist. One model call
  (`prompts/receptionist/v1.md`) turns the caller's sentence into an intent and decides
  nothing else; the rest is a state machine over real inventory. Slots are chosen by
  keypad digit (spoken numbers accepted), a slot taken mid-call is re-offered, and two
  failed turns transfer to a coordinator with a whisper line.
- **`app/notify.py`** — confirmations on **both** channels. Out-of-window WhatsApp uses a
  registered template; in-window uses the one-tap confirm/cancel buttons. A failed send is
  recorded on `Appointment.reminders`, never raised — it cannot undo a booking.
- **`app/campaign.py` + `app/worker.py`** — the D-1 campaign as four idempotent steps
  (plan / launch / dial / reconcile) with the retry ladder living in an `outbound_calls`
  row. Celery beat gets its first real jobs; the job bodies are plain coroutines and the
  schedule is plain data, so both are tested without a broker.
- **`app/routes/appointments.py`** — staff slot/booking REST (409 on a lost race) plus the
  **Exotel status callback**, which meters the call's minutes and always 200s.
- **`voice-gw/gw/reception.py` + `WS /exotel/receptionist`** — the appointment line over
  S14's transport, pump and VAD. Keypad *semantics* moved into a parameter of
  `ExotelTurnSource` (1/2 = yes/no on the intake line; "the time you want" here).
- **WhatsApp one-tap confirm/cancel** (`app/whatsapp/bot.py`), authorised by the number the
  tap arrives from.
- **Migration `48da92857b2a`**, `seeds/slot_templates.json` (12 clinics over 5 doctors),
  `make slots`, `make campaign-dryrun`.

## Decisions made
- **Double-booking is unrepresentable, not merely checked.** Capacity-aware slots plus a
  unique seat row: even if the counter logic were wrong, two appointments cannot hold seat
  1 of the same slot. Do not "simplify" this to a `booked < capacity` read-then-write.
- **The receptionist is not the `IntakeEngine`.** No tree, no red flags, no clinical
  content — folding appointments into the intake engine would put a non-clinical dialogue
  inside the thing that owns clinical truth.
- **Every classifier failure is a coordinator, never a retry loop.** Invented intent, low
  confidence, dead provider, unknown caller, no free slots — all end on a human with a
  whisper summary.
- **Both confirmation channels, always** (not WhatsApp-then-SMS-on-failure): doc 03 §2's AC
  says both, and the handset that answered the call is often not the one with WhatsApp.
- **The campaign ladder is a database row**, because it spans processes and hours (worker
  dials, webhook settles). `campaign_enabled` is **off** by default: a box that boots with
  real Exotel credentials must not start ringing patients because beat came up.
- **A confirmation can never fail a booking.** Send errors are recorded, not raised.

## Deviations from spec
- **Language detection from the caller's greeting (doc 01 §4.4) is not done** — the applet
  passes `lang`. Detecting it needs a full STT round-trip *before* the first prompt, which
  costs the caller seconds of silence on pickup; the number is provisioned per language
  instead. Noted in HANDOFF's backlog.
- **"Cancellations … notify waitlist" (doc 03 §2) is not built** — cancelling releases the
  seat, which is the half that matters for the AC; there is no waitlist table yet. Backlog.
- **`transfer_call` on the real Exotel provider is a second `connect` with the whisper in
  `CustomField`** — Exotel has no "transfer this live leg" REST verb, and the applet that
  reads the whisper to the coordinator is a console artefact, not code. Owed a live smoke.
- **mr/te receptionist copy is romanized placeholders**, same carry as the S14 consent line
  (S21 native + clinical review).

## Tests & evidence
- `make test` — **green**: backend **840 → 907**, voice-gw **15 → 22**, web typecheck +
  lint + 48 conformance. `make lang-qa` clean across [en, hi, mr, te].
- New: `test_scheduling.py`, `test_receptionist.py`, `test_campaign.py`,
  `test_appointments_routes.py`, `test_worker.py`, `voice-gw/tests/test_receptionist_call.py`
  (+ DB fixtures in the voice-gw conftest, which now needs Postgres).
- **Live-stack proof** (rebuilt `api`/`voice-gw`/`worker`/`beat`, `make migrate && make seed
  && make slots` → 12 templates, 802 slots):
  - a real `ws://localhost:8090/exotel/receptionist` call — greeting → offer → DTMF `1` —
    **booked a real slot**: `status=confirmed, source=phone, seat_no=1, booked=1/3`, with
    1272 assistant media frames streamed back;
  - its `reminders` JSONB carries `whatsapp: sent` + `sms: sent`;
  - `plan_campaign` for that day lists exactly that patient
    (`Jackson Bora <+915551900001> hi — 2026-07-27 09:30`);
  - beat fired `opd.campaign.fallback` and the worker returned `campaign disabled` — the
    flag doing its job on a live box.

## Known gaps / stubs introduced
- No waitlist (cancellation releases the seat, notifies nobody).
- Exotel `transfer_call` + the whisper applet are unproven against the vendor.
- The campaign has never dialled a real number (`campaign_enabled=false` everywhere).
- Slot templates are seed/JSON-edited; the admin slot-template editor is still S18-late.
- Receptionist mr/te copy is romanized (S21).

## Commits
30ef564 — S 15: slot inventory that cannot be double-booked
d8e912a — S 15: the AI receptionist — intents, real slots, a whisper handoff
29fb749 — S 15: the D-1 campaign, its retry ladder, and the appointment API
69513a6 — S 15: the appointment line — a second Exotel socket over the receptionist
2287d39 — S 15: one-tap confirm/cancel on WhatsApp, and the two operator commands
