# HANDOFF — after SESSION-M5

**Repo state:** branch `main`. `make test-backend` **1,660 passed**. Research
E2E 7, notes 5, dictation 8, conformance 48, voice-gw 25, `npm run build` /
`tsc` / `eslint` clean. **M5 added one migration** — `9f2ab41c77d3`
(`research_threads`, `research_turns`), additive, no backfill. There are now
**six** pending on Omen: `c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`,
`efb79a43afb3`, `02571a5c1871`, `9f2ab41c77d3`. All applied locally only, and
`make deploy` does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands:** **three of the four Clinical Intelligence modules
are complete** (`sessions/SESSION-CLINICAL-INTEL-PLAN.md`). **MRD** (M1–M2): the
desk photographs a patient's reports, the pipeline reads them, the console
states what is on file before the doctor opens anything — deploying it is
`docs/22-MRD-DEPLOY.md`, read that before touching a box. **Ambient notes** (M4):
a mic floats over every tab, an observation becomes four S/O/A/P fields and a few
tags, the doctor confirms, and admin counts symptom burden across the clinic.
**Research assistant** (M5, this session): a Research tab shows the doctor
exactly what will be sent, they trim it by unticking lines, and the answer is
prose that cannot reach any record. Session log: `sessions/SESSION-M5.md`.

## Next session — M3 if the gate has moved, otherwise pick from below

There is no longer an obvious next module: M5 was the last of the four that had
its inputs. The candidates, roughly in order of what the pilot actually needs:

1. **M3 (PACS stub)** is still parked on its external gate (plan §8.1): until
   the imaging centre registers studies under the UHC ID as the DICOM
   `PatientID`, lookup returns empty for every patient and it can only be proven
   against a fake. If that agreement has landed, it is half a session and worth
   taking first. Read `ContextSpine.tsx`'s header before adding its `Images (n)`
   slot — M4 and M5 both declined to take another slot, deliberately, and the
   argument for refusing one is written there.
2. **Read the research assistant's answers against a real model.** Every M5 test
   and screenshot ran on `LLM_PROVIDER=fake`, whose reply is the string "ok". The
   plumbing is proven; whether the prompt's refusals hold and whether the trials
   it names exist has not been checked once. This is a clinical review with an
   oncologist, not a QA pass, and it gates showing the tab to a real doctor.
3. **Allergy capture** — still the largest gap in the spine, which says outright
   that nothing in this system records one.
4. **Deploy the six pending migrations to Omen**, and give `make deploy` a
   migration step so this stops accumulating.
5. **A correction path** for a signed note, a concluded consult, a confirmed
   note, and now a research thread. Nothing in this system can be amended.

## Watch out for

- **A client chaining two writes can 404 on the first one's row, everywhere
  except `/notes` and `/research`.** FastAPI has torn down `yield` dependencies
  *after* sending the response since 0.106 (this repo runs 0.139), so
  `get_session`'s commit lands after the caller already has its 200. Both those
  routers commit before responding (`_settle`); **the same race is latent on
  every other router** and will surface the next time a client chains two writes.
  Fix it there the same way — a 404 that resolves itself is a contract nobody
  can write against.
- **`app/research/` must not import `app.prescription`, `app.formulary`,
  `app.dictation`, `app.notes`, `app.checkins` or `app.doctor`**, and nothing may
  parse a research answer. Both are pinned (`test_the_research_module_cannot_
  reach_a_clinical_writer`, `test_there_is_no_parser_for_a_research_answer`).
  The absence of a parser is the module's whole safety argument: prose has no
  field on a clinical record to reach. Giving an answer a schema is the first
  step towards one, and `research_threads`/`research_turns` must never grow a
  `status`, `signed_at` or `applied` column.
- **The research context is trimmed by id, never by text.** No request model
  carries context text and none may. `app.phi.assert_clean` can only vouch for a
  payload this repo built.
- **`consoleStyles.ts` is one big set of template literals — a backtick in a
  comment ends the string.** One in a CSS comment took `/doctor` down with a 500
  on every request this session and read exactly like an auth regression.
- **Prettier is not this repo's formatter.** No config, no gate, not a
  dependency. `web/.prettierignore` ignores everything so `npx prettier --write`
  is a no-op; do not "fix" that by adding a `.prettierrc` — at every width tried
  it still rewrites 54+ files. The web gate is `npm run typecheck && npm run
  lint && npm run conformance`.
- **Screenshots: not `fullPage`, and scroll first.** `fullPage` renders sticky
  elements where nobody sees them (it invented a spine-over-panel bug this
  session); an unscrolled 720px viewport is all console and no work area. And
  `toBeVisible` is still not a visibility assertion — an element under the
  sticky spine passes it. Assert geometry, as `research.spec.ts` and
  `notes.spec.ts` both now do.
- **The note dock is fixed to the bottom-right and floats over every tab.** Any
  new surface must keep its primary action clear of it *horizontally* — bottom
  padding only separates them at maximum scroll, which is a fix that passes its
  own test and fails on screen.
- **`scripts.seed_doctor_demo` now clears visit dependants in FK order** —
  research threads/turns, clinical notes, and it nulls `medical_documents.
  visit_id`. **A module that hangs a new table off `visits` has to add it there**;
  MRD, M4 and M5 each broke this script in turn.
- **The seeded demo is keyed to `queue.today()`,** so it goes stale at UTC
  midnight and the console renders an empty day — which looks like a broken
  login. Re-run the seed before any doctor/notes/research E2E.
- **A new `Clinical` model must be registered in `tests/test_audit.py`** (an
  instance *and* the expected-tablenames list). The full backend suite catches
  it; per-file runs do not.
- **The 30-second OTP resend cooldown still bites**, and `notes`, `doctor`,
  `dictation` and `research` all take a token for the same seeded doctor.
- Everything from the previous handoff still holds: the page backup has never
  run for real (doc 22 §5, `pages checked: N` must be non-zero), dump before
  sync and never `--delete`, the MRD extraction contract has no flag field,
  `seeds/lab_reference_ranges.json` ships `review_pending`,
  `/records/scan/failures` must never grow an `extraction` field, page images
  are fetched and not `src`-ed, extraction needs a vision-capable
  `LLM_PROVIDER`, the doctor console's token key is `opd_staff_token`,
  `queue.today()` is the operating day, `grade_mentioned` means "the doctor said
  a grade" and never "graded", and nothing captures an allergy.

## Decisions needed from the human

- **Has the imaging-centre UHC-ID agreement landed?** It is the only thing
  standing between M3 and a half-session build (plan §8.1).
- **Who reviews the research assistant's answers, and when?** The tab is
  complete and switched on by default (`RESEARCH_ENABLED=true`), but no
  oncologist has read a single real answer from it. It should not meet a doctor
  before someone has.

## Backlog additions

- Retrieval and citations for the research assistant (plan §8.4) — its own
  design session, not a search tool bolted onto v1.
- An analytics surface over what doctors ask — the rows are stored and audited
  and nothing reads them.
- Per-department or rupee ceilings for research spend, and an admin editor for
  them; today `RESEARCH_DAILY_TURNS` is an env var needing a restart.
- A migration step in `make deploy`, before the pending count reaches double
  figures.
