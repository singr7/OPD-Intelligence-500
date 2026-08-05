# SESSION-C — The consult and prescription paths

**Date:** 2026-08-05 · **Scope ref:** `sessions/SESSION-ASSIGN-RX-PLAN.md` §5

Sessions A and B settled who a patient is, which doctor sees them, and what that
doctor looks at. What they did not touch is the end of the consult — where this
system had two real gaps and one screen making claims it could not back.
Dictation was mandatory for a prescription, a failed mapping was a dead end, and
a doctor who wrote on a paper pad left a visit indistinguishable from one they
had abandoned halfway.

## Acceptance criteria checklist

- [x] AC1 — a prescription is produced with **no speech at all** ("Type note" →
      empty-transcript draft, `/map` skipped, editable fields).
- [x] AC2 — a forced mapping failure keeps the transcript and still reaches a
      signature.
- [x] AC3 — `blocking_meds` refuses the signature on every path, including typed.
- [x] AC4 — an `external_manual` conclusion leaves an auditable record and a
      `done` queue entry.
- [x] AC5 — print is unreachable before approval.
- [x] AC6 — live-analyser waveform, or an honest timer; `Stop & transcribe` in
      brand green.
- [x] AC7 — prescription preview corrections: name dominance, print gated,
      dominant-action fix, delete confirmation, pills removed.
- [x] AC8 — screenshots taken and self-critiqued against doc 04 §5.

## What was built

**Backend**

- **`app/dictation.py`** — `compose()` opens the editable field set with no model
  in the loop; `map_transcript` now opens those same empty fields alongside
  `mapping_error` on a failure; `validate_meds(..., check_unsaid=)` suppresses
  the `unsaid` verdict when there is no transcript to check against.
- **`app/routes/dictation.py`** — `POST /dictation/{id}/compose`.
- **`app/doctor.py`** — `conclude_visit()` + `Conclusion` + `ConclusionRefused`,
  `_has_signed_note()`; `PatientCard` gained `rx_mode`, `concluded_at`,
  `conclusion_note`, `note_signed`.
- **`app/routes/doctor.py`** — `POST /doctor/visits/{id}/conclude`.
- **`app/models/enums.py`** — `RxMode` (`system` / `external_manual` / `none`).
- **`app/models/clinical.py`** + migration `c063fd91e198` — `Visit.rx_mode`,
  `conclusion_note`, `concluded_at`, `concluded_by`. All nullable, no backfill.

**Console**

- **`DictationPanel.tsx`** — the four-step rail; Dictate / Type note / overflow;
  the analyser-driven meter and elapsed timer; the recoverable mapping-failure
  banner; fully editable meds (dose, route, frequency, duration, delete with
  confirmation, add-a-medicine) and impression / follow-up / advice; the
  pre-signature statement that nothing can be printed yet.
- **`ConcludeDialog.tsx`** — new. Three endings, the named consequences, an
  optional note.
- **`Console.tsx`** — `resettle()` extracted; "Complete consult" routes through
  the conclusion; `signedNotes` seeded from the card.
- **`_lib/dictation.ts`** `composeNote`, **`_lib/doctor.ts`** `concludeVisit`.

## Decisions made

1. **The typed note joins the existing path at the earliest possible point.**
   `compose` writes an empty mapping into `fields` and stops. Everything after
   it — `apply_corrections`, the formulary verdict, `sign`, `generate` — is
   byte-for-byte the dictated path. There is deliberately no second
   prescription-creation route: a parallel writer around the signature boundary
   is how drug-safety validation gets bypassed two quarters from now.
2. **`unsaid` is suppressed when there is no transcript.** It asks "did a model
   rename this drug on the way in?"; on a typed note there was no model. Left
   alone, `_was_said` would have flagged *every* line of *every* typed note,
   which is a flag that means nothing and teaches people to clear flags without
   reading them. Derived from the record (an empty transcript) rather than a
   client-set flag, because `map_transcript` refuses an empty transcript and so
   the two cannot disagree. The formulary check is untouched — that one is about
   the drug, not about who wrote it down.
3. **A failed mapping opens the fields automatically.** No extra tap on a path
   where the doctor is already annoyed and the patient is already waiting.
   Existing fields are never overwritten, so a retry that fails again cannot
   wipe what they have typed.
4. **`conclude` refuses `system` without a signed note.** It is the one of the
   three modes that can be checked, and a conclusion claiming a digital
   prescription that does not exist sends the pharmacy looking for a document
   nobody produced. 400, not 403: the doctor has every right to be there.
5. **The conclusion lives inside the S8 transition table, not around it.**
   `in_consult`/`lab_requeue` → `done`; `called` walks through `in_consult` so
   `started_at` stays truthful; `waiting` is refused outright (nobody saw that
   patient); `done`/`no_show` are left where they are and the conclusion is
   still recorded. Widening `_ALLOWED_TRANSITIONS` for this verb would have
   removed the guard that stops the board showing a patient who is both seen and
   waiting.
6. **Nothing is written until everything that can refuse has.** A rejected
   conclusion that had already stamped `rx_mode` would leave the record saying a
   consult ended in a way it did not.
7. **"Complete consult" is a conclusion now, and still one tap in the normal
   case.** With a signed note it concludes as `system` with no dialog; without
   one the dialog opens. Putting a confirmation on the ordinary ending would
   have trained doctors to click through the dialog that exists for the endings
   that lose something — the same reasoning that kept "Take this patient"
   dialog-free in Session B.
8. **`PatientCard.note_signed` replaces the console's per-session memory.**
   Session B's backlog item, pulled in because the conclusion routing has to
   know the answer after a reload, and guessing would put the lossy dialog in
   front of a doctor who had just signed.
9. **The consequence panel is a fixed-height slot.** The confirm button must not
   move under the cursor when the doctor changes their mind (doc 14 principle 9).
10. **The dialog's confirm button stays green.** Red is clinical danger and
    destruction. Ending a consult you have finished is safe expected progress;
    the marigold panel carries the attention. Painting it red would repeat the
    mistake this session removed from the Stop button.
11. **No bars without an analyser.** An evenly spaced waveform is a claim that
    audio is being captured. With no analyser the doctor gets an elapsed timer
    and a recording indicator, which are both simply true.

## Deviations from spec

- **§5.2's "Drop the `Symptomatic` / `Supportive` pills"** — those are the
  prototype's; this console never had them. Nothing to remove.
- **§5.2's `DOSE:` / `ROUTE:` / `FREQ:` uppercase-label correction** — likewise
  the prototype's. `RxPanel` already uses one column header row, and the
  medicine name at 16px/800 was already the strongest text on the row.
- **The full mapping-failure recovery is proven in `test_dictation.py`, not the
  E2E.** The live stack runs a working fake model and cannot be made to fail
  honestly from a browser; the E2E stubs the `/map` response and proves the part
  only a browser can — that the doctor's words are still on screen afterwards.

## Tests & evidence

- `make test-backend`: **1376 passed** (was 1355). 21 new: the typed note's
  fields, idempotent non-destructive `compose`, typed drugs not called unsaid but
  still formulary-checked, typed → signature → prescription, typed +
  unacknowledged flag refused, mapping failure → fields open → signature →
  prescription, a second failure not wiping typed fields, the HTTP no-speech
  flow; and for the conclusion: paper script recorded, unconcluded ≠ nothing
  prescribed, `system` needing a signature, other-department refusal, the called
  walk-through, the waiting refusal, no-show left alone, audited, on the card,
  `note_signed`, plus three route tests.
- Doctor E2E: **12 passed** (was 11). Dictation E2E: **8 passed** (was 6).
- Conformance: 48 passed. `npm run build` green, `tsc --noEmit` and `eslint`
  clean.
- Screenshots (`web/screenshots/s10/`):
  - `08-capture-steps.png` — the Consult tab at 1280×800.
    *Critique:* the spine, the red flag, the tab row, the four steps and both
    capture buttons fit above the fold with the transcript box starting. Dictate
    is the only filled control in the work area; Type note reads as an equal
    rather than a lesser path, which is the point. "More" sits right-aligned and
    quiet.
  - `06-typed-note.png` — a typed note mid-composition. *Critique:* the medicine
    name stays the strongest text with the four signature fields quiet beneath
    it; the sign bar states why nothing can be printed, so the absence of a
    Print button reads as a rule rather than a missing feature.
  - `07-typed-prescription.png` — a prescription with no speech in it.
    *Critique:* the step rail reading `done · done · done · you are here` is the
    clearest thing on the panel. Found the "not traceable to anything in the
    transcript" line here and fixed it — on a typed note it read as a warning
    about a drug the doctor had typed themselves.
  - `09-overflow.png` — the escape hatch. *Critique:* one item, muted, with a
    sentence saying when to use it. Nothing about it competes with Dictate.
  - `10-conclude-dialog.png`, `11-conclude-none.png` — the conclusion.
    *Critique:* the three consequences are the most legible block in the dialog
    and they are specific, which is the whole design goal. Marigold carries the
    attention; the confirm button stays green.

## Known gaps / stubs introduced

(Mirrored into STATE.md → Stubs & fakes.)

- **The analyser meter is unverified on real hardware.** Headless Chromium has
  no microphone, so the E2E exercises the timer path only. The bars need a look
  on the Omen with a real headset before the pilot.
- **`conclude` cannot be undone.** A doctor who picks the wrong mode has no
  screen to fix it; the audit trail has the change but nothing surfaces it.
- **Migration `c063fd91e198` is applied locally only**, like `c6e3681f5ce1` and
  `520d07f0b3e4` before it.

## Commits

(see `git log` on `assign-rx-identity`)
