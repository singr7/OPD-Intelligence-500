# HANDOFF — after Session S7 (kiosk offline-first)

**Repo state:** branch `main`. `make test` green: backend **541** (was 515),
voice-gw 1, web typecheck+lint clean, **48** pure-logic web tests. Postgres on host
port **5433**; voice-gw on 8090. A migration was added (`bc2e83129ac3`) — run
`make migrate`. `make dev`'s api image predates this session; rebuild it (or run a
local uvicorn) to serve `/kiosk/bundle`.

**One paragraph:** S7 made the kiosk survive an outage (doc 01 §5). The tree
walker + red-flag rules are now ported to TypeScript (`web/.../_lib/tree/`) and run
in the browser when the API is unreachable — a second implementation of clinical
logic, trusted only because a golden-trace conformance suite gates it against the
Python original in `make test` (mutation-tested; a `gte`→`gt` fails it). Token
collisions are made **unrepresentable** by partitioning the number line: online
`< 500`, offline blocks `>= 500`, enforced on both sides. `POST /kiosk/blocks/lease`
leases per-kiosk ranges while online; `POST /kiosk/sync` replays finished offline
intakes idempotently (per `client_id`) and recomputes red flags server-side; the
kiosk caches everything via `GET /kiosk/bundle` + Dexie + a shell service worker,
flips to a marigold Downtime banner after 60s offline, and syncs on reconnect. The
demo AC is proven both at the service layer (`test_offline.py`) and in a browser
(`web/e2e/offline-demo.spec.ts`). Token slips print via an ESC/POS byte builder
with a browser fallback. **Voice-pack manifest and `/kiosk/stt` were deferred.**

## Next session — pick one
**Main line: S8 — Queue service + board + coordinator console** (docs 03 §6, 04 §3,
01 §5). Builds directly on S7: the offline token blocks + `sync` reconciliation are
the raw material for the coordinator's downtime-reconciliation screen, and
`allocate_token` is still the provisional allocator S8 replaces with real
queue-managed issuance (priority/urgent insertion, the wait-time estimator).
- Exact first commands: `make dev` → `make migrate && make seed` → `make test`.
  Rebuild the api image so `/kiosk/bundle` and `/kiosk/sync` are served, or run a
  local uvicorn (see below).

**To re-run the S7 offline demo** (needs a live API with S7 code):
`cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd
.venv/bin/uvicorn app.main:app --port 8123` then
`cd web && NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx next dev -p 3210` then
`KIOSK_URL=http://127.0.0.1:3210 NEXT_PUBLIC_API_BASE=http://127.0.0.1:8123 npx
playwright test --project=offline-demo`.

## Watch out for
- **The dockerized api image is stale** — it was built at S6 and 404s on
  `/kiosk/bundle`. If a kiosk boots and never caches/leases, rebuild the api image
  (`make build`) or run local uvicorn. This bit the demo once (tokens came back as
  online 4,5,6 because the browser hit the old :8000 image).
- **Never let a token cross the base (500).** The online allocator and offline
  blocks are disjoint by construction; that is the whole no-collision guarantee.
  `allocate_token` raises when the online range fills rather than wrapping.
- **The conformance gate is load-bearing.** Change `app/trees/rules.py` or
  `walker.py` and you must `make tree-fixtures` + re-run `--project=conformance`,
  or CI fails. It is mutation-tested; do not weaken the sampler.
- **`make test` now runs the web conformance/offline/print suites** (pure logic,
  fake-indexeddb) but NOT `offline-demo` (needs a live stack) or `kiosk` (S6).
- **The block cursor is client-authoritative while offline** — `saveBlocks` never
  rewinds `nextFree` to the server's stale `used_up_to`. Do not "fix" it to trust
  the server after a reboot; those numbers are on paper slips already.

## Decisions needed from the human
- None blocking. When the GPU box arrives, S-OSS.1 unblocks (unchanged from S6).

## Backlog additions
- **S7-carryover / S8: voice-pack manifest** — format + placeholder TTS-rendered
  packs (per language, per node `audio` clip), served alongside the bundle and
  cached for offline V3 audio. Seam exists (`app.intake.voicepack.VoicePack`);
  today offline audio is the browser's Web Speech. Real human recordings are S21.
- **S7-carryover: `/kiosk/stt`** — a server-STT endpoint over `stt_chain` +
  MediaRecorder for the "trouble hearing?" chief-complaint path (doc 06 S6 line).
- **ESC/POS Devanagari** — set the printer codepage on the box so Hindi lines
  print; today they fall back to `?` and the slip leans on the ASCII token/time.
- **A kiosk admin "list synced intakes" endpoint** — the demo proves sync from the
  client side; a server-side reconciliation list is the coordinator's S8 screen.
