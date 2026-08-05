# SESSION-AR3 — The arrival screens, the kiosk staff strip, the desk's assign control

**Date:** 2026-08-05 · **Scope ref:** `sessions/SESSION-ASSIGN-RX-PLAN.md` §1.1, §1.2, §3 (Session A's UI)

## Acceptance criteria checklist

- [x] **AC1 — Kiosk arrival.** "Have you visited us before?" → phone → optional
      hospital ID, one decision per screen, every one skippable, ≥64px targets,
      audio-first with a replay on each. Neither field gates an intake or a token.
- [x] **AC2 — The kiosk discloses nothing on a match.** The screen says only "we
      may already have your file; our staff will confirm" — and says it to
      *everyone who gave a number*, not only to the arrivals that matched.
- [x] **AC3 — Staff strip.** Locked by default on the token screen; `Unlock` →
      name picker (`GET /kiosk/staff/holders`) → numeric keypad → `POST
      /kiosk/staff/unlock`. Unlocked it shows the candidate, department and
      doctor (pre-selected from `default_doctor_id`), with `Skip` and `Confirm`.
      Idle relock at 45s.
- [x] **AC4 — A reissued token is announced loudly.** A department change takes
      over the strip in marigold with the new number, the old one named as
      invalid, and is spoken. It stays until a human acknowledges it.
- [x] **AC5 — Coordinator console.** An assign control per entry using
      `/queue/entries/{id}/assignable` and `/queue/entries/{id}/assign`.
- [x] **AC6 — English + Hindi only**, with the gap logged as pending (doc 07 §4).
- [x] **AC7 — Screenshots taken and self-critiqued** (doc 04 §5). Three rounds of
      fixes came out of them; see *Tests & evidence*.

## What was built

- `web/app/(kiosk)/kiosk/KioskApp.tsx` — three new screens (`returning`,
  `arrivalPhone`, `arrivalId`) between the caregiver question and registration,
  plus `ArrivalAck`. The progress line counts *this* patient's screens: a
  first-timer sees 4 steps, a returning patient 6.
- `web/app/(kiosk)/kiosk/_components/Keypad.tsx` — one keypad, two sizes: the
  patient's phone number and the coordinator's PIN (masked). Ten fixed keys
  rather than an input the kiosk shell may or may not raise a keyboard for.
- `web/app/(kiosk)/kiosk/_components/StaffStrip.tsx` — the PIN-gated strip. The
  unlock token lives in React state only; the candidate is fetched after the PIN
  and dropped on lock, on idle, and on print.
- `web/app/(coordinator)/coordinator/_components/AssignPanel.tsx` — the desk's
  assign control, opening in place under its row, plus per-row `Unassigned` /
  doctor / `Possible existing file` chips and a "waiting, unassigned" metric.
- `backend/app/queue.py` + `routes/queue.py` — `EntryView` and `ConsoleEntryOut`
  gain `assigned_doctor_id`, `assigned_doctor_name`, `link_state`; new staff
  route `GET /queue/departments`.
- `backend/app/offline.py` — an offline arrival now carries its health ID through
  sync and gets its candidate lookup there.
- `web/e2e/assign.spec.ts` + the `assign` Playwright project (`npm run e2e:assign`).

## Decisions made

1. **The acknowledgement is unconditional.** It shows for every patient who gave
   a phone or an ID, matched or not. Showing it only on a hit would make a public
   terminal an oracle: type a neighbour's ten digits, watch the screen, learn
   that this cancer hospital holds a file on them. The kiosk client is never told
   whether a match happened — `/kiosk/start` does not return it, and it should
   not start.
2. **The console shows that a candidate exists, never who.** A queue row is not a
   disclosure surface; the name stays behind the kiosk PIN and the doctor's card.
3. **`Skip` still records an identity decision.** "Not the same person" is a fact
   a human established; dropping it would re-offer the same wrong match at the desk.
4. **Changing the department clears the doctor and disables the picker** until
   the strip is re-read. The loaded roster belongs to the old department.
5. **The patient's `Print slip` / `Start over` buttons are hidden while the strip
   is unlocked.** `Start over` resets the kiosk and sits a thumb's width from a
   half-finished assignment.
6. **New copy is `tb()`, not `t()`.** A Marathi or Telugu kiosk falls through to
   English rather than to a machine translation nobody has read (AR2 decision).

## Deviations from spec

- The plan's §1.1 draws the phone and the hospital ID as one "Screen B". They are
  two screens here, because doc 04 law 2 is one decision per screen and both are
  independently skippable.
- **Scope added beyond "the UI":** three console fields, `GET /queue/departments`,
  and the offline external-ID carry. The first two are what let the console tell
  an assigned patient from an unassigned one — without them the assign control
  would be guessing. The third stops a patient-typed ID being silently dropped
  during an outage. All three have tests.

## Tests & evidence

- `make test-backend`: **1335 passed, 1 xfailed** (was 1328; +7 new).
- New backend tests: 5 in `test_queue.py` (console assignment fields, candidate
  reported without disclosing the name, `/queue/departments`), 2 in
  `test_offline.py` (an offline arrival carries its ID and finds the prior file;
  no match stays a new file).
- New web tests: `e2e/assign.spec.ts`, 3 tests, all green against the live stack.
  `kiosk`, `ux-smoke`, `offline-demo`, `accessibility`, `conformance` all green.
- `npm run typecheck` and `npm run lint` clean.
- Screenshots — `web/screenshots/s6/` and `web/screenshots/ar3/`:
  - `s6/02a-returning.png` — two big choices, icons carry the meaning. The rail's
    five "not answered yet" rows are noisy this early, but they are honest and
    consistent with the rest of the intake.
  - `s6/02b-arrival-phone.png` — **first cut overflowed**: the audio bar was
    pushed off the top at 1280×720. Fixed by moving the display beside the keys
    on short screens. Keys stayed at 64px+.
  - `s6/08-token.png` — the locked strip reads as staff, not patient, and does
    not compete with the numeral.
  - `ar3/02-strip-unlocked-candidate.png` — **two fixes came from this shot**: the
    strip overlapped the print buttons (`flex: 1` → `flex: 1 0 auto`), and then
    the screen was still 5px over (numeral capped against viewport height, the
    patient's buttons hidden while unlocked).
  - `ar3/05-console-assign-panel.png` — **one fix**: the doctor select sized
    itself to its longest option and pushed the panel outside the department card.

## Known gaps / stubs introduced

- **Marathi and Telugu are missing for all AR3 copy** (`T2` in `_lib/i18n.ts`).
  A mr/te kiosk shows English on these screens. Mirrored into STATE.md.
- The strip cannot assign a doctor in a *newly chosen* department in one pass —
  changing the department reissues the token and relocks; assigning the doctor is
  a second unlock. Correct but clunky; noted in the handoff backlog.
- The offline path still cannot assign at the kiosk (accepted debt, plan §8) —
  but those visits now arrive with a candidate for the console to settle.

## Commits

- `cf0f4a5` — S AR3: give the arrival identity and the staff strip a screen
- `f49ba1e` — S AR3: make the three screens fit, and drive them from a test
