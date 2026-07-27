# 14 - Enterprise UI/UX Revamp

Status: implemented on `uiux-enterprise-revamp`, corrective acceptance pending
Priority: superseded for immediate work by doc 15
Primary surfaces: `/`, `/doctor`, `/coordinator`, `/board`, `/admin`, `/kiosk`

This document turns the existing UI/UX direction into an implementation contract.
It is intentionally detailed enough for a fresh GPT-5.5 or Claude Opus session to
execute without redesigning the product, changing clinical behavior, or inventing
data the backend does not provide.

Operator review on 2026-07-27 did not accept the kiosk intake or prescription
document as complete. Do not merge this feature line to `main` until
`docs/15-KIOSK-INTAKE-AND-PRESCRIPTION-HARDENING.md` is implemented and accepted.

Read this document together with:

- `docs/04-UIUX-GUIDE.md` for patient-safety and multilingual rules.
- `docs/03-FEATURE-SPEC.md` for workflow intent.
- `CODEBASE_MEMORY.md` for invariants and pathway maturity.
- The relevant web E2E test before changing a production surface.

Where this document and doc 04 differ on staff styling, this document is the newer
visual authority. Doc 04 remains authoritative for patient-surface accessibility,
voice, language, reassurance, and interruption safety.

---

## 1. Objective

Create one coherent, highly legible enterprise interface across the full product:

- A landing page that feels like a hospital operations gateway, not a developer
  directory or marketing page.
- A doctor console optimized for a real consultation: identify risk, understand the
  story, act on the queue, dictate, verify, sign, and see the resulting prescription.
- A coordinator console that answers "what needs attention now?" before presenting
  the queue controls.
- A public board readable at distance without disclosing clinical details.
- An admin console that can support repeated operational work without becoming one
  very long page under eight tiny tabs.
- A kiosk that remains rural-first, multilingual, voice-forward, and usable on the
  real 10-11 inch landscape device.

The revamp is successful when every production route looks like part of the same
system, all existing workflows still work, and no clinical or operational meaning
has been weakened for visual neatness.

This is not a cosmetic reskin. It is an information-hierarchy and interaction
overhaul with strict behavior preservation.

---

## 2. Current Baseline And Problems To Correct

### Landing

- `/` is a developer directory with session numbers and implementation language.
- It does not establish the product, hospital context, live pathways, or role entry.
- The existing `/mocks` route is useful direction, not production-ready truth.

### Doctor

- The clinical priority ordering is strong: red flags, concern, symptoms, provenance,
  dictation validation, signature, and immutable prescription generation.
- The queue rail and patient story compete for space rather than forming one stable
  consultation workspace.
- All queue actions are displayed together even when only one is the sensible next
  action.
- Dictation replaces the patient card, making cross-reference harder during review.
- After signing, the prescription is visually subordinate, placed below a long note,
  and easy to miss. Medication, dose, route, schedule, duration, safety status,
  print, and delivery require a clear document-like reading order.
- The current screenshot can place the sticky application bar across the clinical
  workspace during full-page capture. Sticky layers and scroll ownership are not
  consistently defined.

### Coordinator

- Department cards provide controls but no top-level operational picture.
- Useful facts available from the current payload are not summarized: total waiting,
  urgent patients, active consultations, called patients, and department load.
- Longest wait, arrivals, room idleness, and throughput are not in the current wire
  contract. They must not be fabricated.
- Queue rows are readable but action-heavy; the primary next transition should be
  visually dominant and reorder controls should be quiet.

### Board

- The board's distance-first concept is sound.
- It exposes `priority_reason` / `now_serving_reason` on a public screen. Replace
  clinical reasons with a neutral "Priority assistance" treatment.
- Announcement readiness, muted audio, reconnecting, and downtime need explicit,
  non-overlapping states.

### Admin

- Eight horizontal tabs and long, mixed-purpose pages do not scale.
- Controls and tables are visually consistent at a basic level but lack a shared
  application shell, page hierarchy, sticky action regions, and predictable form
  patterns.
- Important states such as draft/live, configured/unavailable, destructive changes,
  import preview, and applied result need standard components.
- Component-local injected CSS makes global refinement expensive and encourages
  surface drift.

### Kiosk

- The kiosk has the strongest purpose-specific foundation and should not be
  reimagined.
- It still needs a production polish pass at the actual Omen viewport, 200 percent
  text scale, all four languages, bright-room contrast, offline/downtime states,
  audio status, and token/print completion.

---

## 3. Non-Negotiable Preservation Contract

The visual overhaul must not alter the system's clinical or operational contracts.

### Do not change during a UI-only session

- Backend route paths, request bodies, response meanings, or queue state names.
- Tree traversal, red-flag evaluation, check-in grading, routing, priority order, or
  offline token allocation.
- Dictation medication validation, acknowledgement rules, signature behavior, or
  prescription creation.
- The rule that no prescription creation verb exists in the web client: signing the
  note is what creates it.
- `slots_known`: when false, the UI must not infer morning/afternoon/night icons.
- Audit behavior, authentication behavior, provider fallback, or channel gates.
- Kiosk service-worker, Dexie, offline walker, reconciliation, print bytes, STT, or
  TTS behavior.
- Public-board polling/WebSocket behavior.

### Allowed

- Rearrange existing information and controls.
- Add pure presentation adapters/selectors derived from existing typed payloads.
- Add ARIA labels, semantic regions, stable `data-testid` attributes, and keyboard
  focus management.
- Add shared visual components and CSS modules.
- Add an optional, backward-compatible summary field or read-only endpoint only in a
  session that explicitly includes backend work, tests the old response behavior,
  and has no migration. Never add backend work merely to fill a decorative metric.

### Test-selector rule

Existing E2E tests encode behavioral evidence. Do not delete a `data-testid` used by
tests. Class selectors may be updated only alongside tests that continue to assert
the same behavior. A screenshot is not a substitute for an interaction assertion.

### Data-truth rule

Never render mock counts, percentages, trends, timestamps, room states, or SLA
claims on production routes. If data is unavailable:

- omit the metric,
- label an honest unavailable state, or
- schedule a small additive backend contract with tests.

The `/mocks` route may retain illustrative data and must remain clearly isolated.

---

## 4. Experience Principles

1. **Clinical risk outranks visual symmetry.** Red flags remain the first content in
   the doctor view even when they make rows uneven.
2. **One dominant action per state.** Secondary actions exist, but never compete
   equally with the expected next transition.
3. **Information density is earned.** Staff screens can be dense; patient screens
   remain spacious. Neither uses giant marketing typography.
4. **Status is text plus shape or icon, never color alone.**
5. **No nested-card architecture.** Page sections are unframed. Cards are reserved
   for repeated records, modals, and genuinely bounded tools.
6. **No decorative gradients, orbs, glass effects, or oversized empty heroes.**
7. **No pill for every piece of text.** Pills are reserved for status, priority, and
   compact filters.
8. **No operational instructions as permanent page copy.** Put contextual help in
   tooltips, empty states, inline validation, or a help drawer.
9. **Every mutation has visible lifecycle.** Idle, pending, success, refused, and
   retry states must be distinguishable without moving the layout.
10. **Real hardware wins.** The final arbiter for kiosk and board is the target
    screen, not a laptop screenshot.

---

## 5. Visual System

### 5.1 Brand character

The product should feel:

- composed, clinical, and operational;
- modern without looking like generic SaaS;
- warm enough for oncology care but never ornamental;
- credible in a government hospital and polished enough for a private enterprise.

The visual signature is a deep charcoal staff shell, clean white work surfaces,
clinic green for safe primary action, marigold for attention, and disciplined red
for clinical danger.

### 5.2 Color roles

Introduce semantic tokens in `web/app/globals.css`; retain compatibility aliases for
the current kiosk until its dedicated migration:

```css
--shell: #17211f;
--shell-raised: #22302d;
--canvas: #f4f6f5;
--surface: #ffffff;
--surface-subtle: #f8faf9;
--line: #d8dfdc;
--line-strong: #b9c6c1;
--text: #17211f;
--text-muted: #5e6d68;
--text-faint: #7d8a86;

--brand: #087f68;
--brand-hover: #066754;
--brand-soft: #e1f1ec;
--attention: #d88a18;
--attention-soft: #fff1d6;
--danger: #bd3434;
--danger-soft: #fdeaea;
--info: #316a8a;
--info-soft: #e9f2f7;
--success: #267257;
```

Rules:

- Body copy uses `--text`, not muted gray.
- Red is reserved for clinical danger, destructive confirmation, and real failure.
- Marigold means attention/pending/priority, not decoration.
- Green primary buttons represent safe expected progress, not arbitrary emphasis.
- Public-board contrast must meet AAA for primary numerals.
- Never use clinical priority color as the only differentiator.

### 5.3 Typography

- Keep the repository's self-hosted Noto family and Indic fallbacks. Do not add a
  remote font dependency; the Omen and kiosk must work without the internet.
- Staff body: 14-16px, line-height 1.45-1.6.
- Staff page title: 24-28px. Panel title: 16-20px. Compact label: 12-13px.
- Kiosk sizes continue to follow doc 04 and the current patient scale.
- Use tabular numerals for tokens, time, dosage quantities, cost, and counts.
- Letter spacing is `0` for ordinary text. Limited positive tracking is allowed
  only for short uppercase metadata labels.
- Never scale text with viewport width. Use explicit responsive type steps.

### 5.4 Shape, elevation, and spacing

Staff surfaces:

```css
--radius-control: 6px;
--radius-panel: 8px;
--radius-dialog: 8px;
--shadow-popover: 0 12px 28px rgba(23, 33, 31, 0.14);
--shadow-focus: 0 0 0 3px rgba(8, 127, 104, 0.20);
```

- Default spacing scale: 4, 8, 12, 16, 24, 32, 48.
- Staff controls are 36-40px high; high-frequency primary controls may be 44px.
- Patient controls remain at least 64px.
- Most staff sections use borders and spacing, not shadows.
- Stable controls must have fixed height/min-width so pending labels do not move
  adjacent content.

### 5.5 Icons

- Add `lucide-react` as the single staff icon library when S-UX.1 starts.
- Use icons for navigation, print, download, refresh, microphone, volume, overflow,
  search, close, chevrons, status, and reorder controls.
- Do not use emoji or hand-drawn glyphs on staff or board surfaces.
- Icon-only controls require an accessible name and tooltip.
- Keep the kiosk's purpose-built clinical icon set where it communicates patient
  choices; do not replace it with abstract line icons.

---

## 6. Shared Frontend Architecture

Create a small shared UI layer. Do not introduce a broad component framework.

```text
web/app/_components/ui/
  AppShell.tsx
  Button.tsx
  IconButton.tsx
  Badge.tsx
  StatusBadge.tsx
  Tabs.tsx
  SegmentedControl.tsx
  PageHeader.tsx
  MetricStrip.tsx
  DataTable.tsx
  EmptyState.tsx
  InlineNotice.tsx
  Dialog.tsx
  Drawer.tsx
  ToastRegion.tsx
  Skeleton.tsx

web/app/_styles/
  foundations.css
  staff-shell.module.css
  controls.module.css
  tables.module.css
  states.module.css
```

Implementation rules:

- Prefer CSS modules and semantic global tokens. Stop adding large injected CSS
  strings. Migrate one surface at a time; do not rewrite all styles in one commit.
- Shared components own visuals and accessibility, not domain behavior.
- Domain components continue calling the existing typed clients.
- Keep state close to the existing owning component. Do not add a global store for
  a visual overhaul.
- Keep server/client boundaries unchanged unless Next.js requires a small wrapper.
- Every page has one scroll owner. Sticky headers, rails, drawers, and action bars
  must be tested together at 100 and 200 percent zoom.
- Use a single shared staff login component parameterized by role label and
  post-login destination. Preserve the existing OTP calls and token storage until
  the separate authentication-hardening work.

Shared states required on every data surface:

- initial loading skeleton;
- empty state with the next valid action;
- recoverable error with Retry;
- authentication expiry;
- mutation pending without layout shift;
- mutation success confirmation;
- disabled action with a reason available to assistive technology;
- offline/reconnecting where applicable.

---

## 7. Surface Specifications

### 7.1 Enterprise gateway (`/`)

Single job: route a user to the correct working surface with immediate confidence
that this is the OPD system.

First viewport:

- Persistent product identity: `Dhara OPD` and hospital/site name.
- A restrained status line: local system state only if backed by real health data.
- Five role/pathway tiles: Patient kiosk, Doctor workspace, Queue operations,
  Public board, Administration.
- Each tile has an icon, literal title, one-line purpose, and clear open action.
- Kiosk is the primary pathway, not an oversized marketing hero.

Do not show:

- session numbers;
- architecture terms such as "route groups";
- fake uptime, patient counts, or provider readiness;
- feature descriptions or a marketing call to action;
- the `/mocks` route.

Responsive behavior:

- 3-column desktop, 2-column tablet, 1-column mobile.
- Entire tile is interactive with visible keyboard focus.
- Product identity remains in the first viewport at every target size.

### 7.2 Doctor workspace (`/doctor`)

Single job: safely complete the current consultation while retaining awareness of
who is next.

Desktop information architecture:

```text
top bar: doctor / department / connection / call next / account
left rail (300-340px): today's ordered worklist
main workspace:
  patient identity + visit state + primary action
  red-flag region
  clinical story
  tabs: Overview | Intake answers | History | Consult note
  consult-note review and prescription workspace
```

Requirements:

- Left rail is independently scrollable and remains visible on desktop.
- On <=1024px, the rail becomes a drawer; selected patient remains the page.
- Patient name, MRN, age/sex, language, token, and visit state form one stable
  identity header.
- Red flags remain above all routine clinical content. Show label, instruction, and
  source/provenance without collapsing urgent information.
- Overview shows concern, the patient's own words, symptom table, and unclear items.
- Intake answers and timeline move into predictable tabs/sections; do not hide risk.
- The primary queue action is derived from current state:
  waiting -> Call; called -> Start consult; in consult -> Complete consult;
  lab requeue -> Return to queue. No-show and send-to-lab are secondary actions
  behind an overflow menu or secondary group and require confirmation where loss of
  position is possible.
- Preserve N and D keyboard shortcuts. Add tooltips and visible focus.
- Subscribe to the existing queue WebSocket in the doctor surface or preserve a
  visible manual refresh; do not silently display stale queue state.

#### Consult note

- Do not replace the entire patient context with dictation. Use the Consult note tab
  or a wide workspace while the identity and red-flag header remain visible.
- Capture -> transcript -> structured review -> safety acknowledgement -> sign is a
  visible step sequence.
- A flagged medication is never visually "resolved" after acknowledgement. It moves
  from danger to acknowledged-attention exactly as today.
- Signing remains disabled until current backend blockers are cleared.
- Signed state is explicit, timestamped, immutable, and not represented only by
  green color.

#### Prescription: critical acceptance surface

After signing, focus or scroll the workspace to a prominent `Prescription issued`
section. It must be visible without hunting below a long note.

Render a document-like clinical preview using data already available from the
patient card plus `Prescription`:

- Header: patient name, MRN, visit date, doctor/department.
- Medication rows with fixed, labeled columns:
  `Medicine | Dose and route | Schedule | Duration | Safety`.
- Medicine name is the strongest text in each row.
- Dose and route are never compressed into low-contrast secondary copy.
- Known time slots use accessible icons plus text labels. Unknown slots show the
  stated frequency verbatim; never infer a time of day.
- Flagged lines have a left danger rule, `Confirm with prescriber` text, and the
  exact flag reason.
- Print patient copy is the primary action. Print clinical copy is secondary.
- WhatsApp and SMS are delivery actions with pending/sent/failed status beside each
  channel; failed digital delivery must leave print clearly available.
- Provide a full-height preview region or dedicated preview mode. Do not squeeze the
  prescription into the bottom of a narrow card.
- At 1280x800, the signed status, first medication, and primary print action must be
  discoverable without leaving the Consult note workspace.
- At 200 percent zoom, medication columns may stack into labeled rows; no value may
  truncate, overlap, or disappear.
- Printed HTML remains generated by the existing authenticated endpoint. The web
  preview must not alter medication data before printing.

### 7.3 Coordinator operations (`/coordinator`)

Single job: see pressure across the OPD and move each patient through the next valid
state.

Top operational strip derived from the current payload:

- Waiting now.
- Urgent waiting.
- Called.
- In consultation.
- Active departments.

Do not display longest wait, throughput, appointment arrivals, room idle time, or
SLA status until the backend provides truthful fields. If added later, use an
additive response object and backend tests.

Queue layout:

- Wide desktop: operational strip, department filter/segmented control, queue table
  or compact department lanes.
- Preserve department grouping and existing ordering semantics.
- One row shows token, state, complaint, priority/red flags, and one primary next
  action.
- Reorder uses a grip icon and keyboard-accessible up/down actions.
- Secondary actions live in an overflow menu; `No-show` requires confirmation.
- Called/in-consult patients stay visually distinct without lowering text contrast.
- Downtime is an unmistakable persistent system state, not merely a recolored bar.
- Reconciliation count appears as a real navigation badge from the existing
  reconciliation response.
- Paper entry and print sheets remain first-class operational tools, not cards
  floating below the queue.

### 7.4 Public board (`/board`)

Single job: tell families which token should go where.

- Preserve huge tabular token numerals, next tokens, wait range, clock, connectivity,
  downtime, WebSocket refresh, and announcement behavior.
- Remove public clinical reasons and red-flag counts. Use neutral `Priority
  assistance` only where needed.
- Room/department name is the second strongest item after the token.
- Define layouts for 1, 2, 3, 4, and 5+ departments without clipping.
- At 1920x1080, primary numerals must remain readable at 8 metres.
- Add an explicit audio-ready/muted state if browser autoplay has not been unlocked.
- Do not add operational KPIs, patient names, MRNs, complaints, or decorative
  content.

### 7.5 Administration (`/admin`)

Single job: safely configure people, pathways, clinical content, and operating
limits.

Replace eight horizontal tabs with a desktop side navigation grouped by purpose:

- Operations: Channels, Operations.
- Workforce: People and roster.
- Clinical content: Trees, Protocols and slots, Templates and voice.
- Finance and control: Cost and tokens, Price book.

At <=900px the navigation becomes a drawer. Preserve current tab state locally; a
URL query such as `?view=people` is preferred so refresh/back works, but must not
change backend behavior.

Every admin view receives:

- page title and one-sentence operational purpose;
- primary action in a stable header location;
- current/live/draft status where applicable;
- filters before data;
- table/editor content;
- mutation result in a shared toast/notice region;
- destructive confirmation dialog with specific impact;
- loading, empty, refused, and retry states.

Specific requirements:

- Channels: readiness, switch state, provider, and reason remain distinct facts.
  Credentials remain write-only password fields. Publishing requires confirmation.
- People and roster: separate `Week`, `People`, and `Import` subviews instead of one
  very long page. Keep dry-run refusal and all-or-nothing import semantics.
- Trees: list -> editor -> test run is a stable master/detail workflow. Preserve
  language switching, red-flag content, draft/publish boundaries, and walker tests.
- Protocols: distinguish question-set authoring, grading rules, regimen assignment,
  and publication. Clinical danger remains visually louder than editor chrome.
- Cost: separate product cost from infrastructure cost. Never blend them into one
  total.
- Price book: use a compact editable table with explicit units and validation.
- Templates and voice: show language completeness and asset state without pretending
  a model-drafted voice/text is clinically reviewed.
- Tables use sticky headers where the viewport scrolls; actions remain visible
  without horizontal clipping.

### 7.6 Kiosk (`/kiosk`)

Single job: help a stressed patient complete one safe intake decision at a time.

Preserve doc 04's patient UX laws and the existing state machine. This session is a
polish and validation pass, not a workflow rewrite.

- Keep one question per screen, audio-first behavior, replay, language access,
  caregiver mode, progress, 64px targets, offline resume, idle privacy, readback,
  token, and print.
- Replace decorative excess only where it improves real-device legibility.
- Keep the assistant present but visually subordinate to the current question.
- Make listening, transcribing, thinking, speaking, offline, and tap fallback states
  distinct and translated.
- Keep the main action in a stable thumb region.
- Do not allow long Hindi/Marathi/Telugu labels to reduce a target below 64px.
- At 200 percent text scale, permit controlled vertical scrolling; never clip the
  action or overlap language controls.
- Test 1280x800 and the actual kiosk resolution in landscape.

### 7.7 Authentication

- Unify doctor/coordinator/admin OTP screens visually.
- Show product identity, role, phone/code step, pending state, validation, and a
  restrained local debug-code hint only when the API returns one.
- Preserve current OTP behavior and storage in the UI sessions. Refresh-token and
  httpOnly-cookie work belongs to security hardening, not a visual commit.
- Do not use promotional copy or a large hero.

---

## 8. Responsive And Accessibility Matrix

Required Playwright screenshot viewports:

| Surface | Required viewports |
|---|---|
| Landing | 1440x900, 1024x768, 390x844 |
| Doctor | 1440x900, 1280x800, 1024x768, 390x844 |
| Coordinator | 1440x900, 1280x800, 1024x768, 390x844 |
| Admin | 1440x900, 1280x800, 1024x768, 390x844 |
| Board | 1920x1080, 1366x768 |
| Kiosk | 1280x800, actual Omen kiosk viewport |

Required checks:

- WCAG AA staff contrast; board AAA for primary content.
- Keyboard-only completion of login and every staff mutation.
- Visible focus at all times.
- Dialog focus trap, Escape behavior, labelled close control, and focus restoration.
- Semantic headings and landmarks.
- No horizontal page overflow at required viewports. Data tables may use a labelled
  local scroller when a stacked representation would damage meaning.
- No overlap between sticky shell, action bars, notices, dialogs, and content.
- No text truncation for medication names, red flags, patient names, or destructive
  consequences.
- Reduced motion.
- 200 percent zoom on doctor prescription, coordinator queue, admin editor, and all
  kiosk languages.
- Hindi, Marathi, and Telugu screenshots with line-height at least 1.6.
- Add `@axe-core/playwright` in the first revamp session and run automated checks on
  `/`, authenticated staff shells, `/board`, and representative kiosk screens.

Automated visual assertions should include:

- `document.documentElement.scrollWidth <= document.documentElement.clientWidth`
  unless the route explicitly owns a local table scroller.
- Every primary action has a non-zero box fully inside the viewport.
- Sticky regions do not intersect the active heading or first clinical row.
- Prescription medication name, schedule/frequency, duration, safety state, and
  print-patient action are visible in the DOM and not clipped.

---

## 9. Build Sequence

The revamp is a dedicated `S-UX` track. Do not attempt all surfaces in one context.
Each session ends with the standard closing ritual.

**Implementation status (2026-07-27):** branch `uiux-enterprise-revamp` implements
the user-facing scope of S-UX.1 through S-UX.4 in one coordinated pass. Production
build, repository gates, language QA, public-surface axe checks, live queue/admin
flows, and the signed-prescription flow pass. S-UX.5 remains open for physical Omen
acceptance, full responsive visual-regression coverage, and migration of the
remaining established injected style blocks to scoped modules.

### S-UX.1 - Foundation, gateway, and doctor prescription

This is the immediate next build session. It may close as S-UX.1A / S-UX.1B if the
context window reaches the protocol limit.

Load:

- `HANDOFF.md`, `STATE.md`, this document, doc 04.
- `web/e2e/doctor.spec.ts`, `web/e2e/dictation.spec.ts`.
- Existing `/mocks` route and doctor screenshots as critique material only.

Build:

1. Semantic staff tokens and shared primitives.
2. Shared staff shell and unified login presentation.
3. Production enterprise gateway at `/`.
4. Doctor shell, patient header, state-aware actions, clinical content hierarchy.
5. Consult-note workspace and highly visible prescription preview.
6. Desktop/tablet/mobile CSS modules; remove doctor injected CSS only after parity.
7. Axe and screenshot harness for the required S-UX.1 viewports.

Do not:

- alter backend routes or clinical rules;
- add appointments to the doctor list;
- implement refresh-token security;
- invent missing vitals, labs, room, task, or appointment data;
- copy static mock data into production.

Acceptance:

- Existing doctor, dictation, prescription, and queue behavior tests pass.
- Signing visibly transitions to immutable note plus prominent prescription.
- The first medication and patient-print action are discoverable at 1280x800.
- Unknown schedule slots remain uninferred.
- Urgent information remains above routine content.
- Landing contains no session/build terminology.
- Required screenshots and axe checks pass with no overlap or page overflow.

### S-UX.2 - Coordinator and public board

Load queue E2E and WebSocket client.

Build:

- shared operations shell;
- truthful top metrics derived from current console data;
- compact state-aware queue rows and accessible reorder;
- reconciliation navigation badge;
- downtime, paper entry, and print workflows;
- public-board privacy and audio-ready treatment.

Acceptance:

- Existing queue transitions, ordering, downtime, reconciliation, print, polling,
  and WebSocket behavior pass.
- No public clinical reason is displayed.
- No fabricated operational metric appears.

### S-UX.3 - Administration

Build:

- grouped side navigation and routable view state;
- standard page headers, forms, tables, notices, dialogs, and editor action bars;
- split People/Week/Import;
- migrate all eight views from injected CSS without changing their API clients.

Acceptance:

- Admin, channels, and people E2E suites pass unchanged in meaning.
- Draft/publish, credential secrecy, dry-run refusal, deactivation consequences,
  and clinical editor behavior remain explicit.
- Every view has loading, empty, error, and mutation feedback.

### S-UX.4 - Kiosk polish

Build:

- visual polish and state consistency only;
- real-device layout, audio states, translated errors, offline/downtime, readback,
  token and print completion;
- all-language and 200 percent scale screenshots.

Acceptance:

- Kiosk online/offline E2E and walker conformance pass.
- One decision remains on each screen.
- All controls meet 64px target size.
- Actual Omen kiosk visual acceptance is recorded.

### S-UX.5 - Product-wide hardening

Build:

- eliminate remaining injected CSS and duplicated presentation;
- cross-surface keyboard/accessibility audit;
- full responsive screenshot matrix;
- loading/error/offline consistency;
- visual-regression baselines;
- update operator/staff documentation screenshots.

Acceptance:

- `make test`, `make lang-qa`, `make lint`, `npm run build`, all relevant E2E suites,
  axe checks, and screenshot review pass.
- No production route relies on `/mocks`.
- No known overlap, clipping, inaccessible action, or unreadable prescription
  remains at required viewports.

---

## 10. Implementation Method For The Building Model

For each session:

1. Read the required context and inspect the exact production components.
2. State the surface's single job and its three most important elements.
3. Run the relevant baseline test before editing.
4. Capture baseline screenshots at the session's required viewports.
5. Inventory every current API call, mutation, state, test selector, and clinical
   warning on the surface.
6. Build shared primitives only when two real uses exist or the sequence explicitly
   requires the foundation.
7. Migrate one vertical workflow at a time. Keep the route usable after every
   coherent commit.
8. Run focused tests after each workflow, then broad gates.
9. Capture after screenshots using the same data and viewport as baseline.
10. Critique screenshots against sections 3-8 of this document. Fix overlap,
    hierarchy, density, contrast, and empty/error states before closing.

Commit boundaries for S-UX.1 should normally be:

1. `S UX.1: add staff design foundations and primitives`
2. `S UX.1: replace the production gateway`
3. `S UX.1: rebuild the doctor consultation workspace`
4. `S UX.1: elevate signed prescription review and delivery`
5. `S UX.1: add responsive and accessibility evidence`
6. `S UX.1: session close - enterprise gateway and doctor workspace`

Do not combine behavioral cleanup, authentication hardening, appointment work, or
backend refactors with visual commits. Record discovered functional work in
`HANDOFF.md`.

---

## 11. Quality Gates And Evidence

Minimum commands for each frontend session:

```bash
make test
make lang-qa
make lint
cd web && npm run build
```

Run the relevant E2E suites:

```bash
cd web
npm run e2e:doctor
npm run e2e:dictation
npm run e2e:queue
npm run e2e:admin
npm run e2e:channels
npm run e2e:people
npm run e2e
```

Only run suites relevant to the current surface during iteration; run the complete
set before S-UX.5 closes.

Evidence layout:

```text
web/screenshots/sux1/
  landing-desktop.png
  landing-mobile.png
  doctor-overview-1280.png
  doctor-urgent.png
  doctor-dictation-review.png
  doctor-prescription-signed.png
  doctor-prescription-200pct.png

web/screenshots/sux2/
web/screenshots/sux3/
web/screenshots/sux4/
web/screenshots/sux5/
```

Every session log must record:

- viewports captured;
- browser used;
- axe result;
- keyboard workflows exercised;
- overflow/overlap result;
- existing behaviors explicitly preserved;
- any data desired by design but unavailable from the current backend.

---

## 12. Definition Of Done

The revamp track is complete only when:

- all six production entry routes share one coherent visual system;
- the landing page is a real enterprise gateway;
- the doctor can identify risk and reach a readable prescription without hunting;
- coordinator operational pressure is visible using truthful data;
- the public board reveals no clinical reason;
- admin navigation and repeated workflows are predictable at desktop and tablet;
- kiosk behavior remains clinically and operationally unchanged and is proven on the
  actual hardware;
- every required state is designed, not only the happy path;
- automated behavior, accessibility, build, language, and visual gates pass;
- no backend contract or clinical invariant was weakened to accommodate the design.
