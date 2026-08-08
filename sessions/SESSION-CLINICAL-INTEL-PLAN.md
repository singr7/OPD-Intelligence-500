# SESSION-CLINICAL-INTEL — Records Digitization, PACS Viewing, Ambient Notes, Research Assistant

Type: planning only. Nothing here is implemented.

Written against the working tree on 2026-08-05 (post SESSION-C, branch
`assign-rx-identity` merged to `main`). Four modules, one intent: **the doctor
opens the consult already knowing what the papers and the scans say**, and what
they observe and wonder during the consult is captured without a keyboard.

The four modules, and the honest size of each:

| # | Module | What it really is | Size |
|---|--------|-------------------|------|
| 1 | Medical Record Digitization (MRD) | New capture surface + new pipeline + new doctor tab | **Large** (2 sessions) |
| 2 | PACS image viewer | Config + one proxy endpoint + a launcher button | **Small** (½–1 session) |
| 3 | Doctor notes (ambient dictation → basic EMR) | A *delta* on the Session-C dictation stack | **Medium** (1 session) |
| 4 | AI research assistant | A guarded chat panel over minimized patient context | **Medium** (1 session) |

Recommended build order: **1 → 2 → 3 → 4** (MRD first because it changes what
the doctor sees before the patient enters — the stated intent; PACS second
because it is a cheap, visible win that completes the "before the consult"
picture; notes third because it builds on code that just shipped; research
assistant last because it depends on the structured context the first three
produce).

## Pilot decisions taken (constraints, not open questions)

1. **The coordinator's phone is the scanner.** No dedicated hardware, no native
   app for this. The capture surface is a mobile-first page of the existing
   Next.js app (installable as a PWA), behind coordinator auth. The ANDROID1
   patient app is *not* the vehicle — it is patient-owned and environment-locked.
2. **Extraction is cloud-LLM-first with a visible degraded state.** The Omen
   local vLLM is text-only today; document vision runs on the `openai_cloud`
   profile (or Gemini, same contract). When the cloud is unreachable the images
   are still stored, viewable, and queued — "awaiting extraction" is a state,
   never a silent failure. This is the existing provider-degrade invariant
   applied to a new pathway.
3. **Outlier judgement is deterministic, not the model's.** The model *extracts*
   values and *writes prose*; whether a value is out of range is computed in code
   against the reference range printed on the report (extracted alongside the
   value) or a curated fallback table. "A model may interpret or summarize; it
   may not decide clinical urgency" (CODEBASE_MEMORY invariants) applies here
   verbatim.
4. **The central PACS is authoritative; local Orthanc is a mirror option.**
   Central: Orthanc on AWS, AET `RAD-RENVA-PACS`, DICOM port 4242, DICOMweb on
   8042. The UHC ID (`Patient.external_id`) is the join key — it must be the
   DICOM `PatientID` used at the modality/CD-import side. That is an operational
   contract with the imaging centre, recorded in §8 as debt, not designed around.
5. **The web viewer already exists and is already connected to the PACS.** This
   repo builds the *stub that launches it*: study discovery by UHC ID, and a
   popup handoff per study. We do not build or vendor a DICOM viewer.
6. **Notes never touch the prescription path.** The Session-C rule — `sign` →
   `prescription.generate` is the only prescription writer — is load-bearing.
   Ambient notes are a separate record type with no drug-order semantics, ever.
7. **The research assistant advises the doctor and only the doctor.** It is
   reference-grade, cited where possible, visibly non-authoritative, and it
   cannot write to any clinical record.
8. **PHI minimization for every cloud call.** Name, phone, MRN, UHC ID never
   leave the box in an LLM payload. Age band, sex, diagnosis, values, units do.
   Same posture the `openai_cloud` voice profile already established.
9. **Two storage modes, and `OBJECT_STORE` is the seam** (settled 2026-08-06,
   after MRD2). The on-premise box is the *first* deployment and its pages live
   on **local disk**: reads are sub-millisecond to every terminal on the LAN,
   they cost nothing per GET, and a doctor opening a lab report never depends on
   a WAN link a rural OPD cannot promise. S3 is that mode's **offsite backup,
   not its store** — synced every 15 minutes, which was chosen over nightly
   deliberately: `s3 sync` is incremental, so both cadences upload identical
   bytes for identical money and S3 ingress is free, while nightly would put a
   day of irreplaceable scans at risk (the paper originals go home with the
   patient). Disk failure itself is answered by **mirrored disks on the box**,
   which backup cadence cannot substitute for. The AWS deployment is the second
   mode and inverts this: **S3 is the object store**, EBS holds Postgres and its
   extracted JSON, and the page-sync backup does not run at all because the
   store is already durable. Migration between the modes is cheap by
   construction — `mrd.page_key` builds identical keys in both, so it is one
   `aws s3 sync` and a changed setting, with no rows rewritten.

---

## 1. Module 1 — Medical Record Digitization (MRD)

### 1.1 The flow, end to end

```
Intake completes ──► Coordinator opens /scan on their phone
                        │  (same login/session as coordinator console)
                        ▼
                 Picks the patient (today's queue, token search, UHC ID)
                        ▼
                 Camera capture, page after page, batched per document
                 "Lab report · 3 pages"  "Biopsy report · 2 pages"
                        │  upload as captured; retry queue if offline
                        ▼
             Backend stores originals ──► Celery job per document
                        ▼
             Vision LLM: page images → structured JSON extraction
                        ▼
             Deterministic pass: units normalized, ranges compared,
             outliers flagged in code
                        ▼
             LLM summary prompt over the *verified structure* (not the
             images again): 5–8 line clinical summary, outliers first
                        ▼
             Doctor console: "Reports" joins the tab rail; the spine
             shows "2 new reports · 4 values out of range" before the
             patient enters the room
```

Latency budget: capture-to-summary under **3 minutes** for a 3-page report.
That is what "before the patient enters" means at a 500-patient/day OPD where
the wait between desk and door is rarely shorter than ten.

### 1.2 Capture surface: `/scan`

A new route group in `web/`, mobile-first, coordinator-authenticated, and
deliberately tiny — three screens:

1. **Patient pick.** Today's visits (token, name if known, department), search
   by token / phone last-10 / UHC ID. Reuses the coordinator queue endpoints.
2. **Capture.** `<input capture="environment">` camera flow; each shot appears
   as a thumbnail strip; the coordinator names the batch with one tap from a
   short list — `Lab report`, `Biopsy / histopath`, `Imaging report`,
   `Discharge summary`, `Prescription (outside)`, `Other` — and can add pages
   until they hit **Done**. Client-side downscale to ~2000px long edge, JPEG
   ~80, before upload: a 4-page document should cost ~1–2 MB on OPD Wi-Fi.
3. **Confirm.** "3 pages · Lab report · Token 14 · uploading… ✓". Then straight
   back to capture for the next patient. Uploads that fail queue in the service
   worker (the kiosk's Dexie/retry pattern, minus long-term retention — synced
   rows are pruned, same rule as kiosk PII).

No editing, no cropping UI, no OCR on the phone. The coordinator's job is
15 seconds per document.

### 1.3 Storage and data model

Originals go to object storage, not Postgres: MinIO container on Omen (joins
`docker-compose.yml`), S3 in the AWS shape — one `ObjectStore` adapter in
`providers/` with the same fake/real split every other provider has. Bucket
layout `records/{patient_id}/{document_id}/page-{n}.jpg`, private, served only
through an authenticated backend URL.

New models in `models/clinical.py` (all `Clinical`, all audited, all soft-delete):

```python
class MedicalDocument(...):   # one scanned document (a batch of pages)
    patient_id, visit_id          # visit nullable: records outlive visits
    kind                          # DocumentKind enum: lab|histopath|imaging_report|discharge|outside_rx|other
    pages: int
    object_keys: list[str]        # JSONB, ordered
    status                        # captured → extracting → extracted → summarized | extraction_failed
    captured_by                   # coordinator user id
    provider_snapshot             # exact vision/LLM providers+models, VOICE1 pattern

class DocumentExtraction(...):  # one per document, the machine's reading
    document_id
    payload: JSONB                # the schema in §1.4
    summary_text: str | None
    outlier_count: int            # computed, denormalized for the spine badge
    prompt_refs: list[str]        # id@vN provenance, existing loader convention
    verified_by / verified_at     # doctor acknowledgement, nullable
```

One additive Alembic migration. No changes to existing tables.

### 1.4 Extraction contract

The provider contract grows one capability: `LLMRequest.images:
Sequence[ImagePart] = ()` (bytes + media type), mapped to OpenAI
`image_url`/data-URI parts and Gemini `inline_data` parts. `FakeLLMProvider`
accepts and records them so the whole pipeline is testable offline — the same
reason every other provider has a fake. Providers that cannot take images
raise `UnsupportedCapability` and the registry treats that as
provider-unavailable, which lands the document in the visible
`extraction_failed`-with-retry state rather than a fake success.

Extraction prompt (new `backend/prompts/mrd_extract/`) returns strict JSON:

```json
{
  "document_kind_guess": "lab",
  "report_date": "2026-07-30",
  "lab_name_present": true,
  "tests": [
    {"name": "Hemoglobin", "value": 8.9, "unit": "g/dL",
     "ref_low": 12.0, "ref_high": 15.0, "ref_source": "printed",
     "page": 1, "confidence": "high"}
  ],
  "narrative_findings": ["Impression text from a histopath report, verbatim…"],
  "illegible_regions": ["page 2, bottom table"]
}
```

Rules the prompt states and the code enforces:

- **Verbatim or absent.** A value the model cannot read is omitted and the
  region listed in `illegible_regions` — never guessed. Same spirit as
  "dictation never silently corrects or invents a drug."
- Reference ranges are extracted *from the report* when printed
  (`ref_source: "printed"`); a curated table in `seeds/` fills gaps
  (`ref_source: "default"`), and the doctor UI shows which was used.
- The deterministic pass (plain Python, unit-normalization table, Decimal
  comparisons) sets `flag: low|high|critical` per test. The model's output has
  no flag field at all — it cannot volunteer one.

The summary prompt (`mrd_summarize/`) then runs over the **flagged JSON**, not
the images: "Write ≤8 lines for an oncologist about to see this patient.
Out-of-range values first with direction and magnitude. No advice, no
differential, no urgency language beyond the computed flags." Two cheap LLM
calls per document, both metered and cost-guarded like every existing call.

### 1.5 Doctor surface

- **Spine** (never unmounts, Session-B rule): a fifth persistent slot —
  `Reports: 2 new · 4 values flagged` — amber when unverified extractions
  exist, quiet grey otherwise. It is a status, not an alarm: red stays
  reserved for the deterministic red-flag lane.
- **`Reports` tab** joins the rail (it is exactly what the "Coming soon"
  disclosure in `WorkTabs.tsx` was built to graduate): summary card on top
  (badged **AI-read, unverified** until a doctor taps *Mark reviewed* →
  `verified_by`), flagged-values table under it (value, unit, range, source
  page link), original page images at full resolution below. The original is
  always one tap away from any extracted number — trust is inspectable.
- **Failure states, all visible:** `extracting` shows a progress row;
  `extraction_failed` shows the images with "Could not auto-read · view
  original" and a retry affordance for the coordinator/admin. The document is
  never invisible because the model was down.

### 1.6 What MRD must never do

- Never gate intake, queue, or consult on extraction.
- Never let extracted values flow into prescription validation, check-in
  grading, or routing — they are *display and summary* in this plan. Wiring
  them into decisions is future work behind its own review (§8).
- Never store page images in IndexedDB beyond successful upload.

---

## 2. Module 2 — PACS study viewer (stub)

### 2.1 Shape

```
Doctor console                    backend                          Orthanc (central AWS
  spine: [ Images (3) ] ──►  GET /doctor/patients/{id}/studies ──► or local mirror)
                                  │  QIDO-RS: /dicom-web/studies?PatientID={UHC}
                                  ▼
                          [{study_uid, date, modality, description,
                            series_count, has_report}]
  click a study ──────►  popup: {PACS_VIEWER_URL}?StudyInstanceUIDs={uid}
                          (the already-connected web viewer; read-only)
  click its report ────►  GET /doctor/studies/{uid}/report  (backend streams the
                          encapsulated PDF/SR retrieved via WADO-RS)
```

### 2.2 Decisions

- **The browser never talks to Orthanc.** The backend proxies QIDO-RS and the
  report bytes, holding Orthanc credentials server-side, checking department
  scope (`patient_card` rules — not narrowed to the assigned doctor, decided
  three times), and writing a `Clinical` audit row per study-list fetch and
  report view. The *viewer* popup is the one exception: it is its own
  authenticated product already connected to the PACS; we hand it a
  StudyInstanceUID and nothing else.
- Config, not code: `PACS_ENABLED`, `PACS_DICOMWEB_URL`, `PACS_AUTH_*`,
  `PACS_VIEWER_URL`, `PACS_AET=RAD-RENVA-PACS` (documentation of the DICOM
  endpoint, `:4242`, even though the web path is what we call). Pointing at
  local Orthanc is changing one URL — that *is* the local-PACS option, plus a
  compose service for the mirror in the Omen file, replication left to Orthanc
  configuration outside this repo.
- Patients with no `external_id`, PACS timeouts, and zero-match responses each
  get their own quiet, truthful state ("No UHC ID on file — images cannot be
  looked up" / "PACS unreachable" / "No studies found"). No fabricated empties.
- Multiple studies: a list, one popup per selected study. No thumbnail
  rendering, no series logic — that is the viewer's job.

This is deliberately a stub. Its acceptance test is a fake DICOMweb server in
the test suite, the same pattern as every messaging/telephony fake.

---

## 3. Module 3 — Ambient doctor notes → basic EMR

### 3.1 What already exists, and the actual delta

Session C built: recording with a real analyser meter, STT via snapshotted
profiles, `dictation_map` prompting, editable field review, signature boundary,
audit. All of it for **prescriptions**. The delta is a second, lighter *use*
of that stack:

- A **floating mic button** on the doctor console, present on every tab
  (rendered beside the spine so it also never unmounts), for capturing
  observations *while browsing* — "post-chemo cycle 3, tolerating well,
  grade 1 mucositis, review CBC next visit."
- A new `ClinicalNote` model: `visit_id`, `transcript` (never overwritten —
  Session-C rule), `structured: JSONB`, `status: draft → confirmed`,
  `provider_snapshot`, `prompt_refs`, audited.
- A new `note_map` prompt family mapping the transcript into a deliberately
  small EMR shape — this is the "useful basic EMR", and staying basic is the
  design:

```json
{"subjective": "...", "objective": "...", "assessment": "...",
 "plan_narrative": "...",
 "tags": {"problems": ["carcinoma breast"], "symptoms": [{"name": "mucositis", "grade_mentioned": "1"}],
          "followups": ["CBC before next cycle"]}}
```

- Review is the Session-C pattern in miniature: transcript on one side, mapped
  fields editable on the other, **Confirm** to store. Mapping failure = fields
  open empty beside `mapping_error`, transcript preserved — the exact
  degraded state `compose` already established.

### 3.2 Hard edges

- **No drug-order semantics.** `plan_narrative` is prose. If a doctor dictates
  a prescription here, nothing validates, generates, or prints it — the
  Consult tab's pathway is where prescriptions happen, and the note UI says so
  in one quiet line. This is decision 6 and it is the whole reason this module
  is safe to build quickly.
- Notes are the doctor's working memory, not patient-facing output. They never
  appear on printed sheets or in the patient app in this plan.
- `tags` exist for analytics (symptom burden per protocol, follow-up debt,
  problem prevalence — queries over confirmed notes only, drafts excluded).
  Tags are model-suggested and doctor-visible at confirm time; analytics over
  them is labelled model-assisted wherever surfaced.

---

## 4. Module 4 — AI research assistant

### 4.1 Shape

A panel (opened from the doctor console, patient-contextual) that starts
pre-grounded and stays interactive:

```
┌ Research — Token 14 ────────────────────────────────────────┐
│ Context sent: 52F · carcinoma breast (from signed note) ·   │
│ Hb 8.9 ↓ · ANC 1.1 ↓ · cycle 3 of AC-T      [view/edit]     │
│                                                             │
│ Suggested: · Anemia management during AC-T                  │
│            · G-CSF secondary prophylaxis criteria           │
│ ▸ doctor asks anything; multi-turn; history kept per visit  │
│                                                             │
│ ⚠ Reference only. Not a recommendation. Verify before use.  │
└─────────────────────────────────────────────────────────────┘
```

- **Context assembly is code, not model:** age band, sex, diagnosis from the
  latest *signed* note (the spine's own rule), computed lab flags from Module
  1, current-visit confirmed note tags from Module 3. Shown to the doctor
  *before* the first call — the doctor can see and trim exactly what leaves
  the box. Decision 8 applies: no identifiers, ever.
- Multi-turn via the existing `LLMRequest.history`; system prompt
  (`research_assist/`) pins the register: evidence-summary style, name
  guidelines and trials when known, state uncertainty, refuse dosing
  calculations and urgency judgements, always end grounded in "discuss
  against local protocol." `json_output=False` — this is the one prose
  surface in the plan.
- Every exchange stored (`ResearchThread` / `ResearchTurn`, per visit,
  audited) — both for medico-legal traceability and because "what doctors ask"
  is itself the analytics.
- Cost-guarded per-doctor-per-day like every metered pathway; provider down →
  the panel says so and closes; nothing queues.
- **v1 is the model's knowledge, clearly dated.** A retrieval layer
  (PubMed/guideline search with citations) is the obvious v2 and is listed in
  §8 rather than smuggled into v1 — a wrong-but-cited answer needs real
  retrieval engineering, not a search tool bolted on in the same session.

---

## 5. Cross-cutting work (lands with Module 1, serves all four)

1. **Vision in the provider contract** (§1.4): `ImagePart`, OpenAI + Gemini
   mapping, fake support, `UnsupportedCapability`, conformance with metering
   and cost guard.
2. **Object storage adapter.** ✅ *Delivered in M1/M2, but not as written here.*
   There is **no MinIO service**: M1 shipped `app/providers/objectstore.py` with
   a filesystem impl on a Compose volume, because a whole extra container to
   serve bytes off the same disk buys nothing on a single box. M2 gave that
   volume a real mount on both compose files (M1 had mounted *nothing* at
   `/data/records`, so pages died on container recreate and the worker could not
   see them at all) and put it in the backup: dump first, sync second, with the
   daily drill failing if a restored document's pages are not in the bucket.
   **What remains is the S3 impl — now Session M6 (§6), not cross-cutting
   work**, because it belongs to the AWS deployment mode rather than to any of
   the four modules, and none of them is blocked on it.
3. **PHI-minimization helper**: one function that builds the "cloud-safe
   patient context" dict, used by MRD summarize and the research assistant,
   unit-tested to reject identifier keys. One implementation of the rule.
4. **Provider profile additions**: `vision` and `research` slots in the
   profile/snapshot machinery so an operator can pin models per module, the
   VOICE1 way.

---

## 6. Session plan

Each session follows doc 07's protocol; gates listed are the acceptance bar.

**Session M1 — MRD capture and pipeline (backend-heavy).**
Object store adapter + MinIO; models + migration; `/scan` PWA (three screens,
offline retry); vision contract extension; extract → deterministic flags →
summarize Celery pipeline; status lifecycle incl. `extraction_failed` + retry;
metering/cost-guard wiring. *Gates:* backend suite green with new unit tests
(extraction schema validation, unit normalization, Decimal range flags, PHI
helper), pipeline E2E against `FakeLLMProvider` with fixture page images,
`/scan` Playwright flow, build/typecheck/lint.

**Session M2 — MRD doctor surface.**
Spine slot + `Reports` tab (summary card, flagged table, page viewer,
*Mark reviewed*, all failure states); coordinator retry surface; doc 20-style
deploy note for MinIO + migration on Omen. *Gates:* doctor E2E extended
(report appears before consult, unverified badge clears on review, failed
extraction still shows originals), conformance untouched, full frontend gates.

**Session M3 — PACS stub.** ✅ *Delivered 2026-08-08
(`sessions/SESSION-M3.md`), last of the four, once §8.1's external gate closed.*
Config, proxy endpoints, a fake DICOMweb server, the study list, the viewer
popup handoff, report streaming and audit rows. Three things differ. There are
**four** truthful empty states, not three — "the switch is off" is a fact about
us and had to stay separate from "the PACS says none". `Images (n)` is a
**clause on the spine's Reports line**, not a sixth slot, and imaging is a
**section of the Reports tab**, not a seventh tab: it has no surface of its own
to justify one, and Reports already means "what is on file from outside this
consult". *Gates met:* backend **1,701**, imaging E2E 6, doctor 12 (that project
had asserted four tabs since MRD2 and nobody had run it), full frontend gates.
**Gate NOT met: manual acceptance against the real `RAD-RENVA-PACS`.** No line
of the DICOMweb path has met a real Orthanc; it is proven only against a fake.
`PACS_ENABLED` defaults false for that reason.

**Session M4 — Ambient notes.** ✅ *Delivered 2026-08-06
(`sessions/SESSION-M4.md`), built ahead of M3 because M3's external gate (§8.1)
is still open.* Floating recorder, `ClinicalNote` + migration `02571a5c1871`,
`note_map` prompts, review panel, confirm flow, mapping-failure state, tags, and
`analytics.note_tags` on the admin Ops tab. Two things differ from this
paragraph. The recorder was **extracted** into `useVoiceCapture` and shared with
`DictationPanel` rather than written twice — the no-bars-without-an-analyser
rule must live in one place. And the surface is a **dock, not a tab**: a tab
would replace what the doctor was reading, which is the failure the context
spine exists to prevent. *Gates met:* backend 1,604, notes E2E 5, dictation E2E
8 unchanged, full frontend gates; the prescription-origination test is
structural (`app.notes` may not import the prescription path at all).

**Session M5 — Research assistant.** ✅ *Delivered 2026-08-08
(`sessions/SESSION-M5.md`).* Context assembly + trim, `ResearchThread`/
`ResearchTurn` + migration `9f2ab41c77d3`, the `research_assist` prompt family,
the panel, storage/audit, the daily guard and the provider-down state. Three
things differ from this paragraph. The context is **trimmed by id, never by
text** — the client can only subtract, because a payload the browser composed is
one `app.phi` cannot vouch for; that asymmetry turned out to be the module. The
guard is a **count of turns, not a sum of rupees**, because metering is async by
design and the rupee is not knowable at the moment the guard must decide. And it
is a **tab, not a panel over the console** — the inverse of M4's dock argument:
reading an evidence summary is the thing being done, not something done while
reading. *Gates met:* backend **1,660**, research E2E 7, notes 5, dictation 8,
conformance 48 unchanged, full frontend gates; the no-clinical-write test is
structural (`app/research` may not import a clinical writer, and nothing parses
an answer).

**Session M6 — the S3 object store, and the AWS storage mode.**
Parked deliberately (decision 9): the on-premise box ships first and does not
need it, and nothing in M3–M5 is blocked on it. **Schedule it when an AWS
deployment is actually dated, not before** — an unused storage backend that has
never run against a real bucket is a liability, not readiness.

Scope, and the decisions already taken so the session does not re-open them:

- **`S3ObjectStore`** against the existing four-method ABC (`put` / `get` /
  `delete` / `exists`). Keys are unchanged — `mrd.page_key` is the only place
  one is built, and it stays the only place. Needs an S3 SDK in
  `backend/requirements.txt`; there is none today.
- **No presigned URLs, in either mode.** S3 makes them a one-liner, which is
  exactly why this is written down: doc 21 §1.3 refuses them because a URL that
  outlives the session that minted it leaks a patient's biopsy report into a
  browser history, a chat, a screenshot. Pages keep streaming through the
  authenticated route.
- **The backup becomes mode-aware, and this is a real bug if it is skipped.**
  `verify-restore.sh` samples object keys out of the restored database and heads
  them under `$BACKUP_BUCKET/pages/`. In the S3 mode the pages live in the
  *records* bucket, so every sampled key would report missing and fail the
  drill. `backup.sh` must skip the page sync entirely (the store is already
  durable), and the drill must either check the records bucket or state plainly
  that durability is the bucket's job — never pass silently.
- **Terraform gains a records bucket**: private, encrypted, versioned, no public
  access, with a lifecycle policy (§8.7). CLOUD1 defines only the backup bucket.
- *Gates:* the objectstore suite green against both impls (a fake keeps parity),
  a test that no code path mints a signed URL, `test-contract.sh` extended for
  the mode-aware backup, and one real round trip against a live bucket recorded
  in the session log — the thing that makes it readiness rather than code.

Rough order of dependency, not just preference: M3 needs nothing from M1/M2
and can swap earlier if the imaging-centre contract (§8.1) resolves first. M6 is
ordered by the *deployment* calendar rather than by this list.

**Where this actually went** (2026-08-08): M1 → M2 → **M4** → **M5**, with M3
skipped on its open gate throughout. Three of the four modules are complete.
M5's context assembly (§4.1) reads the spine's signed-note diagnosis, M1's
computed lab flags and M4's confirmed note tags, so the build order the plan
recommended held even with M3 missing from the middle of it — the research
assistant depended on the first three producing structure, and not on the
imaging stub at all.

**All four modules are now built** (2026-08-08): M1 → M2 → M4 → M5 → M3, with
M3 last rather than third because §8.1's contract took until now to confirm.

**What is left of this plan is not code.** Two of the four modules have never
been exercised against the real thing they wrap — the research assistant has
answered nobody with a real model, and the PACS path has never met a real
Orthanc — and both gates are clinical or operational rather than engineering.
§8's debt list is otherwise intact: the reference-range table still needs
oncologist review, extracted labs still feed no decision, retrieval for the
assistant is undesigned, storage has no lifecycle policy, and the box still has
no mirrored disks. The next session should be chosen from that list, or from the
pilot's own needs, rather than by continuing to number these.

---

## 7. What "simple and effective" means here, held against each module

- MRD: the coordinator does 15 seconds of pointing a phone; everything clever
  happens server-side; the doctor gets eight lines and a table, originals one
  tap away.
- PACS: one button, one list, one popup. The viewer someone already built does
  the viewing.
- Notes: one floating button, one confirm screen, one JSON shape small enough
  to read.
- Research: one panel that shows what it sends before it sends it.

Every module keeps the platform's spine: deterministic where it decides,
model-assisted where it drafts, visible when it degrades, audited when it
writes.

## 8. Debt and external gates (recorded, not designed around)

1. **UHC ID ↔ DICOM PatientID contract** with the imaging centre. Until the
   modality registers studies under the UHC ID, PACS lookup returns empty for
   everyone. Operational agreement, not code.
2. **Reference-range fallback table needs oncologist review** before
   `ref_source: "default"` flags are shown as anything stronger than grey.
3. **Extracted labs feeding decisions** (Rx validation, check-in grading,
   trends across visits) — future work behind clinical review.
4. **Research assistant retrieval/citations (v2)** — needs a real retrieval
   design session.
5. **Handwritten outside prescriptions** will extract poorly; `illegible_regions`
   + original-image viewing is the pilot answer; measure before promising more.
6. **Orthanc mirror replication and its backup** are Orthanc-config/ops work in
   doc-17/18 territory, not backend code.
7. **Storage growth**: page images at 500 patients/day ≈ low single-digit
   GB/week; fine for the pilot disk, needs a lifecycle policy before year one.
   Since MRD2 there are **two** copies growing at that rate — the box's own
   volume and the `pages/` prefix in the backup bucket — and neither has a
   policy. The store is append-only by design (nothing deletes a key), so this
   only ever grows; whatever policy is written has to answer a clinical
   retention question, not a storage one.
8. **Mirrored disks on the on-premise box** (decision 9). A single disk is the
   pilot's most likely total-loss event and the 15-minute backup bounds it at
   fifteen minutes, not at zero. Hardware/ops, not code, and not yet specified.
