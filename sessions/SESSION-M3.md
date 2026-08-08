# SESSION-M3 — The PACS stub: the scans are somewhere else, and the console says so honestly

**Date:** 2026-08-08 · **Scope ref:** `sessions/SESSION-CLINICAL-INTEL-PLAN.md`
§2 + §6 (Session M3). Branch `main`. Baseline at start: backend **1,660** green
(immediately after M5, same day).

**The external gate closed.** Plan §8.1 — the imaging centre registering studies
under the UHC ID as the DICOM `PatientID` — was confirmed by the operator as the
contract that will be enforced. That is the only thing that had been keeping M3
parked since the plan was written, through MRD1, MRD2, M4 and M5.

With this, **all four Clinical Intelligence modules are built.**

## What this session was for

The doctor knowing what imaging exists for the patient in front of them, and
being able to open it, without this repo owning a DICOM viewer or a DICOM
client any larger than it must.

## Acceptance criteria, restated and checked

- [x] Config, not code: `PACS_ENABLED`, `PACS_PROVIDER`, `PACS_DICOMWEB_URL`,
      `PACS_AUTH_*`, `PACS_VIEWER_URL`, `PACS_AET`, `PACS_DICOM_PORT`.
- [x] Backend proxies QIDO-RS; the browser never talks to Orthanc.
- [x] Study list by UHC ID, newest first, undated studies last.
- [x] Viewer popup handoff carrying a StudyInstanceUID and nothing else.
- [x] Report streaming via the backend, inline, no identifier in the filename.
- [x] Department scope on both routes; a `Clinical` audit row per study-list
      fetch and per report view.
- [x] **The three truthful empty states the plan asks for — and a fourth.**
- [x] A fake DICOMweb server in the test suite (plan §2.2's acceptance test).
- [x] Gates: backend **1,701**, imaging E2E **6**, doctor **12**, reports 7,
      research 7, notes 5, dictation 8, conformance 48, voice-gw 25,
      build / `tsc` / `eslint` clean.
- [ ] **Manual acceptance against the real `RAD-RENVA-PACS` — NOT DONE.** The
      plan lists this as a gate with "external gate if unreachable". No
      credentials or reachable endpoint were available in this session, so
      every line of the DICOMweb path is exercised only against a fake. This is
      the module's headline gap, recorded below and in the handoff.

Not in scope and not done: series or instance listing, thumbnails, any pixel
access, a local Orthanc mirror (doc-17/18 ops territory, plan §8.6), and
anything that writes to the PACS.

## Decisions made

1. **Four empty states, not three.** The plan names three (no UHC ID, PACS
   timeout, zero matches). Building it produced a fourth — the switch being off
   — and keeping it separate matters for the same reason the others are
   separate: "nothing was asked because this installation has no PACS" and "the
   PACS says this patient has no scans" are different facts. `LookupState`
   carries which, and a parametrised route test holds that all four survive
   serialisation as four distinct answers.
2. **`PacsProvider` is not a metered `Provider`.** Orthanc is our own server on
   our own account: no per-unit price, and a `usage_events` row for it would
   reconcile to nothing on the S18 dashboard. It follows `objectstore` — one
   interface, config-selected, with a fake — and skips the billing machinery.
3. **Two verbs and no third.** `studies` and `report`. No series, no instances,
   no pixels. The viewer does the viewing (plan decision 5), and every method
   added here is a step towards owning a DICOM client we have no reason to own.
   A test asserts the ABC's verb set.
4. **A StudyInstanceUID is not a secret, so the report route re-checks it.** It
   is in the viewer URL and in the console's own HTML. Streaming any UID to any
   authenticated doctor would be an enumeration hole dressed as a convenience;
   one extra QIDO call per report view is the right trade.
5. **The viewer URL is built server-side.** The console never learns the
   viewer's shape and so cannot be talked into composing a different one. It
   carries a study UID and nothing else — no token, no patient id, no return
   URL — because the viewer authenticates the doctor itself and anything more
   would be a credential in a URL that outlives the tab.
6. **`AuditAction.READ`, and no migration.** The `before_flush` hook audits
   writes to `Clinical` models; a study never enters this database, so "who
   looked at this patient's scans" had nowhere to go. Recording it as `create`
   would corrupt every audit query that counts creates. I wrote a migration to
   widen the action CHECK constraint, then found there is no CHECK constraint —
   `enum_type` builds a VARCHAR with `create_constraint` defaulted off — and
   deleted it. Verified against the live column rather than assumed.
7. **Imaging is a section of Reports, not a seventh tab.** See below.
8. **`PACS_ENABLED` defaults false**, unlike the other module switches. A PACS
   that is configured but pointed at the wrong join key returns empty for every
   patient, which reads exactly like "this patient has never been scanned".
   Better an operator turns it on deliberately, having checked, than that it
   appears to work everywhere.
9. **The demo fake answers for any UHC ID.** `FakePacsProvider.demo()`, wired
   in the registry for `PACS_PROVIDER=fake`, so the whole module is
   demonstrable on a laptop with no imaging centre attached — the MRD2 habit,
   because a module nobody can see is a module nobody reviews. Tests construct
   the class with an explicit study dict instead; a fake that invents data would
   hide the very bugs the four empty states exist to catch.

## Why a section of Reports and not a seventh tab

`WorkTabs.tsx` opens with "Four working tabs. Not five, not seven." That has
stretched twice — Reports in MRD2, Research in M5 — and M5 wrote down that the
next module should have to *argue* for a seventh. This one argued and lost, on
purpose.

Reports is already "what is on file about this patient from outside this
consult". A doctor asking what investigations a patient has had should find the
scanned histopath report and the CT in one place, not two tabs apart. And
imaging has no surface of its own to justify a tab: it is a list of five-word
rows and a button that opens somebody else's viewer.

So the row stays at six, and the spine keeps its five slots — the plan's
`Images (n)` (§2.1) rides on the existing Reports line rather than taking a
sixth, which `ContextSpine.tsx`'s header says should be refused. Nothing in the
imaging clause is a number a doctor could act on without opening it, which is
the test that slot has to pass.

## The doc 04 §5 self-critique, and what it changed

**The deliberate aesthetic risk is: none.** This is the first surface in the
build to take none on purpose, and that is the critique's main finding —
it hands off to a product somebody else designed, and a flourish here would be
decoration on a doorway. The care went into the four empty states instead.

Three things the screenshots showed that reading the code did not:

1. **The spine read "nothing scanned for this patient · 2 scans".** Two senses
   of the same root word forty pixels apart — photographed paper, and imaging —
   which parses as a contradiction before it parses as two facts. It says
   "2 imaging studies" now, and the E2E asserts the clause never uses the word
   the scanned-paper tally uses.
2. **The note dock's mic sat on a study's Report button.** M5's conclusion
   applied rather than M5's first attempt: horizontal clearance, because the
   dock is pinned to the right edge and these rows scroll. Checked at three
   scroll positions.
3. **The note drawer then covered the context spine**, and M4's notes E2E
   failed — correctly. M4 sized the drawer to the pixel (52vh/54vh), leaving the
   spine's bottom edge within about two pixels of the drawer's top on a 720px
   viewport; a three-pixel spine clause tipped it over. **The third input M4 did
   not write down is that the spine grows with every module that ships.**
   48vh/50vh now, with roughly a line of headroom and the reasoning recorded
   beside the numbers.

## Deviations from spec

- **The plan's spine slot `Images (3)` is a clause on the Reports line**, not a
  sixth slot. See above.
- **`has_report` is always false on a listed study.** QIDO does not say whether
  a study carries an encapsulated report, and asking per study at list time
  would be a fetch per row for a question most doctors will not ask. The Report
  link is therefore always offered and the backend answers "not reported yet"
  with a 404. Honest, and plain — the field is kept on the contract because a
  future QIDO `includefield` may answer it cheaply.
- **A fourth empty state** (decision 1).

## Tests & evidence

- `make test-backend`: **1,701 passed** (1,660 → +41): 26 in `test_imaging.py`,
  15 in `test_imaging_routes.py`.
- `npx playwright test --project=imaging` → **6 passed** against a live stack
  (api on :8123 with `PACS_ENABLED=true PACS_PROVIDER=fake`, web on :3210).
- `--project=doctor` **12**, `--project=reports` **7**, `--project=research`
  **7**, `--project=notes` **5**, `--project=dictation` **8**,
  `--project=conformance` **48**.
- `make test-voicegw` **25 passed**; `make test-web` green.
- `npm run build` / `tsc` / `eslint` clean. `/doctor` is 41.4 kB (39.8 → 41.4).
- Screenshots: `web/screenshots/m3/01…03`, self-critiqued above.

## Migration

**None.** The one I wrote — widening the `audit_action` CHECK constraint — was
deleted when the constraint turned out not to exist (decision 6). The six
pending on Omen are unchanged from M5: `c6e3681f5ce1`, `520d07f0b3e4`,
`c063fd91e198`, `efb79a43afb3`, `02571a5c1871`, `9f2ab41c77d3`.

## Known gaps / stubs introduced

(Mirrored into STATE.md → Stubs & fakes.)

- **No line of this has met a real Orthanc.** The DICOMweb provider is written
  against the documented QIDO-RS/WADO-RS shapes and driven entirely against an
  `httpx.MockTransport`. The plan lists manual acceptance against
  `RAD-RENVA-PACS` as a gate; it is unmet. Specifically unverified: whether the
  report endpoint answers a study-level PDF `Accept` the way this expects,
  whether `includefield` returns the series count, and whether the modality
  actually registers the UHC ID as `PatientID` for a real patient.
- **`has_report` is never true** (see Deviations).
- **No local Orthanc mirror**, and no compose service for one. Plan §8.6 puts
  replication and its backup in doc-17/18 ops territory.
- **The study list is not cached and not polled.** Every patient open is a QIDO
  call; a study acquired during the consult appears on the next open.
- **Nothing links a scanned imaging *report* (MRD `imaging_report` documents) to
  the PACS study it describes.** They sit in the same tab and nothing knows they
  are about the same scan.

## The process failure worth recording

**I ran `npm run build` while the E2E dev server was up.** The previous
handoff's own "Watch out for" list warns about exactly this — it overwrites
`.next` under the running server, every page load 404s its chunks, and it
presents as a login regression. I lost several minutes to it and then read my
own warning. The lesson is not new; the reason it is written down again is that
having the note did not stop it, so the note now sits in the run instructions
rather than only in a bullet list.

## Commits

(see `git log` on `main`, prefixed "S M3:")
