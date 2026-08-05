# SESSION-B — The doctor workspace

**Date:** 2026-08-05 · **Scope ref:** `sessions/SESSION-ASSIGN-RX-PLAN.md` §3 and §4

Session A (AR1/AR2/AR3) made the kiosk and the desk able to settle who a patient
is and which doctor will see them. Nothing on the doctor's side respected any of
it: `day_list` was department-scoped and ignored `Visit.doctor_id`, so staff were
doing real work at the kiosk for no observable effect. That is what this session
closes.

## Acceptance criteria checklist

- [x] AC1 — `GET /doctor/day?scope=mine|unassigned|department`, default `mine`.
- [x] AC2 — every response carries counts for all three scopes, so the
      `Unassigned` badge is truthful without a second call.
- [x] AC3 — a non-zero unassigned count **with waiting patients** renders as an
      attention state while its tab is closed, in words as well as colour.
- [x] AC4 — the doctor console's unassigned figure agrees with the coordinator
      console's "Waiting, unassigned" metric, by construction.
- [x] AC5 — "Take this patient" on an unassigned row *and* on a colleague's,
      audited.
- [x] AC6 — authorization stays at department scope: `patient_card` and
      `dictation.assert_visit_scope` are not narrowed to the assigned doctor.
- [x] AC7 — the context spine is sticky and never unmounts, carrying exactly
      identity + token, diagnosis, allergies, red flags.
- [x] AC8 — four working tabs and one muted, feature-flagged "Coming soon (4)"
      entry with no mock clinical content.
- [x] AC9 — scoping filters rows and never reorders them.
- [x] AC10 — screenshots taken and self-critiqued against doc 04 §5.

## What was built

- **`app/doctor.py`** — `DayScope` / `DAY_SCOPES`, `DayCounts`, scoped
  `day_list(..., scope=...)`, `take_visit()`, and `_diagnosis()`. `DayRow` gained
  `assigned_doctor_id` / `assigned_doctor_name` / `is_mine`; `PatientCard` gained
  the assignment pair, `diagnosis` and `caregiver_answered`.
- **`app/routes/doctor.py`** — `GET /doctor/day?scope=`, `POST
  /doctor/visits/{visit_id}/take` (`require_doctor`, notifies the queue hub).
- **`web/app/(doctor)/doctor/_components/ContextSpine.tsx`** — new. Sticky,
  four elements, mounted for every tab.
- **`web/app/(doctor)/doctor/_components/WorkTabs.tsx`** — new. Overview /
  Intake answers / History / Consult, plus the "Coming soon" disclosure.
- **`PatientCard.tsx`** — rebuilt as the three read tabs, unframed sections, no
  accordions, no nesting. Ends with the provenance line.
- **`DayRail.tsx`** — scope tabs with counts, the attention line, whose-patient
  labelling, and the per-row take button.
- **`Console.tsx`** — scope state, `onTake`, tab state; `D` toggles the Consult
  tab rather than swapping the whole stage.
- **`scripts/seed_doctor_demo.py`** — leaves Sita Kumari (token 16) in the
  department pool the way an offline kiosk does, and closes out anyone else
  already queued in the department today so the demo owns the day.
- **`web/e2e/doctor.spec.ts`** — five new tests covering the whole of the above.

## Decisions made

1. **The `Unassigned` tab badge shows the scope's row count; the attention state
   is driven by `unassigned_waiting`.** Those are two different numbers when
   someone unassigned has already been called. A badge that disagrees with the
   list under it is worse than a badge that disagrees with a metric on another
   screen — and `unassigned_waiting` uses the coordinator console's exact
   definition, so the desk and the consulting room cannot quote different
   figures at each other.
2. **"Take this patient" is offered on a colleague's row too, and is not behind a
   confirm dialog.** Cover is routine in an OPD; requiring a coordinator turns
   one doctor's absence into a stalled line. It is neither hidden nor blocked —
   it lands in the audit trail via the existing `Clinical` `before_flush` hook,
   where the previous assignment is recoverable. A confirm step on the one
   action that unblocks a stalled line teaches doctors to click through dialogs.
3. **`take_visit` delegates to `assignment.assign`.** One implementation of
   "point this visit at a doctor", one set of eligibility rules. The department
   check is done first only so the error is worded from the doctor's side.
4. **The diagnosis line reads the latest *signed* note, across visits.** A draft
   is a doctor thinking out loud mid-consult; promoting one would put an
   unreviewed machine transcription where a clinician reads a diagnosis. Its date
   rides along, because an unqualified line silently belonging to a note from
   March is worse than no line at all.
5. **The spine states that allergies are not captured, and does not say "no known
   allergies".** Nothing in this system records one — not the kiosk intake, not
   the consult note. The second phrasing is a clinical claim this record is in no
   position to make, and a doctor would act on it.
6. **No `stage` on the diagnosis line.** The plan's mock shows "Stage IIIA ·
   Invasive ductal carcinoma"; there is no staging field in this schema, and
   inventing a vocabulary the record cannot support would be worse than the
   shorter line.
7. **The encounter bar stopped restating identity.** The spine is 40px below it
   and never unmounts, so the bar states the one thing the spine cannot: how many
   are still waiting in the department.
8. **The consult note only seeds its transcript when nobody has typed in it.**
   Found by the dictation E2E, but reachable by hand: the panel renders
   immediately and fetches the stored note a moment later, and that fetch called
   `setTranscript` unconditionally — so a doctor typing the instant the note
   opened lost the opening sentence. A `touched` ref now guards it. Losing
   dictated words is the one failure that surface exists to prevent.
9. **`seed_doctor_demo` closes out, rather than deletes, other queue entries.**
   Only the queue entry state moves; no visit, intake, note or patient is
   touched. A shared dev database accumulates arrivals from the kiosk demo and
   the assign spec, and the E2E assertions about *which* token leads the rail
   have to mean what they say.

## Deviations from spec

- **§4.3's vitals guidance is not implemented.** There is no vitals field
  anywhere in this schema — nothing captures blood pressure, SpO₂, height or
  weight — so there was nothing to give clinical emphasis to. Logged as backlog
  rather than mocked.
- **§4.3's "patient's questions" section is likewise absent from the Overview.**
  The intake contract (doc 03 §4) has no such field; `unclear` is the nearest
  thing and is already rendered.
- **Allergies are a stated gap rather than content** — see decision 5.

## Tests & evidence

- `make test-backend`: **1355 passed** (was 1335 passed + 1 xfailed; the strict
  xfail AR2 left as this session's gate is deleted, and its behaviour is now
  covered by real tests).
- New backend tests: 13 — three scopes, counts on every scope,
  `unassigned_waiting` agreeing with the coordinator's definition, scope
  validation, order preserved under scoping, take on unassigned / on a
  colleague / refused across departments / audited, the three diagnosis rules,
  and the card refusing to narrow to the assigned doctor.
- `npm run build`: green. `tsc --noEmit`: clean. `eslint`: clean.
- Doctor E2E: **11 passed** (`npm run e2e:doctor` against a live stack +
  `seed_doctor_demo`). Dictation E2E: **6 passed** — it needed two changes and
  one product fix. It now reads the `Department` scope, because its row indices
  are positions in the department's queue order and `Mine` omits the pooled
  arrival; and `DictationPanel` no longer overwrites a transcript the doctor has
  already typed into (see decision 8).
- Screenshots (`web/screenshots/s9/`):
  - `02-day-and-card.png` — the rail's three scopes over the spine and Overview.
    *Critique:* the two "nothing recorded" lines (diagnosis, allergies) stack as
    a grey pair at the top of the spine. Honest, but on a patient with neither it
    is the quietest part of the most important element. Acceptable while both are
    genuinely empty; revisit when the diagnosis is usually present.
  - `03-context-spine.png` — the spine above the tab row.
    *Critique:* the red-flag stamp is the only saturated block on the page and
    it earns it. The token at 32px reads across a desk without competing with it.
  - `04-tabs.png` — History.
    *Critique:* unframed sections read much better than the old accordions; past
    visits with today highlighted is the strongest thing on the tab.
  - `07-unassigned-badge.png`, `08-unassigned-scope.png` — the attention state
    and the pool. *Critique:* the marigold tab plus the spelled-out line is
    conspicuous without being an alarm, which is the right register for a state
    that is normal but must be resolved.
  - `09-coming-soon.png` — the disclosure.
    *Critique:* muted, right-aligned, does not read as a tab. The Lab reports
    caveat is the longest line in the panel, which is correct — it is the one
    that could be misread as broken.
  - `05-called-next.png`, `06-morning-cleared.png` — unchanged behaviour,
    re-shot.

## Known gaps / stubs introduced

(Mirrored into STATE.md → Stubs & fakes.)

- **Allergies are not captured anywhere in the product.** The spine and the
  History tab both say so in words. This is the largest remaining gap in the
  spine's four elements.
- **No vitals in the schema**, so §4.3's vitals treatment is unbuilt.
- **The `signedNotes` set is still per-session console state.** The card model
  carries no note status, so a console reload forgets which visits it watched get
  signed. The Consult tab shows the real state when opened.
- Migrations `c6e3681f5ce1` and `520d07f0b3e4` remain applied **locally only**.

## Commits

(see `git log` on `assign-rx-identity`)
