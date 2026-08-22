# SESSION-AYUR-4 — framing, the sweep, and the documentation

**Date:** 2026-08-22 · **Scope ref:** docs/24-AYURVEDA-MODULE.md §8 → SESSION-AYUR-4

## Acceptance criteria checklist
- [x] Research prompt pack + guideline-pack label. **Landed in AYUR-3** — see
      Deviations; the flag has one meaning and wiring two of its three dispatch
      sites would have shipped an ayurveda console framed for an oncologist.
- [x] Cross-cutting sweep for `care_system ===` / `== CareSystem.` outside the
      two mapping modules.
- [x] `STATE.md`, `CODEBASE_MEMORY.md` §4 invariants, `seeds/README.md`.
- [x] Session log; HANDOFF for the deploy step.
- [x] A written checklist of what remains **content review** vs code.
- [ ] Canned fake replies for the research assistant — **not done, on purpose.**
      See Known gaps.

## What was built

Documentation and one verification pass; no behaviour changed.

- **The sweep.** `grep` for member references and string comparisons across
  `backend/app` and `web/app`. Three hits, all correct and all deliberate:
  `models/org.py` (the column default — the definition itself),
  `FacilityTab.tsx` (the admin's *system of medicine* selector, where the raw
  value is genuinely the data, which doc 24 §7 and `careSystem.ts` both
  provide for). **No leaks.** That is not luck: `test_only_the_mapping_names_a_
  care_system_member` and `e2e/care-system.spec.ts` have failed the build on
  every attempt since AYUR-0, including one of mine this session.
- **`CODEBASE_MEMORY.md`** gained seven invariants: one derivation consumed as
  flags; capabilities derived from the record and never the request; red flags
  stay deterministic and allopathic; symmetric formulary discipline including
  no cross-shelf suggestions; dosha language is record, never triage; and the
  UNREVIEWED stance on all ayurveda content.
- **`seeds/README.md`** now documents the two shelves, the default-to-allopathy
  reading, the raise-on-unknown-scope rule, and the BAMS gate.
- **`STATE.md`** — a new Built block, and the "three flags nothing reads" stub
  retired, since all eight now have consumers.

## Decisions made

- **The sweep is a test, not a ritual.** The two guard tests already make this
  property structural; this session's grep confirmed them rather than replacing
  them. A future session should trust the tests and not repeat the grep.
- **`docs/24` is left as written.** Its §8 session split no longer matches what
  landed where (the research pack moved into AYUR-3), and the honest record of
  that is here and in `SESSION-AYUR-3.md` rather than a retroactive edit to a
  document that says at its head it was written before execution began.

## Deviations from spec

- The research prompt pack and the guideline-pack label landed in AYUR-3, not
  here. Recorded in both session logs.

## Tests & evidence

- `make test`: green. Backend **1,947**, voice-gw 25, conformance 135,
  typecheck, lint, android. No new tests — this session added no behaviour.
- Sweep output: three hits, all allowlisted and explained above.

## Known gaps / stubs introduced

- **No canned fake reply for `research_assist` in either pack.** Doc 24 §8 asks
  for one here. Adding it for ayurveda alone would give the new system a better
  `LLM_PROVIDER=fake` demo than the oncology console has had since M5, and
  adding it for both changes a surface this session was not scoped to touch.
  Left symmetric and registered in `STATE.md → Stubs & fakes`.

## What remains content review, not code

Nothing below is a code task, and none of it is unblocked by a green suite.

1. **BAMS sign-off on the 89 ayurveda formulary entries** — that each classical
   preparation and proprietary brand is real, correctly spelled, and one this
   OPD would stock. `seeds/formulary.json`, `_comment_ayurveda`.
2. **BAMS sign-off on the three ayurveda prompt packs** — the register, the
   refusals, and specifically that the dictation prompt's transliteration rule
   ("Ashwagandha and Ashwagandhadi Churna are not the same thing") names the
   confusions that actually occur. `backend/prompts/*_ayurveda/v1.md`.
3. **BAMS sign-off on the five ayurveda trees** (carried from AYUR-2), including
   the TB red-flag rule in `ayurveda_respiratory.json`.
4. **Native review of the mr/te text** in every ayurveda tree (carried from
   AYUR-2) and of the Hindi hospital name (carried from AYUR-1).
5. **A vaidya reading the assessment field set** — whether prakriti / vikriti /
   agni / koshtha / nidana is the right five, and whether free text is right
   where a dropdown might be expected. The fields are free text on purpose;
   that decision wants a practitioner's agreement, not a developer's.
6. **An oncologist reading the research assistant's answers** — carried from
   seven consecutive handoffs, and now doubled: the ayurveda pack needs the
   same read by a BAMS practitioner.

## Commits
(documentation only — see the session-close commit)
