# SESSION-MRD1 — Medical record digitisation: capture and pipeline

Plan: `sessions/SESSION-CLINICAL-INTEL-PLAN.md` §1 + §6 (session M1).
Design doc written this session: `docs/21-MEDICAL-RECORD-DIGITISATION.md`.
Branch: `main`. Baseline at start: backend 1,404 green.

## What this session was for

The first of the four Clinical Intelligence modules: a coordinator photographs a
patient's lab and biopsy reports on a phone, and the doctor opens the consult
already knowing what they say. M1 is capture + pipeline; the doctor's Reports tab
is M2 and is deliberately **not** built.

## Acceptance criteria, restated and checked

- [x] Vision reaches the provider contract, with a fake, and a text-only
      provider refuses rather than answers from pages it never saw.
- [x] Page bytes live outside Postgres, behind one interface, with a fake.
- [x] `MedicalDocument` + `DocumentExtraction`, additive migration, both audited.
- [x] Outlier flagging is deterministic, on `Decimal`, and the model has no way
      to set a flag.
- [x] One shared PHI minimiser, ready for the research assistant to reuse.
- [x] Capture → extract → flag → summarise, with every failure leaving the pages
      viewable and a named status.
- [x] `/scan`: pick, photograph, done — on a phone, behind staff auth.
- [x] A sweep that is safe to run alongside the API's nudge.
- [x] Gates: backend **1,553**, scan E2E **5** against a live stack, production
      build, typecheck, lint all green.

Not in scope and not done: the doctor's Reports tab (M2), an offline capture
queue, an S3 store.

## The decisions worth knowing about

**The extraction contract has no flag field.** Not in the JSON schema, not in the
prompt. A model may read "8.9" off a page; deciding 8.9 is low is
`app/mrd/ranges.py`, in Python. `test_a_flag_in_the_models_reply_is_ignored_entirely`
feeds a reply that calls a normal platelet count `critical_low` and asserts it
lands as `normal`. This is CODEBASE_MEMORY's determinism invariant applied to
numbers, and it is the single most important line in the module.

**The range printed on the report beats our table, and is used with no unit
conversion at all.** Value and range came off the same page in the same units;
normalising could only introduce error, and the lab calibrated that range to its
own analyser. It also makes any of a lab menu's hundreds of tests flaggable
where our table has eighteen. The table is the fallback, ships
`status: review_pending`, and every flag carries `ref_source` so the M2 UI can
show a table-derived flag as the weaker signal it is.

**An unconvertible unit is `UNKNOWN`, never an assumption.** 150 is a normal
platelet count in 10³/µL and a catastrophic one in /µL. Name matching is
exact-or-alias and deliberately not fuzzy — the formulary can fuzzy-match
because it *suggests* to a doctor who then chooses, and nobody chooses here.

**The summariser never sees the images.** It reads the flagged structure. Beyond
cost, this makes the prose provably about the same numbers the doctor's table
shows; a second reading could disagree and nothing would arbitrate.

**The object store is not a `Provider`.** It wraps no vendor, so it meters and
prices nothing; a `usage_events` row for a local disk write reconciles to nothing
on the S18 dashboard. It keeps the layer's other habits — one interface, config
selection, a fake, and an unknown name failing at boot.

**`app/phi.py` names what may leave, rather than filtering what may not.** Under
a denylist a column added to `Patient` next year reaches a vendor by default and
nobody notices until it is in a log.

## Two bugs the E2E caught that unit tests could not

**The live `FileList`.** `addPages` read `e.target.files` across an `await` while
the change handler reset `input.value` — which empties that same live list.
Every page was silently dropped: no error, no upload, a page count that stayed
at zero. It would have failed identically on a real phone, and quietly. Fixed by
copying the list out synchronously before the first await.

**Two definitions of "today".** The scan worklist computed its own operating day
from the hospital timezone while the queue uses `queue.today()` (UTC). Between
midnight and 05:30 IST the scanner showed an empty list while the coordinator
console two feet away showed a queue — which is exactly how it presented, at
04:50 IST. Now uses the queue's definition, pinned by a test.

## Evidence

- `make test-backend` → **1,553 passed**. New: `test_objectstore` (13),
  `test_mrd_ranges` (36), `test_mrd_contract` (14), `test_mrd_pipeline` (22),
  `test_phi` (31), `test_records_routes` (23), plus vision wire tests in
  `test_providers_vendors` and object-store/sweep tests in the registry and
  worker suites.
- `npx playwright test --project=scan` → **5 passed** against a live stack
  (backend on :8010 with `OBJECT_STORE=filesystem`, web dev server on :3210).
- `npm run build` / `tsc` / `eslint` clean. `/scan` is 7.38 kB.
- Screenshots: `web/screenshots/mrd1/{01-pick,02-capture,03-done,04-failed-page}.png`,
  self-critiqued per doc 04 §5. The capture screen's copy was corrected as a
  result: the document type locks when the document is created, not when a page
  is stored, and saying "fixed once pages are in" beside a count of zero was the
  screen contradicting itself.

## Migration

`efb79a43afb3` — two new tables, nothing touched, no backfill. **Applied locally
only.** It joins `c6e3681f5ce1`, `520d07f0b3e4` and `c063fd91e198` in the set
still pending on Omen, and `make deploy` does not run migrations.

## Debt this session created

Recorded in `STATE.md` → Stubs & fakes and doc 21 §8. The two that matter most:

1. **The backup job does not include `OBJECT_STORE_DIR`.** Postgres alone is no
   longer a complete restore. The page route answers 410 when an object is
   missing, so the failure is visible rather than a broken image — but the
   operator work is real and unstarted.
2. **`seeds/lab_reference_ranges.json` has not been seen by an oncologist.** It
   is used only where a report prints no range of its own, and it announces its
   own unreviewed status in the file, in the flag payload, and in STATE.
