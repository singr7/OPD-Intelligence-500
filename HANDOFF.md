# HANDOFF — after SESSION-AYUR-0

**Repo state:** branch `main`, last commit `a9d19f0`. `make test` green, exit 0 —
backend **1,771** (was 1,741), voice-gw 25, typecheck, lint, conformance **115**
(was 92). Also green this session against a live stack: `doctor` 12, `kiosk` 3,
`admin` 4, `assign` 3, `allergy` 6, `notes` 5.

**One new migration, `4ce8cb36a165`** (`departments.care_system`; additive, one
column, server default, **no backfill**). That makes **eight** pending on Omen:
`c6e3681f5ce1`, `520d07f0b3e4`, `c063fd91e198`, `efb79a43afb3`, `02571a5c1871`,
`9f2ab41c77d3`, `8ef31aa60c55`, `4ce8cb36a165` — applied locally only, and
`make deploy` still does not run migrations.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open.

**Where the build stands.** This session opened `docs/24-AYURVEDA-MODULE.md` —
the platform's **second system of medicine**. AYUR-0 built only the flag and the
derivation: `Department.care_system` is stored once, turned into eight named
capability flags in exactly two files (`backend/app/care_system.py`,
`web/app/_lib/careSystem.ts`), held together by a golden fixture the way the two
tree walkers are, and delivered on the three payloads that will need it. **Nothing
renders differently anywhere** — every UI change is a field arriving in a payload
no component reads yet. That is the acceptance criterion, not a shortfall.

## Next session — SESSION-AYUR-1, admin configurability

Objective (doc 24 §8): `PATCH /admin/hospital`; department create/edit with a
system-of-medicine selector; audit entries for both; confirmation copy for a
care-system change. Verify letterhead propagation — rename the hospital in a test
and check the pass print and Rx snapshot show the new name.

**What AYUR-0 changes about how it starts:**

- `GET /admin/departments` already returns `care_system`, so the selector has its
  value. The console has no editor yet.
- **`AYUR` exists as an inactive row.** AYUR-1's department editor is the natural
  place for the "activate" control, but read the warning below before wiring it.
- `care_system_of()` is the parse to use for any admin JSON body — do not call
  `CareSystem(...)`, a test forbids it outside the mapping module.

First commands:

```
make dev && make migrate && make seed && make test
```

The three long-standing non-coding items are unchanged and still the most
valuable things nobody has done: **print a pass on the real printer** (doc 23
§11), **point M3 at the real `RAD-RENVA-PACS`**, and **have an oncologist read
the research assistant's answers** (asked in four consecutive handoffs now).
After those: **deploy the eight pending migrations to Omen** and give
`make deploy` a migration step.

## Watch out for

- **Do not activate the `AYUR` department until its trees exist (AYUR-2).** It is
  seeded `active: false` on purpose. A department is offered on the kiosk chooser
  the moment it is active, and `routes/kiosk.py` asserts `routed.tree is not None`
  after routing — so an active Ayurveda card is a patient tapping it into a 500.
  This is also the single thing holding AYUR-0's "zero rendered change" promise.
- **Two source tests enforce doc 24 §2 and they will fail you.** No module under
  `backend/app` may name a `CareSystem` member outside `app/care_system.py` and
  `app/models/org.py`; no file under `web/app` may compare against
  `"allopathy"`/`"ayurveda"` outside `_lib/careSystem.ts`. If no capability flag
  fits what you need, **add a flag to both mappings and regenerate the fixture**
  (`make care-system-fixtures`) — do not write the comparison. All three gates
  were mutation-tested, so they do bite.
- **`make test` now diffs two fixtures, not one.** Changing `app/care_system.py`
  without running `make care-system-fixtures` fails `check-care-system-fixtures`
  with the command to run in the error.
- **`web/e2e/people.spec.ts` is red, and it predates this session by ~2 weeks.**
  `people.spec.ts:54` clicks `nav button:has-text('People & roster')`, but the
  enterprise UI overhaul (`5be4c28`, 2026-07-27) renamed the tab to **"People and
  roster"** four commits after the spec was written. It is a one-word fix in the
  spec; left alone here because it is not this session's suite. `admin` and
  `assign` are green.
- **Do not run two live E2E projects in parallel against one database.** `notes`
  and `allergy` together failed `notes` on a missing `station-27`; each passed
  alone. Playwright's 2 workers both write to the same demo day.
- Everything from the previous handoff still holds, in particular: **never run
  `npm run build` while a dev server is up on 3210**; re-run `seed_doctor_demo`
  before any doctor E2E (the demo day is keyed to `queue.today()` and goes stale
  at UTC midnight); `scripts.seed_doctor_demo` clears visit *and* patient
  dependants in FK order and a new table hanging off either must be added there;
  the three allergy states must never collapse into two and nothing composes the
  phrase "no known allergies"; nothing checks a stated allergy against a
  prescribed drug, on purpose; `never_asked` is deliberately quiet, not amber;
  `consoleStyles.ts` is template literals and a backtick in a comment takes
  `/doctor` down with a 500; `OTP_RESEND_COOLDOWN_SECONDS=0` saves a wait on
  every E2E token; a new `Clinical` model must be registered in
  `tests/test_audit.py`; `PACS_ENABLED=false` means "nothing was asked" (four
  states, never three); and `offline-demo` is still red and still predates
  everything.

## Decisions needed from the human

- **Unchanged and now asked four times:** which thermal printer and when can
  someone stand at it; when can M3 be pointed at the real PACS and by whom; who
  reviews the research assistant's answers.
- **Unchanged from SESSION-ALLERGY:** does a coordinator need to record an
  allergy? The desk sits between the doctor and the patient all day and has no
  way in.
- **New — who is the BAMS practitioner?** Doc 24 §9 makes clinical sign-off a
  launch gate for every ayurveda tree, formulary entry and prompt pack. AYUR-2
  starts authoring that content in the next session but one, and it ships
  UNREVIEWED until a named person signs it. Naming them now is what stops the
  gate from being discovered at the end.
- **New — is "Ayurveda" the right department name and `leaf` the right icon?**
  Both are placeholders chosen by the executor and both are patient-facing.

## Backlog additions

- **`web/e2e/people.spec.ts:54` selector fix** — "People & roster" → "People and
  roster" (see above). One word, any session.
- **`make lint` is red on backend formatting and predates this session** —
  `ruff format --check` wants `app/trees/schema.py` and
  `tests/test_voice_profile_vendors.py` (plus two others) reformatted. Not in
  `make test`, which is why it went unnoticed. Untouched by this session.
- **`_upsert_departments` now lets the seed file own `active`**, which means a
  department an admin deactivates in the console is reactivated by the next
  `make seed` if the file says so. Pre-existing shape (the field was hard-coded
  `True` before), newly visible. Worth a decision in AYUR-1 when the admin editor
  lands.
- Everything already on the list: allergies on the boarding pass; a kiosk "I
  don't know" recording nothing; no coded substance vocabulary; `offline-demo`
  failing; desk-side re-print of a pass; linking an MRD `imaging_report` to its
  PACS study; `has_report` on a listed study; a local Orthanc mirror; retrieval
  and citations for the research assistant; an analytics surface over what
  doctors ask.
