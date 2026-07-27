# HANDOFF - enterprise UI/UX revamp queued

> **Immediate next build: S-UX.1** from
> `docs/14-ENTERPRISE-UIUX-REVAMP.md`. The operator wants a pristine,
> enterprise-oriented overhaul across the entire UI, beginning with the production
> gateway and doctor workspace. Prescription legibility is the sharpest first
> defect: after signing, the generated prescription is too quiet and too easy to
> miss below the consult note.

## Current state
- Branch: `main`; local HEAD when this handoff was written:
  `fc3c0c9 fix(api): cryptography missing from requirements.txt`.
- This planning pass changed documentation only. No production UI was modified.
- Baseline verified during the preceding repository review: backend 1212 tests,
  voice-gw 25, web typecheck/lint and 48 conformance cases, Android JVM build/tests,
  and four-language QA all passed.
- Automatic GitHub CI remains off. Local gates plus manual CI are authoritative.
- `/mocks` is isolated design reference. It is not production and must never provide
  data to a production route.
- Preserve unrelated working-tree changes, especially
  `web/tsconfig.tsbuildinfo` and the existing untracked `web/app/mocks/`.

## Next session - S-UX.1

**Objective:** establish the enterprise design foundation, replace the developer
landing directory, and rebuild the doctor consultation/prescription hierarchy while
preserving every existing backend and clinical contract.

**Mandatory load, in order:**

1. This file, then `STATE.md`.
2. `docs/14-ENTERPRISE-UIUX-REVAMP.md`.
3. `docs/04-UIUX-GUIDE.md`.
4. `CODEBASE_MEMORY.md` invariants.
5. `web/e2e/doctor.spec.ts` and `web/e2e/dictation.spec.ts`.
6. Current doctor, prescription, landing, login, and global-style files.

**Build:**

1. Add semantic staff tokens and the small shared primitive set specified by doc 14.
2. Add the shared staff shell/login presentation without changing OTP behavior.
3. Replace `/` with the real enterprise gateway; no sessions, fake metrics, or mock
   data.
4. Rebuild `/doctor` around a stable worklist, patient identity, red flags, clinical
   story, state-aware actions, and consult-note workspace.
5. Make the signed prescription a prominent document-like surface with readable
   medicine/dose/route/schedule/duration/safety and clear print/delivery actions.
6. Add axe and responsive screenshot evidence at doc 14's S-UX.1 viewports.

S-UX.1 may close as S-UX.1A/S-UX.1B if context approaches doc 07's limit. Do not
compress the prescription or responsive/accessibility proof to avoid a split.

## Acceptance criteria

- Existing doctor, dictation, prescription, queue, and authentication behavior still
  passes.
- Red flags remain before routine clinical content.
- Signing remains the only operation that creates a prescription.
- Acknowledged off-formulary medication remains visibly flagged; unknown dosing
  slots are never inferred.
- At 1280x800, signed status, first medication, and Print patient copy are
  discoverable in the Consult note workspace.
- At 200 percent zoom, prescription values stack rather than clip or disappear.
- Desktop, tablet, and mobile layouts have no incoherent overlap or page overflow.
- Landing is a role/pathway gateway and contains no developer/session terminology.
- Keyboard, visible focus, axe, reduced-motion, build, lint, language, and relevant
  Playwright checks pass.

## Hard scope boundary

Do not add appointments to the doctor list, implement refresh-token security, change
API response meanings, rewrite queue state transitions, alter dictation validation,
modify prescription generation/print HTML, or invent vitals/labs/tasks/rooms. Those
are separate functional sessions. Shared components own presentation, not domain
logic.

Do not attempt coordinator, board, admin, and kiosk in S-UX.1. They are S-UX.2,
S-UX.3, and S-UX.4; S-UX.5 is the product-wide hardening pass.

## Omen warning before any deploy

The 2026-07-27 Omen upgrade exposed a repository defect not yet fixed in this working
tree: the API container does not mount `config/tiers.yaml`. The resulting
`TierConfigError: tiers config not found at /config/tiers.yaml` makes
`POST /kiosk/start` return 500 while `/health` remains green.

Before the next Omen deploy, commit a source fix that mounts
`./config:/config:ro` into the API service alongside `./seeds:/seeds:ro`, recreate
only the API, and verify `/config/tiers.yaml` inside it. Omen uses existing nginx on
80/443; Caddy must remain stopped or excluded. Read
`docs/13-OMEN-UPGRADE-RUNBOOK.md`; never use `docker compose down` on Omen.

## Clinical/go-live decisions still open

- Tree bank and check-in protocol bank require oncologist review.
- Marathi/Telugu patient-facing text requires native and clinical review.
- The Surgical Oncology new-lump tree is missing.
- S-GL.3 real-hardware acceptance remains required even though S-UX is now the
  immediate build track.

## Start commands

```bash
git status --short
make dev
make test
make lang-qa
make lint
cd web && npm run build
```

Use the current local API/browser recipe only if the Compose images are stale.
Never run write-heavy admin E2E suites against the Omen database.

## Closing ritual

Follow doc 07 exactly: focused and broad gates, screenshots and self-critique,
`sessions/SESSION-UX1.md` (or UX1A/UX1B), overwrite this handoff for the next
session, update `STATE.md` and `CODEBASE_MEMORY.md` if architecture/maturity changed,
then make the session-close commit.
