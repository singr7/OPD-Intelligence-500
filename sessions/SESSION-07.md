# SESSION-07 — Kiosk part 2: offline-first, downtime mode, token slip

**Date:** 2026-07-18 · **Scope ref:** docs/06-BUILD-PLAN.md → S7

## Acceptance criteria checklist
- [x] Service worker + IndexedDB caching (trees, token blocks, session/queue) —
      `kiosk-sw.js` (shell) + Dexie stores (`_lib/offline/db.ts`).
- [x] Offline token blocks: server allocation API + kiosk consumption —
      `POST /kiosk/blocks/lease`, `app.offline.lease_blocks`, client cursor.
- [x] Downtime Mode UI + auto-detection + sync/reconciliation — marigold banner,
      `NetMonitor` (probes `/health`, not `navigator.onLine`), `POST /kiosk/sync`.
- [x] ESC/POS print bridge for the token slip — `_lib/print.ts` (raw bytes +
      browser fallback).
- [~] Voice-pack manifest format + placeholder TTS packs — **deferred to backlog**
      (see HANDOFF). V3 audio offline is Web Speech; the manifest seam
      (`app.intake.voicepack.VoicePack`) already exists from S5.
- [x] **Demo AC:** kill API → 3 offline intakes with valid tokens → restart → all
      sync, zero collisions — `web/e2e/offline-demo.spec.ts` (green) +
      `backend/tests/test_offline.py` at the service layer.

## What was built
- **Offline tree engine in TypeScript** (`web/app/(kiosk)/kiosk/_lib/tree/`):
  `rules.ts` + `walker.ts`, a line-by-line port of `app/trees/rules.py` +
  `walker.py`. Gated against drift by golden traces (`app/tree_fixtures.py` →
  `web/e2e/conformance.spec.ts`), regenerated + diffed in `make test`
  (`make check-tree-fixtures`). `Tree.to_json()` added as the canonical,
  desugared wire shape so the port never re-implements the validator.
- **Offline token blocks + sync** (`backend/app/offline.py`): the token line is
  partitioned — online `< base(500)`, offline `>=` — so a collision is
  unrepresentable, not merely unlikely. `POST /kiosk/blocks/lease` (idempotent),
  `POST /kiosk/sync` (idempotent per `client_id`, recomputes red flags
  server-side). `Intake` gained `client_id` + `tree_ref` (migration
  `bc2e83129ac3`). `app.kiosk.allocate_token` now refuses to cross the base.
- **`GET /kiosk/bundle`**: canonical trees + department chooser the kiosk caches,
  ETag + `no-cache`.
- **Offline web layer** (`web/app/(kiosk)/kiosk/_lib/offline/`): Dexie store,
  local intake flow (`local.ts`), reachability monitor (`net.ts`), the
  online-or-local `flow.ts` seam, background `sync.ts`, and the `useOffline`
  lifecycle hook. `KioskApp` calls `flow.*` and shows a marigold downtime banner.
- **`kiosk-sw.js`**: shell-only service worker (HTML/JS/CSS), never data.
- **ESC/POS print bridge** (`_lib/print.ts`): raw 58mm byte builder + browser
  print fallback, wired to a "Print slip" button on the token screen.

## Decisions made
- **The offline walker/rules are a second implementation of clinical logic, and
  are trusted only because they are gated.** The golden-trace conformance suite
  (1705 steps + 398 rejections over 11 seeded trees + a synthetic op-coverage
  tree) is mutation-tested: a `gte`→`gt` change fails it. Do not weaken it, and
  regenerate fixtures (`make tree-fixtures`) whenever `app/trees/` changes.
- **Token collisions are prevented structurally, not checked.** The online
  allocator and the offline blocks draw from disjoint ranges (`< base` vs `>=`).
  Never let either cross the base; `allocate_token` raises rather than wrap.
- **Sync recomputes red flags from the answers server-side** and ignores the
  kiosk's own flag list — the port is belt-and-braces, not the sole defence.
- **`validate()` was deliberately NOT ported** — validation is a publish-time
  server act; trees ship pre-desugared via the canonical form.

## Deviations from spec
- Downtime banner uses marigold `--accent` per doc 04 §3 (initially built green;
  fixed after the screenshot self-critique). AA contrast verified (5.39:1).
- Voice-pack manifest generation deferred (backlog). Offline V3 audio is the
  browser's Web Speech; no regression vs S6, which also had no recorded packs.

## Tests & evidence
- `make test`: **541** backend (was 515), voice-gw 1, web typecheck+lint clean,
  **48** pure-logic web (walker conformance + offline-store + ESC/POS).
- New tests: `tests/test_offline.py` (20 — token partition, sync idempotency,
  collision attempts, bundle ETag), `tests/test_trees.py` canonical-form +
  fixed-point cases, `web/e2e/conformance.spec.ts`, `offline-db.spec.ts`,
  `print.spec.ts`, `offline-demo.spec.ts` (live-stack AC proof).
- Screenshots (`web/screenshots/s7/`):
  - `01-downtime-welcome.png` — marigold offline banner, calm reassuring copy
    ("your token and answers are safe"). Reads as informative, not an error.
  - `02-offline-token.png` — token 700 (a block token ≥500) on the train-board
    screen; marigold numerals on deep green, doc 04 §3 aesthetic executed.
  - `03-synced-welcome.png` — banner gone after reconnect + sync; back to calm.

## Known gaps / stubs introduced (mirrored to STATE.md)
- Voice-pack manifest format/generation not built; offline audio is Web Speech.
- `/kiosk/stt` server-STT endpoint (S6 carryover) still not built.
- ESC/POS: no printer has printed a slip; Devanagari needs the printer codepage
  set on the box (prints `?` until then). Browser fallback is the demo path.
- `AdmissionController`/`tiers.yaml` ladder wiring is still S-OSS.2 (unchanged).
- The demo e2e needs a live stack + `next dev` pointed at an API with S7 code;
  the dockerized image must be rebuilt to serve `/kiosk/bundle` (it predates it).

## Commits
- fc669f8 — S 07: canonical tree serialisation — the offline kiosk's wire shape
- 2cf5389 — S 07: offline tree walker + red-flag rules in TypeScript, golden traces
- 906cfd0 — S 07: offline token blocks + downtime sync — collisions unrepresentable
- 57c217a — S 07: GET /kiosk/bundle — the trees and chooser a kiosk caches
- 6f0305c — S 07: kiosk offline-first — Dexie, local intake flow, downtime, sync
- 2f75e05 — S 07: offline demo e2e — the S7 AC + marigold downtime bar
- d9b2845 — S 07: ESC/POS token-slip print bridge
