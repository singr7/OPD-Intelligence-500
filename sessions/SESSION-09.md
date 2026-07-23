# SESSION-09 — Doctor console + summary view

**Date:** 2026-07-22/23 · **Scope ref:** docs/06-BUILD-PLAN.md → S9 · **Branch:** `feat/doctor-console`

## Acceptance criteria checklist
- [x] **Phone-OTP login** — lifted from the S8 coordinator login, as intended; same
  `/auth` endpoints, same `opd_staff_token` key so one staff session covers a shift.
- [x] **Day list** — `GET /doctor/day`, the doctor's own department queue in the queue's
  own urgent-first order, with the patient behind each token.
- [x] **Patient card** — summary hero (doc 03 §4 contract), red-flag strip, answers
  accordion, visit timeline, symptom sparklines. `GET /doctor/patients/{visit_id}`.
- [x] **Call-next / no-show / lab-requeue wired to the S8 queue** — imported, not
  rebuilt (`app.queue.call_next` / `set_state` via the existing `/queue/*` routes).
- [x] **Keyboard shortcuts** — N calls the next patient; D reports that dictation is S10
  rather than pretending to have it.
- [x] **Doctor completes a full morning simulation on seed data** — `web/e2e/doctor.spec.ts`,
  project `doctor`: sign in → read the urgent card → call next → lab re-queue → no-show →
  consults completed → list empty.
- [x] **Summary renders ≤20s-scannable** — red flags first, concern, compact symptoms
  table, everything else collapsed; screenshots self-critiqued below.
- [x] **Every action audited** — the queue verbs write `Visit.status`, which is `Clinical`
  and therefore audited by the `before_flush` hook; asserted in
  `test_doctor_drives_the_queue_verbs_and_the_change_is_audited`.

## What was built
- **`backend/app/doctor.py`** — two reads, no writes. `day_list` (the department queue
  with patient identity attached) and `patient_card` (stored §4 summary, rule-engine red
  flags, answers rendered against the tree in `tree_ref`, visit timeline, check-in trends).
- **`backend/app/routes/doctor.py`** — `GET /doctor/day`, `GET /doctor/patients/{visit_id}`,
  both `require_doctor`. Registered in `app/main.py`.
- **`backend/scripts/seed_doctor_demo.py`** — five MEDONC walk-ins for the seeded
  Dr. Anil Gupta, answers played through a real `Walk`, red flags from `walk.red_flags()`,
  plus visit history and check-in trends.
- **`web/app/(doctor)/doctor/`** — `Console.tsx` (orchestrator, shortcuts, actions),
  `DayRail.tsx` (the spine), `PatientCard.tsx` (the hero), `Sparkline.tsx`, `Login.tsx`,
  `consoleStyles.ts`, `_lib/doctor.ts` (typed client), `_lib/session.ts`.
- **`web/e2e/doctor.spec.ts`** + playwright project `doctor` + `npm run e2e:doctor`.
- **`backend/tests/test_doctor.py`** — 22 tests.

## Decisions made
- **S9 adds no action endpoints.** The doctor's call-next / no-show / lab-requeue are the
  same `app.queue` verbs the coordinator console drives. A doctor-flavoured copy of the
  queue state machine would drift within a session or two and give the board and the
  console two orders that disagree. *Do not add `/doctor/call-next` later.*
- **The card never re-derives clinical judgement.** Red flags are read from
  `Intake.red_flags` (rule engine) and the summary from
  `summary_lang_versions[...]["structured"]` (summarizer). A doctor screen that re-decided
  a flag would disagree with what the kiosk told the patient and what the queue prioritised.
- **Flagged answers come from the fired rule's `when` condition**, not just `source_node`.
  `source_node` is only populated for node-level sugar; the clinically interesting rules are
  multi-node (`fever ≥38 AND within 14 days of chemo`) and carry none, so highlighting on
  `source_node` alone silently left the febrile-neutropenia patient's fever unmarked. The
  fired *set* is still the rule engine's — this only asks the tree which questions each
  fired rule was about.
- **`/doctor/*` is `require_doctor`, not `require_staff`.** It is the one surface returning
  name + phone + answers + history together, which is more than a queue coordinator needs.
- **The card is scoped to the doctor's department** and raises rather than returning an
  empty card: "not yours" and "not there" are different answers.

## Deviations from spec
- **Appointments are not in the day list.** doc 03 §5 says "appointments+walk-ins"; the S8
  queue holds walk-ins, and `Appointment` has no check-in flow until S15. The worklist is
  therefore the queue only. Noted as backlog rather than faked.
- **"Start dictation" is not one of the four one-tap actions.** Dictation is S10; the D
  shortcut is wired and says so rather than showing a dead button.

## Tests & evidence
- `make test`: **green** — backend **603** (581 → 603), voice-gw 1, web typecheck + lint
  clean, 48 conformance.
- New tests: `backend/tests/test_doctor.py` (22) + `web/e2e/doctor.spec.ts` (7, live stack).
- **Baseline fix:** the run started red — `opd_test` was stamped at `a1b2c3d4e5f6`, the
  S-ADAPT migration from `feat/adaptive-intake`, which does not exist on this branch, so
  `alembic upgrade head` could not compute a path. Reset the test schema. See HANDOFF.

### Screenshots (`web/screenshots/s9/`) — doc 04 §5 self-critique
- `01-login.png` — same card as the coordinator's, doctor badge in green not marigold.
  Quiet, does one thing. Fine.
- `02-day-and-card.png` — the 20-second read works: danger stamp → name → concern →
  symptoms. The spine reads as a line being worked down, and the one filled marigold node
  is unambiguous. Semi-priority token 15 sitting above routine 14 is the queue's ordering
  showing through correctly, not a sorting bug.
- `03-red-flag-strip.png` — **critique applied here.** The rule's `instruction` is
  patient-facing copy ("Please tell a nurse now, before you sit down"), and unlabelled it
  read as an instruction to the doctor. Now prefixed "PATIENT WAS TOLD:", which also tells
  the doctor what the kiosk already said. Kept, labelled.
- `04-card-expanded.png` — flagged answers (fever, days-since) highlighted from the
  multi-node rule; sparklines read at a glance (fatigue rising red 3→8, pain falling green
  6→3). The appbar appearing mid-image is a `fullPage` + `position:sticky` artifact, not a
  layout bug.
- `05-called-next.png` — after N, the marigold node has moved and the card followed.
- `06-morning-cleared.png` — empty state says where tokens come from rather than "no data".

## Known gaps / stubs introduced
(Mirrored into STATE.md → Stubs & fakes)
- The demo's **structured summaries are authored fixtures** standing in for the LLM path;
  the deterministic V3 `TemplateSummarizer` emits no symptom table. Answers and red flags
  in the same seed are genuinely derived.
- **No appointments in the day list** (above).
- **The doctor's staff token is localStorage**, shared with the coordinator console —
  same S19/S20 hardening note as S8.
- **`/doctor/*` has no WebSocket.** The console refetches after its own mutations; a
  coordinator moving the same line elsewhere is not pushed to the doctor until they act.
- **Check-in trends read a shape S17 does not write yet** — `Checkin.responses` numerics
  are read defensively, so sparklines light up when S17 lands and stay empty until then.

## Commits
- `3d1cdcc` — S 9: doctor console read models — day list + patient card
- `b465625` — S 9: seed a doctor's morning for the console demo
- `eed52d1` — S 9: the doctor console — day rail, patient card, queue actions, shortcuts
