# HANDOFF — after SESSION-AYUR-1

**Repo state:** branch `main`. `make test` green, exit 0 — backend **1,822** (was
1,771), voice-gw 25, typecheck, lint, conformance 115.

**One new migration, `28e0ff23658b`** (`hospitals.name_i18n`; additive, one JSONB
column, server default `{}`, **no backfill**). That makes **nine** pending on
Omen: `c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3`,
`02571a5c1871`, `9f2ab41c77d3`, `8ef31aa60c55`, `4ce8cb36a165`, `28e0ff23658b` —
applied locally only, and `make deploy` still does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands.** AYUR-0 stored a system of medicine and derived it;
AYUR-1 made a hospital **configurable by the person who runs it**. `app/facility.py`
is the two facts a hospital owns about itself that used to require editing
`seeds/hospital.json` on the box: what it is called, and which departments it
runs. Both audited. Two things are worth knowing beyond the feature:

- **Doc 24 §3.2 told us to verify the letterhead rather than assume it, and it was
  half wrong.** The prescription reads `Hospital.name`. The kiosk brand bar and the
  **intake boarding pass did not** — they rendered a four-language constant that had
  already drifted from the seeded name. A rename would have changed the
  prescription and not the paper in the patient's hand. Fixed: the stored name
  rides on `GET /kiosk/bundle`, inside the ETag.
- **AYUR is now held dark by code, not by a comment.** `_assert_has_intake` refuses
  to activate a department that resolves no intake tree, which is exactly the 500
  the last handoff asked this session to remember. It will open on its own the
  moment AYUR-2's trees exist.

**Both of the decisions this handoff was going to ask for were answered by the
operator mid-session, and are built.**

- **`make seed` no longer overwrites what an administrator set up.** For the rows
  a console can edit — hospital, departments, staff users, doctors, clinic
  templates — it creates what is missing and never overwrites what it finds.
  Adding a department or a doctor to a seed file and re-running is still how new
  reference data arrives. A row left alone *because it differs* is counted and
  logged in a fourth report bucket, **`kept`**, so a run says so out loud. The
  files are still validated on every run whether or not they are written.
- **The hospital has an English name and a Hindi one.** `Hospital.name_i18n`
  (JSONB) with `name` as English and as the fallback, read through one
  derivation, `Hospital.name_in(lang)`. **Sixteen call sites** that held a
  language while reading `hospital.name` were rewritten. `TRANSLATABLE_LANGUAGES
  = (Lang.HI,)` is the whole of "Hindi only" — mr/te fall back to English rather
  than carry a guess at a facility's own name, and widening it is one tuple entry
  plus the text.

## Next session — SESSION-AYUR-2, intake content and routing

Objective (doc 24 §8): author the five ayurveda trees of §5 in Hindi +
Hinglish-en + mr/te with `_comment` blocks carrying the UNREVIEWED flag and the
language decision; the **TB red-flag rule** in `ayurveda_respiratory.json` with a
dedicated test; inactive-destination option filtering; the AYUR branch in the
GENMED and PULM routing trees; the kiosk department card for ayurveda.

**What AYUR-1 changes about how it starts:**

- **You no longer have to remember to keep AYUR closed.** Opening it is refused
  until a tree resolves for `AYUR` — via a published row *or* `seeds/trees/`. So
  the moment you author `seeds/trees/ayurveda_*.json` with `"department":
  "AYUR"` and re-seed, the console's Open button for Ayurveda enables itself and
  the refusal stops firing. `backend/tests/test_facility.py` has the pair of
  tests that prove both directions; do not weaken them.
- **`GET /admin/facility` is the editor's read**, `GET /admin/departments` is
  still the active-only picker for the create-a-doctor form. Do not merge them.
- **AYUR-2 ships patient-facing kiosk content, so the hospital-name rule applies
  to it too**: a string a patient reads must come from `name_in(lang)` or from the
  tree, never from a constant compiled into the bundle. That mistake is what this
  session found in the boarding pass.
- **The care-system change confirmation is derived.** If AYUR-2 or AYUR-3 adds a
  capability flag, add its sentence to `FLAG_LABELS` in `app/care_system.py` in
  the same edit — a test fails otherwise, on purpose.

First commands:

```
make dev && make migrate && make seed && make test
```

The three long-standing non-coding items are unchanged and still the most
valuable things nobody has done: **print a pass on the real printer** (doc 23
§11), **point M3 at the real `RAD-RENVA-PACS`**, and **have an oncologist read
the research assistant's answers** (asked in five consecutive handoffs now).
After those: **deploy the nine pending migrations to Omen** and give
`make deploy` a migration step.

## Watch out for

- **A seed file no longer wins over a row that already exists.** If you edit
  `seeds/hospital.json` (or `doctors.json`, or `slot_templates.json`) on a box
  that has already been seeded, **nothing happens** — by design, see above. The
  run tells you: `kept (yours, not this file's): hospital=1`. To make a file edit
  land, change the row in the console too, or delete it and re-seed. This bites
  every developer exactly once; the local dev box hit it the first time the Hindi
  hospital name was seeded.
- **`hospital.name` is almost never the right read any more.** If your code has a
  language in hand — a patient's, a user's, a request's — use
  `hospital.name_in(lang)`. Bare `name` is correct in exactly two places and both
  say why in a comment: the *clinical* copy of a prescription (the pharmacy's and
  the chart's) and the downtime print sheets (every language on one page under
  one header).
- **Two source tests still enforce doc 24 §2 and they still bite.** No module
  under `backend/app` may name a `CareSystem` member outside `app/care_system.py`
  and `app/models/org.py` — `app/facility.py` stays inside the rule by never
  naming one, coercing through `care_system_of` and diffing through
  `differences()`. No file under `web/app` may *compare* against
  `"allopathy"`/`"ayurveda"` outside `_lib/careSystem.ts`; holding the strings in
  a selector is fine, which is how `FacilityTab.tsx` renders the picker. If no
  capability flag fits, **add one to both mappings and regenerate the fixture**
  (`make care-system-fixtures`).
- **`published_trees` is not "can a patient be asked anything".** It counts
  DB-published rows only; nine of the ten seeded departments have zero and are
  open, because `resolve_tree` falls through to `seeds/trees/`. Read `has_intake`
  for the real question. A screenshot caught this surfaced wrongly in the console
  and it is the kind of mistake that looks fine until someone acts on it.
- **`web/e2e/people.spec.ts` is still red** and still predates all this:
  `people.spec.ts:54` clicks `nav button:has-text('People & roster')`, renamed to
  **"People and roster"** by `5be4c28` on 2026-07-27. One word, any session.
- Everything from the previous handoff still holds, in particular: **do not run
  two live E2E projects in parallel against one database**; **never run `npm run
  build` while a dev server is up on 3210**; re-run `seed_doctor_demo` before any
  doctor E2E (the demo day goes stale at UTC midnight); `scripts.seed_doctor_demo`
  clears visit *and* patient dependants in FK order; the three allergy states must
  never collapse into two and nothing composes the phrase "no known allergies";
  nothing checks a stated allergy against a prescribed drug, on purpose;
  `never_asked` is deliberately quiet, not amber; `consoleStyles.ts` is template
  literals and a backtick in a comment takes `/doctor` down with a 500;
  `OTP_RESEND_COOLDOWN_SECONDS=0` saves a wait on every E2E token; a new
  `Clinical` model must be registered in `tests/test_audit.py`; `PACS_ENABLED=false`
  means "nothing was asked" (four states, never three); and `offline-demo` is
  still red and still predates everything.

## Decisions needed from the human

- **New — is "अलवर जिला कैंसर केंद्र" the right Hindi name for this hospital?** It
  is model-drafted, it is now on the Hindi kiosk's brand bar and on the patient's
  copy of every prescription, and an administrator can correct it in the console
  without a deploy. Someone who knows the facility should read it once.
- **Unchanged, now asked five times:** which thermal printer and when can someone
  stand at it; when can M3 be pointed at the real PACS and by whom; who reviews
  the research assistant's answers.
- **Unchanged — who is the BAMS practitioner?** Doc 24 §9 makes clinical sign-off a
  launch gate for every ayurveda tree, formulary entry and prompt pack. **AYUR-2
  is the session that starts authoring that content**, so this is now the next
  session's problem rather than a future one.
- **Unchanged — is "Ayurveda" the right department name and `leaf` the right icon?**
  Both are patient-facing placeholders chosen by an executor. Both are now
  editable in the console without a deploy, which lowers the cost of being wrong
  but not the cost of shipping wrong.
- **Unchanged from SESSION-ALLERGY:** does a coordinator need to record an allergy?

## Backlog additions

- **An admin E2E for the facility tab** — doc 24 §8 asked AYUR-1 for one; per the
  operator's instruction the AYUR sessions do session-scope tests and E2E lands in
  the last one. Both flows (rename → letterhead, create an ayurveda department)
  were driven in a browser and screenshotted this session; the spec was deleted
  rather than committed. Whichever session closes the module inherits it.
- **`make lint` is red on 96 errors, all in `alembic/versions/`** — boilerplate
  from alembic's own revision template (`from typing import Sequence, Union`,
  `Union[...]` annotations, one long `sa.Enum` line). The `ruff format --check`
  half recorded in the last handoff is **fixed** (commit `192dc80`). What remains
  is a one-line decision about whether this repo lints generated migrations.
- **The downtime print sheets head in English** even on a Marathi or Telugu page
  (`routes/queue.py::_hospital_name`). Per-language headers there need a
  `print_sheets` signature change — every language renders on one page under one
  header today. Small, any session.
- **Marathi and Telugu have no hospital name of their own** and fall back to
  English. One entry in `TRANSLATABLE_LANGUAGES` (`app/facility.py`) plus the text
  when a native speaker supplies it.
- **`web/e2e/people.spec.ts:54` selector fix** — one word, any session.
- Everything already on the list: allergies on the boarding pass; a kiosk "I
  don't know" recording nothing; no coded substance vocabulary; `offline-demo`
  failing; desk-side re-print of a pass; linking an MRD `imaging_report` to its
  PACS study; `has_report` on a listed study; a local Orthanc mirror; retrieval
  and citations for the research assistant; an analytics surface over what
  doctors ask.
