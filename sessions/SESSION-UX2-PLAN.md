# SESSION-UX2 - kiosk intake and prescription corrective plan

**Date:** 2026-07-27
**Current branch:** `uiux-enterprise-revamp`
**Next build branch:** `uiux-kiosk-rx-hardening`

## Operator findings

- The kiosk intake does not ask for the patient's name.
- Choice buttons and labels are not consistently aligned.
- The intake needs a live, neatly organized view of patient name, chief complaint,
  duration, and symptom answers.
- The layout must work without overflow on the Omen kiosk and an Android tablet.
- The prescription is difficult to use as a document and needs a clean letterhead,
  real PDF download, and reliable print output.

## Decision

Do not merge `uiux-enterprise-revamp` to `main` yet. Automated gates passed, but the
two core workflows failed operator acceptance. Preserve that branch and build the
correction on a child feature branch.

## Contract findings

- Online kiosk creation and offline reconciliation both currently create
  `Walk-in patient`; `Patient.name` already exists, so no schema migration is expected.
- Patient name must be added to the web online API, local session, IndexedDB queue,
  sync wire, backend route, and both patient-creation paths.
- Synced IndexedDB rows currently retain full payloads; adding a name requires purge
  or redaction after successful reconciliation.
- The tree schema has no stable presentation category for duration or symptom rows.
  Add optional `summary_role` metadata and preserve it through Python, TypeScript,
  fixtures, seeds, and admin editing. It must never affect clinical logic.
- Prescription print currently returns authenticated HTML. Add an authenticated PDF
  route using the same renderer and install verified Indic-shaping dependencies and
  fonts in the backend image.
- Hospital data currently supports name, city, and district. Do not fabricate
  address, phone, logo, or credentials for visual completeness.

## Next-session instruction

Implement `docs/15-KIOSK-INTAKE-AND-PRESCRIPTION-HARDENING.md` in its stated commit
order. Run its complete automated and physical acceptance matrix. Update this log
with evidence or create the closing session record, then update `HANDOFF.md` before
considering a merge.
