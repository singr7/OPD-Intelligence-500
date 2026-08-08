# HANDOFF — after SESSION-M3

**Repo state:** branch `main`. `make test-backend` **1,701 passed**. Imaging E2E
6, doctor 12, reports 7, research 7, notes 5, dictation 8, conformance 48,
voice-gw 25, `npm run build` / `tsc` / `eslint` clean. **M3 added no migration.**
The six pending on Omen are unchanged from M5: `c6e3681f5ce1`, `520d07f0b3e4`,
`c063fd91e198`, `efb79a43afb3`, `02571a5c1871`, `9f2ab41c77d3`. All applied
locally only, and `make deploy` does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands: all four Clinical Intelligence modules are built**
(`sessions/SESSION-CLINICAL-INTEL-PLAN.md`). **MRD** (M1–M2): the desk
photographs a patient's reports, the pipeline reads them, the console states what
is on file before the doctor opens anything — deploying it is
`docs/22-MRD-DEPLOY.md`. **Ambient notes** (M4): a mic on every tab, an
observation becomes S/O/A/P plus tags, admin counts symptom burden.
**Research assistant** (M5): a tab that shows what will be sent before it is
sent, and answers in prose that cannot reach any record. **Imaging** (M3, this
session): study discovery by UHC ID and a popup handoff to the viewer already
connected to the same Orthanc. Logs: `sessions/SESSION-M3.md`, `SESSION-M5.md`.

## Next session — BUILD THE INTAKE BOARDING PASS (pilot requirement, 2026-08-08)

A hard pilot requirement arrived and is fully designed: **the intake boarding
pass** — a fixed 80mm × 200mm printed pass (token, name, mobile, age/sex,
UHC ID, and the intake summary at max real estate) with a Print/Re-print
screen, printing on the kiosk thermal bridge *or* any attached printer.
**The complete build brief is `docs/23-INTAKE-BOARDING-PASS.md`** — read it
first; §10 is the build order, §6 the architecture (pure `layoutPass()` +
one SVG renderer + SVG→canvas→`GS v 0` raster for thermal, which is also what
finally gets shaped Devanagari/Telugu onto paper). It supersedes the old
text-mode slip in `web/app/(kiosk)/kiosk/_lib/print.ts` but does not delete it
until a real printer has printed a pass. No backend or migration work.

## After that — the plan is out of modules; pick from the pilot's needs

Nothing is left in the clinical-intelligence plan except debt. **Two modules
have never met the real thing they wrap, and both gates are clinical or
operational rather than engineering** — these are the two most valuable things
to do next and neither is a coding session:

1. **Point M3 at the real `RAD-RENVA-PACS`.** No line of the DICOMweb path has
   met a real Orthanc; it is proven only against an `httpx.MockTransport`. Plan
   §2.2 lists this as a gate and it is unmet. Specifically unverified: whether
   the report endpoint answers a study-level PDF `Accept` as expected, whether
   `includefield` returns the series count, and — the one that matters —
   whether the modality actually registers the UHC ID as `PatientID` for a real
   patient. `PACS_ENABLED` defaults **false** for exactly this reason; leave it
   off on any box a doctor uses until someone has looked.
2. **Have an oncologist read the research assistant's answers.** Every M5 test
   and screenshot ran on `LLM_PROVIDER=fake`, whose reply is the string "ok".
   Whether the prompt's four refusals hold and whether the trials it names exist
   has not been checked once. `RESEARCH_ENABLED` defaults **true**.

Then, roughly in order of what the pilot needs:

3. **Allergy capture** — still the largest gap in the spine, which says outright
   that nothing in this system records one.
4. **Deploy the six pending migrations to Omen**, and give `make deploy` a
   migration step before the count reaches double figures.
5. **A correction path** for a signed note, a concluded consult, a confirmed
   note and a research thread. Nothing in this system can be amended.
6. **The reference-range table needs oncologist review** (plan §8.2) before
   `ref_source: "default"` flags are shown as anything stronger than grey.

## Watch out for

- **A client chaining two writes can 404 on the first one's row, everywhere
  except `/notes` and `/research`.** FastAPI tears down `yield` dependencies
  after sending the response, so `get_session`'s commit lands after the caller
  has its 200. Those two commit before responding (`_settle`); the race is
  latent on every other router.
- **The spine grows, and the note drawer is sized against it.** M4 set the
  drawer to 52vh/54vh, which left the spine's bottom edge ~2px above the
  drawer's top; M3's five-word imaging clause added three pixels and the notes
  E2E failed, correctly. It is 48vh/50vh now with about a line of headroom. **A
  module that adds a fact to the spine must re-run `--project=notes`**, and if
  headroom runs out the answer is a shorter spine line, not a thinner drawer.
- **The note dock is fixed bottom-right over every tab.** A new surface must
  keep its primary action clear of it *horizontally* — bottom padding separates
  them only at maximum scroll, which is a fix that passes its own test and fails
  on screen. Both `.rsx-composer-r` and `.img-row` reserve the gutter.
- **`PACS_ENABLED=false` is the safe default and means "nothing was asked".**
  Four states, not three, and they must never collapse: `ok` (the PACS answered),
  `unreachable`, `no_uhc_id`, `disabled`. Only `ok` with an empty list is a fact
  about the patient. Rendering an empty array as "no imaging on file" tells a
  doctor something the server never said.
- **The imaging join key is `Patient.external_id` ↔ DICOM `PatientID`.** If the
  imaging centre registers under a hospital MRN instead, every lookup returns
  "no scans" for patients who have had ten, and nothing in the code can tell.
  First thing to check when a doctor says imaging is missing.
- **`app/research/` must not import a clinical writer and nothing may parse a
  research answer**; `app/notes.py` must not reach the prescription path. Both
  pinned by tests that read the source. `research_threads`/`research_turns` must
  never grow a `status`, `signed_at` or `applied` column.
- **`consoleStyles.ts` is one big set of template literals — a backtick in a
  comment ends the string** and takes `/doctor` down with a 500 on every request.
- **Never run `npm run build` while a dev server is up on 3210.** It overwrites
  `.next` underneath it and every page load 404s its chunks, presenting as a
  login regression across every E2E project at once. This has now cost two
  sessions, including this one, *despite being in the previous handoff*.
- **Prettier is not this repo's formatter.** `web/.prettierignore` ignores
  everything so `npx prettier --write` is a no-op. Do not add a `.prettierrc` —
  at every width tried it still rewrites 54+ files. The web gate is
  `npm run typecheck && npm run lint && npm run conformance`.
- **Screenshots: not `fullPage`, and scroll first.** `fullPage` renders sticky
  elements where nobody sees them; an unscrolled 720px viewport is all console.
  And `toBeVisible` is not a visibility assertion — an element under the sticky
  spine passes it. Assert geometry.
- **Run the E2E projects you did not touch.** `doctor` had asserted four tabs
  since MRD2 and stayed unrun for three sessions; M3 found it. The doctor-console
  projects are `doctor`, `reports`, `notes`, `research`, `imaging`, `dictation`.
- **`scripts.seed_doctor_demo` clears visit *and* patient dependants in FK
  order** — research threads/turns, clinical notes, and document extractions and
  medical documents (which hang off the patient and so outlive every visit). **A
  module that hangs a new table off `visits` or `patients` has to add it there.**
  The demo is keyed to `queue.today()` and goes stale at UTC midnight, rendering
  an empty day that looks like a broken login: re-seed before any E2E run.
- **A new `Clinical` model must be registered in `tests/test_audit.py`** — an
  instance *and* the expected-tablenames list. The full suite catches it; a
  per-file run does not.
- **`OTP_RESEND_COOLDOWN_SECONDS=0` on the api** is worth setting for any E2E
  session; the 30-second default costs a wait on every token.
- Everything from the previous handoff still holds: the page backup has never
  run for real (doc 22 §5), dump before sync and never `--delete`, the MRD
  extraction contract has no flag field, `seeds/lab_reference_ranges.json` ships
  `review_pending`, page images are fetched and not `src`-ed, extraction needs a
  vision-capable `LLM_PROVIDER`, the console's token key is `opd_staff_token`,
  `grade_mentioned` means "the doctor said a grade", and nothing captures an
  allergy.

## Decisions needed from the human

- **When can M3 be pointed at the real PACS, and by whom?** It is the only thing
  standing between this module and being real. Everything else about it is done.
- **Who reviews the research assistant's answers, and when?** Unchanged from the
  last handoff and still unanswered; the tab is on by default.

## Backlog additions

- Link an MRD `imaging_report` document to the PACS study it describes — they
  sit in the same tab and nothing knows they are about the same scan.
- `has_report` on a listed study, if a QIDO `includefield` can answer it cheaply.
- A local Orthanc mirror and its replication (plan §8.6, doc-17/18 ops work).
- Retrieval and citations for the research assistant (plan §8.4).
- An analytics surface over what doctors ask — rows stored and audited, nothing
  reads them.
