# HANDOFF — after SESSION-ALLERGY

**Repo state:** branch `main`. `make test` green — backend **1,741** (was
1,701), voice-gw 25, typecheck, lint, conformance **92** (was 79). Also green
this session against a live stack: **`allergy` 6 (new)**, `kiosk` 3, `doctor`
12, `notes` 5, `assign` 3, `pass-ui` 5, `ux-smoke` 2, `accessibility` 3.

**One new migration, `8ef31aa60c55`** (`patient_allergies`; additive, one table,
**no backfill**). That makes **seven** pending on Omen: `c6e3681f5ce1`,
`520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3`, `02571a5c1871`, `9f2ab41c77d3`,
`8ef31aa60c55` — applied locally only, and `make deploy` still does not run
migrations. The count is now in double figures' shadow; see item 4.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands.** All four Clinical Intelligence modules are built
(`sessions/SESSION-CLINICAL-INTEL-PLAN.md`): MRD, ambient notes, research
assistant, imaging — plus the intake boarding pass (doc 23). This session built
the largest thing that plan left behind: **allergy capture**
(`sessions/SESSION-ALLERGY.md`). The doctor's context spine has said "Allergies
not captured by this system yet" since Session B; it now renders a real
derivation, the kiosk asks every patient in four languages, and a doctor can
record, confirm and withdraw one from the spine without losing their tab.

## Next session — the three most valuable things are still not coding sessions

Unchanged from the last handoff, and now more pointed: every module is built and
**three of them have never met the real thing they wrap.**

1. **Print a pass on the real printer.** `docs/23 §11` is the list: 80mm vs 58mm
   head confirmed and `NEXT_PUBLIC_PASS_GEOMETRY` set to match, whether the clone
   takes the cut command and a 1600-row raster, **Noto Sans + Devanagari + Telugu
   installed on the kiosk OS** (doc 05 §6a — without it the pass prints tofu, and
   shows tofu in the preview first), and the feed-to-cut time for 200mm. Someone
   with the printer and twenty minutes closes most of it.
2. **Point M3 at the real `RAD-RENVA-PACS`.** No line of the DICOMweb path has
   met a real Orthanc. Specifically unverified: whether the report endpoint
   answers a study-level PDF `Accept`, whether `includefield` returns the series
   count, and — the one that matters — whether the modality registers the UHC ID
   as `PatientID` for a real patient. `PACS_ENABLED` defaults **false**.
3. **Have an oncologist read the research assistant's answers.** Every M5 test
   ran on `LLM_PROVIDER=fake`, whose reply is the string "ok". Asked in three
   consecutive handoffs now. `RESEARCH_ENABLED` defaults **true**.

Then, roughly in order of what the pilot needs:

4. **Deploy the seven pending migrations to Omen**, and give `make deploy` a
   migration step. This has been item 5 for three sessions and the list grows
   every time.
5. **A correction path for the rest of the record** — a signed note, a concluded
   consult, a confirmed note, a research thread. Allergies now have one
   (`retract`); nothing else does, and the shape it took here is the pattern to
   copy: a state change with a clinician and a reason, never a delete.
6. **The reference-range table needs oncologist review** (plan §8.2) before
   `ref_source: "default"` flags are shown as anything stronger than grey.
7. **`offline-demo` is red** and has been for several sessions — still nobody's.

## Watch out for

- **`offline-demo` is still red, and it was red before this session.** Same
  failure, same line: the downtime banner never appears
  (`offline-demo.spec.ts:158`), well before the token screen. Its kiosk walk was
  patched for the new allergy screen, so **that patch is unverified** — nothing
  in that suite gets far enough to exercise it. Everything else listed above is
  green.
- **The kiosk grew a screen between the last tree question and the read-back.**
  Any suite that walks an intake to a token has to answer it. Four did and were
  fixed (`kiosk`, `assign`, `pass-ui`, `offline-demo`); a fifth written later
  will hang on `data-screen="allergy"` with no clue why. Tapping
  `allergy-unsure` is the fastest way past it and records nothing.
- **The three allergy states must never collapse into two.** `never_asked` /
  `none_stated` / `known`, derived *only* by `app.allergies.for_patient`. No
  surface re-derives them, no wire model carries a `has_allergies` boolean, and
  **nothing composes the phrase "no known allergies"** — it is the summary of a
  chart review nobody here has performed, and a doctor reads it and prescribes on
  it. Two E2E tests assert it appears nowhere on the whole console.
- **Nothing checks a stated allergy against a prescribed drug, on purpose.**
  `app.allergies` imports no formulary and the prescription path never calls it,
  pinned by a source test. A match against free text a patient typed at a kiosk
  is a safety feature made of guesses, and a *missed* match is a doctor who
  trusted a green tick. Do not add one without a coded vocabulary and a clinical
  owner.
- **`never_asked` is deliberately quiet, not amber.** Every patient starts there,
  so colouring it puts an amber band on every console all day — and the first cut
  of this rendered the unknown state *louder* than a severe penicillin allergy.
  Doc 04 §5 self-critique in `sessions/SESSION-ALLERGY.md`; do not "fix" it.
- **`scripts.seed_doctor_demo` clears visit *and* patient dependants in FK
  order.** `patient_allergies` is now in there — cleared by patient, before the
  visits, because a statement can reference both. A module that hangs a new table
  off `visits` or `patients` has to add it there, and this session broke on
  exactly that despite the warning being in the last handoff.
- **Never run `npm run build` while a dev server is up on 3210.** It overwrites
  `.next` underneath it and every page load 404s its chunks, presenting as a
  login regression across every E2E project at once. Three handoffs running.
- **A module that adds a fact to the doctor's spine must re-run
  `--project=notes`.** The drawer is 48vh/50vh with about a line of headroom
  above the spine's bottom edge. The allergy slot grew from a line of text to a
  bordered button and `notes` still passes, but that headroom is now smaller.
- **`consoleStyles.ts` is template literals — a backtick in a comment ends the
  string** and takes `/doctor` down with a 500. Hit again this session.
- Everything from the previous handoff still holds: `PACS_ENABLED=false` means
  "nothing was asked" (four states, never three); the imaging join key is
  `Patient.external_id` ↔ DICOM `PatientID`; a client chaining two writes can 404
  on the first one's row everywhere except `/notes` and `/research` (the allergy
  routes commit before returning, so they are safe); screenshots are not
  `fullPage` and `toBeVisible` is not a visibility assertion;
  `OTP_RESEND_COOLDOWN_SECONDS=0` saves a wait on every E2E token; a new
  `Clinical` model must
  be registered in `tests/test_audit.py`, the page backup has never run for real
  (doc 22 §5), dump before sync and never `--delete`, the MRD extraction contract
  has no flag field, `seeds/lab_reference_ranges.json` ships `review_pending`,
  page images are fetched and not `src`-ed, extraction needs a vision-capable
  `LLM_PROVIDER`, the console's token key is `opd_staff_token`, every pass layout
  number lives in `_lib/pass/geometry.ts` (576 dots and English-only labels are
  both deliberate — doc 23 §12), and `.tokenScreen` uses `justify-content: safe
  center` for a fixed bug with a test.

## Decisions needed from the human

- **Which thermal printer, and when can someone stand at it?** The pass is
  finished and cannot be finished further from here. An 80mm/203dpi unit is what
  `NEXT_PUBLIC_PASS_GEOMETRY=roll80` expects.
- **When can M3 be pointed at the real PACS, and by whom?** Unchanged.
- **Who reviews the research assistant's answers, and when?** Asked three times
  now; the tab is on by default.
- **New: does a coordinator need to record an allergy?** Today only a doctor can
  (the console panel) and only a patient can state one unprompted (the kiosk).
  The desk sits between them all day and has no way in. Deliberately not built —
  it needs a decision about whether a coordinator's typing counts as a statement
  with the same weight as the patient's own.

## Backlog additions

- **Allergies on the boarding pass** — the patient's own statement read back on
  the paper they carry. Blocked on doc 23's fixed 80×200mm geometry: something
  has to give up a line.
- **A kiosk "I don't know" records nothing**, so "asked, did not know" reads the
  same as "never asked". The instruction to the doctor is identical, which is why
  it was left, but it is a real loss of information.
- **No coded substance vocabulary.** Two spellings of penicillin are two
  statements and nothing merges them. Prerequisite for any interaction checking.
- **`offline-demo` is failing** and predates this session.
- **Desk-side re-print of a pass** (doc 23 §9) — needs a retention decision first.
- Link an MRD `imaging_report` document to the PACS study it describes.
- `has_report` on a listed study, if a QIDO `includefield` can answer it cheaply.
- A local Orthanc mirror and its replication (plan §8.6).
- Retrieval and citations for the research assistant (plan §8.4).
- An analytics surface over what doctors ask — rows stored and audited, nothing
  reads them.
