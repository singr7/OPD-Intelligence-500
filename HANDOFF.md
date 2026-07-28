# HANDOFF - run physical acceptance for kiosk and prescription hardening

> **Immediate next action:** deploy committed branch `uiux-kiosk-rx-hardening` to
> the Omen using `docs/13-OMEN-UPGRADE-RUNBOOK.md`, then execute doc 15's physical
> kiosk/tablet/printer matrix. Do not merge to `main` until photographs and operator
> acceptance are recorded.

> **Approved work after that gate:** execute doc 16 in order:
> `SESSION-VOICE1` (three selectable voice profiles), `SESSION-CLOUD1` (GPU-free
> AWS standby), then `SESSION-ANDROID1` (one signed APK paired to Omen or AWS).
> The execution plans are in `sessions/SESSION-VOICE1-PLAN.md`,
> `sessions/SESSION-CLOUD1-PLAN.md`, and `sessions/SESSION-ANDROID1-PLAN.md`.

## Current state

- Branch: `uiux-kiosk-rx-hardening`, based on `uiux-enterprise-revamp`.
- Build commits are the ordered seven-commit sequence in doc 15.
- Local automated build is complete and green.
- The parent enterprise branch remains preserved as the review line.
- The existing local `web/tsconfig.tsbuildinfo` modification is intentionally
  uncommitted and was not included in this work.

## What changed

1. Kiosk name capture now reaches online visits, offline reconciliation, and the
   doctor-facing patient record. Old clients retain the documented walk-in fallback.
2. Successfully synced offline queue rows are deleted, removing patient name and
   answer PII from unattended kiosk storage.
3. Optional tree `summary_role` metadata populates a truthful live intake rail and
   is excluded from routing, traversal, branching, red flags, and clinical rules.
4. Kiosk controls use stable responsive grids. The primary action remains visible
   at 1280x800, 1024x768, and 800x1280 at 100% and 200% text scale.
5. Prescription preview/download/print use one protected letterhead renderer.
   Downloads are real authenticated PDFs; bearer tokens never enter query strings.
6. A concurrent duplicate offline-block lease now adopts the winning row, avoiding
   the development double-boot race found during live offline acceptance.

## Automated evidence

- `make test`: backend 1,223; voice-gw 25; web conformance 48; Android unit tests.
- `make lang-qa`: en/hi/mr/te clean.
- `make preflight`: API and voice-gateway images build and import.
- `cd web && npm run build`: optimized production build passes.
- `cd web && npm run e2e`: 3 kiosk tests pass, including the six-case tablet matrix.
- `npx playwright test --project=offline-demo`: three offline intakes sync with
  distinct tokens and leave zero local PII rows.
- Focused backend UX suites: 102 pass; prescription suite: 61 pass.
- PDF visual QA: one-page Hindi patient copy and two-page 24-medication clinical
  copy render cleanly with repeated headers, coherent signature, and reserved footer.
- Updated acceptance screenshots are tracked in `web/screenshots/s6/` and `s7/`.

One full `make test` attempt hit a pre-existing async voice-gateway fake timing
failure. The isolated assertion passed three consecutive runs, and the complete
repository gate then passed.

## Physical release gate still required

1. Complete online intake on the Omen in en/hi/mr/te.
2. Complete offline intake, restore connectivity, and verify reconciliation.
3. Exercise landscape and portrait on the target Android tablet.
4. Download and print both prescription copies with short and long medication lists.
5. Confirm real paper has no clipping and Indic glyph shaping is correct.
6. Confirm idle/reset cannot reveal the previous patient's name or answers.
7. Add screenshots, print photographs, deployed commit SHA, and operator acceptance
   to `sessions/SESSION-UX2.md`.

## Deployment notes

The Compose API config mount fix is already committed (`374efed`). Omen uses existing
nginx on ports 80/443: keep Caddy stopped/excluded and never use `docker compose down`
casually. Deploy only committed code; do not patch the Omen manually.
