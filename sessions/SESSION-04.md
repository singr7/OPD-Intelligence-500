# SESSION-04 — Question-tree engine + oncology tree bank v1

**Date:** 2026-07-16 · **Scope ref:** docs/06-BUILD-PLAN.md → S 04

## Acceptance criteria checklist

- [x] **Tree validator rejects malformed trees.** `app/trees/schema.py`; 40+ rejection
      tests in `tests/test_trees.py`. Beyond the obvious (bad types, missing fields), it
      rejects the ones that are invisible in review: unreachable nodes, cycles, a
      declared language that is incomplete, >5 options on a tap node (doc 03 §1a), a
      rule whose op cannot apply to the node it reads, a red flag with severity
      `routine`, and branching on a list-valued answer.
- [x] **Walker unit tests cover branching + red flags.** `app/trees/walker.py`;
      `tests/test_trees.py` + `tests/test_tree_bank.py` cover branch selection,
      amendment/pruning, completion, the answers-JSONB round trip, cross-node flags,
      severity ordering, and recomputation.
- [x] **Author the actual trees** — 11 trees, en+hi, 89 nodes, 40 red flags. Med-onc
      new patient / between-cycle (CTCAE-lite) / pain, radiation on-treatment review,
      surgical post-op, palliative ESAS-lite, and 5 thin routing trees. Every
      department in `hospital.json` has a tree.
- [x] **Dept-classification prompt with an eval set of 60 labeled utterances** —
      `app/routing.py` around the existing `routing@v1`; `backend/evals/routing_eval.json`.
- [ ] **Classifier ≥85% on eval set — NOT MET, and not meetable from here.** The set,
      the harness (`app/evals.py`), and the 85% gate exist and run (`make eval-routing`),
      but the only provider available is the fake. No vendor key has ever been used in
      this repo (STATE.md: "No live vendor has ever accepted a call"), and scoring the
      fake measures the harness, not a model. One live run with a `GEMINI_API_KEY`
      settles it — see HANDOFF. **The test suite deliberately does not fake this
      number**: scripting the fake with the right answers and asserting 100% would be a
      green tick that reads like a met AC while measuring nothing.

## What was built

- `app/trees/rules.py` — the deterministic red-flag expression language (and/or/not over
  leaf ops against node answers), plus a validator that type-checks each leaf against the
  node it reads. Evaluation is total (never raises on a live intake); validation is loud.
- `app/trees/schema.py` — doc 03 §3's node schema, parsed into typed objects. `parse()` is
  the only constructor, so a `Tree` in memory is one that passed every check.
- `app/trees/walker.py` — `Walk`: one patient's position in one tree, **derived from the
  answers rather than stored**. `to_json()` is the `answers` JSONB shape doc 03 §1 requires
  of every tier/channel; matches `Intake.answers`.
- `app/trees/bank.py` — loads/validates `seeds/trees/*.json`; `for_department()` is how a
  classifier result becomes something to ask.
- `seeds/trees/*.json` — the 11 authored trees.
- `app/seed.py` — `_upsert_trees()`, idempotent, seeds as **draft**, `--publish-trees` opt-in.
- Migration `fbcaee31fa43` — drops `question_trees.lang`, keys trees `(key, version)`.
- `app/routing.py` — `classify_department()`, `DepartmentGuess`, `pilot_departments()`.
- `app/evals.py` + `evals/routing_eval.json` + `make eval-routing` — the eval harness.

## Decisions made

1. **Language lives inside the node; `question_trees.lang` is gone.** Doc 02 §4 sketched
   one row per language, keyed (key, lang, version); doc 03 §3 then specified the node as
   `text:{en,hi,mr,te}`. Both cannot hold. Doc 03 §3 wins because doc 03 §1 makes language
   **switchable mid-intake** and `Intake.lang` is per-intake: with text in the node that is
   a re-render of the same node id, while per-language rows make it a mid-session swap onto
   a different row that is only safe if the rows happen to share node ids and branching.
   It also single-sources branching and red flags, so an S21 sign-off covers the tree
   rather than one of four drifting copies, and it makes S13 (mr/te) additive.
   **Wants ratification** — doc 02 §4's `question_trees` line should lose `lang`.
2. **A red flag is never decided by a model, on any tier.** Rules are data evaluated in
   `rules.py`. If a flag depended on the model, "is this fever dangerous?" would have
   three answers depending on which vendor was up (V1/V2/V3), and none reviewable.
3. **`free_voice` answers are unmatchable by rules, enforced by the validator.** Matching
   ASR text would make a flag depend on how Sarvam transcribed an accent, and would fire
   "no blood in my stool" as bleeding.
4. **Position is derived, not stored.** No cursor. This is what makes a mid-session tier
   downgrade lossless (hand the same answers to a new `Walk`) and makes an amendment
   correct: rerouting drops the answers stranded on the abandoned branch, so a corrected
   answer cannot reach the doctor's summary. A stored cursor would be a second source of
   truth that disagrees exactly when a provider is failing over.
5. **Flags are recomputed, never accumulated** — an amendment that removes the alarming
   answer removes the flag. A flag outliving its evidence jumps the queue for something
   the patient corrected thirty seconds ago.
6. **Trees seed as draft.** Doc 03 §3: the bank is "clinically reviewed before go-live";
   publishing is a clinical act (S21) and `TreeStatus` exists to model it. Costs downstream
   nothing — `app.trees.bank` reads files, so S5's engine never consults `status`.
7. **The classifier's output is all distrusted** — invented department, absent/non-numeric
   confidence, unreadable JSON, and outages each route to triage with `needs_human`.
   Degrade, never deny (doc 02 §5): a patient at a kiosk cannot be told the AI is down.
8. **The 8 CTCAE symptoms are separate nodes, not one multi-select.** Forced by doc 03
   §1a's 5-option limit, and it is the better tree — CTCAE grades each symptom by function.

## Deviations from spec

- **doc 02 §4 `question_trees(… lang …)`** — contradicted and dropped; see decision 1.
  Doc not edited pending ratification (S3 set the precedent with `PriceUnit.CHAR`).
- **doc 02 §4 line 73** says the tree JSONB schema "is defined in doc 03 §4". It is doc 03
  §3; §4 is summarization. Stale cross-reference, worth a one-word fix.
- **Doc 03 §3 lists a Surgical Oncology "new lump/lesion intake"**, but doc 06's S4 line
  lists only "surgical post-op". Followed doc 06 and left it to the backlog rather than
  quietly widening scope.
- **HANDOFF (S3) said to wrap classifier calls in `usage_scope(purpose=UsagePurpose.ROUTING)`.**
  `usage_scope()` takes no `purpose` — it is a kwarg on the provider call
  (`provider.complete(request, purpose=...)`), which is what `app/routing.py` does. The
  attribution the note wanted is in place; only the mechanism differed.

## Tests & evidence

- `make test`: **green — 466 backend** (was 231), 1 voice-gw, web typecheck+lint clean.
- New tests: `tests/test_trees.py` (93 — engine), `tests/test_tree_bank.py` (93 — the
  authored content), `tests/test_routing.py` (43 — classifier + harness), plus 6 in
  `tests/test_seed.py`. Two S2 `test_crud.py` tests updated for the schema change.
- Content-level checks worth naming: every tree walks to an end from both extremes of
  every branch (no dead ends); every `and`-rooted red flag references nodes that can
  co-occur (an unsatisfiable flag reads as reviewed and never fires); all five of doc 03
  §1's starter flags fire on a constructed scenario; the febrile-neutropenia boundary is
  pinned at exactly 38.0°C and 14 days.
- Migration verified by hand: `upgrade → downgrade → upgrade` clean; `test_schema.py`
  confirms it still matches the models.
- `make seed` twice: 11 trees created, then 0 created / 0 updated / 11 unchanged.
- No UI in this session, so no screenshots.

## Known gaps / stubs introduced

(Mirrored into STATE.md → Stubs & fakes)

- **The classifier's ≥85% is unmeasured** — needs one live run with a key.
- **The Hindi is model-authored and unreviewed** by a native speaker or a clinician. The
  tests check the text is present and structurally sound; they cannot check it is good
  Hindi or good medicine. Same for the eval set's utterances, which were written from doc
  01/03's description of the catchment rather than collected from real patients.
- **The trees are unreviewed clinical content**, seeded as draft, pending S21.
- **No `audio` clips on any node** — the field is authored-empty; V3's voice packs are S7,
  real recordings S21. TTS covers the gap.
- **Red-flag satisfiability is only checked for `and`-rooted rules, and only in tests.**
  `or` across branches is legitimate and `unanswered` is satisfied by a node being
  off-path, so a general check needs real satisfiability, not reachability. S18's editor
  will need it when non-engineers author these.
- **Red flags are per-tree, deliberately** (a tree is the unit of publish and sign-off), so
  the shared ones — "tell a nurse now" instructions, the fever rule — are duplicated across
  med-onc trees and can drift.
- Enum columns still have no CHECK constraint (carried from S3).

## Commits

- `1f995ee` — S 04: add the question-tree engine — schema, validator, rules, walker
- `7a3c4ae` — S 04: key question_trees by (key, version), drop lang
- `096800e` — S 04: author the pilot tree bank and seed it
- `3225cc3` — S 04: add the department classifier and its 60-utterance eval set
