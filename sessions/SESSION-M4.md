# SESSION-M4 — Ambient consult notes: the doctor thinking aloud

**Date:** 2026-08-06 · **Scope ref:** `sessions/SESSION-CLINICAL-INTEL-PLAN.md`
§3 + §6 (Session M4). Branch `main`. Baseline at start: backend **1,560** green.

M3 (the PACS stub) was the plan's next session and was skipped deliberately: its
external gate (§8.1 — the imaging centre registering studies under the UHC ID as
the DICOM `PatientID`) is unresolved, so it could only ever have been proven
against a fake. The handoff named M4 as the alternative for exactly that reason.

## What this session was for

Session C built a whole dictation stack and every line of it exists to produce a
**prescription**. This is the second, lighter use of the same shape: the doctor
says something mid-consult, a model turns it into four short fields and a few
tags, the doctor reads it back and confirms. Everything downstream of Session
C's signature is deliberately absent.

## Acceptance criteria, restated and checked

- [x] A floating recorder on the doctor console, present on every tab and never
      unmounting.
- [x] `ClinicalNote` + migration `02571a5c1871`: transcript never overwritten,
      `structured` JSONB, draft → confirmed, `provider_snapshot`, `prompt_refs`,
      audited.
- [x] `note_map` prompt family → S/O/A/P plus `tags{problems, symptoms, followups}`.
- [x] Review panel: transcript beside editable mapped fields; **Confirm** stores.
- [x] Mapping failure opens the fields empty beside the reason, transcript kept.
- [x] Tags, doctor-editable at confirm time, counted for analytics.
- [x] One analytics query surfaced in admin as proof of the mapping's value.
- [x] **An explicit test that no prescription artifact can originate from a note.**
- [x] Gates: backend **1,604**, notes E2E **5**, dictation E2E **8** (unchanged),
      conformance **48**, production build / `tsc` / `eslint` clean.

Not in scope and not done: notes feeding the research assistant (M5), notes on
printed sheets or in the patient app, an amendment path for a confirmed note.

## Decisions made

1. **A note cannot prescribe, and it is structural rather than a check.**
   `NoteMapping` has no medication field, so there is nowhere for a drug order to
   be parsed *into* — a model that volunteers `meds` has it dropped by a parser
   that reads five named fields and writes five. `confirm` calls nothing where
   `dictation.sign` generates a prescription and drafts a check-in plan. And
   `app/notes.py` imports neither `app.prescription`, `app.formulary`,
   `app.dictation` nor `app.checkins` — read off the source by
   `test_the_note_module_cannot_reach_the_prescription_path`, because behaviour
   tests only cover the paths somebody thought to write. It is also why
   `assert_visit_scope` is a local copy rather than an import of dictation's:
   eight duplicated lines are cheaper than a shared helper that couples the two
   paths. (`app.doctor` already carries four copies of the same check.)
2. **Several notes per visit, never merged.** `Dictation.start` reopens the
   existing draft because there is one prescription. The mic here is on the
   console for the whole consult, so something said at minute two and something
   at minute nine are two observations. Merging would mean the second capture
   rewriting the first, and the first is the one nobody can recreate.
3. **`grade_mentioned`, not `grade`.** It records that the doctor said a grade
   out loud. Null means unsaid, never mild. CTCAE grading is a clinical
   judgement and "a model may interpret or summarize; it may not decide clinical
   urgency" covers it. The admin column is headed **Grade said**, never "graded".
4. **`UsagePurpose.NOTE` rather than reusing `DICTATION`.**
   `analytics._per_dictation` divides dictation spend by the count of *signed
   dictations*, and notes produce none — sharing the purpose would inflate
   cost-per-prescription by however many observations a doctor muttered. Same
   argument `DOCUMENT` made in MRD1. No migration: the column is a varchar(11).
5. **It is a dock, not a tab.** The plan says "capturing observations *while
   browsing*", and that word decides the shape: a tab would replace the thing
   being read, which is the failure Session B built the context spine to fix.
6. **Tag counts are over confirmed notes only, and the excluded count is
   published.** A draft is a machine reading nobody checked. A week where most
   notes were never confirmed is exactly when these numbers stop representing
   the clinic, so `drafts_excluded` is a field rather than a footnote.
7. **Tags fold on case and nothing more.** "Mucositis" and "mucositis" are one
   row; deciding that "oral mucositis" and "mucositis" are the same thing is a
   clinical judgement this module has no business making. That limit is why the
   payload carries its own `basis` sentence and every surface renders it verbatim.
8. **The confirm button is green.** Confirming a note you have read is safe
   expected progress; red is clinical danger and destruction. The same argument
   that kept Session C's conclusion dialog green.

## The bug this session found in the platform

**A client chaining two writes can 404 on the first one's row.** FastAPI has torn
down `yield` dependencies *after* sending the response since 0.106 (this repo
runs 0.139), so `get_session`'s commit lands after the client already has its
200. Nothing had hit it because no client used the id from one write to make
another without a round trip in between — the dock is the first, capturing an
observation and immediately asking for it to be mapped. Against a live stack the
map arrived first and 404'd on a row that was about to exist.

Fixed inside this module: every mutating `/notes` route commits before
responding (`_settle`), so 200 means the words are on the record. Deliberately
not fixed by making the client retry a 404 — that is a contract no caller can be
written against. **The same race is latent everywhere else in the codebase**;
recorded in the handoff rather than fixed platform-wide in a session that was
not scoped for it.

## The doc 04 §5 self-critique, and what it changed

Four things the screenshots showed that reading the code did not:

1. **The drawer sat on top of the spine.** Diagnosis, allergies and the red flag
   all behind it — the exact failure this module is shaped to avoid. Worse, the
   E2E passed: Playwright's `toBeVisible` means "in the DOM with a non-zero
   box", which a covered element still has. Fixed with a 52vh drawer, matching
   bottom padding on the console while it is open, and a scroll to the spine's
   sticky offset — on the *next frame*, because in the frame that sets the flag
   the padding has not been laid out and `scrollTo` is clamped to the old
   maximum. The assertion is geometry now: the spine's bottom edge, and each
   part of it, must sit above the drawer's top.
2. **A note no model touched was badged "AI-drafted".** The condition keyed on
   `mapping_error`, so a note that arrived with no mapping at all — every word
   typed by the doctor — had the screen crediting a model. Keyed on `mapped` now,
   and it reads "yours, unconfirmed" without the amber that means model-drafted.
3. **The tags were below the fold** while the transcript column held two lines
   and a void. They are the one genuinely new thing on the screen and they
   belong beside the words they were drawn from; moving them fixed both.
4. **Two mics on the Consult tab, told apart only by position.** The FAB read
   "20 notes to review" beside the prescription path's `Dictate`. It names what
   it records first now — which mic a doctor is about to speak a drug into
   should never be worked out from a count.

The deliberate aesthetic risk (one per surface): the **level ring** on the mic —
real RMS readings off the analyser drawn as a stroke, so a doctor mid-sentence
can see the room is being heard without opening anything. Held to Session C's
rule, which is the only reason it is allowed to exist: **no ring without an
analyser**. A ring pulsing on a timer would be an animation claiming audio is
being captured, which it cannot know.

## Deviations from spec

- **The plan's `note_map` shape is unchanged, but `unclear` was not added.** The
  dictation contract has one; this one does not, because there is no drug name
  here whose exactness a patient's safety depends on, and a field the UI would
  render as "the model was unsure" invites the doctor to trust the rest.
- **The recorder was extracted rather than duplicated.** `useVoiceCapture` is
  new and `DictationPanel` now uses it. Not in the plan, but writing the Web
  Speech / `MediaRecorder` / analyser stack twice would have meant the
  no-bars-without-an-analyser rule living in two places.

## Tests & evidence

- `make test-backend`: **1,604 passed** (1,560 → +44): 24 in `test_notes.py`,
  15 in `test_notes_routes.py`, 5 in `test_analytics.py`.
- `npx playwright test --project=notes` → **5 passed** against a live stack
  (api on :8123 with `LLM_PROVIDER=fake`, web dev server on :3210,
  `scripts.seed_doctor_demo`).
- `--project=dictation` → **8 passed**, unchanged after the recorder extraction.
  `--project=conformance` → **48 passed**.
- `npm run build` / `tsc --noEmit` / `eslint` clean. `/doctor` is 35.7 kB
  (30.9 → 35.7).
- `/admin/analytics/note-tags` checked against the live stack: 2 counted, 2
  drafts excluded, `mucositis with_grade 1` beside `mouth soreness with_grade 0`.
- Screenshots: `web/screenshots/m4/01…06`, self-critiqued above.

## Migration

**One, additive: `02571a5c1871`** (`clinical_notes`). No existing row changes
meaning and no backfill runs. It joins the four already pending on Omen —
`c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3` — so there are
now **five**, all applied locally only. `make deploy` does not run migrations.

## Known gaps / stubs introduced

(Mirrored into STATE.md → Stubs & fakes.)

- **The level ring is unverified on real hardware.** Headless Chromium has no
  microphone, so the E2E exercises neither the ring nor the timer. It joins
  Session C's meter, which has the same gap for the same reason.
- **A confirmed note cannot be amended or deleted.** Same shape as a signed
  dictation, and this system still has no amendment path anywhere.
- **Notes reach no other surface.** Not the research assistant (M5, by design),
  not printed sheets, not the patient app, and no other doctor's screen
  summarises them — a colleague sees them only by opening the same visit.
- **The tag counts have no department or doctor filter.** One clinic-wide number
  over seven days; anything finer wants the filter machinery the cost tab has.

## Commits

(see `git log` on `main`, prefixed "S M4:")
