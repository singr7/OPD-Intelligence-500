# HANDOFF — after SESSION-PASS

**Repo state:** branch `main`. `make test` green — backend **1,701**, voice-gw
25, typecheck, lint, conformance **79** (was 48). Also green this session:
`pass-ui` 5, kiosk 3, ux-smoke 2, accessibility 3, assign 3, `npm run build`.
**SESSION-PASS added no migration and no backend change.** The six pending on
Omen are unchanged: `c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`,
`efb79a43afb3`, `02571a5c1871`, `9f2ab41c77d3` — applied locally only, and
`make deploy` still does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands.** All four Clinical Intelligence modules are built
(`sessions/SESSION-CLINICAL-INTEL-PLAN.md`): **MRD** (the desk photographs a
patient's reports and the console states what is on file before the doctor opens
anything — deploying it is `docs/22-MRD-DEPLOY.md`), **ambient notes**,
**research assistant**, **imaging**. This session built the one thing outside
that plan: **the intake boarding pass** (`docs/23`), a fixed 80mm × 200mm
document the kiosk hands a patient at the end of intake, carrying the token,
their identity and their own answers, printing on a thermal head as pixels or on
any attached printer as an 80 × 200mm page. Log: `sessions/SESSION-PASS.md`.

## Next session — the plan is out of modules; these two are the most valuable

Nothing is left in the clinical-intelligence plan except debt, and **the three
most valuable things to do next are not coding sessions.** All three are the
same shape: a module that has never met the real thing it wraps.

1. **Print a pass on the real printer.** New this session, and now the shortest
   path from built to real. The rasterisation is proven in a real browser — a
   `pass-ui` test asserts 115,206 bytes of correctly-framed ESC/POS reaching a
   bridge — but **no print head has accepted a byte of it.** `docs/23 §11` is
   the list: 80mm vs 58mm head confirmed and `NEXT_PUBLIC_PASS_GEOMETRY` set to
   match, whether the clone takes the cut command and a 1600-row raster, **Noto
   Sans + Devanagari + Telugu installed on the kiosk OS** (doc 05 §6a — without
   it the pass prints tofu, and shows tofu in the preview first), and the
   feed-to-cut time for 200mm. Someone with the printer and twenty minutes
   closes most of it.
2. **Point M3 at the real `RAD-RENVA-PACS`.** No line of the DICOMweb path has
   met a real Orthanc; it is proven only against an `httpx.MockTransport`.
   Specifically unverified: whether the report endpoint answers a study-level
   PDF `Accept`, whether `includefield` returns the series count, and — the one
   that matters — whether the modality registers the UHC ID as `PatientID` for a
   real patient. `PACS_ENABLED` defaults **false** for exactly this reason.
3. **Have an oncologist read the research assistant's answers.** Every M5 test
   and screenshot ran on `LLM_PROVIDER=fake`, whose reply is the string "ok".
   Whether the prompt's four refusals hold and whether the trials it names exist
   has not been checked once. `RESEARCH_ENABLED` defaults **true**.

Then, roughly in order of what the pilot needs:

4. **Allergy capture** — still the largest gap in the spine, which says outright
   that nothing in this system records one.
5. **Deploy the six pending migrations to Omen**, and give `make deploy` a
   migration step before the count reaches double figures.
6. **A correction path** for a signed note, a concluded consult, a confirmed
   note and a research thread. Nothing in this system can be amended.
7. **The reference-range table needs oncologist review** (plan §8.2) before
   `ref_source: "default"` flags are shown as anything stronger than grey.

## Watch out for

- **`offline-demo` is red, and it was red before this session.** Verified by
  running it against `75153fb` in a scratch worktree: same failure, same line —
  the downtime banner never appears, well before the token screen exists. Not
  the pass's, but nobody has run that project in several sessions and it needs
  an owner. Everything else listed above is green.
- **Never run `npm run build` while a dev server is up on 3210.** It overwrites
  `.next` underneath it and every page load 404s its chunks, presenting as a
  login regression across every E2E project at once. This has now cost two
  sessions *despite being in two consecutive handoffs*; this session stopped the
  server first and the build was clean.
- **Every pass layout number lives in `_lib/pass/geometry.ts`, and two of them
  look wrong until you know why.** The raster is **576** dots wide, not 640 —
  that is the 72mm print head, not the 80mm roll, so `marginMm` is doing double
  duty as the unprintable edge. And the identity grid's four field labels are
  **English only**, which is deliberate and argued in `labels.ts`: bilingual
  there measures 26mm against a 14mm column and prints as a smudge. Doc 23 §12
  records both. Do not "fix" either.
- **A module that adds a fact to the doctor's spine must re-run
  `--project=notes`.** The drawer is 48vh/50vh with about a line of headroom
  above the spine's bottom edge; if it runs out the answer is a shorter spine
  line, not a thinner drawer. (Unchanged — the pass touched the kiosk, not the
  console.)
- **A pane added to the kiosk token screen inherits a fixed bug and its test.**
  `.tokenScreen` centres with `overflow-y: auto`, and a centred flex box pushes
  its first child *above* the top edge where scrolling cannot reach it — the
  pass pane made it tall enough to eat the "your token number" label on the
  800×1280 tablet. It is `justify-content: safe center` now and the `pass-ui`
  tablet-matrix test asserts nothing sits above the top edge at rest.
- **`PACS_ENABLED=false` means "nothing was asked".** Four states, not three,
  and they must never collapse: `ok`, `unreachable`, `no_uhc_id`, `disabled`.
  Only `ok` with an empty list is a fact about the patient.
- **The imaging join key is `Patient.external_id` ↔ DICOM `PatientID`.** If the
  imaging centre registers under a hospital MRN instead, every lookup returns
  "no scans" for patients who have had ten. First thing to check when a doctor
  says imaging is missing.
- **A client chaining two writes can 404 on the first one's row, everywhere
  except `/notes` and `/research`.** FastAPI tears down `yield` dependencies
  after sending the response, so `get_session`'s commit lands after the caller
  has its 200. Those two commit before responding (`_settle`).
- **`app/research/` must not import a clinical writer and nothing may parse a
  research answer**; `app/notes.py` must not reach the prescription path. Both
  pinned by tests that read the source.
- **`consoleStyles.ts` is one big set of template literals — a backtick in a
  comment ends the string** and takes `/doctor` down with a 500.
- **Prettier is not this repo's formatter** (`web/.prettierignore` ignores
  everything). The web gate is `npm run typecheck && npm run lint && npm run
  conformance`.
- **Screenshots: not `fullPage`, and scroll first.** And `toBeVisible` is not a
  visibility assertion — an element under a sticky header passes it. Assert
  geometry; `pass-ui` uses `elementFromPoint` for exactly this.
- **`scripts.seed_doctor_demo` clears visit *and* patient dependants in FK
  order.** A module that hangs a new table off `visits` or `patients` has to add
  it there. The demo is keyed to `queue.today()` and goes stale at UTC midnight,
  rendering an empty day that looks like a broken login: re-seed before any E2E.
- **`OTP_RESEND_COOLDOWN_SECONDS=0` on the api** is worth setting for any E2E
  session; the 30-second default costs a wait on every token.
- Everything from the previous handoff still holds: a new `Clinical` model must
  be registered in `tests/test_audit.py`, the page backup has never run for real
  (doc 22 §5), dump before sync and never `--delete`, the MRD extraction
  contract has no flag field, `seeds/lab_reference_ranges.json` ships
  `review_pending`, page images are fetched and not `src`-ed, extraction needs a
  vision-capable `LLM_PROVIDER`, the console's token key is `opd_staff_token`,
  and nothing captures an allergy.

## Decisions needed from the human

- **Which thermal printer, and when can someone stand at it?** The pass is
  finished and cannot be finished further from here. An 80mm/203dpi unit is what
  the design assumes and what `NEXT_PUBLIC_PASS_GEOMETRY=roll80` expects.
- **When can M3 be pointed at the real PACS, and by whom?** Unchanged, still
  the only thing between that module and being real.
- **Who reviews the research assistant's answers, and when?** Asked in the last
  two handoffs and still unanswered; the tab is on by default.

## Backlog additions

- **`offline-demo` is failing** and predates this session — someone should find
  out why the downtime banner no longer appears.
- **Desk-side re-print of a pass** (doc 23 §9): today the summary lives only in
  kiosk client state and an offline intake has no server record until sync, so
  this needs a retention decision before it needs code.
- Link an MRD `imaging_report` document to the PACS study it describes.
- `has_report` on a listed study, if a QIDO `includefield` can answer it cheaply.
- A local Orthanc mirror and its replication (plan §8.6).
- Retrieval and citations for the research assistant (plan §8.4).
- An analytics surface over what doctors ask — rows stored and audited, nothing
  reads them.
