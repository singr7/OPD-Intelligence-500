# HANDOFF — after SESSION-AR2

**Repo state:** branch `assign-rx-identity`, last commit `74319d1`.
`make test-backend` 1325 passed. Migrations `c6e3681f5ce1` and `520d07f0b3e4`
are applied **locally only**.

The ANDROID1/CLOUD1/VOICE1 external-release gate is unchanged and still open; see
`sessions/SESSION-ANDROID1.md`.

**Where the build stands:** Session A of `sessions/SESSION-ASSIGN-RX-PLAN.md` is
complete on the backend. A kiosk arrival can carry a phone and an optional UHC
ID; a returning patient is matched and recorded as a candidate without the
terminal disclosing anything; a coordinator unlocks a PIN-gated staff strip and
settles identity + department + doctor in one action; a department change moves
the queue entry and reissues the token; a skipped or offline arrival can be
assigned later from the console. **None of it has a screen yet** — AR3 is the UI,
and until it lands nothing a user touches has changed.

## Next session (AR3) — the two screens

1. **Kiosk arrival**: "Have you visited us before?" → phone → optional UHC ID.
   Doc 04 rural-first laws apply — audio-first, one decision per screen, ≥64px
   targets, tap alternative always present, and all four languages. On a match
   the screen says only "we may already have your file; our staff will confirm".
2. **Kiosk staff strip** on the token screen: locked by default, `Unlock` →
   name picker (`GET /kiosk/staff/holders`) → numeric keypad → `POST
   /kiosk/staff/unlock`. Unlocked, it shows the candidate, department, doctor
   (pre-selected via `default_doctor_id`), `Skip` and `Confirm`. Idle relock.
   On a token reissue the new number must be announced loudly — the patient is
   holding a stale slip.
3. **Coordinator console**: an assign control per entry, using
   `/queue/entries/{id}/assignable` and `/queue/entries/{id}/assign`.

Wire shapes are in `app/routes/kiosk.py` (`StripOut`, `AssignIn`, `AssignOut`)
and `app/routes/queue.py` (`EntryAssignIn/Out`).

First commands:

```
git checkout assign-rx-identity
make test-backend            # expect 1325 passed
docs/04-UIUX-GUIDE.md        # mandatory before any screen
sed -n '1,80p' sessions/SESSION-ASSIGN-RX-PLAN.md   # §1.2 sketches the strip
```

## Watch out for

- **Do not widen the `kiosk_staff` token.** It is deliberately accepted only by
  `require_kiosk_staff`. A PIN typed in a public corridor must not reach the
  coordinator console, the patient card or the audit trail.
- **The kiosk must not render the candidate anywhere outside the unlocked strip.**
  `test_kiosk_strip.py` asserts the unauthenticated response is clean; the screen
  is the other half of that promise and has no test yet.
- `Queue` stays per-department with `doctor_id` NULL. Assignment is a visit
  attribute and a worklist filter, never a second queue.
- `make deploy` does not run migrations. Two are pending for Omen.
- The test suite still pins absolute 2026 dates in places; prefer
  `tests/factories.generation_start()` / `a_weekday_ahead()` over a new pin.

## Decisions needed from the human

1. **PIN issuance.** Nothing in the admin UI sets a coordinator's kiosk PIN yet —
   `app.auth.kiosk_pin.set_pin` exists but has no route. Should AR3 add it to the
   admin people screen, or should the pilot's single PIN be seeded and rotated by
   hand? Seeding is fine for one coordinator and avoids building a screen nobody
   will use twice.
2. **Language coverage for the new kiosk copy.** The arrival questions need
   Hindi, Marathi and Telugu, and doc 07 §4 requires patient-facing strings in all
   active languages or an explicit pending note. Is native review available for
   AR3, or should the new screens ship English + Hindi with the other two flagged?

## Backlog additions

- Sessions B (doctor workspace IA, including the `Unassigned` count that is the
  compensating control for every skipped assignment) and C (consult/prescription
  paths) of `SESSION-ASSIGN-RX-PLAN.md` are unstarted. **Session B matters more
  than it looks**: until the doctor console has scoped worklists, assignment
  changes nothing a doctor experiences.
- `app.kiosk._departments` is private while its sibling `department_by_code` is
  public. Harmonise on the next touch of that module.
- Pre-existing E501 in `app/config.py:260`, left alone deliberately.
- A sweep for remaining wall-clock coupling in the test suite; two real instances
  were found and fixed in AR1.
