# SESSION-ALLERGY — an allergy this system actually knows

**Date:** 2026-08-08 → 2026-08-09 · **Scope ref:** HANDOFF item 4 ("the largest
gap in the spine"), plan §4.2 element 3. Not a clinical-intelligence module —
that plan is out of modules; this is the debt it left behind.

## Acceptance criteria checklist

- [x] **AC1 — the record can hold an allergy.** `patient_allergies`, hung off the
  patient, append-only, audited. Migration `8ef31aa60c55`, additive, no backfill.
- [x] **AC2 — three states that never collapse.** `never_asked`, `none_stated`,
  `known`, derived server-side in one place (`app.allergies.for_patient`) and
  shipped whole on the patient card. Pinned by 23 backend tests and 13 pure-logic
  web tests.
- [x] **AC3 — the phrase "no known allergies" appears nowhere**, in any state, on
  any surface. Asserted over the whole console in two E2E tests, not merely over
  the line that renders it.
- [x] **AC4 — the kiosk asks, in four languages, online and during an outage.**
  `POST /kiosk/{sid}/allergies`; offline the answer rides in with the intake.
- [x] **AC5 — the doctor can record, confirm and withdraw**, from the spine,
  without losing the tab they were reading. Three routes, department-scoped.
- [x] **AC6 — a correction path.** Withdrawal is a state change with a clinician
  and a reason; the row survives struck out. The first correction path in this
  system, built first because a wrong allergy is its most dangerous stale fact.
- [x] **AC7 — screenshots taken and self-critiqued** against doc 04 §5. Three
  real defects found and fixed (below).

## What was built

- **`backend/app/models/patient.py` → `PatientAllergy`** — one row is one
  *statement*: somebody was asked at a knowable moment and answered. Enums
  `AllergyKind` (`substance` | `none_known`), `AllergySeverity`, `AllergySource`
  (`patient_kiosk` | `caregiver_kiosk` | `doctor`).
- **`backend/app/allergies.py`** — the derivation, the writes, and the refusals.
  `for_patient`, `from_intake` (idempotent per visit), `record_by_doctor`,
  `confirm`, `retract`.
- **`POST /kiosk/{sid}/allergies`** and an `allergies` field on the sync payload
  (`app/offline.py`, `app/routes/kiosk.py`).
- **Three doctor writes** under `/doctor/visits/{id}/allergies` (+ `/confirm`,
  `/retract`), and `allergies: AllergyView` on the patient card.
- **The kiosk's last question** — `AllergyScreen` in `KioskApp.tsx`, on `Stage`,
  all four languages, with the same `VoiceCapture` mic the chief complaint uses.
- **The console's third slot** — `ContextSpine` renders a derivation instead of
  an apology; `AllergyPanel.tsx` is where the doctor acts; the History tab shows
  every statement in full with its provenance.
- **`web/app/(doctor)/doctor/_lib/allergies.ts`** — the pure line-composing
  rules, tested without a browser in `e2e/allergy-line.spec.ts`.

## Decisions made

1. **`none_known` is a row, not an absence of rows.** "Asked and told there are
   none" and "never asked" are different clinical situations, so they are kept
   apart at the storage layer rather than in the console's wording. The migration
   deliberately backfills nothing: every existing patient genuinely has never
   been asked, and inventing a "none" for them would be this migration's worst
   possible act.
2. **`known` outranks a later `none`.** A rushed second intake tapping "no" must
   not silence a penicillin anaphylaxis. Un-saying an allergy takes `retract` —
   a clinician and a reason.
3. **Everything retracted reads as `never_asked`, not as `none_stated`.** A
   withdrawal is not reassurance; the next reader should be asking again.
4. **The kiosk question is not a tree node.** An allergy is not a department's
   clinical question — it must be asked of the ENT walk-in and the palliative
   review alike, on the tap-only tier, in every language, during an outage. As a
   node it would need authoring into all eleven trees, where eleven copies drift,
   ten get reviewed by nobody, and the twelfth tree ships without it.
5. **Three answers at the kiosk, not two.** "I don't know" is the same size and
   treatment as yes and no, and records **nothing**. A patient forced to choose
   between yes and no about her own drug history will guess, and a guessed "no"
   reaches a prescribing doctor looking exactly like a fact.
6. **What she says is stored unsplit.** "Penicillin and sulfa" reaches the doctor
   as "penicillin and sulfa". Splitting a sentence into two clinical facts means
   inventing the boundary between them.
7. **The copy never says "allergy" on its own.** Many patients here would say a
   medicine "did not suit" them, or that they came out in a rash — so it is asked
   the way it gets answered, and the examples do the defining.
8. **No drug matching, anywhere.** Nothing in `app.allergies` reads the formulary
   and nothing in the prescription path calls it — pinned by a source test, the
   `app/notes.py` pattern. An interaction check against free text a patient typed
   at a kiosk would be a safety feature made of guesses, and the failure mode of
   a *missed* match is a doctor who trusted a green tick. The spine puts the
   words in front of the doctor; the doctor decides. **This should stay true
   until there is a coded substance vocabulary and somebody clinical owns it.**
9. **Severity stays `unknown` on everything the kiosk writes.** The patient named
   a substance; nobody asked what it did to her, and a default of `mild` would be
   the system inventing the reassuring half of a fact it does not have.
10. **`never_asked` is quiet, not amber.** See the self-critique — it is the state
    every patient starts in, and it must never outshout a severe allergy.

## Deviations from spec

- Plan §4.2 asks for allergies as spine element 3, which is what this is. The
  plan says nothing about *capture*, so the kiosk screen, the three states and
  the correction path are this session's own design, argued above and in the
  module docstrings.
- The spine's third slot is now a **button** rather than a line of text. It is
  still one line that never wraps; nothing else about §4.2 changes.

## Tests & evidence

- `make test`: **1,741 backend** (1,701 → +40), voice-gw 25, typecheck, lint,
  **conformance 92** (79 → +13, the pure-logic allergy-line suite).
- New backend tests: `tests/test_allergies.py` **23**, plus 8 in `test_doctor.py`,
  6 in `test_kiosk.py`, 3 in `test_offline.py`, 1 in `test_audit.py`.
- E2E against a live stack (api :8123 `LLM_PROVIDER=fake`, web dev :3210,
  `scripts.seed_doctor_demo`): **`--project=allergy` 6 passed** (new),
  `kiosk` 3, `doctor` 12, `notes` 5, `assign` 3, `pass-ui` 5, `ux-smoke` 2,
  `accessibility` 3.
- **`offline-demo` is still red, for the reason it was red before this session** —
  the downtime banner never appears (line 158), well before the token screen and
  well before the new question. Its walk was patched for the new screen, so that
  patch is *unverified*: nothing gets far enough to exercise it.

### Screenshots, self-critiqued (doc 04 §5)

`web/screenshots/allergy/01…07`, `web/screenshots/s6/06a`–`06b`.

1. **`01-never-asked`** — the line reads as an instruction and the console says
   "no known allergies" nowhere. *First cut was wrong:* it was filled amber, so
   every patient in the pilot would carry an amber band all day until the kiosks
   had asked them, which is how amber stops being read by Thursday.
2. **`04-spine-severe`** — *the defect the screenshots existed to catch:* the
   amber "not established" rendered **louder** than a severe penicillin allergy
   in pale pink, so the state where we know nothing outshouted the state where we
   know something dangerous. Danger now carries a solid edge and weight; the
   red-flag lane keeps its solid fill.
3. **`03-panel-known`** — the panel reads well: substance, severity pill, the
   reaction, and who stood behind it. `Confirm` is one tap on the patient's own
   row and never asks the doctor to re-type a substance.
4. **`06-withdrawn`** — *fixed:* striking out the only statement left a lone
   "Withdrawn" heading over a blank, which reads as an absence rather than the
   open question it is. It now says so and tells the doctor to ask again.
5. **`s6/06a-allergy-ask`** — three cards, same size, "I don't know" among them.
   The 2+1 grid puts it third in reading order, which is right for that answer.
6. **`s6/06b-allergy-which`** — *fixed:* the first cut was a row of text inputs,
   which asks a patient who may not read to type a drug name in Devanagari on a
   tablet. It is the same `VoiceCapture` mic as the chief complaint now.

## Known gaps / stubs introduced

- **Nothing checks a stated allergy against a prescribed drug.** Deliberate
  (decision 8), and the most likely thing for a future session to "fix" wrongly.
- **`substance` is free text with no vocabulary.** Two spellings of penicillin
  are two statements; nothing merges them, by decision.
- **"I don't know" records nothing**, so a patient who was asked and did not know
  is indistinguishable from one nobody asked. Defensible — the instruction to the
  doctor is the same — but it is a real loss of information.
- **The pass does not carry allergies** (doc 23 geometry is fixed at 80×200mm).
- **The kiosk's mr/te strings are model-drafted**, like the rest of the shell,
  pending native review.
- **A server session that loses the network at this screen loses the statement.**
  `flow.allergies` reports it and the screen tells her to tell the doctor
  herself, rather than swallowing it. Same limitation `answer` has documented
  since S7: the offline-first guarantee is for intakes that *started* offline.

## Commits

- `9473d4f` — S ALLERGY: a log of statements, not a list of allergies
- `02a97d1` — S ALLERGY: three writes a doctor can reach, and a state the card ships
- `e4af103` — S ALLERGY: the kiosk asks, online and during an outage
- `891f97b` — S ALLERGY: the kiosk's last question, and the third answer that makes it usable
- `70ee652` — S ALLERGY: the spine's third slot stops apologising
- `353ead0` — S ALLERGY: drive it in a browser, and fix the three things the screenshots showed
- `5e00ea9` — S ALLERGY: a mic on the one screen that asks a patient to name a drug
