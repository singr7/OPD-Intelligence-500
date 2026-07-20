# SESSION-08 — Queue service + board + coordinator console

**Date:** 2026-07-20 · **Scope ref:** docs/06-BUILD-PLAN.md → S8 (doc 03 §6, 04 §3, 01 §5)

## Acceptance criteria checklist
- [x] **AC1 — three browsers (kiosk/board/coordinator) live-sync.** A confirmed
  kiosk intake enqueues and appears on the board + console with no refresh; a
  console `call-next` updates the board live. Proven in `web/e2e/queue.spec.ts`
  ("board updates live when the console calls the next token") over the real
  `/queue/ws` fan-out.
- [x] **AC2 — urgent red-flag intake jumps the queue with a reason chip.** A
  red-flag intake lands `urgent` (severity from the rules, not re-decided),
  sorts ahead of routine tokens, and shows a reason chip on both board and
  console. Unit-tested (`test_urgent_token_jumps_ahead_of_earlier_routine`) and
  visible in `screenshots/s8/board.png` + `coordinator-queue.png` (token 12
  jumped ahead of 10/11/13).
- [x] **AC3 — downtime drill end-to-end.** Coordinator enters downtime → board +
  console flip to the marigold OFFLINE banner (doc 04 §3); offline tokens
  continue on the S7 blocks; on sync, offline + paper intakes enqueue and appear
  on the reconciliation list with zero duplicate tokens (partition guarantee
  unchanged from S7). Covered by `test_offline.py` (sync) + new
  `test_reconciliation_*`, `test_paper_entry_*`, and the downtime toggle e2e.

## What was built
- **`app/queue.py`** — the queue service: `enqueue` / `enqueue_from_intake`
  (idempotent per visit; priority from `priority_from_red_flags`), `call_next`,
  a guarded `set_state` state machine (waiting→called→in_consult→done, no-show,
  lab-requeue rejoins at the back), `reorder` (priority still wins the sort),
  `estimate_wait` (observed mean consult time, seeded), and the `board` /
  `department_queue` read models. `paper_entry` for downtime recovery.
- **`app/queue_hub.py`** — in-process WebSocket fan-out + the in-memory downtime
  flag; broadcasts change-pings so board/console re-fetch.
- **`app/routes/queue.py`** — board (public), console + action verbs (staff),
  downtime get/set, reconciliation, paper-entry, and two print routes.
- **`app/print_sheets.py`** — downtime paper intake sheets (one fillable A4 form
  per tree, bilingual) + tear-off token-block sheet, both from live data. HTML→
  browser-print (no native PDF dep).
- **Kiosk confirm + offline sync now enqueue** and broadcast, so a token is on
  the board the moment it's issued — online or synced-from-downtime.
- **Web:** the TV **board** (`app/(board)/board`) — train-platform numerals,
  next-3, wait ranges, LIVE + clock, two-language chime + speech on change,
  marigold downtime banner. The **coordinator console**
  (`app/(coordinator)/coordinator`) — phone-OTP login, per-department queue with
  call-next/state actions/drag + up-down reorder, downtime enter/exit (repaints
  app bar marigold), reconciliation table, paper-entry form, print-sheets tab.
  Shared `app/_lib/queue.ts` + `useQueueSocket.ts`.
- **`backend/scripts/seed_queue_demo.py`** — deterministic demo seeder (resets
  today's demo state) so the board/console screenshots and the AC demo repeat.

## Decisions made
- **Token issuance was not replaced; the queue wraps it.** `allocate_token`
  already owns the online/offline partition (the whole S7 no-collision
  guarantee); S8 adds the `QueueEntry` around it rather than a second allocator.
- **Ordering is derived** from `(priority_rank, position, token_no)` — urgent
  jump falls out of the sort, drag only moves `position`, and a drag can never
  demote an urgent token below a routine one.
- **Downtime is an ephemeral in-memory flag**, not a row: it must be settable
  while the DB write path is the very thing that's down, and a restart (recovery)
  correctly resets it to "up".
- **WS + downtime carry no PII**, so the board holds the socket with no login.
- **The reason chip is stored in English** (staff-facing clinical label), not the
  patient's language; the board renders it as an urgent chip.
- **Paper entry is the one place a human sets priority** (a nurse's judgement on
  a paper sheet), and it carries a written reason for the audit trail.

## Deviations from spec
- **PDF is browser-printed HTML, not a server-rendered PDF.** Embedding Indic
  fonts + shaping in a hand-rolled PDF is fragile and a real HTML→PDF engine
  needs native libs we don't ship — so the print routes return print-optimised
  HTML (the same browser-fallback stance as the S7 ESC/POS bridge). A
  server-side PDF with embedded fonts is a deploy decision (backlog, S19/S21).
- **Queue is per-department, `doctor_id` null.** The board is per room/department
  and a walk-in kiosk chooses no doctor; per-doctor queues are a later split when
  rooms are assigned (backlog, S9/S18).

## Tests & evidence
- `make test`: **577** backend (was 541) + 1 voice-gw + web typecheck/lint + 48
  conformance — all green.
- New tests: `tests/test_queue.py` (36) — service (enqueue/priority/ordering/
  state machine/reorder/estimator/board/paper), hub fan-out, print rendering,
  and routes (board public, console staff-gated, downtime, reconciliation,
  paper-entry, call-next). Live e2e: `web/e2e/queue.spec.ts` (project `queue`, 5
  tests) drives the real WS.
- Screenshots (`web/screenshots/s8/`), self-critiqued vs doc 04 §5:
  - `board.png` — train-board realised: 8m-legible marigold numerals on deep
    green, urgent chip, next-3, wait ranges, LIVE + clock. The chosen risk
    (train-board numerals + flip + chime) executed; everything else quiet. ✓
  - `coordinator-queue.png` — dense-but-calm, same tokens; urgent token 12 at the
    top with reason + red-flag chips; Hindi complaints render. ✓
  - `coordinator-downtime.png` — app bar repaints marigold + "OFFLINE — tokens
    continue" banner, exactly doc 04 §3. ✓
  - `coordinator-login.png`, `coordinator-paper.png` — clean, on-brand.

## Known gaps / stubs introduced (mirrored to STATE.md)
- The `/queue/ws` route is covered by the `queue` e2e, not a unit test (the
  ASGITransport client can't easily do WS); the hub logic itself is unit-tested.
- Board/console reason chip + department names render in English until S13.
- The downtime flag + hub are per-process (one api container at pilot scale); a
  second replica needs Redis pub/sub (backlog, S19/S20) — same caveat as the
  cost-guard override store and the OSS AdmissionController.
- Staff token is localStorage (not httpOnly); a cookie hardening pass is S19/S20.
- Wait estimator is coarse (±20%); it uses observed mean consult time once the
  day has completed consults, else a configured seed.

## Commits
- 5d68b59 — S 08: queue service — real issuance, urgent red-flag jump, wait estimator, live fan-out
- 91d22a9 — S 08: downtime paper sheets — fillable intake forms + tear-off token block, from live data
- de8e555 — S 08: queue board + coordinator console — live-sync train-board, drag reorder, downtime, reconciliation
- 6419993 — S 08: queue e2e demo + WS route fix + hydration fix — the three-browser AC, proven live
