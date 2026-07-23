# SESSION-10 — Dictation → structured mapping

**Date:** 2026-07-23 · **Scope ref:** docs/06-BUILD-PLAN.md → S10 · **Branch:** `feat/doctor-console`

## Acceptance criteria checklist

- [x] **Dictation capture** — Web Speech in the console with a MediaRecorder running
      alongside it; the recording only goes to the server when Web Speech produced
      nothing. `POST /dictation/stt` is the server pass (local Whisper on a V-OSS box).
      A textarea is always present, so a browser with neither still works.
- [x] **Mapping prompt → structured JSONB** — `dictation_map@v1` (authored in S3, first
      used here) on the LLM chain, into `Dictation.structured`.
- [x] **Formulary fuzzy-match validation, ~300 drugs** — `seeds/formulary.json`: 189
      generics, **617 dictatable names** (a doctor dictates the brand, so the brands
      count). Fuzzy matching produces suggestions only.
- [x] **Unknown drugs flagged, never auto-corrected** — `known` is set by exact match
      alone; there is no code path from a fuzzy score to a written name.
- [x] **10 sample Hinglish dictations as fixtures, zero silent drug substitutions** —
      `backend/tests/fixtures/dictations.json`, asserted three ways across all ten.
- [x] **Review/diff UI** — the console's consult note: `mapped` frozen, `fields`
      editable, provenance shown per value.
- [x] **Sign flow, signing locks the record** — `POST /dictation/{id}/sign`; every
      mutating entry point 409s afterwards.

## What was built

- **`seeds/formulary.json`** — the Indian oncology OPD shelf: cytotoxics, targeted
  agents, immunotherapy, hormonals, and the whole supportive-care half of a real
  prescription (antiemetics, G-CSF, opioids, PPIs, antibiotics, mucositis care).
- **`backend/app/formulary.py`** — normalisation (form words + strength tokens
  stripped, brand suffixes kept), exact-match `known`, advisory `suggestions`,
  and `ambiguous` for names within reach of two different generics.
- **`backend/app/dictation.py`** — the contract (`DictationMapping`, `MedLine`,
  `TreatmentEvent`, `FollowUp`), `validate_meds`, `_was_said`, `DictationMapper`
  (LLM-chain adapter), and the record's state machine: `start` → `map_transcript`
  → `apply_corrections` → `sign`.
- **`backend/app/routes/dictation.py`** — six routes, all `require_doctor`,
  scoped to the doctor's own department like the S9 card.
- **`web/app/(doctor)/doctor/_components/DictationPanel.tsx`** + `_lib/dictation.ts`
  + the `DICTATION_CSS` block — the consult note on the console stage.
- **`web/e2e/dictation.spec.ts`** (project `dictation`) + `screenshots/s10/`.
- **`backend/tests/test_formulary.py`** (42) and **`test_dictation.py`** (63).

**No migration.** `Dictation` has existed since S2 and `structured` is JSONB.

## Decisions made

1. **`known` is exact-match only; fuzzy matching never writes.** The two jobs are
   split so that "zero silent drug substitutions" is a property of the code, not a
   thing we tested once. A helpful UI that resolves a near-miss is the exact failure
   this session exists to prevent — it produces a field that *looks right*, so it
   does not get read.
2. **Names are also checked against what the doctor said (`_was_said`).** Writing the
   fixtures exposed a hole: a formulary check cannot see a rename. When the doctor
   says "Vinblastin" and the model writes "vinblastine", the result is a real
   formulary drug and every check passes. The only evidence left is the doctor's own
   words, so every drug name is looked for in `as_spoken` (falling back to the
   transcript). On the ten fixtures it fires exactly twice — the helpful correction
   and the hallucination — and on none of the eight where the drug was plainly said.
   **A heuristic, deliberately:** a false positive costs one tap; a false negative
   costs a patient.
3. **Ambiguity is treated as worse than unknown.** A name within reach of two
   *different* generics is never resolved for the doctor. The real look-alikes in
   this book are `Cytolatin` (cisplatin/vinblastine) and `Lukeran`
   (chlorambucil/melphalan) — an Indian shelf, one letter apart.
4. **Signing requires acknowledging every flagged drug, and off-formulary stays
   signable.** The formulary is incomplete by nature, so the doctor must be able to
   prescribe past it — but as an act, not by not noticing. A flag that clears itself
   by being ignored trains people to ignore flags.
5. **`mapped` is frozen; corrections land in `fields` with an append-only trail.**
   That is what makes the review genuinely diff-style rather than "here is some
   text, trust it". Re-mapping resets both and clears the trail, because the trail
   describes edits to *a* mapping.
6. **No deterministic fallback for mapping.** The intake summarizer degrades to a
   template because an intake must complete offline (V3). A Hinglish paragraph has
   no honest deterministic floor — a template that guessed would be inventing a
   prescription. A dead model fails loudly and the transcript survives on the draft.
7. **The mapper is a provider-chain adapter, nothing else** (operator request this
   session). `LLM_PROVIDER=local_vllm` moves the whole thing onto the box's Qwen3
   with no code change, exactly like `LLMSummarizer`. Dictation is the most private
   text in the system, so this seam matters more here than anywhere else.
8. **Signing emits no prescription and no check-in plan.** doc 03 §7 says it should;
   those are S11 and S17. A half-shaped `Prescription` row now is a migration for a
   later session.
9. **The fake LLM answers `dictation_map` with a contract-shaped payload.** A fake
   that says "ok" to a `response_format: json` prompt can only demonstrate the
   failure path, and the fakes exist so whole flows can be demonstrated without a
   vendor. The canned reply carries one off-formulary drug on purpose.

## Deviations from spec

- doc 06 says "~300 drugs"; the file carries 189 generics / 617 names, because
  matching has to work on the brand a doctor actually says.
- doc 03 §7's "signing generates prescription (§8) and check-in plan draft (§9)" is
  deferred to S11/S17 — see decision 8.
- `_was_said` is not in doc 03 §7. It is an addition, not a deviation; doc 03 should
  gain a line about it when §7 is next revised.

## Tests & evidence

- **`make test`:** green — backend **708** (was 603), voice-gw 1, web typecheck +
  lint clean, 48 conformance.
- **New tests:** `test_formulary.py` (42), `test_dictation.py` (63, of which 30 are
  the ten fixtures × three invariants), `web/e2e/dictation.spec.ts` (6, live stack).
- **Screenshots** (`web/screenshots/s10/`), self-critiqued per doc 04 §5:
  - `01-capture.png` — mic + transcript. The note takes the stage rather than
    floating over the card; correct, but the empty state leans on the placeholder
    text more than it should.
  - `02-review.png` — mapped fields with provenance lines. Reads in one pass.
  - `03-flagged.png` — the flagged drug is unmissable, keeps its dictated name, and
    the disabled signature names it. **This one caught a real fault:** the
    impression's provenance line reprinted the entire transcript from a few
    centimetres above. A provenance line that is always the same text stops being
    read, and then it stops being read on the rows where it matters. Removed.
  - `04-acknowledged.png` — the row calms from danger-red to marigold. Reads as
    "seen and standing", which is exactly right; it is not cleared.
  - `05-signed.png` — locked, capture gone, names no longer tappable.

## Known gaps / stubs introduced

- `_was_said` is token-presence, not alignment: a drug the doctor said in a
  *different* sentence than the one the model quoted still passes. Tighter matching
  needs the transcript's timing, which we do not store.
- The canned `dictation_map` reply in `FakeLLMProvider` is a demo fixture. Anything
  asserting on mapped *content* must queue its own script.
- `/dictation/stt` meters against no channel (dictation is not a patient channel);
  cost is attributed to the visit instead.
- The `dictation` e2e's sign test cannot repeat on the same row without re-seeding —
  signing is terminal by design. It now fails with a message saying so.

## Commits

- `1742dac` — S 10: the formulary — 189 generics, 617 dictatable names, and no path from a fuzzy score to a written name
- `565955e` — S 10: dictation mapping — the model maps, the formulary judges, the doctor signs
- `53c5172` — S 10: the consult note in the console — capture, review against your own words, sign
- `385bd89` — S 10: the S10 AC in a browser — the flag is seen, the signature is refused, the name survives
