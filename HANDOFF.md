# HANDOFF — after SESSION-M4

**Repo state:** branch `main`. `make test-backend` **1,604 passed**. Notes E2E 5,
dictation E2E 8, conformance 48, `npm run build` / `tsc` / `eslint` clean.
**M4 added one migration** — `02571a5c1871` (`clinical_notes`), additive, no
backfill. There are now **five** pending on Omen: `c6e3681f5ce1`,
`520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3`, `02571a5c1871`. All applied
locally only, and `make deploy` does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands:** two of the four Clinical Intelligence modules
(`sessions/SESSION-CLINICAL-INTEL-PLAN.md`) are complete. **MRD** (M1–M2): the
desk photographs a patient's reports, the pipeline reads them, and the doctor's
console states what is on file before they open anything — deploying it is
`docs/22-MRD-DEPLOY.md`, read that before touching a box. **Ambient notes**
(M4, this session): a mic floats over every tab of the doctor console, an
observation becomes four S/O/A/P fields and a few tags, the doctor confirms, and
admin counts symptom burden and follow-up debt across the clinic. Session log:
`sessions/SESSION-M4.md`.

## Next session — M5 (research assistant), or M3 if the gate has moved

`SESSION-CLINICAL-INTEL-PLAN.md` §4 + §6 "Session M5": context assembly the
doctor can see before it is sent, thread models + migration, a `research_assist`
prompt family, the panel, storage/audit, cost guard, provider-down state. **It is
the natural next one now** — its context is assembled from exactly what the
first three modules produce: the spine's signed-note diagnosis, M1's computed
lab flags, and M4's confirmed note tags, all three of which now exist.

**M3 (PACS stub) is still parked on its external gate** (plan §8.1): until the
imaging centre registers studies under the UHC ID as the DICOM `PatientID`,
lookup returns empty for every patient and the module can only be proven against
a fake. If that agreement has landed since this session, M3 is half a session and
worth taking first. Read `ContextSpine.tsx`'s header before adding its
`Images (n)` slot — M4 did *not* take a sixth slot, deliberately, and the
argument for refusing one is written there.

The other candidates are unchanged: allergy capture (still the largest gap in
the spine), deploying the five pending migrations to Omen, and a correction path
for a signed note or a concluded consult — to which M4 adds a confirmed note.

## Watch out for

- **A client chaining two writes can 404 on the first one's row, everywhere
  except `/notes`.** FastAPI has torn down `yield` dependencies *after* sending
  the response since 0.106 (this repo runs 0.139), so `get_session`'s commit
  lands after the caller already has its 200. M4 was the first client to use an
  id from one write in the very next request with no round trip between, and it
  404'd against a live stack roughly one time in three. `/notes` now commits
  before responding (`app/routes/notes.py::_settle`); **the same race is latent
  on every other router** and will surface the next time a client chains two
  writes. Fix it there when it does, the same way — a 404 that resolves itself
  is a contract nobody can write against.
- **`app/notes.py` must not import `app.prescription`, `app.formulary`,
  `app.dictation` or `app.checkins`**, and `NoteMapping` must not grow a
  medication field. Both are pinned (`test_the_note_module_cannot_reach_the_
  prescription_path`, `test_the_note_contract_has_no_medication_field`). This is
  the module's whole safety argument: a note maps prose and generates nothing,
  so a drug the doctor dictates stays prose in `plan_narrative`.
- **`grade_mentioned` means "the doctor said a grade", not "graded".** Null is
  *unsaid*, never mild. The admin column is headed "Grade said" for the same
  reason. Nothing in this system grades a symptom and nothing may start to look
  as though it does.
- **Screenshots find what code review does not, and `toBeVisible` is not a
  visibility assertion.** The notes E2E passed while the drawer sat squarely on
  top of the context spine — Playwright's visibility means "in the DOM with a
  non-zero box". The spec now compares bounding boxes. Any future surface that
  overlays the console should assert the same way.
- **`.nd-drawer`'s 52vh and the console's 54vh bottom padding are one decision
  made twice.** Change either and the spine goes back behind the drawer.
- **`boundingBox()` on a locator matching nothing waits with no timeout.** It
  hung an E2E run for nine minutes instead of failing it. Call `count()` first.
- **Do not run `npm run build` while the E2E dev server is up.** It overwrites
  `.next` under the running server and every subsequent page load 404s its
  chunks — it looks exactly like a login regression. Cost most of an hour.
- **`scripts.seed_doctor_demo` fails against a database that has scanned
  documents on today's demo visits** — it deletes the visits and trips the
  `medical_documents` FK. Clear `document_extractions` then `medical_documents`
  first (dev boxes only). It will now hit `clinical_notes` the same way.
- **The 30-second OTP resend cooldown still bites**, and the `notes`, `doctor`
  and `dictation` projects all take a token for the same seeded doctor.
- Everything from the previous handoff still holds: the page backup has never
  run for real (doc 22 §5, `pages checked: N` must be non-zero), dump before
  sync and never `--delete`, the MRD extraction contract has no flag field,
  `seeds/lab_reference_ranges.json` ships `review_pending`,
  `/records/scan/failures` must never grow an `extraction` field, page images
  are fetched and not `src`-ed, extraction needs a vision-capable
  `LLM_PROVIDER`, the doctor console's token key is `opd_staff_token`,
  `queue.today()` is the operating day, and nothing captures an allergy.
