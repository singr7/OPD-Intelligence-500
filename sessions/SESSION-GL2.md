# SESSION-GL2 — Staff onboarding + roster (doc 03 §10's unbuilt half)

**Date:** 2026-07-27 · **Scope ref:** [doc 12](../docs/12-GO-LIVE-PLAN.md) §6/§7 → S-GL.2
(Phase 1 of the go-live track, second of three)

The second kiosk-first go-live session. Its subject is doc 12 §6 in one sentence: onboarding a
doctor meant editing `seeds/doctors.json` on the box and re-running `make seed`, which is not
something a hospital administrator can do and made "we hired an oncologist" a deploy. The
slot-template panel was the admin console's last honest deferral marker, unbuilt since S18E.

## Acceptance criteria checklist

- [x] **A new doctor is onboarded, given a Tuesday clinic by CSV import, has slots generated, and
      appears in the receptionist's inventory and the doctor console — entirely from the console,
      with no seed run and no deploy.** Proven twice: at the service layer against the queries
      those surfaces actually call (`test_a_doctor_onboarded_from_the_console_is_bookable_and_visible`
      → `scheduling.find_slots`, then a real `scheduling.book` into the slot it returned), and in a
      browser against a live stack (`web/e2e/people.spec.ts`, which reads
      `GET /appointments/slots` — the AI receptionist's own query — and checks the weekday in
      Asia/Kolkata).
- [x] **The import dry-run refuses a row naming an unknown doctor and says which row.**
      `test_the_dry_run_names_the_row_that_names_a_doctor_we_do_not_have`, and on screen as
      `web/screenshots/sgl2/02-dry-run-refusal.png` — row 3, the name that was not found, and the
      Apply button disabled.
- [x] **Deactivation does not orphan booked appointments — it surfaces them and makes the admin
      decide.** Two steps by construction: `deactivation_impact` lists the patients by name and
      `deactivate` refuses without an acknowledgement.
- [x] `make test` green: backend **1212** (was 1156), voice-gw 25, web typecheck + lint + 48
      conformance, Android 6. `make lang-qa` clean across [en, hi, mr, te]. `make lint` green.
- [x] Playwright: **5 tests** in a new `people` project, plus four screenshots self-critiqued
      against doc 04 §5 (see *What rendering it found*).

## What was built

### 1. `app/people.py` — staff onboarding

**A doctor is two rows.** `User` is the login identity; `Doctor` is the clinical profile that
hangs off it. `create_doctor` writes both in one transaction — a `Doctor` with no `User` cannot
log in, and a doctor-role `User` with no profile breaks every screen that joins on it.

**An invite is not a credential.** The OTP login already exists, so "invite this person" means
exactly *"this phone number can now sign in"*, which creating an active `User` row already
accomplished. `send_invite` sends an SMS saying so through the provider layer. It mints nothing:
no token to leak, expire or reset, and re-sending is idempotent by nature. The test asserts the
message contains no URL.

**`normalise_phone` is load-bearing, not tidying.** The OTP flow looks up `users.phone` by exact
string match, so a number stored as `98765 43210` is an account that silently cannot sign in —
created successfully, audited, and useless. Six typing shapes normalise to one.

**Patients cannot be minted here.** `STAFF_ROLES` excludes `patient` and `caregiver`: those come
from registration and from a consented grant (S16), and a console path into them would be a
second, unaudited door into the patient identity model.

### 2. `app/roster.py` — the clinic grid, and the trap

The handoff's design warning ("decide what happens to a booked slot whose template moved before
writing the button, not after") turned out to have a wrong obvious answer, and finding that was
most of the session's engineering.

`generate_slots` dedupes on `(doctor, instant)` **regardless of `blocked`**. So the natural
implementation — block this template's future slots, then regenerate — blocks the clinic and then
never refills it, because generation skips every instant that already has a row. The clinic
empties out, silently, and the receptionist starts telling callers a working clinic is full.

`_reconcile` does three different things instead:

| instant | what happens |
|---|---|
| the new shape **no longer runs** it, slot empty | `blocked` |
| the new shape **no longer runs** it, somebody booked | untouched, and returned by patient name |
| the new shape **still runs** it | updated in place — length, type, capacity — and unblocked |
| it runs, no row yet | left to `generate_slots`, the one thing that creates inventory |

The instants come from `scheduling.instants_on`, which generation also uses — made public for
exactly this reason. Two implementations of "what times does this clinic run", one generating and
one reconciling, would disagree precisely when a template was edited, which is the only case that
matters.

Capacity is never shrunk below what is already booked: `booked <= capacity` is a database CHECK,
so that is a 500 rather than a policy. Those slots keep their capacity and the admin sees the
clinic and the seat count disagree, which is true.

### 3. The import — all-or-nothing, dry-run first

A roster is one document. Half of it applied is worse than none: the admin cannot tell what
landed without reading the database, and re-uploading the fixed file would double-apply the good
rows. So `plan_roster` evaluates **every** row (an administrator fixing a file wants all of its
problems, not one upload per typo), reports errors against the line number in their own
spreadsheet, and `apply_roster` refuses a plan with any. The dry run is the same code path with
the write withheld, so what it previews is what happens.

XLSX is read without a workbook library — an .xlsx is a zip of XML, and the only shape needed is
"the first worksheet's cells as strings". A full parser would be a dependency with a far larger
surface than the six columns it reads. Excel's fractional-day time serial (`0.4375` = 10:30) is
handled, because that is what a roster exported without text formatting actually contains.

### 4. The People & roster tab

Second in the tab bar, behind Channels: a hospital with an open channel and nobody on the roster
has the same problem one rung down. The week is drawn as a **timetable** — seven columns, one row
per doctor — rather than a table sorted by id, and an **ungenerated clinic is drawn hollow**
rather than merely annotated, because *authored* and *bookable* are different facts and the
hollow block is the one the receptionist cannot offer.

## What rendering it found (and a test would not have)

- **The confirmation landed below the fold.** The roster import sits above a long people table, so
  a flash message rendered after it was several hundred pixels down: an operator pressed *Apply
  the roster* and saw nothing happen. It moved to the top of the tab.
- **The add-a-clinic affordance was hover-only.** A `+` at `opacity: 0` until hover is a control a
  first-time administrator never finds, and it is the one that turns an empty week into a clinic.
  Now faint, then solid.

## Decisions worth recording

- **People are not versioned.** Trees, the protocol bank and the channel document are all
  draft→publish→resolve, and this deliberately is not — the handoff predicted it and it held up. A
  doctor is not authored content with a review cycle; a two-step publish would add a way to leave
  hiring somebody half-done and buy no safety. `record_admin_action` carries the accountability
  instead.
- **Nothing is ever deleted, on either side.** A clinic that stops is deactivated and its empty
  future slots are blocked; a person who leaves is deactivated and can be reactivated. Matching
  `app.scheduling`'s own rule, and the reason both flows can be undone.
- **Reactivation restores the login, not the clinic.** "She is back" and "she is back on Tuesdays
  at ten" are different facts, and the second is a decision somebody should make on purpose.
- **A refusal about state is a 409, not a 422.** The console needs to tell "fix your input" from
  "confirm this", because the second is a button and the first is a form error.

## What I would flag to the operator

- **No migration.** `users`, `doctors` and `slot_templates` have existed since S2/S15; this
  session only writes to them from a new place. Nothing to run before deploying it.
- **The invite SMS is not templated for DLT.** It goes out as free text through the SMS provider,
  which on a real Indian gateway means it may be rejected in a way the fake never shows. It is
  staff-facing rather than patient-facing, and `send_invite` records the vendor's refusal rather
  than raising — but the first real invite is a first-contact item.
- **A clinic edit reconciles inventory inside the console's request.** On a doctor with a year of
  generated slots that is a few hundred row updates in one transaction. Fine at pilot scale;
  worth remembering if a hospital ever generates a much longer horizon.
- **Nothing here has been run on the box.** Every screenshot is a local dev stack — the same
  caveat S-GL.1 carried, and now an S-GL.3 item for one more tab.

## Files

New: `backend/app/{people,roster}.py`, `backend/tests/{test_people,test_roster}.py`,
`web/app/(admin)/admin/_components/PeopleTab.tsx`, `web/e2e/people.spec.ts`,
`web/screenshots/sgl2/`.

Changed: `app/routes/admin.py` (the People + roster surface, replacing the last deferral marker),
`app/scheduling.py` (`_instants` → public `instants_on`), `tests/test_admin.py` (the marker
assertion), `web/app/(admin)/admin/_lib/api.ts`,
`web/app/(admin)/admin/_components/{Console,ProtocolsTab,adminStyles}.tsx`,
`web/{playwright.config.ts,package.json}`.
