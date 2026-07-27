# HANDOFF - correct kiosk intake and prescription before merge

> **Immediate next action:** create `uiux-kiosk-rx-hardening` from
> `uiux-enterprise-revamp` and implement `docs/15-KIOSK-INTAKE-AND-PRESCRIPTION-HARDENING.md`.
> Do not merge the current UI branch to `main`: operator review found the kiosk intake
> and prescription document below acceptance.

## Current state

- Branch: `uiux-enterprise-revamp`.
- Design plan and mocks commit: `82b7719`.
- Implementation commit: see `git log -1` after this session closes.
- `/` is now the production role gateway; `/mocks` remains isolated reference.
- Doctor, prescription, coordinator, public board, admin, shared staff sign-in, and
  kiosk visual treatment use the enterprise system.
- The branch is technically green, but its kiosk still omits patient name and a live
  intake summary, control alignment is not reliable on tablet sizes, and the current
  prescription is authenticated print HTML rather than a polished downloadable PDF.

## Corrective build scope

1. Add patient name through online start, offline queue/sync, and `Patient.name`,
   with a rolling-deploy fallback for old clients.
2. Add a deterministic live summary rail for name, concern, department, duration,
   and symptom answers. Tree metadata may classify presentation only; it must never
   affect clinical behavior.
3. Replace wrapping/auto-fit choice layouts with stable kiosk/tablet grids and prove
   no overflow in all four languages at 100 and 200 percent.
4. Produce one clean prescription letterhead source with authenticated PDF preview,
   download, and print.
5. Purge or redact successfully synced kiosk PII from IndexedDB.

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

Follow doc 15's branch command, file map, ordered commits, tests, and physical-device
gate. Preserve the enterprise branch as the parent review line. Merge only after the
corrective branch is accepted on Omen and the target Android tablet.
