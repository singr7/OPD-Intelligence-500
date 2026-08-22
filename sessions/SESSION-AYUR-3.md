# SESSION-AYUR-3 — the doctor console, capability-gated

**Date:** 2026-08-22 · **Scope ref:** docs/24-AYURVEDA-MODULE.md §8 → SESSION-AYUR-3

## Acceptance criteria checklist
- [x] Capabilities wired into the console bootstrap; cycles / regimen lines /
      check-in / NCCN framing hidden under ayurveda.
- [x] **The oncology console renders unchanged** — `dictation` (8) and `doctor`
      (12) E2E untouched and green.
- [x] Ayurveda assessment panel + pathya–apathya in the Rx composer and the
      shared renderer; both on the signed record and the print.
- [x] Ayurveda formulary entries + `validate_meds` scoping.
- [x] Dictation-mapping and intake-summary prompt packs.
- [x] Canned fake-LLM ayurveda replies.
- [x] Doctor E2E for an ayurveda consult end to end on `LLM_PROVIDER=fake`.
- [x] A test that an ayurvedic preparation validates in AYUR and is flagged in
      MEDONC — and the reverse, which matters as much.

## What was built

- **`app/formulary.py` — one book, two shelves.** `Drug.scope`,
  `get_formulary(scope=...)` cached per shelf, `FormularyError` on an unknown
  scope. A `Formulary` is *one system of medicine's* formulary, not the file.
- **`app/prompts/loader.py` — packs.** `load_packed(id, pack)` resolves
  `summarize` + `ayurveda` → the `summarize_ayurveda` directory when it exists,
  the base prompt when it does not. Three new prompt files (dictation mapping,
  intake summary, research assistant), all flagged UNREVIEWED in their own
  front matter.
- **`app/facility.py`** — `capabilities_for_visit` and
  `care_system_of_department`: the two derivations every clinical path uses.
- **Dispatch wiring** — `DictationMapper`, `LLMSummarizer` and the research
  `Assistant` all take capabilities; the routes derive them from the record.
- **`seeds/formulary.json`** — 89 ayurveda entries (classical preparations and
  the proprietary brands an Indian OPD dictates), tagged `scope: ayurveda`.
- **The console** — `Console` derives capabilities once and passes flags down.
  `PatientCard` (cycle trend), `WorkTabs` (guideline label), `DictationPanel`
  (regimen lines, and the two new sections).
- **The note record** — `AyurvedaAssessment` (prakriti / vikriti / agni /
  koshtha / nidana) and `pathya_apathya`, as structured fields on the existing
  record: same `fields`/`edits` trail, same signature, no new table.
- **`scripts/seed_ayurveda_demo.py`** and `e2e/ayurveda-console.spec.ts`.

## Decisions made

- **A `Formulary` is one shelf, and the scope is a *loading* concern.** The
  first cut indexed per scope inside one object and two existing tests failed on
  it — correctly, because `book.drugs` and `prompt_hint()` mean "the book". The
  rewrite left every existing test passing with no body edited, which is doc 24
  §2's "both systems remain stable" doing its job rather than being asserted.
- **A pack forks only the three prompts whose *wording* is care-system
  specific.** `routing` and `mrd_extract` are about the task; four copies of
  them would drift, and that is how a fix reaches one system and not the other.
  The resolved id lands in `Prompt.ref`, so the audit trail still answers which
  text produced an output, and a test asserts the three exist for every pack so
  the fallback can never quietly become the reason an ayurveda consult is
  summarised in oncology language.
- **Capabilities are derived from the record, never taken from the request.** A
  client that could choose its formulary scope could have an ayurveda consult
  validated against 189 cytotoxics.
- **`DEFAULT_CAPABILITIES` / `DEFAULT_CARE_SYSTEM` live in the mapping.** Five
  services needed to name "today's behaviour" as a default argument, and
  spelling that `CareSystem.ALLOPATHY` at each site is the first step of the
  erosion doc 24 §2 forbids — whether or not the line is a branch on the day it
  is written. `test_only_the_mapping_names_a_care_system_member` caught it.
- **`assessment` is merged by key, not replaced.** A documented exception to
  whole-field replacement, and the opposite hazard: the rule exists for `meds`,
  where an index means something different after a reorder. Five independent
  strings travelling as one object have the reverse problem — a client holding a
  stale copy erases a sibling nobody touched.
- **Nothing in the assessment is ever machine-written.** No model produces these
  fields, the prompts refuse to infer a prakriti, and the only path in is the
  doctor typing. That is why they render with no "as spoken" line.
- **The BAMS physician is demo data, not seed data.** The AYUR department is
  real; a named practitioner the hospital has not hired would appear on the
  admin console's people list as a person who does not exist.

## Deviations from spec

- Doc 24 §8 lists the research prompt pack under SESSION-AYUR-4. It landed here
  instead, because the `prompt_pack` flag has exactly one meaning and wiring two
  of its three dispatch sites while leaving the third on the oncology text would
  have shipped an ayurveda console whose research tab was framed for an
  oncologist. AYUR-4 keeps the sweep and the documentation.
- **One existing test body was edited**, which doc 24 §8 otherwise forbids:
  `test_the_prompt_version_is_pinned_not_latest` asserts on the literal source
  line this session had to change (`load` → `load_packed`). The property it
  guards — the version is pinned, not latest — is unchanged and still asserted.

## Tests & evidence

- `make test`: green. Backend **1,947** (baseline 1,908), voice-gw 25,
  conformance 135, typecheck, lint, android.
- New tests: `backend/tests/test_care_system_consumers.py` (39) —
  both-shelf verdicts, no cross-shelf suggestions, pack resolution and
  contracts, the patch/response-model shape guards, the in-flight merge, and
  the check-in gate.
- E2E: `ayurveda` 5 passed; `dictation` 8 and `doctor` 12 passed **untouched**.
- Screenshots `web/screenshots/ayur3/`:
  - `01-worklist.png` — the ayurveda morning. Reads as the same console,
    because it is; the department name in the appbar is the only tell.
  - `02-formulary-scope.png` — a churna known, one preparation flagged. The
    flag looks exactly like an oncology flag, which is the point.
  - `03-assessment.png` — the five fields. The first cut glossed every label
    ("Prakriti (constitution)") and the screenshot showed the cost: this panel
    lays labels in a fixed gutter, so a three-line label widened the column for
    *every* field on the note. Fixed by moving the gloss to the placeholder.
  - `04-prescription.png` — the signed note. Three blank assessment lines are
    absent rather than shown as dashes; five labelled dashes read as five
    findings of normal.

## Known gaps / stubs introduced

- The 89 ayurveda formulary entries and the three ayurveda prompts are
  **model-drafted and UNREVIEWED**. Both say so in the files themselves. A BAMS
  practitioner must sign them off before this is enabled for real patients —
  the oncology tree bank's gate (doc 24 §9).
- `research_assist` has no canned fake reply in **either** pack, so the Research
  tab answers "ok" on `LLM_PROVIDER=fake` for both systems. Deliberately left
  symmetric rather than giving ayurveda a better demo than oncology.
- The ayurveda prompts' `treatment_events: []` contract is enforced by the
  prompt text, not by the parser. A model that emitted one anyway would have it
  stored and then not rendered (`shows_regimen_events` is false).

## Commits
6ddced0 — the book gets a second shelf, and prompts get a register
9e65c9b — the department decides the shelf and the register, not the caller
edf9d08 — 89 preparations on the second shelf, and a demo that flags one
5c02a70 — the console reads the flags, and gains the two ayurveda surfaces
04f0afc — an ayurveda consult, end to end — and three bugs the screenshot found
8adf172 — the last flag finds its consumer, and it is a safety gate
