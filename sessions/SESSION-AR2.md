# SESSION-AR2 — Staff strip: PIN, identity lookup, assignment, department transfer

**Date:** 2026-08-04 · **Scope ref:** `sessions/SESSION-ASSIGN-RX-PLAN.md` → Session A
**Branch:** `assign-rx-identity`

Completes the **backend** half of Session A. The kiosk and coordinator *screens*
are AR3.

## Human decisions taken into this session

1. Coordinator authenticates to the strip with a **numeric PIN**.
2. A department change **reissues the token**.
3. The identifier is a **UHC ID / MRN**, may appear on a document the patient
   carries, and stays **optional**.

## Acceptance criteria checklist

- [x] `/kiosk/start` accepts an optional phone and UHC ID and matches a prior file.
- [x] Nothing patient-identifying leaves the unauthenticated kiosk routes.
- [x] PIN-gated strip: `/staff/holders`, `/staff/unlock`, `/{sid}/strip`,
      `/{sid}/assign`.
- [x] Link confirm/reject and department/doctor settle in one action.
- [x] Department change re-homes the queue entry **and** reissues the token.
- [x] `Skip` yields an unassigned visit in the department pool.
- [x] Coordinator console can assign skipped and offline arrivals.
- [x] An intake with no phone and no UHC ID completes unchanged.
- [ ] Kiosk arrival screens and the strip UI — **AR3**
- [ ] Coordinator console assign control (UI) — **AR3**
- [ ] Doctor worklist scoping — Session B

## What was built

- `app/auth/kiosk_pin.py` — PIN set/clear/verify, the narrow `kiosk_staff` token,
  attempt cap and lockout, trivial-PIN rejection.
- `app/auth/rbac.py` — `require_kiosk_staff`.
- `app/models/org.py` — `User.kiosk_pin_hash`, `kiosk_pin_attempts`,
  `kiosk_pin_locked_until`; migration `520d07f0b3e4`.
- `app/queue.py` — `transfer_department` + `Transfer`; a module logger.
- `app/assignment.py` — `assign` now returns `Assignment` and performs the
  transfer; reports the old and new token.
- `app/kiosk.py` — `create_walk_in` takes `patient_external_id` and records a
  candidate; `department_by_code`.
- `app/routes/kiosk.py` — the four strip routes; `patient_external_id` on
  `StartIn`; module docstring corrected.
- `app/routes/queue.py` — `/entries/{id}/assign`, `/entries/{id}/assignable`.
- Tests: `test_kiosk_pin.py` (23), `test_kiosk_strip.py` (13), `test_assignment.py`
  grown to 24.

## Decisions made

- **A PIN never mints a staff session.** `verify_pin` issues a token of type
  `kiosk_staff`; `current_principal` decodes with `expected_type="access"` and
  refuses it. Tests assert *both* directions of the substitution. If a later
  screen finds this token convenient, widening it would put the coordinator
  console, the patient card and the audit trail behind four digits typed in front
  of a queue. Do not.
- **Trivial PINs are refused at set-time.** With a five-try cap the attacker's
  entire budget is the top handful of choices, so `1234`/`0000`/`4321` are where
  the defence actually lives.
- **`/staff/holders` is unauthenticated and discloses staff names.** It has to
  be: the strip cannot ask who you are after you have identified yourself. It
  returns an opaque id and a name — which is on the badge of the person standing
  in the corridor — and nothing contactable. A wrong PIN and an unknown user id
  return an identical 401 so the endpoint cannot enumerate staff.
- **The queue entry is moved, not recreated,** on a department change. Queue
  reads in `app.queue` do not filter `deleted_at`, so a soft-deleted entry would
  keep appearing on the board; moving also preserves the priority and reason a
  red-flag patient must not lose by being re-routed.
- **A department change is refused once the consultation has started.** By then
  there is a clinical record attached to the department it happened in.
- **Identity resolves before assignment** in both assign routes, so a confirmed
  match assigns against the patient's real file, not the walk-in row.
- **The walk-in row also stores the UHC ID the patient gave.** A coordinator who
  *rejects* the match still ends up with the identifier.

## Deviations from spec

None. The plan's Session A is complete except for its two screens.

## Tests & evidence

- `make test-backend`: **1325 passed** (1265 at AR1 close).
- 36 new tests. Route registration and `alembic current` verified:
  `520d07f0b3e4 (head)`, six new paths in the OpenAPI document.
- No UI, so no screenshots. `docs/04 §5` self-critique is AR3's gate.
- One **pre-existing** lint error remains in `app/config.py:260` (E501). Not
  touched by this session; left rather than mixed into an unrelated commit.

## Known gaps / stubs introduced

- **No UI at all.** Every route here is reachable only by an HTTP client. Nothing
  a coordinator or a patient can see has changed, so this is deployable but not
  demonstrable.
- **`make deploy` does not run migrations** (`CODEBASE_MEMORY.md`). Two
  migrations are pending on Omen; they must be applied explicitly.
- The kiosk's **offline** path neither matches a returning patient nor assigns —
  accepted debt, per the pilot decision. Both are recorded in `STATE.md`.
- `_departments`/`_department_by_code` naming: `app.kiosk._departments` stays
  private while the new `department_by_code` is public. Tidy on next touch.

## Commits

- `ce30acd` — S AR2: kiosk staff PIN, and department change that moves the queue
- `74319d1` — S AR2: the staff strip's HTTP surface, on the kiosk and at the desk
