# SESSION-AYUR-2 — intake content and routing

**Date:** 2026-08-09 · **Scope ref:** docs/24-AYURVEDA-MODULE.md §5, §8

## Acceptance criteria checklist

- [x] The five trees of §5 authored in Hindi + Hinglish-`en` + mr/te, each with a
      `_comment` block carrying the UNREVIEWED flag and the language decision.
- [x] The TB red-flag rule in `ayurveda_respiratory.json`, with dedicated tests —
      four shapes that must fire it and three ordinary coughs that must not.
- [x] Inactive-destination option filtering.
- [x] The AYUR branch in the GENMED and PULM routing trees.
- [x] Kiosk department card for ayurveda (icon + care-system-styled card).
- [x] Offline path exercised rather than assumed.
- [x] Walker tests per tree including every red-flag route; conformance fixture
      covers the routing tree (it covers all seventeen).
- [ ] Kiosk E2E of a full ayurveda intake → token → pass. **Deliberately not
      committed**, per the operator's instruction at the top of this session:
      the ayurveda sessions do session-scope tests and E2E lands in the last one.
      Both flows were driven in a browser against the live stack and
      screenshotted (`web/screenshots/ayur2/`); the spec was deleted.

## What was built

- **`Option.department` and `RedFlagSpec.route_to`** (`app/trees/schema.py`) —
  two optional fields that let a tree name the department a patient belongs in:
  the preference she states, and the destination a deterministic rule names.
- **`Walk.destination()`** (`app/trees/walker.py`) answers both as one question,
  with the precedence that is the safety rule (see Decisions).
- **`app/trees/visibility.py`** — `for_active(tree, open_codes)` removes an offer
  of a closed department, server-side, in `store.resolve_tree` and the offline
  bundle. It removes the whole node and rewires its parents, then re-`parse`s the
  result.
- **`SessionState.open_departments`**, pinned at `/start` and read by
  `IntakeEngine._tree`, so every turn of an intake asks the same questions the
  first one did.
- **`/kiosk/{sid}/confirm` applies the destination before allocating the token**;
  `confirmLocal` does the same offline, drawing from the destination
  department's leased block.
- **Five ayurveda trees** in `seeds/trees/` (routing, digestion, joint pain,
  lifestyle/prameha, respiratory) — 37 nodes, 14 red flags, four languages.
- **The AYUR offer** in `general_medicine_routing` (v3) and `pulmonology_routing`
  (v2), and **AYUR seeded active**.
- **The kiosk's ayurveda card** — a leaf icon and its own ground, keyed off
  `data-care-system` on the DOM.
- Five ayurveda cases and one TB-suspect case added to the routing eval set.

## Decisions made

- **One destination mechanism, not two.** An option's preference and a red flag's
  `route_to` both answer "which department does this completed walk belong to",
  so they are resolved by one function with one precedence: a fired flag's
  `route_to` wins (worst severity first, then id); **any fired flag with no
  destination cancels a preference**; only then does an answered option decide.
  Doc 24 §4's "a wellness framing must never soften an emergency" is that middle
  clause, and `test_a_red_flag_cancels_a_preference` is where it lives.
- **A closed department's question is removed, not disabled.** Doc 24 §5 asked
  for a filter over the department list; doing it *server-side on the canonical
  tree* means the offline kiosk needs no filtering logic at all — it never
  receives the question, so an outage cannot surface one — and WhatsApp and
  telephony inherit it. The alternative, filtering in each channel's renderer,
  would have left the bot offering a shut department to whoever texted in.
- **The authoring contract is what makes that safe.** `_validate_offers` requires
  an offering node to be single-choice with exactly two options, branching only
  on `default`, and read by no red flag. Those constraints exist so that removing
  the node changes nothing else — no orphaned branch, no rule left reading a node
  that is gone — and the pruned tree goes back through `parse()` to prove it.
- **`open_departments` is pinned, not re-read.** Same reasoning as
  `contract_version`: a patient three questions in is not the person to discover
  an administrator's edit.
- **The destination is applied before the token, never after.** The token series
  is per department per day, so a visit re-homed after allocation would be
  holding one department's number on another's board.
- **AYUR is seeded active.** The only reason it was dark was that it had nothing
  to ask; it now has. On an already-seeded box this changes nothing — since
  AYUR-1 the loader never overwrites a department a console can edit — so the
  switch stays in an administrator's hand. The clinical review is untouched and
  is still a launch gate (doc 24 §9).
- **Hinglish is content, not a locale** (doc 24 §5, restated in each tree's
  `_comment`): the `en` slot carries the words an Alwar patient uses, `hi` is
  Devanagari. The `Lang` enum was not widened.

## Deviations from spec

- **Doc 24 §5 says "kiosk-side filter"; this is server-side.** Same outcome for
  the patient, strictly better properties (see Decisions). Doc 24 should be read
  as "one filter over the department list the backend already serves", which it
  is — it just runs before the tree leaves the server.
- **The §5 table's "routes to AYUR sub-flows"** is not a cross-tree jump: the
  engine has no such concept and adding one would be a schema change, which doc
  24 §5 makes a stop-and-reconsider moment. `ayurveda_routing.json` branches into
  its own per-concern questions, and the four sub-trees are ordinary bank content
  the console can publish — exactly the relationship `med_onc_pain.json` already
  has to `med_onc_new_patient.json`.
- **The kiosk E2E is deferred to the last ayurveda session** by operator
  instruction (see the AC checklist).

## Tests & evidence

- `make test`: **exit 0** — backend **1,899** (was 1,822), voice-gw 25,
  conformance **135** (was 115, and the fixture now covers 17 trees), typecheck,
  lint, android.
- New tests: `backend/tests/test_tree_destination.py` (33) —
  the precedence, the TB rule in both directions, the authoring contract, and
  that no red flag in the bank routes *into* an ayurveda department.
  `backend/tests/test_kiosk_destination.py` (9) — the destination applied to a
  live confirm, the offer's absence while AYUR is closed, and the bundle ETag.
  `web/e2e/offline-destination.spec.ts` (5) — the outage path, including the two
  honest failures (a department never cached, and a destination with no block).
- Existing tests changed, all of them manifests rather than behaviour: the tree
  bank's list of trees and its count, the offline bundle's tree count, the
  seed's active-department count, `pilot_departments`, and the routing eval
  set's size. `test_facility.py`'s "a department with no intake tree cannot be
  opened" moved its example off AYUR onto a new `CODE_WITHOUT_A_TREE` — the
  guard is unchanged; AYUR simply stopped being an example of a department with
  nothing to ask.
- Screenshots, `web/screenshots/ayur2/`:
  - `01-chooser-ayurveda-card.png` — the Ayurveda card reads as a different kind
    of medicine from three metres (leaf, warmer ground) without becoming a second
    brand. **Critique:** every department name on this screen is English on a
    Hindi kiosk. Pre-existing and not this session's to fix, but it is the most
    visible thing wrong with the screen — backlogged.
  - `02-ayurveda-first-question.png` — "पाचन (अग्नि) या पेट की तकलीफ़": the
    familiar word does the work and the classical term rides in brackets, which
    is the tone doc 24 §5 asked for. Caught the joint-pain option drawing a red
    alert triangle; fixed.
  - `06-genmed-ayurveda-offer.png` — the offer inside the General Medicine walk.
    Caught the "no" option falling back to a bare dot; fixed.
  - `03-question-*.png` — one of each node type in the ayurveda intake.

## Known gaps / stubs introduced

- **All ayurveda content is model-drafted and UNREVIEWED.** Doc 24 §9 makes BAMS
  sign-off a launch gate. Nothing in code enforces it; the department is open in
  a fresh seed and an operator must close it until the review happens.
- **Two urgent flags naming two departments break the tie by flag id.** Both are
  on the coordinator's strip and the patient is urgent either way, but which
  queue gets the token is arbitrary between them.
- **mr/te remain model-drafted** across the new trees, like the rest of the bank.
- **Department names are single-language** — see the screenshot critique.

## Commits

- `3a0053b` — S AYUR-2: a tree can name the department a patient belongs in
- `c81b8e5` — S AYUR-2: the offer is asked, applied, and taken away when the OPD closes
- `9933e76` — S AYUR-2: prove the outage path rather than assume it
- `f570bb1` — S AYUR-2: the icons a patient actually sees, and the screenshots
