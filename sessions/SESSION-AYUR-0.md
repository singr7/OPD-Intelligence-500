# SESSION-AYUR-0 — the flag and the derivation

**Date:** 2026-08-09 · **Scope ref:** `docs/24-AYURVEDA-MODULE.md` §8 → SESSION-AYUR-0

## Acceptance criteria checklist

- [x] `CareSystem` enum + `Department.care_system` column + additive migration
- [x] `backend/app/care_system.py` capabilities mapping; unit tests pinning the
      ALLOPATHY row to today's behaviour
- [x] `web/app/_lib/careSystem.ts` + a Python↔TS conformance fixture, extending
      the existing conformance-suite pattern
- [x] Seed loader reads `care_system` (defaulting allopathy); `seeds/hospital.json`
      gains the AYUR department
- [x] `care_system` exposed in admin `GET /departments`, the kiosk department
      list, and the doctor worklist (as a **capabilities object**, not a raw
      string, wherever a UI consumes it)
- [x] Evidence: migration applied locally; full suite green; zero behaviour
      change anywhere a UI renders

## What was built

- **`CareSystem`** in `app/models/enums.py` — `allopathy` (default) / `ayurveda`.
- **`Department.care_system`** (`app/models/org.py`) + migration `4ce8cb36a165`:
  additive, server default, **no backfill**. `native_enum=False` with
  `create_constraint` off makes it a plain `varchar(9)` with no CHECK, so a third
  system is a code change and not a migration. (Nine characters is the ceiling —
  "homeopathy" is ten and would need one.)
- **`app/care_system.py`** — eight named capability flags, two rows,
  `capabilities_for()` and `care_system_of()`. The only server-side file that
  knows what "ayurveda" means operationally.
- **`web/app/_lib/careSystem.ts`** — the same mapping in the browser, plus
  `fromPayload()`, the one adapter between the wire's snake_case and the
  console's camelCase.
- **`app/care_system_fixtures.py`** → `web/e2e/fixtures/care-system-conformance.json`,
  wired into `make test` as `check-care-system-fixtures` beside the walker's
  `check-tree-fixtures`. `web/e2e/care-system.spec.ts` (23 tests) replays it.
- **Seeds** — every department in `seeds/hospital.json` states its system; AYUR
  is added **inactive**. The loader parses through `care_system_of` and now reads
  `active` from the file instead of forcing `True`.
- **Payloads** — `DayOut.capabilities` (doctor worklist), `DeptOut.care_system`
  (kiosk, six construction sites incl. the offline bundle and token blocks),
  `DepartmentOut.care_system` (admin). TS types follow on all three.

## Decisions made

**1. The capabilities object deliberately does not carry the care system's name.**
Doc 24 §2 says components take flags and never `if (careSystem === "ayurveda")`.
A `care_system` field sitting inside `CareSystemCapabilities` would be read by the
first component whose need did not quite fit a flag, so it is not there — and a
test asserts it is not. Where the raw value genuinely *is* the data (the admin
selector, a kiosk card's styling) it travels **beside** the object, never inside.

**2. Two source tests enforce "one derivation", one per side.** Python: an AST
walk over `backend/app` failing if any module outside `app/care_system.py` and
`app/models/org.py` names a `CareSystem` member. TypeScript: a scan of `web/app`
failing on any comparison against `"allopathy"`/`"ayurveda"` outside the mapping.
Doc 24 §8 hands the leak-sweep to SESSION-AYUR-4; these tests mean that sweep
should find nothing, because nothing can land. **All three gates were
mutation-tested** — a leaked comparison in a component, a leaked
`CareSystem.AYURVEDA` in a backend module, and a hand-edited fixture each
produced the failure they exist for.

**3. The seed loader parses through `care_system_of`, not `CareSystem(...)`.**
So even the string→enum coercion lives in one file, and `app/seed.py` needs no
place on the allowlist. Silence means allopathy (a `hospital.json` written before
doc 24 keeps loading); a *misspelt* value raises. Defaulting "ayurved" would hand
an ayurveda clinic the oncology prompt pack and look right on every screen.

**4. AYUR is seeded `active: false`, and must stay that way until SESSION-AYUR-2.**
This is the load-bearing decision of the session. A department is offered on the
kiosk chooser the moment it is active, and AYUR has no intake trees until
AYUR-2 — `routes/kiosk.py` asserts `routed.tree is not None` after routing, so an
active Ayurveda card is a patient tapping it into a 500. Seeding it dark is also
what keeps this session's promise of zero rendered change: every screen still
shows the same nine departments. Activation is an admin action in AYUR-1.

**5. The offline kiosk carries the care system in memory, not in IndexedDB.**
Making `Dept.care_system` required surfaced two offline sites building a
department object by hand. Rather than fabricate a value, it is threaded through
the in-memory `LocalSession` from the chooser that already had it — and
deliberately **not** into the sync row, because the server already knows which
system a department is, and a value round-tripped through a kiosk is a second
place it can be wrong.

**6. The bundle ETag covers `care_system`.** A department that switched system
must invalidate a cached offline bundle exactly the way a rename does, or the
kiosk keeps drawing the old card through an outage.

## Deviations from spec

**One existing test body changed.** `test_seed.py::test_seed_loads_the_pilot_dataset`
asserted nine departments; doc 24 §3.4 instructs this session to add a tenth. The
count is a fact about the seed dataset that the plan itself changes, so it is
restated rather than worked around, and it now asserts *both* numbers — ten rows,
nine active — which is the distinction that actually matters. This is the only
test body touched; doc 24 §2's "zero test-body edits" held everywhere else.

**One latent bug fixed on the way.** `routing.pilot_departments()` never filtered
inactive departments, though the session-backed `kiosk._departments` always has.
Adding AYUR exposed it. An inactive department the classifier can name is a
patient routed somewhere with nothing to ask them, so it filters now — which is
what restored three routing/eval tests without touching a test body. The
`department_codes()` helper in `test_tree_bank.py` was corrected to match (a
helper, not a test body: its callers all mean "departments a walk-in can reach").

**No `Sparkline`/`DictationPanel`/`WorkTabs` changes.** Doc 24 §6 lists the
surfaces to gate, but that is SESSION-AYUR-3's work; AYUR-0 only makes the
capabilities reach the console's bootstrap so AYUR-3 reads a payload rather than
inventing one.

## Tests & evidence

- **`make test` green, exit 0** — backend **1,771** (was 1,741), voice-gw 25,
  conformance **115** (was 92), typecheck, lint, android.
- **Migration `4ce8cb36a165` applied locally**; column verified against the live
  table as `character varying(9) not null default 'allopathy'`, no CHECK.
- **`make seed` run**: 10 departments, AYUR `ayurveda`/inactive, other nine
  `allopathy`/active.
- **New tests:** `backend/tests/test_care_system.py` (20), plus care-system tests
  in `test_seed.py` (3), `test_doctor.py` (3), `test_kiosk.py` (3),
  `test_people.py` (1); `web/e2e/care-system.spec.ts` (23).
- **Live-stack spot checks** (because `care_system` touched shared payloads):
  `doctor` 12, `kiosk` 3, `admin` 4, `assign` 3, `allergy` 6, `notes` 5 — all
  green against a local api on 8123 + dev server on 3210. A live
  `POST /kiosk/start` returns the same nine departments as before, each now
  stating its system, with AYUR absent.
- **No screenshots**: this session rendered nothing new. Every UI change is a
  field arriving in a payload that no component reads yet.
- Per the operator's instruction mid-session, per-session E2E is not the bar for
  the AYUR sessions — full E2E lands in the last one.

## Known gaps / stubs introduced

- **AYUR is a row, not a department.** Inactive, no trees, no formulary entries,
  no prompt pack, no console sections. Nothing renders differently anywhere.
- **`formulary_scope` is a string nothing reads yet.** `validate_meds` is
  untouched; scoping is AYUR-3.
- **`guideline_pack` / `prompt_pack` likewise** — no prompt dispatch site reads
  them yet (AYUR-3/AYUR-4).
- Everything doc 24 §9 says about content review still stands: nothing
  ayurvedic has been authored yet, so there is nothing for a BAMS practitioner
  to sign off on *yet* — that begins in AYUR-2.

## Commits

- `20fe0e0` — S AYUR-0: one flag, one derivation, and a test that keeps it one
- `e3d33ba` — S AYUR-0: the same mapping in TypeScript, held there by a golden fixture
- `5719f7d` — S AYUR-0: the seed learns a system of medicine, and AYUR ships dark
- `a9d19f0` — S AYUR-0: the derivation reaches the three payloads that need it
