# SESSION-AR1 — Assignment & identity: baseline repair, schema, service layer

**Date:** 2026-08-04 · **Scope ref:** `sessions/SESSION-ASSIGN-RX-PLAN.md` → Session A
**Branch:** `assign-rx-identity`

This is Session A part 1 of the plan. It stops at the service boundary: the HTTP
routes, the kiosk arrival screens and the coordinator strip are AR2.

## Acceptance criteria checklist

From the plan's Session A. Part 1 covers the data and service half:

- [x] Migration adds `Patient.external_id` / `external_id_kind`,
      `Visit.candidate_patient_id` / `patient_link_state`; additive, nullable,
      no backfill; applied to the local stack.
- [x] A returning patient is matched on UHC ID first, then on the last ten
      digits of a phone.
- [x] A match is recorded as a candidate and discloses nothing.
- [x] Confirming a link repoints the visit and retires only the generated
      walk-in row, with no data loss.
- [x] `doctor_id = None` is a first-class outcome (the department pool).
- [x] A doctor from another department cannot be assigned.
- [x] Roster-backed doctor list for a given day, on-duty first.
- [ ] `POST /kiosk/{session_id}/assign`, PIN-gated — **AR2**
- [ ] Kiosk arrival screens + staff strip — **AR2**
- [ ] Coordinator console assign action — **AR2**
- [ ] Department change → queue re-home + token reissue — **AR2** (see below)

## What was built

- `backend/app/assignment.py` — new module: `find_candidate`, `note_candidate`,
  `confirm_link`, `reject_link`, `assignable_doctors`, `default_doctor`, `assign`.
- `backend/app/models/enums.py` — `PatientLinkState` (`none` / `candidate` /
  `confirmed` / `rejected`).
- `backend/app/models/patient.py` — `external_id`, `external_id_kind`.
- `backend/app/models/clinical.py` — `Visit.candidate_patient_id`,
  `Visit.patient_link_state`.
- `backend/alembic/versions/…c6e3681f5ce1…` — the migration, applied locally.
- `backend/tests/test_assignment.py` — 20 tests.
- Baseline repair (see below) across `app/checkins/delivery.py`, `tests/factories.py`,
  `tests/test_{checkin_engine,config,roster,people,scheduling}.py`.

## The baseline was red on arrival

`make test-backend` was **14 failed / 1251 passed** before any work. It was red on
the clock, not on a code change, which is why it had not been noticed:

1. **A real bug.** `_send_whatsapp` judged the 24-hour WhatsApp session window
   with `datetime.now()` while the rest of the beat tick used the `now` it was
   handed. Benign in steady state; wrong for a replay, a backfill, or a skewed
   box. Fixed by threading `now` through.
2. `test_the_session_acceptance_criterion` never passed `approve()` a clock, so
   rungs were scheduled at `max(due_at, real_now)` and the July ladder it drives
   could never fire. `max(due_at, now)` is deliberate product behaviour and
   stays; the test now approves on the scenario's clock.
3. `_prod()` in `test_config.py` went stale when CLOUD1 made `ENVIRONMENT_ID`
   and `RELEASE_SHA` production-checked — it asserted that a config which is no
   longer safe passes.
4. **A time bomb.** The roster/people/scheduling tests pinned `date(2026, 8, 3)`
   or generated inventory from *today* and then booked `slots[0]`. Both hold
   until the wall clock passes the pinned date, or until today happens to be the
   template's weekday with the clinic hour already past — which is exactly
   today, a Tuesday, at 22:30. `generation_start()` and `a_weekday_ahead()` in
   `tests/factories.py` anchor generation at tomorrow, so these now hold at
   every hour on every weekday.

No assertion was weakened to get green.

## Decisions made

- **`Queue` is untouched.** Still one row per department with `doctor_id` NULL.
  Per-doctor queues would fragment the per-department token series, its unique
  constraint, and the offline `OfflineTokenBlock` leases, and would split the
  public board into N lines. Assignment is an attribute on the visit and a
  filter on the worklist. A future session must not "finish the job" by making
  queues per-doctor.
- **Nothing merges automatically.** A phone/UHC match is a *candidate* for a
  human. A wrong merge in an oncology record is worse than a duplicate, and only
  the duplicate is repairable without argument.
- **The kiosk discloses nothing on a match.** The service records the candidate;
  no patient-identifying data is returned to the terminal. AR2 must preserve this
  when it builds the arrival screens.
- **Previous `WALKIN-` rows are never offered as a match.** Otherwise the
  coordinator is shown an earlier anonymous arrival as though it were a file.
- **Off-duty doctors are listed, not hidden**, with an honest `on_duty` flag. A
  pilot roster is often incomplete; a coordinator who cannot find the consultant
  in the room assigns nobody at all.
- **`default_doctor` guesses only when there is nothing to guess** — exactly one
  doctor on duty. An unnoticed default is how a patient lands on the wrong list.
- **Not ABHA.** `external_id_kind` is a deployment-configured label. A real ABHA
  integration is ABDM registration, consent artefacts and linkage duties — a
  programme, not a column.

## Deviations from spec

None material. The plan's Session A is being delivered in two parts for context
reasons, not scope reasons.

## Tests & evidence

- `make test-backend`: **1265 passed** (was 14 failed / 1251 passed).
- `make test-voicegw`: 25 passed. `make test-web`: 48 passed.
- New tests: `tests/test_assignment.py`, 20 cases covering handset-format
  matching, UHC-over-phone precedence, walk-in exclusion, hospital isolation,
  candidate recording, confirm/reject/idempotency, registered-patient safety,
  roster ordering, default selection, and cross-department refusal.
- No UI in this part, so no screenshots.

## Known gaps / stubs introduced

- `app.assignment` has no HTTP surface yet — nothing calls it in production paths.
  `create_walk_in` does not yet call `find_candidate`.
- Department change in `assign()` moves the visit but does **not** yet re-home the
  queue entry or reissue the token. AR2 owns that, and it must not ship without
  it: a visit whose department moved without its queue entry is on a board it is
  not queued in.
- No coordinator PIN mechanism exists yet. It is new auth surface and needs a
  deliberate decision (see below).

## Commits

- `d22dabb` — S AR: repair the red baseline before building on it
- `cee5488` — S AR: identity matching and doctor assignment
