# 15 - Kiosk Intake And Prescription Hardening

Status: approved corrective build specification
Priority: immediate next build
Parent branch: `uiux-enterprise-revamp`
Build branch: `uiux-kiosk-rx-hardening`

This document is the implementation contract for the operator feedback received
after the enterprise UI build. The existing branch must not merge to `main` until
this corrective build passes local, Omen, and physical-device acceptance.

Read, in order:

1. `HANDOFF.md`
2. `CODEBASE_MEMORY.md`
3. `docs/04-UIUX-GUIDE.md`
4. `docs/14-ENTERPRISE-UIUX-REVAMP.md`
5. this document
6. `sessions/SESSION-UX2-PLAN.md`

## 1. Outcome

Deliver two production-ready workflows:

- A kiosk/tablet intake that asks for the patient's name, keeps its controls aligned,
  and builds a calm, truthful intake summary as answers are collected.
- A clean prescription document with a real hospital letterhead, reliable A4 layout,
  authenticated PDF download, and print support.

The result must preserve all deterministic routing, tree traversal, red-flag, queue,
dictation, signature, audit, and offline-sync behavior.

## 2. Merge And Branch Decision

Do not merge `uiux-enterprise-revamp` to `main` yet. The branch is technically green,
but its kiosk intake and prescription workflows have not passed operator acceptance.

Start the corrective build from the reviewed UI branch:

```bash
git fetch origin
git switch uiux-enterprise-revamp
git pull --ff-only origin uiux-enterprise-revamp
git switch -c uiux-kiosk-rx-hardening
git push -u origin uiux-kiosk-rx-hardening
```

After the corrective branch passes the gates in section 9:

1. merge it into `uiux-enterprise-revamp`;
2. repeat the Omen smoke matrix on that exact commit;
3. merge the accepted enterprise branch to `main`;
4. tag or record the deployed commit in the session log.

Never patch these changes manually on Omen. Deploy a committed branch.

## 3. Patient Name Contract

### Required behavior

- Add a dedicated `name` step after caregiver selection and before chief complaint.
- If a caregiver is answering, ask explicitly for the **patient's** name.
- Accept voice or typed input and preserve the patient's original Unicode script.
- Do not translate, title-case, transliterate, or infer a name.
- The new kiosk UI requires a non-empty name. Trim surrounding whitespace, reject
  control characters, and cap the stored value at the existing model limit of 200.
- Do not add phone collection, identity matching, or registration search in this
  build.
- Idle reset, completion reset, logout, and a new intake must clear the name and all
  locally rendered summary data.

### Compatibility rule

Add `patient_name` as an optional request field during the rollout window. New
clients must send it; old clients may continue to create `Walk-in patient`. This
keeps an API-first or web-first rolling deployment from breaking active kiosks.
Backend tests must prove both paths. A later release may make the API field required
after every deployed client has moved.

### End-to-end files

Update all of these contracts together:

- `web/app/(kiosk)/kiosk/KioskApp.tsx`
- `web/app/(kiosk)/kiosk/_lib/api.ts`
- `web/app/(kiosk)/kiosk/_lib/offline/flow.ts`
- `web/app/(kiosk)/kiosk/_lib/offline/local.ts`
- `web/app/(kiosk)/kiosk/_lib/offline/db.ts`
- `web/app/(kiosk)/kiosk/_lib/offline/sync.ts`
- `backend/app/routes/kiosk.py`
- `backend/app/kiosk.py`
- `backend/app/offline.py`

Both online creation and offline reconciliation must store the supplied value in
`Patient.name`. No database migration is expected because that field already exists.

### Offline privacy

The current IndexedDB sync path retains successfully synced intake payloads. A name
makes that retained data directly identifying. After a successful sync, delete the
local intake or redact its patient name and answers. Keep failed/rejected rows only
for an explicit reconciliation period and expose their status without displaying
patient data on an unattended kiosk.

## 4. Kiosk And Android-Tablet Layout

### Landscape composition

At 1280x800 and 1024x768, use a stable two-column application frame:

- summary rail: approximately 280-320 px;
- active question area: the remaining width;
- Dhara/audio status belongs in the rail header and must not become a third column;
- the primary action must remain visible without page scrolling.

The summary rail contains only collected facts:

- Patient
- Main concern
- Department, after routing is resolved
- Duration, when answered
- Symptoms, as short answer rows

Use placeholders such as `Not answered yet`; never invent or model-summarize content.
The final read-back remains the authoritative confirmation step.

### Portrait composition

At 800x1280 and comparable Android portrait sizes:

- convert the rail to a compact summary strip above the question;
- allow an accessible expand/collapse control for the full summary;
- keep the active question and its primary action in the first viewport;
- never place a scrollable card inside a scrollable page.

### Control geometry

- Replace `auto-fit` and wrapping button rows with deterministic grids.
- Two choices use two equal columns in landscape and one column when space requires.
- Three or four choices use a stable 2x2 grid in landscape.
- Five choices use two columns with the final choice spanning both columns.
- Preserve at least 64 px touch targets.
- Give icons a fixed column and let labels wrap independently.
- Apply `min-width: 0`, normal line wrapping, and `overflow-wrap: anywhere` to labels.
- Do not truncate patient-facing labels or reduce font size to hide overflow.
- Loading, selected, focus, hover, error, and disabled states must not resize controls.

### Name input

The name screen needs a large text field, microphone action, clear action, and one
obvious continue action. It must work with the Android on-screen keyboard without the
continue action being hidden. Voice transcription must be editable before continue.

## 5. Truthful Live Intake Summary

Do not derive meaning from English question text or fragile node-id substrings.

Add an optional presentation-only `summary_role` to the canonical tree node schema.
Allowed values:

- `primary_symptom`
- `duration`
- `severity`
- `symptom_detail`
- `context`

Update the Python node schema/parser/serializer, canonical TypeScript type,
conformance fixtures, seed trees, and admin tree editor. The admin editor must
preserve this field when a tree is loaded, edited, saved, and published.

This metadata must never affect:

- the next node;
- red-flag evaluation;
- queue priority;
- department routing;
- clinical summary generation.

The kiosk stores the displayed question and answer at answer time. It uses
`summary_role` only to place that answer in the side summary. Render option labels,
numbers with units, and other accepted values exactly as shown to the patient.
Show at most three symptom rows plus a count for additional answers. Language changes
must not relabel a prior answer incorrectly.

Tree content changes remain versioned. Update drafts, run validation and conformance,
then follow the existing explicit publish process; do not silently mutate a published
clinical tree in place.

## 6. Prescription Document And PDF

### One rendering source

Keep `backend/app/rx_sheets.py` as the source of document semantics. Refactor only as
needed so HTML preview and PDF are produced from the same data and layout. Do not
create a second medication-rendering implementation in the web app.

### Letterhead

The first page must clearly show:

- hospital name;
- city/district when present;
- department;
- prescribing doctor's name, qualification, and registration number when present;
- prescription/document identifier and date.

Use only stored or explicitly configured data. Do not invent an address, phone
number, accreditation, logo, doctor credential, or legal statement. Support an
approved configured logo, but retain a polished text letterhead when none is set.

Then show patient identity, MRN where present, age/sex where present, diagnosis,
medications, instructions, safety notices, signature, and a restrained footer.
Clinical and patient copies remain distinct.

### PDF contract

Retain the current authenticated HTML print endpoint for compatibility and add:

```text
GET /prescriptions/{id}/pdf?copy=clinical|patient&lang=en|hi|mr|te
```

Return:

- `Content-Type: application/pdf`;
- an attachment `Content-Disposition` with a safe filename;
- the same authorization and doctor ownership rules as existing prescription reads.

Generate the PDF server-side from the shared HTML using a pinned, proven HTML-to-PDF
renderer with Pango/Indic shaping support. Add the required native libraries and
Noto Devanagari/Telugu fonts to the backend image. Verify exact Debian package names
against the image during implementation and run `make preflight`.

The web prescription panel must expose three clear actions:

- Preview
- Download PDF
- Print

Fetch protected content with the existing bearer token and use a blob URL. Never put
the access token in a query string. Revoke blob URLs after use.

### Page quality

- A4 margins and printable contrast.
- Repeating medication table headers.
- No medication row or signature block split incoherently across pages.
- No clipped text at one, two, or three-plus pages.
- Patient copy uses large, readable instructions and retains explicit flagged-drug
  warnings.
- Complete and verify Marathi and Telugu document strings and fonts; do not claim
  four-language completion while the sheet contains English/Hindi-only text.

## 7. Implementation Sequence

Use narrow commits in this order:

1. `feat(kiosk): carry patient name through online and offline intake`
2. `feat(trees): add presentation-only intake summary roles`
3. `feat(kiosk): build responsive live intake summary`
4. `fix(kiosk): stabilize multilingual tablet control layout`
5. `feat(rx): add shared letterhead and authenticated PDF output`
6. `test(ux): add kiosk tablet and prescription document coverage`
7. `docs(session): close kiosk and prescription hardening build`

Do not mix queue-state, clinical-rule, authentication, appointment, or provider
changes into this branch.

## 8. Required Tests

### Backend

- Online start stores a Unicode patient name.
- Offline sync stores the same name.
- An old client without `patient_name` follows the documented fallback.
- Name normalization rejects control characters and respects the model limit.
- `summary_role` round-trips through parser, canonical JSON, admin save, and publish.
- Existing tree validation, golden traces, branching, and red flags remain unchanged.
- PDF route enforces RBAC/ownership.
- PDF response has correct headers and begins with `%PDF`.
- Extracted PDF text includes patient, drug, doctor registration, and safety warning.
- One-page and multi-page fixtures have expected page counts.

### Browser

- Full intake: language, caregiver, name, complaint, questions, read-back, token.
- Name appears in the live rail and in the resulting doctor-facing patient record.
- Offline intake preserves name through sync and then purges/redacts local PII.
- Summary values populate only after their corresponding answer.
- Back/reset/idle behavior cannot leak the previous patient's facts.
- Long labels in en/hi/mr/te do not overflow or overlap.
- Download produces an `application/pdf` file; print opens the protected document.

### Viewports

Capture and review at minimum:

- 1280x800 landscape kiosk;
- 1024x768 Android tablet landscape;
- 800x1280 Android tablet portrait;
- each of the above at browser 100 percent and 200 percent text scaling.

At each viewport assert:

- no horizontal overflow;
- no overlapping bounding boxes;
- no clipped labels;
- the active question and primary action are visible;
- keyboard focus is visible;
- the on-screen keyboard path remains operable.

## 9. Acceptance And Release Gate

Required automated evidence:

```bash
make test
make lang-qa
make preflight
cd web && npm run build
cd web && npm run e2e
```

Required manual evidence:

1. Complete an online intake on the physical kiosk in all four languages.
2. Complete an offline intake, restore connectivity, and verify reconciliation.
3. Exercise landscape and portrait on the target Android tablet.
4. Download and print both prescription copies with short and long medication lists.
5. Confirm the real printer output has no clipping and Indic text is shaped correctly.
6. Confirm a previous patient's name and answers cannot be recovered from the idle UI.
7. Record screenshots, printed-page photographs, commit SHA, and operator acceptance
   in the closing session log.

Only then is it a good idea to merge the UI feature line to `main`.
