# HANDOFF — after SESSION-MRD1

**Repo state:** branch `main`. `make test-backend` **1,553 passed**. Scan E2E 5
passed against a live stack, `npm run build` / `tsc` / `eslint` clean. Migration
**`efb79a43afb3`** is applied **locally only** — it joins `c6e3681f5ce1`,
`520d07f0b3e4` and `c063fd91e198` in the set pending on Omen, and `make deploy`
does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands:** the first of the four Clinical Intelligence modules
(`sessions/SESSION-CLINICAL-INTEL-PLAN.md`) is half built. A coordinator can
photograph a patient's reports on a phone at `/scan`; the pipeline reads them,
flags the values in Python, and writes a short summary. **No screen shows any of
it to a doctor yet** — the read endpoints are built and tested, the Reports tab
is M2. Design doc: `docs/21-MEDICAL-RECORD-DIGITISATION.md`.

## Next session — M2, and it has a plan

`sessions/SESSION-CLINICAL-INTEL-PLAN.md` §6 "Session M2": the doctor's Reports
tab and spine slot. Everything it needs from the backend exists:

- `GET /records/patients/{id}/documents` — newest first, failed ones included
- `GET /records/documents/{id}` — one document with its reading
- `GET /records/documents/{id}/pages/{n}` — the original photograph
- `POST /records/documents/{id}/verify` — "I have read this against the pages"

The tab is what the feature-flagged "Coming soon" disclosure in
`WorkTabs.tsx` was built to graduate. Also worth doing in M2: a coordinator-facing
retry surface for `extraction_failed` (the endpoint exists, nothing calls it).

The other candidates are unchanged from the last handoff: allergy capture,
deploying the pending migrations to Omen, and a correction path for a signed
note or a concluded consult.

## Watch out for

- **The extraction contract has no flag field, and that is load-bearing.** A
  model may read a number; deciding it is abnormal is `app/mrd/ranges.py`, in
  Python, on `Decimal`. If anything ever starts parsing a `flag` out of a model
  reply, the determinism invariant is gone.
  `test_a_flag_in_the_models_reply_is_ignored_entirely` is the guard.
- **`seeds/lab_reference_ranges.json` ships `status: review_pending`, and the UI
  must key off it.** Flags derived from that table carry `ref_source: "default"`
  and must be shown as a weaker signal than a flag from a range the lab printed.
  Flipping `status` to `reviewed` without an oncologist actually reviewing it
  silently promotes every grey row.
- **The backup job still does not include `OBJECT_STORE_DIR`.** Postgres alone
  is no longer a complete restore. A missing page answers 410 with a sentence,
  so it fails visibly — but the operator work is unstarted and it is the largest
  debt this module added.
- **Extraction needs a vision-capable `LLM_PROVIDER`.** Sarvam and the local
  vLLM declare `supports_images = False` and raise `UnsupportedCapability`
  *before* being dialled. Do not "fix" that by stripping the images — a summary
  of pages the model never saw reads exactly like a real one.
- **The doctor's tab must label an unverified reading as a draft**, and
  re-extraction clears a previous verification on purpose (`_store_extraction`).
- **Tests default `MRD_ENABLED=false`** (conftest). The post-upload nudge builds
  its own engine, so under ASGITransport it would dial the real DSN; drive the
  pipeline directly, as `test_records_routes._extract_now` does.
- **The 30-second OTP resend cooldown still bites.** `e2e/scan.spec.ts` takes one
  token through the API in `beforeAll` and waits out a 429 rather than signing in
  per test; the doctor and dictation projects still need ~35s between runs.
- **`queue.today()` is the operating day** — UTC-based, and the scanner, board
  and console must all use it. Computing a day independently is how the scanner
  showed an empty list at 04:50 IST while the console showed a queue.
- Everything from the previous handoff still holds: no second prescription
  path, `unsaid` off without a transcript, `conclude` inside the S8 table,
  `patient_card` not narrowed to the assigned doctor, the spine never unmounts,
  and nothing captures an allergy.
