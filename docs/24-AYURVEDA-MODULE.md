# 24 — AYURVEDA MODULE: Design & Execution Plan

Status: **DESIGN — approved for execution.** This document is the instruction
set for the executing model (Opus). It was produced in a planning-only session
on 2026-08-09 after reading the codebase; every file path named here was
verified to exist at planning time. Re-verify paths at the start of each
session (the repo moves between sessions).

---

## 1. What is being built, in one paragraph

The platform gains a second **system of medicine**. An admin can name the
facility anything (including "Ayurveda Hospital") and can mark any department
as `ayurveda` or `allopathy`. An ayurveda department gets its own kiosk intake
trees (Hindi + Hinglish, ayurveda-flavoured but patient-friendly), routing
rules that send the right walk-ins to it, and a doctor console **derived from
the existing one** with oncology-specific pieces removed and ayurveda-relevant
pieces added. Everything else — research assistant, imaging viewer,
prescription flow, floating dictation, MRD reports, allergies, queue, pass —
is shared, unchanged, and works identically for both systems. Both systems
coexist in one deployment, are individually configurable, and have identical
provider options: `fake`, `local_vllm` (local-only / Omen), and `openai`
(cloud), through the existing adapter layer.

## 2. Architecture principle: one flag, one derivation, consumed as capabilities

The single most important design decision, and the one the executor must not
erode: **`care_system` is stored once (on `Department`), derived once per
side (one backend module, one web module), and consumed everywhere as named
capability flags — never as `if (careSystem === "ayurveda")` scattered through
components.**

This mirrors the pattern the codebase already trusts: allergy state is derived
in one place (`app.allergies.for_patient`), voice providers are selected as
one versioned profile (`app/providers/profiles.py`). The care-system mapping
is the same shape:

```
backend/app/care_system.py          # NEW — the only file that knows what
                                    #       "ayurveda" means operationally
web/app/_lib/careSystem.ts          # NEW — same mapping, conformance-tested
                                    #       against the Python one
```

```python
class CareSystem(StrEnum):
    ALLOPATHY = "allopathy"   # default; today's behaviour, bit-for-bit
    AYURVEDA = "ayurveda"

@dataclass(frozen=True, slots=True)
class CareSystemCapabilities:
    shows_cycles: bool            # chemo cycle sparkline, cycle events in dictation
    shows_regimen_events: bool    # regimen/cycle lines in DictationPanel event list
    checkin_protocols: bool       # S17 chemo check-in machinery surfaces
    guideline_pack: str           # "nccn" | "ayush" — research-tab framing only
    formulary_scope: str          # which formulary entries validate_meds sees
    ayurveda_assessment: bool     # prakriti/agni/nidana panel in doctor console
    pathya_apathya: bool          # diet/lifestyle section in Rx composer + print
    prompt_pack: str              # "oncology" | "ayurveda" — system-prompt selection
```

Rules for the executor:

- Components receive booleans/strings from this mapping via existing payloads
  (worklist card, kiosk department list, doctor session bootstrap). They never
  import `CareSystem` themselves.
- Adding a third system later (Unani, Homeopathy) must be: one enum value, one
  capabilities row, content. If a session ends with that not being true, the
  session introduced scattered conditionals — go back and centralise.
- The **default mapping for `ALLOPATHY` reproduces today's behaviour exactly.**
  Every existing test must pass with zero test-body edits (new fixtures/params
  are fine). That is the "both must remain stable" requirement made checkable.

## 3. Data model & config changes (all additive)

1. **`Department.care_system`** — new column, `enum_type(CareSystem)`,
   `server_default='allopathy'`, in `backend/app/models/org.py`. One additive
   Alembic migration, **no backfill** (every existing department genuinely is
   allopathy). Follow the migration notes convention in STATE.md: additive,
   applied locally only, counted in the pending-on-Omen list.
2. **Hospital naming** — `Hospital.name` is already free text seeded from
   `seeds/hospital.json`. What's missing is an admin edit path: add
   `PATCH /admin/hospital` (name, city, district, default_lang) + a small
   admin console surface (§7). The prescription/pass letterhead already reads
   stored hospital facts, so "Ayurveda Hospital" propagates for free — verify
   with the pass and Rx print tests, don't assume.
3. **No new provider settings.** `llm_provider = fake | gemini | openai |
   local_vllm`, the fallback chain, voice profiles (`local_oss` /
   `openai_cloud`), cost guard and metering in `backend/app/config.py` and
   `app/providers/` are already care-system-agnostic. Ayurveda inherits
   local-only and OpenAI cloud/Omen options **by construction** — the plan
   deliberately adds nothing here, and the executor must not fork the adapter
   layer. What does change: prompt selection (§6) and canned fake-LLM replies
   so the module is demonstrable with `LLM_PROVIDER=fake` (the MRD precedent).
4. **Seeds** — `seeds/hospital.json` gains one department:
   `{ "code": "AYUR", "name": "Ayurveda", "care_system": "ayurveda", "icon": "leaf" }`
   (all others get explicit `"care_system": "allopathy"` for readability; the
   seed loader defaults missing to allopathy so third-party seed files don't
   break). New ayurveda trees land in `seeds/trees/` (§5), new formulary
   entries in `seeds/formulary.json` tagged with scope (§6.3).

## 4. Safety invariants (extend CODEBASE_MEMORY's non-negotiables)

These are load-bearing; put them in the code comments where they bite:

- **Red flags stay deterministic and stay allopathic.** An ayurveda intake
  tree uses the same `app.trees.rules` engine, and a fired red flag routes to
  urgent/staffed allopathic care exactly as today — chest pain, hemoptysis,
  breathlessness at rest, altered sensorium never route to AYUR. A wellness
  framing must never soften an emergency.
- **TB is a notifiable disease.** The pulmonology/respiratory ayurveda tree
  must treat TB-suspect answers (cough > 2 weeks, blood in sputum, evening
  fever + weight loss) as a red-flag route to Pulmonology/DOTS, with the
  ayurveda visit as adjunct at most. This is a hard rule in the tree content,
  reviewed like the oncology tree bank (clinical sign-off gate, §9).
- **Dosha language is flavour, never triage.** `summary_role`-style metadata
  and ayurveda terminology are presentation; traversal, red flags and routing
  read only answer IDs. A patient is never required to know or pick a dosha.
- **Formulary discipline is symmetric.** Dictation never silently corrects or
  invents a drug in either system; ayurvedic preparations validate against
  ayurveda-scoped formulary entries, and the unsigned-prescription boundary is
  identical.
- **All ayurveda content ships model-drafted and UNREVIEWED**, flagged for
  sign-off by a BAMS practitioner — the same stance as the oncology tree bank
  and check-in protocols. Say so in the seed files' `_comment`.

## 5. Intake: trees, language, routing

**Schema: unchanged.** The ayurveda trees use the existing deterministic tree
schema (`app/trees/schema.py`), the same walker, the same offline TypeScript
conformance. If the executor believes a schema change is needed, that is a
stop-and-reconsider moment, not a migration.

**Language.** Trees carry per-language text `en/hi/mr/te`. Decision: **do not
extend the `Lang` enum** (it ripples through models, kiosk shells, TTS packs,
patient app). Hinglish is authored content, not a locale: for ayurveda trees
the `en` slot carries accessible Hinglish ("Pet mein jalan ya gas banti hai?")
and `hi` carries Devanagari Hindi. `mr`/`te` get honest plain translations
(owed native review, like everything else). Record this decision in each
tree's `_comment`.

**Tone.** Ayurveda context without jargon walls: terms appear as
*Hindi-first with the familiar word doing the work* — agni/digestion as
"पाचन (अग्नि)", not a dosha quiz. Icons and department card copy give the
ayurveda feel; questions stay symptom-and-daily-life concrete (sleep, bowel
habit, appetite, diet, season) because those are genuinely what an ayurveda
OPD asks and patients can answer.

**Tree bank (new files in `seeds/trees/`):**

| file | routes to | notes |
|---|---|---|
| `ayurveda_routing.json` | AYUR sub-flows / staffed desk | the department's own walk-in root: digestion, joints, skin, sleep/stress, lifestyle review |
| `ayurveda_digestion.json` | AYUR | agni/pachan: appetite, acidity, bowel pattern, diet; red-flags GI bleeding, dysphagia, mass → allopathy urgent |
| `ayurveda_joint_pain.json` | AYUR | sandhi-shool: pattern, morning stiffness, swelling; red-flags hot swollen joint, trauma, fever → allopathy |
| `ayurveda_lifestyle_prameha.json` | AYUR | diabetes/prameha lifestyle intake; red-flags hypo symptoms, foot ulcer, blurred vision → GENMED |
| `ayurveda_respiratory.json` | AYUR / **PULM on TB-suspect** | shwas/kasa; the §4 TB rule lives here |

**Routing rules into ayurveda.** Existing `general_medicine_routing.json` and
`pulmonology_routing.json` gain an option branch — *only when an active
ayurveda department exists* — of the shape "मैं आयुर्वेद इलाज के लिए आया/आई
हूँ" (I came for ayurveda treatment), routing to AYUR. Implementation: the
tree bank already resolves department codes; add a rule/visibility mechanism
so an option whose destination department is inactive is not rendered
(kiosk-side filter on the department list the backend already serves —
**not** a schema fork). Kiosk department picker shows Ayurveda with its icon
and care-system-styled card when the department is active.

## 6. Doctor console: derive, don't fork

**One console, capability-gated.** There is no `web/app/(ayurdoctor)` route
group. The existing `(doctor)/doctor` console reads capabilities from the
doctor-session bootstrap (the doctor's department → care_system →
capabilities, added to the existing session/worklist payloads in
`app/routes/doctor.py`) and renders accordingly. Verified oncology-specific
surface is small — the console is already mostly generic:

**Removed under ayurveda capabilities** (`shows_cycles=false`, etc.):
- `Sparkline.tsx` "symptom across cycles" trendline usage
- regimen/cycle lines in `DictationPanel.tsx` (~lines 486–487)
- chemo check-in protocol surfaces (S17) where they appear
- "NCCN" framing in `WorkTabs.tsx` / research-tab copy → guideline pack label

**Retained unchanged:** worklist, context spine (identity, diagnosis,
allergies, red-flag strip, on-file line), consult rail
(Capture → Review → Sign → Prescription), dictation + typed-note parity,
floating dictation, Rx panel + signature boundary + snapshot/print, Reports
(MRD), Imaging, Research assistant, allergy panel, conclude flow, audit.

**Added under ayurveda capabilities:**
1. **Ayurveda assessment panel** — structured, optional note fields on the
   consult record: prakriti (constitution), vikriti/dosha involvement
   (free-text + chips), agni, koshtha, nidana (aggravating factors). Stored as
   structured note fields on the existing note record (same `fields`/`edits`
   trail, same signature), **not** a new note type or table. Nothing here is
   machine-decided.
2. **Pathya–Apathya (diet & lifestyle) section** in the Rx composer and on the
   printed prescription — free-text lines the doctor writes/dictates, shown in
   the shared prescription renderer for both HTML and future PDF.
3. **Ayurveda formulary scope** — `seeds/formulary.json` entries tagged
   (classical + proprietary preparations, with anupana/instructions field
   reusing the existing instruction machinery; dose-time inference rules
   unchanged). `validate_meds` filters by the department's formulary scope so
   an ayurvedic drug isn't flagged "not in formulary" in an ayurveda consult
   and vice versa.
4. **Prompt pack** — ayurveda variants of the intake-summary, dictation-
   mapping and research-assistant system prompts in `backend/prompts/`,
   selected by `capabilities.prompt_pack` at the dispatch sites that already
   choose prompts. The research assistant keeps its citations/behaviour;
   only framing and formulary context change. Fake LLM gains canned ayurveda
   replies (MRD precedent) so every flow demos on `LLM_PROVIDER=fake`.

## 7. Admin console

In `web/app/(admin)/admin/_components/RegistryTab.tsx` (or a sibling if
RegistryTab is the wrong home — executor judges on reading it):

- **Hospital identity card**: edit name/city/district/default language →
  `PATCH /admin/hospital`, audited. This is where "Ayurveda Hospital" happens.
- **Department editor**: create/edit departments with a *System of medicine*
  selector (Allopathy default / Ayurveda), active toggle. Changing an existing
  department's care system is allowed but confirmed with explicit copy about
  what changes (intake trees offered, doctor console sections, formulary
  scope) — and is audited.
- Trees tab needs no structural work: ayurveda trees are ordinary versioned
  trees in the existing editor/publish flow.

## 8. Session plan for the executor

Follow `docs/07-SESSION-PROTOCOL.md` ritual per session (read HANDOFF, STATE,
this doc; baseline tests; session log; update STATE/HANDOFF). Each session
below is sized to land green in one sitting; do not merge sessions. **Gate for
every session:** full `make test` green, typecheck, lint, conformance; plus
the session's own listed evidence. No session may edit an existing test's body
to make it pass.

### SESSION-AYUR-0 — the flag and the derivation (backend + web core)
- `CareSystem` enum + `Department.care_system` column + additive migration.
- `backend/app/care_system.py` capabilities mapping; unit tests pinning the
  ALLOPATHY row to today's behaviour.
- `web/app/_lib/careSystem.ts` + a Python↔TS conformance fixture (extend the
  existing conformance suite pattern).
- Seed loader reads `care_system` (defaulting allopathy); `seeds/hospital.json`
  gains AYUR department.
- Expose `care_system` in: admin `GET /departments…`, kiosk department list,
  doctor session/worklist payloads (capabilities object, not raw string,
  wherever a UI consumes it).
- Evidence: migration applied locally; full suite green with zero behaviour
  change anywhere a UI renders.

### SESSION-AYUR-1 — admin configurability
- `PATCH /admin/hospital`; department create/edit with care-system selector;
  audit entries for both; confirmation copy for care-system change.
- Verify letterhead propagation: rename hospital in a test → pass print and Rx
  snapshot show the new name.
- Evidence: admin E2E covering rename + department creation as ayurveda.

### SESSION-AYUR-2 — intake content and routing
- Author the five trees of §5 (Hindi + Hinglish-en + mr/te), `_comment` blocks
  carrying the UNREVIEWED flag and the language decision.
- TB red-flag rule in `ayurveda_respiratory.json` with a dedicated test.
- Inactive-destination option filtering; AYUR branch in GENMED/PULM trees.
- Kiosk department card styling for ayurveda (icon, copy) within the existing
  shell; offline path exercised (trees are content, so offline should be free
  — prove it, don't assume it).
- Evidence: walker tests per tree incl. every red-flag route; conformance
  fixtures for at least the routing tree; kiosk E2E of one full ayurveda
  intake → token → pass.

### SESSION-AYUR-3 — doctor console, capability-gated
- Wire capabilities into the console bootstrap; hide cycles/regimen/check-in/
  NCCN surfaces under ayurveda; assert **oncology console renders unchanged**
  (existing doctor E2E untouched and green).
- Ayurveda assessment panel + pathya–apathya in Rx composer and shared
  renderer; snapshot includes them.
- Ayurveda formulary entries + `validate_meds` scoping; dictation-mapping and
  summary prompt packs; fake-LLM canned ayurveda replies.
- Evidence: doctor E2E for an ayurveda consult end-to-end (intake → worklist →
  dictate → validate → sign → print) on `LLM_PROVIDER=fake`; a test that an
  ayurvedic drug validates in AYUR and is flagged in MEDONC.

### SESSION-AYUR-4 — research assistant framing, hardening, docs
- Research prompt pack + guideline-pack label; canned fake replies.
- Cross-cutting sweep: grep for any `care_system ===`/`== CareSystem.` that
  leaked outside the two mapping modules; fix by capability flag.
- Update `STATE.md`, `CODEBASE_MEMORY.md` (§4 invariants), seeds README;
  session log; HANDOFF for the deploy step.
- Evidence: full gates; a written checklist of what remains **content review**
  (BAMS sign-off of trees/formulary/prompts, native mr/te review) vs code.

### Explicitly out of scope (future docs, do not build now)
Ayurveda check-in protocols (panchakarma follow-up), patient-app ayurveda
content, voice-tier ayurveda dialogue, a third care system. Note them; build
none of them.

## 9. Launch gates (beyond code)

Same stance as the oncology bank: ayurveda trees, formulary entries, prompt
packs and any patient-facing mr/te text are **model-drafted and unreviewed**
until a BAMS practitioner signs them off and native speakers review the
languages. The module can be demoed on fake providers before that; it must not
be enabled for real patients before it.
