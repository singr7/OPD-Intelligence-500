# HANDOFF - enterprise UI branch ready for Omen validation

> **Immediate next action:** review and deploy branch
> `uiux-enterprise-revamp` on the local/Omen stack. The production UI overhaul is
> implemented and committed separately from `main`; do not merge until the operator
> has completed pathway checks.

## Current state

- Branch: `uiux-enterprise-revamp`.
- Design plan and mocks commit: `82b7719`.
- Implementation commit: see `git log -1` after this session closes.
- `/` is now the production role gateway; `/mocks` remains isolated reference.
- Doctor, prescription, coordinator, public board, admin, shared staff sign-in, and
  kiosk visual treatment use the enterprise system.
- Backend routes, queue transitions, dictation validation, signature semantics,
  prescription generation, and offline walker logic were not changed.

## What to validate

1. Run the branch at `http://localhost:3030` and review `/`, `/doctor`,
   `/coordinator`, `/board`, `/admin`, and `/kiosk`.
2. At 1280x800, sign a doctor note and confirm the issued prescription, first
   medicine, safety state, and patient-print action are easy to find.
3. Confirm the coordinator metrics remain truthful and public board urgency never
   exposes its clinical reason.
4. Confirm admin grouping does not hide Trees, Protocols, People, Channels, Prices,
   Analytics, Cost guard, or AI configuration.
5. Exercise Hindi, Marathi, Telugu, and English on the physical kiosk at 200 percent
   text scale before accepting the visual work.

## Verified this session

- `npm run build`
- `make test`: backend 1212, voice-gw 25, web conformance 48, Android unit tests
- `make lang-qa`: all four languages clean
- `npm run e2e:a11y`: gateway, staff sign-in, and kiosk clean
- Queue live-stack suite passed.
- Admin suite passed after a precise accessible-heading selector correction.
- Focused signed-prescription flow passed after fixing a nested sticky-header click
  obstruction.

## Known follow-up

- The doctor, coordinator, and admin surfaces still keep their established large
  scoped CSS strings. Shared tokens/login/primitives are extracted, but completing
  the CSS-module migration and visual-regression matrix remains S-UX.5 hardening.
- Existing OTP acceptance suites now reuse one token per serial worker to respect the
  local rate limiter.
- Automatic GitHub CI remains disabled; local gates plus manual CI are authoritative.

## Omen deploy blocker

The API container still requires `./config:/config:ro` in Compose. Without it,
`POST /kiosk/start` can fail with
`TierConfigError: tiers config not found at /config/tiers.yaml` while `/health`
remains green. Fix and commit that mount before deploying this branch.

Omen uses existing nginx on ports 80/443. Keep Caddy stopped/excluded, follow
`docs/13-OMEN-UPGRADE-RUNBOOK.md`, and never use `docker compose down` casually.

## Next session

Create a narrow infrastructure commit for the `/config` mount, validate the branch
locally, then deploy it to Omen and perform the physical kiosk/doctor/queue smoke
matrix. Record real-hardware findings before merging to `main`.
