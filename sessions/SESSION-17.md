# SESSION-17 — Check-in engine

**Date:** 2026-07-26 · **Scope ref:** docs/06-BUILD-PLAN.md → S17 (doc 03 §9)

## Acceptance criteria checklist

- [x] **Sign a fixture dictation → correct plan drafted.** `tests/fixtures/checkin_dictations.json`
      carries three signed notes; signing the carboplatin one drafts a **platinum** plan with
      days [2, 7, 14] and question sets [gi_platinum, myelosuppression, myelosuppression].
      The post-op note drafts a wound plan; the routine-review note drafts **nothing**.
      (`test_checkin_plan.py`, and the walk in `test_checkin_engine.py`.)
- [x] **Doctor approves in one tap.** `POST /checkins/plans/{id}/approve`, no body. Approving
      is what materialises the `Checkin` rows; a drafted plan messages nobody.
- [x] **A simulated D+2 red answer escalates within 1 min.** It escalates **synchronously with
      the answer** — `grading.answer_one` grades on every answer, and a red one ends the
      check-in on the spot and alerts the doctor who signed plus the coordinator. The AC test
      asserts `escalated_at - before < 1 minute`; in practice it is the same request.
- [x] **Quiet hours respected.** 21:00–08:00 hospital-local, in `app.checkins.window`, called by
      both the scheduler and the ladder. A check-in due at 22:00 is deferred to 08:00 **without
      consuming an attempt**. Asserted at the unit level and inside the AC walk.
- [x] Delivery ladder WhatsApp → voice → SMS, grading rules + LLM assist, nurse review queue,
      escalation, next-cycle reminders (D-2, D-0), Celery beat scheduler.

## What was built

- **`seeds/protocols.json` + `app/checkins/protocols.py`** — the protocol bank: six regimen
  families (platinum, taxane, anthracycline, radiotherapy, post-op, palliative) and seven
  question sets, all four languages, with grading rules written in the **S4 red-flag rule
  language** and validated against the question types at load.
- **`app/checkins/plan.py`** — protocol choice (formulary *class* of the prescribed drugs +
  keywords over the structured note), the deterministic schedule, and the LLM personalisation
  that may only rewrite the covering message. Hooked into `app.dictation.sign`.
- **`app/checkins/grading.py`** — answer validation against the frozen question snapshot,
  deterministic grading, the bounded free-text assist, escalation, the nurse review queue.
- **`app/checkins/delivery.py` + `window.py`** — the ladder and the clock.
- **`app/checkins/cycles.py`** — D-2 / D-0 next-cycle reminders, reusing S15's appointment
  confirmation when there is a slot to confirm.
- **`app/routes/checkins.py`** — drafts + approve + cancel (`require_doctor`), review + resolve
  (`require_clinical`).
- **`app/whatsapp/bot.py`** — the `ck:` branch: a tap or a typed answer, one question at a time.
- **`app/worker.py`** — `opd.checkins.send` (every 10 min) and `opd.checkins.cycles` (hourly).
- **Migration `ae3caebf5e9a`**, `prompts/checkin_triage/v1.md`, `Channel.SMS`, `CheckinState`,
  four `CHECKIN_*` settings + `EXOTEL_CHECKIN_APPLET_URL`, `make checkin-demo`.
- **`GET /admin/protocol-templates`** stopped being a `{deferred}` marker and returns the real
  bank; the admin console tab renders it (and is no longer called "Coming soon").
- **`make lang-qa`** gained the protocol bank as its own surface.

## Decisions made

- **Grading rules are the tree's red-flag rules.** Same language, same evaluator, same
  validator. No second dialect to review, and `free_voice` answers are unmatchable by
  construction — a check-in grade cannot depend on how the transcriber heard an accent.
- **Green is the absence of a fired rule**, never a rule of its own. A `green` grade in the
  bank is a load error, because a green rule could quietly cancel a red one by ordering.
- **The LLM assist may raise green→amber and nothing else.** A deliberate narrowing of doc
  03 §9's "LLM assist for free text": a red rings a doctor's phone, and an amber is a nurse
  reading the sentence herself within the hour. Every reason carries its `source`.
- **The model writes wording, never schedule.** `apply_personalisation` copies messages back
  rung by rung against a draft the protocol already fixed; day offsets and question sets are
  never read from the reply. A dead model still produces a plan, in the patient's language.
- **The ladder advances on silence, not on a failed send.** A message Meta accepted and a
  patient never opened looks the same to us; a refused send advances at once, an accepted one
  waits `ANSWER_WINDOW` (6h).
- **A red answer ends the check-in immediately** rather than finishing the questionnaire. The
  nurse who rings her can ask the rest; waiting three more questions to escalate a fever would
  be this session's point, missed.
- **A check-in nobody could reach expires with no grade.** "We could not reach her" and "she
  said she is fine" are different clinical facts.
- **A doublet takes the higher-precedence family** (anthracycline > taxane > platinum >
  radiotherapy > post-op > palliative), and every family that matched is recorded on the plan
  so the approving doctor can see the choice. Precedences must be unique or the bank fails to
  load — a tie would make the choice depend on JSON ordering.
- **`app.checkins.plan` cannot fail a signature.** Every failure degrades to fewer or plainer
  check-ins, like `app.prescription`'s delivery.
- **The protocol bank stays a validated seed file**, not a table. The admin panel is read-only
  for the same reason the message-template registry is.

## Deviations from spec

- **The LLM assist cannot produce a red** (doc 03 §9 implies it could). Rationale above;
  registered in STATE → Stubs & fakes.
- **The "immediate call task" for a red is the nurse queue entry plus an SMS to the doctor and
  the coordinator**, not a task row — this pilot has no task table, and writing a half-shaped
  one for a later session to undo is the mistake S10 already named about prescriptions.
- **The SMS rung is a nudge, not a questionnaire.** Structured answers over a DLT-templated
  Indian SMS gateway does not work; the rung tells her to reply on WhatsApp or call, and what
  it buys is a human knowing to ring her.
- **The voice rung has no voice-gw handler.** `EXOTEL_CHECKIN_APPLET_URL` is empty by default
  and the rung records "not configured" and falls through to SMS, rather than dialling a
  patient into an applet that answers with silence. The handler is backlog.
- **`treatment_events` is doc 03 §9's, and it already existed** as a field on
  `DictationMapping` (S10). No new table.

## Tests & evidence

- `make test`: **1071 backend** (was 932), voice-gw 22, web typecheck + lint + 48 conformance,
  Android JVM build green. `make lang-qa` clean across [en, hi, mr, te].
- New tests: `test_checkin_protocols.py` (24), `test_checkin_plan.py` (31),
  `test_checkin_grading.py` (31), `test_checkin_delivery.py` (32), `test_checkin_engine.py` (21)
  — the last holds `test_the_session_acceptance_criterion`, the AC as one walk with the clock
  moved by hand.
- `make checkin-demo` runs the whole session against the dev database through the real
  services; output verified (platinum plan, D+2 red with both rules, D+7 left due).
- Screenshots: **none taken.** The only new UI is the admin console's protocol panel, and the
  admin console has never been seen rendered on a screen at all (S18E carryover). It is on the
  same "owed on omen" line rather than a second one.

## Known gaps / stubs introduced

(All mirrored into STATE.md → Stubs & fakes.)

- The protocol bank is **model-drafted and clinically unreviewed**, exactly like the tree bank
  — six families, seven question sets, 41 grading rules, none signed off by an oncologist.
  S21. The mr/te text is additionally unreviewed as language.
- **Nothing has been delivered to a real patient.** Every rung is proven against the provider
  fakes, like every other channel's first-send caveat.
- **The voice rung is unbuilt** past `place_call` (no voice-gw check-in applet).
- **The free-text assist has never run on a real model** — `FakeLLMProvider` has no canned
  `checkin_triage` reply, so on a fake stack it degrades to no assist.
- **`checkin_personalize` has no canned fake reply either**, so a local demo shows the plain
  four-language message rather than a personalised one.
- **One protocol per plan.** A carboplatin/paclitaxel doublet is followed on the taxane
  protocol only; the platinum GI questions are not merged in.
- **No patient-app surface for check-ins** — the app is a fourth channel that would already
  know the patient, and it is the cheapest one. Backlog.

## Commits

- f5c05a2 — S 17: the protocol bank — six regimen families, and grades that reuse the red-flag rules
- 9e4cc3b — S 17: what a check-in plan and a check-in have to remember
- 301ed2e — S 17: signing drafts the plan — the protocol picks the days, the model picks the words
- 7aadea0 — S 17: the grade is the rules, and the model only gets a nurse's attention
- 406b8f6 — S 17: the delivery ladder — WhatsApp, voice, SMS, and a night nobody is woken in
- 9e6ec0c — S 17: the check-in a patient answers, the queue a nurse works, and the next cycle
- 873d6d4 — S 17: the admin console stops promising protocol templates and shows them
- d82dec7 — S 17: make checkin-demo — the session end to end against the dev database
