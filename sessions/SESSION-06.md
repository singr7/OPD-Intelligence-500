# SESSION-06 — Kiosk PWA part 1 (flow + design system)

**Date:** 2026-07-17 · **Scope ref:** docs/06-BUILD-PLAN.md → S6

## Acceptance criteria checklist
- [x] **Design tokens + component library** — expanded tokens (type scale, spacing,
  motion) on the doc 04 §1 palette; `OptionCard, FacesScale, BodyMap, Stepper,
  AssistantAvatar, AudioBar, ProgressDots, MicButton` + a duotone icon set.
- [x] **Kiosk flow screens** — language → caregiver toggle → voice chief complaint
  (Web Speech + tap-to-type fallback) → department chooser → tree questions with
  auto-read-aloud → summary read-back + confirm → token screen.
- [x] **Full kiosk intake in hi against the local stack** — Playwright drives
  welcome→token end-to-end through the real api + seeded dev DB (`e2e/kiosk.spec.ts`).
- [x] **Every screen passes the audio-first laws** — auto-play + replay on every
  prompt, one decision per screen, ≥64px targets, language one tap away, faces
  scale, 60s idle prompt / 90s privacy blur, warm second-person copy.
- [x] **Playwright screenshot suite + self-critique** — 11 screens under
  `web/screenshots/s6/`, critiqued below.
- [~] **en full intake** — en welcome/caregiver verified + screenshotted; the tree
  text itself is hi/en in the bank, so an en walk renders en. Only the hi walk is
  scripted end-to-end in the suite (time); en is a one-line variant for S7.

## What was built
- **`backend/app/routes/kiosk.py`** — the intake engine's first HTTP surface. Thin
  REST mirroring the four-tool contract: `POST /kiosk/start` (route Q1 + first node),
  `GET /{sid}/next`, `POST /{sid}/answer`, `POST /{sid}/finish` (read-back),
  `POST /{sid}/confirm` (token + finalise cost). One `IntakeEngine` on `app.state`.
- **`backend/app/kiosk.py`** — non-HTTP service: `route_complaint` (classifier,
  honouring `needs_human` → chooser), `select_tree`, `create_walk_in`
  (Patient+Visit+Intake), `allocate_token` (provisional, savepoint-guarded).
- **`backend/tests/test_kiosk.py`** — 6 tests: full walk to token, chooser path,
  bad-answer re-ask, resume, 404, unknown-dept 422.
- **Web kiosk** — `web/app/(kiosk)/kiosk/`: `KioskApp.tsx` (state machine),
  8 components, `_lib/{i18n,icons,api,speech}`, `kiosk.module.css`; self-hosted
  Noto Sans + Devanagari via `next/font`; expanded tokens in `globals.css`.
- **Playwright** — `web/playwright.config.ts` + `e2e/kiosk.spec.ts` (`npm run e2e`).

## Decisions made
- **The kiosk is a V3 client** (HANDOFF S6): taps drive `ToolDispatcher` directly,
  no model in the walk. The one model call is Q1's department classifier.
- **`needs_human` is a product path, not an error.** An uncertain route returns a
  **department chooser** (also the doc 03 §1a "call staff"/tap fallback); the kiosk
  re-calls `/start` with a confirmed `dept_key`. This is why the local demo works
  against the fake classifier (which always triages) without guessing a department.
- **REST, not a websocket**, with the tool contract as the wire shape — so S12/S14
  reuse the vocabulary (HANDOFF called for either; REST was enough for taps).
- **A kiosk walk-in gets a real Visit+Intake** (anonymous `WALKIN-…` MRN) so answers
  and cost persist; the registration desk / doctor screen (S9) attaches identity.
- **Icons: ship a branded duotone subset + aliases + a neutral fallback.** The full
  ~65-key custom set is a design-asset job (S7/S21); no option is ever iconless.

## Deviations from spec
- **Token issuance is provisional** (`max(token_no)+1` per dept/day). Real
  queue-managed issuance (priority insertion, offline blocks, reconciliation) is
  S8/S7 — the allocator is replaced then, not the shape.
- **Server-STT toggle** (doc 06 S6 line) is not shipped; Web Speech + always-present
  tap-to-type cover the audio-first + fallback laws. A `/kiosk/stt` endpoint
  (MediaRecorder → `stt_chain`) is a small S7 follow-up. No dead toggle shipped.
- **Department names render in English** on the hi flow (seeded English names);
  dept-name localisation is S13.

## Tests & evidence
- `make test`: **backend 492 passed** (486 + 6 kiosk), **voice-gw 1 passed**,
  **web typecheck + lint clean** (0 warnings).
- `npm run e2e`: full hi kiosk intake welcome→token green against the local stack.
- New tests: `tests/test_kiosk.py` (6); `web/e2e/kiosk.spec.ts` (2).
- Screenshots (`web/screenshots/s6/`) — self-critique (doc 04 §5):
  - `01-welcome` — calm, breathing Dhara, bilingual, big language cards. Pass.
  - `02-caregiver` — two big human-iconed choices. Pass.
  - `03-complaint` — marigold mic + transcript + tap-to-type. Pass.
  - `04-chooser` — 9 departments, each a distinct duotone icon. Pass. *(long dept
    names clip a hair on the narrowest card — cosmetic, S7.)*
  - `05-question-single` — avatar + dots "1 में से 8" + audio bar + big option
    cards. Pass. *(Fixed mid-session: `report`/`question`/`unknown` had collapsed
    to one glyph; `report` now a distinct document icon.)*
  - `06-question-{multi,number,free_voice}` — each control legible, ≥64px. Pass.
  - `07-readback` — hi read-back script with the patient's own words; confirm
    marigold bottom-right (thumb zone), "change" as ghost. Pass.
  - `08-token` — the train-board signature: huge marigold numeral on deep green.
    The strongest screen. Pass.
  - `09-caregiver-en` — English shell renders. Pass.

## Known gaps / stubs introduced (mirrored to STATE.md)
- Kiosk session store is **in-memory locally** (one api process); Redis in prod.
- **No true "back" inside the tree** — the walk has no rewind endpoint; the
  read-back "change something" restarts. A per-node amend is S7/S9.
- Server STT endpoint pending (above). ProgressDots total is a hint (branching tree).
- `finalize_cost` for a pure-V3 kiosk intake is ~₹0 (no metered calls in the walk);
  the routing classifier's cost is not yet attributed to the intake (it runs before
  the intake_id exists) — a small usage_scope fix, backlog.

## Commits
- 7ee8712 — S 06: fix flaky OTP cooldown test — move factory phone out of seed namespace
- 40c9caf — S 06: add the kiosk channel — first HTTP surface over the intake engine
- 115547e — S 06: kiosk PWA part 1 — design system, component library, full flow
- (session-close commit follows this file)
