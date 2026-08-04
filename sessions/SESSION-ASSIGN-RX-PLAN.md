# SESSION-ASSIGN-RX — Kiosk Assignment, Patient Identity, and the Doctor Workspace

Type: planning only. Nothing here is implemented.

Supersedes the first draft of this file. Written against the working tree on
2026-08-04, the pilot decisions taken in review, and the external doctor prototype
(`oncology-opd-doctor-zwh2.bolt.host`), which is **not** this repo's `/doctor`.

## Pilot decisions taken (constraints, not open questions)

1. **One kiosk, one coordinator, standing at it.** Throughput objections to a
   kiosk-side staff step do not apply. The coordinator picker goes on the kiosk's
   last screen as requested.
2. **Offline assignment is accepted debt.** When the kiosk is offline it cannot
   assign; the visit syncs unassigned and is assigned from the coordinator console.
   Recorded in §8, not designed around.
3. **Phone / UHC ID returning-patient lookup is in scope** and gets built.
4. **Lab reports joins the not-yet-available set.**
5. Everything else is derived from principle: doc 04's anti-generic clause, doc 14's
   experience principles and visual system, and the invariants in
   `CODEBASE_MEMORY.md`.

## Design-system decision

Adopt the prototype's **information architecture** — it is stronger than what
`/doctor` ships today — and re-skin it onto the **existing** semantic tokens
(`--brand: #087f68`, `--attention: #d88a18`, `--danger: #bd3434`, doc 14 §5.2).
Do not adopt its royal-blue identity. Docs 04 and 14 stay authoritative; no design
doc is rewritten by this work.

Three prototype behaviours are rejected outright and are not to be reproduced:
patient context disappearing during dictation, red flags demoted to a sidebar, and
red used for a safe forward action. See §5.

---

## 1. Kiosk: returning-patient identity, then coordinator handover

The kiosk gains two things at opposite ends of the intake. They are designed
together because they are resolved by **one** coordinator action, not two.

### 1.1 Arrival: have you been here before?

New first step, before the chief complaint, following doc 04's rural-first laws —
one decision per screen, audio-first, ≥64px targets, tap alternative always
present.

```
Screen A   "Have you visited us before?"        [ Yes ]  [ No / First time ]
Screen B   (Yes only) "Your phone number"        big numeric keypad, spoken digits
           "UHC ID (optional)"                   skippable, never a gate
```

- Both fields are optional at every point. A patient who cannot or will not answer
  proceeds exactly as today. Neither a phone number nor a UHC ID may ever gate an
  intake or a token.
- Lookup is server-side on last-10-digits phone (the existing
  `receptionist._patient_by_phone` normalisation) or on `Patient.external_id`.

**The kiosk reveals nothing on a match.** No name, no MRN, no diagnosis, no last
visit. It says only:

> "Thank you — we may already have your file. Our staff will confirm it in a moment."

This is not a softening of the requirement; it is the requirement built correctly.
A public unauthenticated terminal that prints a named oncology history to anyone who
types a ten-digit number is a disclosure incident with a queue attached. The
recognition happens; the *disclosure* moves to the staffed step three screens later,
where a human is already standing.

### 1.2 Last screen: patient token, then the coordinator strip

The last screen keeps its current job for the patient — token number in the
train-board treatment, department, red-flag instruction, all spoken. Below it, a
**coordinator strip**:

```
┌─ Patient area (unchanged) ───────────────────────────────┐
│   TOKEN 14        Medical Oncology                        │
│   Please wait for your number to be called.               │
└───────────────────────────────────────────────────────────┘
┌─ Staff ──────────────────────────────── [ Unlock ] ──────┐
│  (locked by default — shows nothing until a PIN is entered)│
└───────────────────────────────────────────────────────────┘
```

Unlocked (coordinator PIN, session held for the shift, auto-relocks on idle):

```
┌─ Staff ───────────────────────────────────────────────────┐
│  Possible existing file                                    │
│    Lakshmi Nair · 52F · MRN-48901 · last visit 12 Jun 2026 │
│    [ Same person — link ]   [ Not the same — new file ]    │
│                                                            │
│  Department   [ Medical Oncology        ▾ ]  (routed)      │
│  Doctor       [ Dr. Ananya Rao          ▾ ]  (on duty)     │
│                                                            │
│                     [ Skip ]        [ Confirm ]            │
└───────────────────────────────────────────────────────────┘
```

Rules:

- **PIN-gated.** The picker is never visible on an idle public terminal. Locked is
  the resting state; the strip shows only an `Unlock` affordance.
- **Both fields pre-filled.** Department = the routed department. Doctor = the
  single on-duty doctor for that department when there is exactly one, otherwise
  unset. In the pilot's common case this is one tap on `Confirm`.
- **Doctor list is roster-backed** (`roster.py` clinics/slot templates for today),
  not all active doctors. Assigning to a doctor who is not in clinic is the failure
  this control invites.
- **`Skip` is a legal, first-class outcome.** The visit lands unassigned in the
  department pool and is visible in the doctor console's `Unassigned` tab (§3).
  Skip is also what an offline kiosk does implicitly.
- **Identity confirmation and assignment are one action.** One `Confirm` writes both
  the patient link and the doctor assignment.
- Idle timeout returns to the patient token view and relocks.

### 1.3 What `Confirm` writes

```
POST /kiosk/{session_id}/assign      (coordinator PIN session)
  { link_patient_id?: uuid | null,        # confirm the candidate match
    reject_match?: bool,
    department_key?: str,
    doctor_id?: uuid | null }
```

- Sets `Visit.doctor_id` (already exists, `models/clinical.py:58`, currently written
  by nothing in the walk-in path).
- On a confirmed link, repoints `Visit.patient_id` to the existing patient and soft-
  deletes the throwaway walk-in patient row. **The merge is audited with both IDs and
  destroys no data.** The visit keeps the token it was issued.
- On a department change, re-homes the queue entry and **reissues the token** in the
  new department's series, announcing the new number. The board and the printed slip
  are keyed on the department series and cannot honestly display a foreign token. The
  old token is cross-referenced in the audit record.
- A department change **never** re-walks the tree, re-derives red flags, or
  re-classifies urgency. Deterministic clinical state is immutable here. Where
  `intake.tree_ref`'s department ≠ the assigned department, the doctor's card states
  it plainly: *"Intake taken as Medical Oncology; reassigned to Surgical Oncology by
  <coordinator>, 10:22."*

### 1.4 Retained by design

**One queue per department, `Queue.doctor_id` NULL** (`queue.py:165-181`). Do not
create per-doctor queues. It would fragment the per-department token series that the
unique constraint and the offline `OfflineTokenBlock` leases depend on, split the
public board into N lines, and make urgent-first ordering meaningless across a
department. Assignment is an attribute on the visit and a filter on the worklist.

---

## 2. Data model

| Change | Detail |
|---|---|
| `Patient.external_id` | nullable, indexed, `String(64)`. Optional everywhere. |
| `Patient.external_id_kind` | nullable label, configured per deployment. |
| `Visit.candidate_patient_id` | nullable FK → `patients.id`. The unconfirmed match. |
| `Visit.patient_link_state` | enum: `none` / `candidate` / `confirmed` / `rejected`. |
| `Visit.doctor_id` | **exists.** No migration; start writing it. |

Deliberately **not** ABHA. `external_id_kind` is a deployment-configured label. An
actual ABHA integration pulls in ABDM registration, consent artefacts, and linkage
obligations — a programme, not a form field. If the pilot site wants ABHA later, it
slots into the same column behind that decision.

One Alembic migration. All new columns nullable; no backfill.

---

## 3. Doctor worklist scoping

Because assignment now happens at the kiosk for essentially every patient, "assigned
to me" becomes a reliable default rather than a guess. The earlier objection is
resolved by the constraint — but the safety net stays, because `Skip` and offline
both produce unassigned visits.

`GET /doctor/day?scope=mine|unassigned|department` — default `mine`. Every response
returns counts for all three scopes so badges are truthful without a second call.

- **Mine** — `Visit.doctor_id == me`. The default view.
- **Unassigned** — `doctor_id IS NULL` in my department. **The count badge is
  visible even when the tab is not open**, and a non-zero count with waiting patients
  renders as an attention state, not a quiet number. An unassigned waiting patient is
  never invisible to every doctor in the department.
- **Department** — everything, for cover and handover.

Add **"Take this patient"** on an unassigned or another doctor's row, setting
`Visit.doctor_id = me`, audited. Cover is routine in an OPD; making it require a
coordinator turns one doctor's absence into a stalled line.

**Authorization stays at department scope.** `doctor.patient_card` and
`dictation.assert_visit_scope` must not narrow to the assigned doctor. A covering
colleague, a lab re-queue picked up by whoever is free, and a second opinion all need
to open the card. Filtering a *list* is a UX decision; narrowing *access* is a
clinical-continuity decision this codebase already made the other way, on the record.

Queue order stays the queue's — urgent first, identical to board and coordinator.
Scoping filters rows; it never reorders them.

---

## 4. The doctor workspace: derived information architecture

Single job, restated per doc 04 §5: **safely complete the consultation in front of
you without losing sight of what makes this patient risky or who is next.**

The three most important elements, in order: (1) who this is and what is dangerous
about them, (2) the current step of the consult, (3) the next patient.

### 4.1 Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ top bar   doctor · department · connection · Call next · account      │
├──────────────┬───────────────────────────────────────────────────────┤
│ worklist     │  ╔═ CONTEXT SPINE (sticky, never unmounts) ═════════╗ │
│ rail         │  ║ Lakshmi Nair  52F  MRN-48901  TOKEN 14  In room  ║ │
│ 300–340px    │  ║ Stage IIIA · Invasive ductal carcinoma           ║ │
│              │  ║ ⚠ ALLERGY penicillin — anaphylaxis               ║ │
│ Mine (3)     │  ║ ⚠ 2 red flags   <label · instruction · source>   ║ │
│ Unassigned 1 │  ╚══════════════════════════════════════════════════╝ │
│ Department   │                                                        │
│              │  Overview | Intake answers | History | Consult  ·  ⌄  │
│ [rows]       │  ─────────────────────────────────────────────────────│
│              │  (work area)                                          │
└──────────────┴───────────────────────────────────────────────────────┘
```

### 4.2 The context spine — the answer to "stay in context"

This is the core of the design and the direct fix for the prototype's worst
behaviour. The spine is **sticky, and it never unmounts for any state of the
consult** — not for dictation, not for the note, not for the prescription.

It carries exactly four things and refuses more:

1. **Identity** — name, age/sex, MRN, **token number** (train-board treatment,
   tabular numerals; this is also what reconnects the console to the board and the
   coordinator), visit state, language.
2. **Diagnosis + stage** — one line, never truncated.
3. **Allergies** — severe allergies are persistent, not one tab away. The prototype
   surfaces penicillin anaphylaxis only as a banner at prescribing time; by then the
   doctor has already composed the plan.
4. **Red flags** — label, instruction, and provenance, **above all routine content**
   (doc 14 principle 1), never in a sidebar, never colour-only.

Nothing else earns a permanent place. Vitals, history, and summary live in the work
area below.

**Red flags render only what is present.** The prototype lists "Morning stiffness
>1h threshold not met" and "No bone pain" in danger red — reassuring *absences*
styled as danger, which inverts the colour. Ruled-out criteria belong in the
Overview's clinical reasoning, in neutral treatment. If there are no red flags, the
strip says so plainly in a calm state; it does not disappear, because absence of the
strip and absence of flags must not look identical.

### 4.3 Tabs

Four working tabs. Not five, not seven.

- **Overview** (default) — the 20-second read: chief concern, the patient's own
  words in prose, symptom table, vitals, patient's questions, unclear items.
  Unframed sections; **no nested cards** (doc 14 principle 5 — the prototype nests
  three deep).
  - **Vitals get clinical emphasis**, not six identical tiles. Values outside range
    are marked with text and shape, never colour alone; height and weight are
    demoted to a secondary line. A screen that renders SpO₂ 94% and height 158cm at
    identical weight has stopped being clinical.
  - **No confidence percentage.** Replace the prototype's "88% confidence" with
    provenance a doctor can act on: source (patient / caregiver), tier, language,
    completion time. A number nobody can calibrate is false precision.
- **Intake answers** — the answers as asked, flagged ones marked.
- **History** — past visits, timeline, conditions, family/social. Allergies appear
  here in full *and* in the spine.
- **Consult** — capture → review → sign → prescription (§5).

### 4.4 Not yet available — one quiet entry, not four tabs

Imaging, **Lab reports**, AI Research, and NCCN Guidelines are all not-yet-built.
Rendering them as four tabs would make four of eight tabs dead and push the two
tabs carrying real clinical content to compete with placeholders.

Instead: a single muted trailing item in the tab row.

```
Overview | Intake answers | History | Consult          ⌄ Coming soon (4)
```

Opening it shows a small quiet panel — one line each, no mock content:

> **Imaging** — scans and radiology reports in the console.
> **Lab reports** — results delivered back into the visit.
> **AI Research** — evidence lookup for this patient's presentation.
> **NCCN Guidelines** — guideline reference at the point of decision.

Constraints:

- **Feature-flagged.** A pilot build must be able to hide the entry entirely.
- **No mock clinical content behind any of them**, at any fidelity. A greyed
  "NCCN Guidelines · coming soon" is fine; a sample guideline excerpt is not.
- Items are not focusable as navigation and do not hover-highlight like tabs, so a
  placeholder is never mistaken for a broken feature.
- **Lab reports carries a caveat.** This system already has a `lab_requeue` queue
  state, so labs are a live workflow. A doctor will tap it expecting results. Its
  line must say what it will do and that it is not yet live — this is the one of the
  four with a real chance of being misread as broken.
- **Do not advertise dictation as upcoming.** The prototype's landing card offers
  "EMR Voice Transcription — upcoming". This repo ships it. A card calling it future
  teaches doctors not to look for it. It is removed, not restyled.

### 4.5 Removed from the prototype

- The empty hero and the permanent numbered "Review / Dictate / Prescribe" tuition
  cards (doc 14 principle 8 — operational instructions are not permanent page copy).
  The no-patient-selected state becomes a quiet empty state on the work area with the
  worklist already carrying the doctor's attention.
- The dotted background texture and the blue identity.
- Diagnosis truncation in the worklist rail. The rail sheds a competing chip instead;
  the diagnosis is the field that must survive.

---

## 5. The consult: capture, review, sign, prescribe

### 5.1 What already exists — most of this is not new work

| Requirement | Where it lives today |
|---|---|
| Transcript captured and persisted | `POST /dictation/visits/{visit_id}` → `Dictation.transcript` |
| LLM maps transcript to fields | `POST /dictation/{id}/map` → `structured.mapped` |
| **Mapping fails → fields editable** | `mapping_error` + `PATCH /dictation/{id}` (`routes/dictation.py:276`) |
| Corrections tracked against model output | `structured.fields` + `structured.edits`, diffed against frozen `structured.mapped` |
| Prescription generated | `dictation.sign` → `prescription.generate` |
| Everything persisted and audited | `Dictation`, `Prescription.meds` snapshot, append-only `audit_log` |

The "if the LLM fails to map, make the fields editable so the doctor can fill them
in" requirement is **existing backend behaviour**. The work is surfacing it.

### 5.2 The Consult tab

A visible four-step sequence, with the context spine present throughout:

```
①  Capture        ②  Review        ③  Sign        ④  Prescription
```

**① Capture** — three entries, weighted correctly:

- **Dictate** — primary, brand green.
- **Type note** — secondary, equal legitimacy, plain.
- **Conclude without a system note** — tertiary, in an overflow menu. The prototype
  styles this escape hatch in marigold, making the only path that produces no record
  the most eye-catching control on the screen. That inversion is reversed.

Recording state fixes two prototype faults:

- The waveform must be **driven by the live analyser node**. A decorative evenly-
  spaced bar pattern is a false claim that audio is being captured. If a real level
  meter is not available, show an honest elapsed timer and a recording indicator and
  nothing else.
- **`Stop & Transcribe` is brand green, not red.** Red is reserved for clinical
  danger and destruction. Stopping to transcribe is the expected safe progression;
  painting it red trains the doctor to ignore red where it matters.

**② Review** — transcript on one side, structured fields on the other, both always
editable. On mapping failure: a plain banner — *"We could not structure this note.
Your recording is saved. Fill the fields below and continue."* — with the transcript
prominent and the fields empty and editable. No dead end, no lost recording.

**③ Sign** — explicit, timestamped, immutable, and not represented by green alone.
`blocking_meds` still refuses the signature while an unrecognised or unsaid drug is
unacknowledged. A flagged medication never becomes visually "resolved" after
acknowledgement; it moves from danger to acknowledged-attention.

**④ Prescription** — the prototype's document preview (screens 7–8) is the best thing
in it and is largely adopted, with four corrections:

- **The medicine name is the strongest text in each row**, and dose/route are not
  compressed into low-contrast secondary copy (doc 14 §7.2). The uppercase
  `DOSE:` `ROUTE:` `FREQ:` labels stop consuming as much ink as their values.
- **Print is not available before approval.** The prototype offers Print at equal
  weight beside an unapproved AI draft. Printing an unapproved draft prescription is
  a real hazard.
- **The dominant button is the one you can press.** The prototype styles
  "Complete Consultation" as primary-but-disabled while "Review & Approve" — the
  action that unlocks it — is a ghost button. One dominant action per state.
- **Row delete gets an accessible name, tooltip, and confirmation.**
- Drop the `Symptomatic` / `Supportive` pills; a pill with no action attached is
  decoration (doc 14 principle 7).

### 5.3 The two genuine gaps

**a. Dictation is currently mandatory for a prescription.** `prescription.generate`
takes a `Dictation` and refuses an unmapped one (`prescription.py:263-285`).

Implement **"Type note"** as an empty-transcript dictation draft that skips `/map`
and opens the same editable field set. Same record, same `fields`/`edits` history,
same signature boundary, same `blocking_meds` refusal, same audit trail. **Do not add
a second prescription-creation path** — a parallel writer around the signature
boundary is how the drug-safety validation gets bypassed two quarters from now.

**b. There is no recorded "manual / off-system prescription" conclusion.**

```
POST /doctor/visits/{visit_id}/conclude
  { rx_mode: "system" | "external_manual" | "none", note?: str }
```

- `external_manual` records that the doctor wrote a paper or other-system script.
- Moves the queue entry to `done` **through the existing `queue.set_state` verb** —
  no second state machine.
- The confirmation dialog says what is actually lost, not the prototype's vague
  "won't capture this visit findings":

  > No consult note and no digital prescription will be recorded for this visit.
  > The patient's app and records will not show today's medicines, the pharmacy
  > will have no digital copy, and follow-up reminders cannot be generated.
  > Continue?

  Vague warnings get clicked through. This one is meant to make a doctor pause.
- The conclusion is itself a clinical record: written, audited, not a blank visit.

### 5.4 Retained by design

- **`generate` returning `None` for an advice-only note.** A consult ending in advice
  and a follow-up date is complete. "Generate prescription" must never fabricate a
  med list to fill a form.
- **`mapped` frozen, `fields` mutable.** The diff is the evidence the doctor
  reviewed the model's output.
- **Dose times are never inferred** from incomplete instructions.
- **Audio is not stored.** `Dictation.audio_url` stays unpopulated for the pilot —
  transcribe and discard, stated in the consent copy. Retaining consult audio is a
  privacy commitment with retention, access, subject-request, and breach
  consequences; it is a separate decision with a policy attached, not a storage
  default.

---

## 6. Cross-cutting corrections

- **Colour discipline.** Red = clinical danger, destructive confirmation, real
  failure. Marigold = attention/pending/priority. Green = safe expected progress.
  The prototype currently spends red on a stop button and on absent red flags, and
  marigold on the lossy manual-Rx path and on routine SOAP labels. Four meanings
  across two colours teaches staff to stop reading colour.
- **Status is text plus shape or icon, never colour alone** (doc 14 principle 4).
- **One date treatment.** The prototype shows "Tuesday 4 August", "04 Aug 2026", and
  "8770h ago" — three formats, one wrong. Absolute date-time everywhere clinical;
  relative time only where recency is the point, and never both in one line.
- **Patient/visit identity assertion.** The prototype renders a stage IV NSCLC
  pembrolizumab note and prescription under a stage IIIA breast-cancer patient. The
  console must bind every consult write to the visit ID in the spine and fail loudly
  on mismatch rather than render. This is a guard, not a style rule.
- **Tabular numerals** for tokens, times, doses, quantities, counts.
- **`lucide-react`** as the single staff icon library; no emoji on staff surfaces.
- Every mutation shows idle / pending / success / refused / retry **without moving
  the layout** (fixed control heights).

---

## 7. Sequencing

**Session A — Identity and assignment** *(kiosk + coordinator + backend)*
- Migration: `Patient.external_id`, `external_id_kind`, `Visit.candidate_patient_id`,
  `Visit.patient_link_state`.
- Kiosk arrival-intent screens; optional phone / UHC ID; non-revealing match.
- PIN-gated coordinator strip on the last screen; `POST /kiosk/{id}/assign`;
  roster-backed doctor list; link-confirm + assign in one action.
- Department change → queue re-home + token reissue + provenance line on the card.
- Coordinator console gains the same assign action for skipped and offline arrivals.

  *Acceptance:* an intake completes with no phone and no UHC ID; a matched patient
  sees no identifying data on the kiosk; a confirmed link repoints the visit and
  audits both IDs with no data loss; `Skip` yields an unassigned visit that appears
  in the doctor console's Unassigned tab; the locked strip reveals nothing without a
  PIN; the public board is unchanged.

**Session B — Doctor workspace IA**
- `GET /doctor/day?scope=`, three scopes, always-visible unassigned count,
  "Take this patient".
- Context spine (identity + token + diagnosis + allergies + red flags), sticky and
  persistent through every consult state.
- Four working tabs; single feature-flagged "Coming soon (4)" entry incl. Lab
  reports; removal of the hero, tuition cards, and the EMR-voice upcoming card.
- Vitals emphasis; provenance replaces the confidence score; nesting removed.
- Re-skin to the existing green/marigold token set.

  *Acceptance:* an unassigned waiting patient is visible and countable from every
  doctor login in the department; the spine's red flags and allergy remain on screen
  in every consult state including active dictation; at 1280×800 the spine, the
  current step, and the primary action are visible without scrolling; at 200% zoom
  nothing truncates or overlaps; no placeholder renders mock clinical content.

**Session C — Consult and prescription paths**
- "Type note" → empty-transcript draft, `/map` skipped, editable fields.
- `mapping_error` surfaced as a recoverable editable state with the transcript kept.
- Live-analyser waveform (or an honest timer); green `Stop & Transcribe`.
- `POST /doctor/visits/{id}/conclude` with `rx_mode` and the honest dialog; `done`
  via `set_state`.
- Prescription preview corrections: name dominance, print gated behind approval,
  dominant-action fix, delete confirmation, pills removed.

  *Acceptance:* a prescription is produced with no speech at all; a forced mapping
  failure keeps the transcript and still reaches a signature; `blocking_meds` refuses
  the signature on every path including typed; an `external_manual` conclusion leaves
  an auditable record and a `done` queue entry; print is unreachable before approval.

---

## 8. Debt register (accepted, tracked, not designed around)

1. **Offline kiosk cannot assign.** No roster, no doctor list. The visit syncs
   unassigned; the coordinator assigns from the console afterwards. The doctor
   console's Unassigned badge is the compensating control, which is why it is not
   optional. Revisit if a second kiosk or a second coordinator appears.
2. **Offline kiosk cannot match a returning patient.** Same reason. The visit syncs
   as a new patient with `patient_link_state = none`; linking is a later staff
   action. Duplicate patient rows are the expected outcome and must be mergeable
   without data loss.
3. **PIN on a public terminal** is a pilot-grade control. Adequate for one supervised
   kiosk with one coordinator; not adequate for an unattended terminal. Auto-relock
   and idle timeout are mandatory, not optional.
4. **Prescription PDF** is still HTML through the browser; a real authenticated PDF
   sharing one renderer with the HTML remains pre-existing debt
   (`CODEBASE_MEMORY.md`), not created here.
5. **Whether a returning patient gets a shortened intake tree is a clinical
   decision**, expressed as tree data and walked deterministically. This plan does
   not decide it, and no shortening ships without oncologist review.

---

## 9. Invariants this plan does not touch

Red flags, tree traversal, check-in grading, and escalation remain deterministic;
assignment never influences priority. The walker position stays derived from
persisted answers. Clinical writes stay structurally audited and the audit log stays
append-only. Dictation never silently corrects or invents a drug. A prescription
exists only after doctor review and signature, and is snapshotted. A missing provider
produces a visible degraded state, never a fabricated success.
