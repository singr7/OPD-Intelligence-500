# STATE

**Built (S1):** Monorepo skeleton — `backend/` (FastAPI api + Celery worker/beat), `voice-gw/`
(FastAPI), `web/` (Next.js 14, 5 route groups, design tokens), `infra/` (Terraform pilot,
plan-only + Caddyfile). Full docker-compose stack (11 services) runs healthy via `make dev`. CI
(GitHub Actions), Makefile, pre-commit.

**Built (S2):** Full doc 02 §4 schema — 21 SQLAlchemy models + Alembic migration that round-trips
and matches the models. Phone-OTP login → JWT (access + rotating refresh with revocation), Argon2
hashing, RBAC guards. Append-only audit trail covering every clinical write. Idempotent seed
(1 hospital, 9 departments, 5 doctors + 3 staff, 50 deterministic patients).

**Built (S3):** Provider layer (doc 02 §9) — seven interfaces, each with a fake and a real impl:
SMS (**MSG91 + Exotel, both**), LLM (Gemini Flash + OpenAI), STT (Sarvam + Google), TTS (Sarvam +
Google), Messaging (Meta WhatsApp), Telephony (Exotel); Realtime = interface + fake only.
Usage metering into `usage_events` (async, batched, priced against `price_book`), cost
computation, retry + circuit breaker, provider health registry (`GET /providers/health`),
cost-guard (budget → tier downgrade), and `prompts/` — four versioned vendor-neutral prompts +
the V1/V2 shared tool contract. 231 backend tests. `make test` green.

**Built (S4):** Question-tree engine (`app/trees/`) — doc 03 §3's node schema + a validator
that rejects unreachable nodes, cycles, incomplete languages, >5 options (doc 03 §1a) and
rules that can never fire; a deterministic red-flag rule language (`rules.py`) no model
participates in; and `Walk`, one patient's position in one tree, **derived from the answers**
(the V3 tier, and the engine under S5's four tools). 11 authored trees in `seeds/trees/`
(en+hi, 89 nodes, 40 red flags) covering all 9 departments, seeded as **draft**. Department
classifier (`app/routing.py`) around `routing@v1`, plus a 60-utterance eval set and harness
(`app/evals.py`, `make eval-routing`). 466 backend tests. `make test` green.

**Built (S5):** Intake Engine (`app/intake/`) — one `IntakeEngine` driving an intake
across the V1/V2/V3 tier ladder, all calling the same four-tool contract over one
`Walk` via `ToolDispatcher`. `SessionState` in Redis (in-memory local) stores the
**answers, not a cursor**, plus configured + active tier. V1 = Gemini Live session
bridge (audio passthrough hook for voice-gw); V2 = STT→LLM→TTS turn pipeline; V3 =
deterministic walker + `voicepack` (TTS fallback). Automatic downgrade on provider
failure OR cost-guard, rebuilding the walk from stored answers (lossless). Summarizer
(`summary.py`) = doc 03 §4 contract + patient read-back, LLM path with a deterministic
offline fallback; red flags always from the rules. `finalize_cost` sums `usage_events`
by intake_id onto `Intake.cost_inr`. `prompts/intake/v1.md` = the dialogue driver.
466→**486 tests**. Not wired to any route (channels are S6/S12/S14).

**Built (S6):** The **kiosk channel** — the intake engine's first HTTP surface.
`app/routes/kiosk.py` = thin REST mirroring the four-tool contract (start / next /
answer / finish / confirm); `app/kiosk.py` = the service (route Q1 through the
classifier honouring `needs_human` → a department chooser; create the walk-in
Visit+Intake; provisional token allocation). One `IntakeEngine` on `app.state`.
The **kiosk PWA** (`web/app/(kiosk)/kiosk/`) — a V3 client driven by taps + audio:
expanded design tokens on the doc 04 §1 palette, self-hosted Noto Sans/Devanagari,
a component library (breathing **Dhara** avatar, AudioBar, OptionCard, FacesScale,
Stepper, BodyMap, ProgressDots, MicButton, duotone icons), and the full flow
language → caregiver → voice chief complaint → chooser → tree questions
(auto-read-aloud) → read-back + confirm → **train-board token**. Audio-first, ≥64px
targets, 60s idle prompt / 90s privacy blur. Playwright suite (`web/e2e`) drives a
full hi intake welcome→token against the local stack; 11 screens in
`web/screenshots/s6/`. **492 backend tests** (486→492) + web e2e. `make test` green.

**Built (S7):** The **kiosk goes offline-first** (doc 01 §5). The tree walker + red-flag
rules are ported to TypeScript (`web/app/(kiosk)/kiosk/_lib/tree/`) so an intake completes
in the browser with no API, gated against the Python original by a golden-trace conformance
suite (`app/tree_fixtures.py` → `web/e2e/conformance.spec.ts`, regenerated + diffed in
`make test` via `make check-tree-fixtures`; mutation-tested). `Tree.to_json()` is the
canonical desugared wire shape. **Offline token blocks** (`app/offline.py`): the token line
is partitioned — online `< kiosk_offline_token_base(500)`, offline blocks `>=` — so a
collision is unrepresentable; `POST /kiosk/blocks/lease` (idempotent) + `POST /kiosk/sync`
(idempotent per `Intake.client_id`, recomputes red flags server-side). `Intake` gained
`client_id` + `tree_ref` (migration `bc2e83129ac3`); `allocate_token` refuses to cross the
base. `GET /kiosk/bundle` ships canonical trees + chooser (ETag). Web offline layer
(`_lib/offline/`: Dexie store, local intake flow, `/health` reachability monitor, the
online-or-local `flow` seam, background sync, `useOffline` lifecycle) + a shell service
worker (`kiosk-sw.js`) + a **marigold Downtime banner** (doc 04 §3). ESC/POS token-slip
print (`_lib/print.ts`) + browser fallback. Demo AC proven at the service layer
(`test_offline.py`) and in a browser (`web/e2e/offline-demo.spec.ts`). 515→**541 tests** +
48 pure-logic web tests.

**Built (S8):** The **live queue** over the tokens (doc 03 §6). `app/queue.py` = a
`QueueEntry` per visit with ordering *derived* from `(priority_rank, position,
token_no)`, so an urgent red-flag intake jumps the line by construction (severity
from the rules, never re-decided) with a reason chip; plus `call_next`, a guarded
state machine (waiting→called→in_consult→done / no-show / lab-requeue-to-back),
drag `reorder` (priority still wins), a wait estimator (observed mean consult time,
seeded), `board`/`department_queue` read models, and `paper_entry` for downtime
recovery. The kiosk **confirm** and the S7 offline **sync** now `enqueue_from_intake`
+ broadcast, so a token is on the board the instant it's issued (online or
synced-from-downtime). `app/queue_hub.py` = in-process WebSocket fan-out + the
in-memory downtime flag. `app/routes/queue.py` = board (public) + `/queue/ws`,
console + action verbs (staff), downtime get/set, a reconciliation list (offline +
paper intakes), a paper-entry form, and two print routes. `app/print_sheets.py` =
downtime paper intake forms (one fillable A4 per tree, bilingual) + a tear-off
token-block sheet, both from live data (HTML→browser-print). Web: the **TV board**
(`app/(board)/board` — train-platform numerals, next-3, wait ranges, LIVE + clock,
2-lang chime + speech announce, marigold downtime banner) and the **coordinator
console** (`app/(coordinator)/coordinator` — phone-OTP login, call-next/state/drag
reorder, downtime enter/exit repainting the app bar marigold, reconciliation table,
paper-entry form, print tab); shared `app/_lib/queue.ts` + `useQueueSocket.ts`.
541→**577 tests** + `web/e2e/queue.spec.ts` (live) + `scripts/seed_queue_demo.py`.
No migration (Queue/QueueEntry existed since S2).

**Built (S9):** The **doctor console** (doc 03 §4/§5). `app/doctor.py` is two reads and
no writes: `day_list` (the doctor's own department queue, in the queue's own urgent-first
order, with the patient behind each token) and `patient_card` (the stored doc 03 §4
summary, the rule engine's red flags, the answers rendered against the tree in `tree_ref`,
the visit timeline, and the check-in trendline). `app/routes/doctor.py` exposes
`GET /doctor/day` + `GET /doctor/patients/{visit_id}`, both `require_doctor`. **S9 added no
action endpoints** — call-next / no-show / lab-requeue are the S8 `/queue/*` verbs the
coordinator console already drives. Web: `app/(doctor)/doctor` — phone-OTP login lifted
from S8, the day list as a **vertical clinical spine** (tokens as stations, the patient in
the room the one filled marigold node, urgent tokens ringed in danger), and the patient
card ordered by clinical urgency: red-flag **stamps** carrying the rule's own instruction,
then chief concern + compact symptoms table, then everything else collapsed. Keyboard
shortcuts N (call next) and D (dictation → honest "S10"). 581→**603 tests** +
`web/e2e/doctor.spec.ts` (project `doctor`, the session AC as a test) +
`scripts/seed_doctor_demo.py`. No migration.

**Built (S10):** **Dictation → structured fields** (doc 03 §7). `app/formulary.py` +
`seeds/formulary.json` (189 generics / 617 dictatable names, chemo through supportive
care): `known` is set by **exact match only**, fuzzy matching produces advisory
`suggestions` and an `ambiguous` flag, and there is no code path from a score to a
written name. `app/dictation.py` = the doc 03 §7 contract, `validate_meds` (which
discards the model's own `known` claim), `_was_said` (a drug name absent from the
doctor's own words is flagged — the only way to catch a model that *renamed* a drug
into another real one), `DictationMapper` (the `dictation_map@v1` prompt on the LLM
chain, so `LLM_PROVIDER=local_vllm` runs it on the box's Qwen3 unchanged), and the
record's state machine `start → map → correct → sign`. `mapped` is frozen and `fields`
carries the doctor's corrections with an append-only `edits` trail, which is what makes
the review diff-style. Signing locks the record and refuses while any flagged drug is
unacknowledged. `app/routes/dictation.py` = six routes, all `require_doctor`. Web:
`_components/DictationPanel.tsx` — the consult note on the console stage, where every
written value hangs under the phrase it came from. 603→**708 tests** +
`web/e2e/dictation.spec.ts` (project `dictation`) + `backend/tests/fixtures/dictations.json`
(ten Hinglish notes). No migration.

**Built (S-OSS.0):** The **V-OSS** software layer (doc 08) — the fully-open-source local
voice tier as ordinary provider adapters, no GPU required to build. `app/providers/local_oss/`:
`LocalLLMProvider` (vLLM, reusing the OpenAI wire, keyless), `LocalSTTProvider` (Whisper,
OpenAI-audio-compatible), `LocalTTSProvider` + `VoiceboxTTSProvider` — all config-only-selectable
(`LLM_PROVIDER=local_vllm`, `STT_PROVIDER=local_whisper`, `TTS_PROVIDER=local_tts|voicebox`) and
metering `provider=local-*`, priced from amortized `local-*` `price_book` rows. `config/tiers.yaml`
+ `app/tiers.py` = per-channel tier ladder loader (validated at boot); `AdmissionController` =
`MAX_OSS_SESSIONS` concurrency cap that routes overflow to the next tier and frees seats on crash.
492→**515 tests**. The **GPU half** (S-OSS.1 bake-off, S-OSS.2 Pipecat realtime + 12-concurrent
proof, S-OSS.3 Dhara cloning) needs the physical 24 GB box — not built here; `local-pipecat`
realtime refuses to build until then.

**Not built yet:** channel adapters for WhatsApp (S12) and
telephony (S14); real voice packs / the voice-pack manifest + `/kiosk/stt` (S7
carryover → backlog); the real Gemini Live impl (S14); the V-OSS **GPU half**
(S-OSS.1/.2/.3 — needs the GPU box).

## How to run
```
make dev                 # full stack (11 services)
make migrate             # apply migrations to the local DB
make seed                # load the pilot dataset + price book + trees (idempotent)
make test                # backend pytest + voice-gw pytest + web typecheck/lint
make migration m="..."   # autogenerate a revision from model changes
make eval-routing        # score the routing classifier (needs a real LLM key to mean anything)
```
Queue board + coordinator console (S8): served at `/board` (public TV) and
`/coordinator` (staff, phone-OTP). The board holds a WebSocket to `/queue/ws` and
re-fetches on every change ping. Live demo (needs a live api with S8 code — the
dockerised image predates it): run a local uvicorn with `OTP_RESEND_COOLDOWN_SECONDS=0`,
`python -m scripts.seed_queue_demo` for a deterministic demo queue, then
`npm run e2e:queue`. Coordinator login: `+915550000002` (seeded coordinator); the
OTP is echoed on the login screen locally. See HANDOFF.md for the exact commands.
Kiosk PWA: `web/app/(kiosk)/kiosk`, served at `/kiosk` (web on :3000, api on :8000;
`NEXT_PUBLIC_API_BASE` points the browser at the api). The Playwright screenshot
suite runs against a live stack: `cd web && npm run e2e` (needs `make dev` + a
seeded dev DB; drives welcome→token, writes `web/screenshots/s6/`). The kiosk is a
V3 client — the fake classifier always triages, so Q1 lands on the department
chooser locally; pick a department to proceed.
Local login: `POST /auth/otp/request {"phone": "+915550001001"}` (seeded doctor) returns
`debug_code` when `OTP_DEBUG_ECHO=true`; POST it to `/auth/otp/verify` for a JWT.
Provider status: `GET /providers/health` (unauthenticated; names + health only, never keys).

## Environment gotchas
- **Postgres: host port 5433**, not 5432 — a native Postgres owns 127.0.0.1:5432 on this dev
  machine and wins over Docker's bind, so 5432 silently reaches the wrong database. In-cluster
  URLs are unchanged (`postgres:5432`). Tests default to `localhost:5433/opd_test`
  (`TEST_DATABASE_URL` to override); `ALEMBIC_DATABASE_URL` overrides for alembic by hand.
- **voice-gw on host port 8090** (8080 taken by another local project).
- **`.env` is gitignored and does not auto-update.** `make .env` only copies `.env.example` when
  the file is missing — after a session that adds keys, append them by hand. S3 added ~30. All
  providers default to `fake`, so a stale `.env` runs fine but ignores any vendor you configure.
- `terraform` is not on PATH (brew blocked by old Xcode); CI covers `terraform validate`.
- Tests require a real Postgres and build the schema via `alembic upgrade head` — SQLite would
  not have JSONB or the audit triggers.

## Env vars
See `.env.example` (authoritative). Notable: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET` (≥32
chars), `OTP_DEBUG_ECHO` (local only), and the S3 provider block — one `*_PROVIDER` selector per
interface (`fake` by default), optional `*_FALLBACK_PROVIDER` chains, vendor credentials, and
`DAILY_BUDGET_INR` (per-channel cost-guard caps; a channel with no entry is uncapped). **V-OSS
(doc 08):** `local_vllm|local_whisper|local_tts|voicebox` are valid provider selectors backed by
`LOCAL_VLLM_BASE_URL`, `LOCAL_STT_URL`, `LOCAL_TTS_URL`, `VOICEBOX_URL` (a base URL is all a local
provider needs to count as configured — no key); per-channel ladder + `max_oss_sessions` live in
`config/tiers.yaml`, not env. **Offline kiosk (S7):** `KIOSK_OFFLINE_TOKEN_BASE` (default 500 —
online tokens stay below it, offline blocks at/above) and `KIOSK_OFFLINE_BLOCK_SIZE` (default 50).
**Queue (S8):** `QUEUE_DEFAULT_CONSULT_MINUTES` (default 6) — the wait-estimator seed before
a department has any completed consults to measure; no other queue config (downtime is an
in-memory flag, not env).
Web: `NEXT_PUBLIC_PRINT_BRIDGE_URL` (a kiosk's local thermal-print daemon; absent = browser print
fallback).

Doctor console (S9) + consult note (S10): served at `/doctor` (staff, phone-OTP). Needs a
live api with S9/S10 code and a seeded demo morning. **Re-seed before every e2e run** —
signing a note is terminal, and the seed hard-deletes its own dictations to stay repeatable:
```
cd backend && DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
  .venv/bin/python -m scripts.seed_doctor_demo      # 5 MEDONC walk-ins, one urgent
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npm run e2e:doctor                                # the full-morning AC + screenshots
cd web && API_BASE=http://127.0.0.1:8123 KIOSK_URL=http://127.0.0.1:3210 \
  npm run e2e:dictation                             # the S10 AC + screenshots
```
Doctor login: `+915550001001` (seeded Dr. Anil Gupta, MEDONC); the OTP is echoed locally.
In the console, pick a patient and press **D** for the consult note.

CI (GitHub Actions) is **manual-only** since 2026-07-23 (operator: it was burning free
Actions minutes). `.github/workflows/ci.yml` is intact; only its `push`/`pull_request`
triggers are commented out. `gh workflow run ci.yml` to run it. **`make test` locally is
the only gate right now.**

## Invariants (don't quietly break these)
- **Anything patient-affecting subclasses `Clinical`** (`app/models/base.py`) — that alone makes
  writes audited, via a `before_flush` hook on `AuditedSession`. There is no per-route audit call.
- **`audit_log` is append-only in the database** — triggers reject UPDATE/DELETE/TRUNCATE for
  every client, including psql. Pruning requires an explicit migration that drops them.
- **Audit records that a field changed, never the PII** (`REDACTED_FIELDS` in `app/audit.py`).
- **Soft deletes only** on clinical tables; set `deleted_at`, never DELETE.
- **Money is `Numeric`/`Decimal`, never float** — costs must reconcile exactly against
  `usage_events` (S18 AC).
- **New model ⇒ import it in `app/models/__init__.py`**, or it is silently missing from migrations.
- **Every external call behind a provider interface**, each with a fake (doc 02 §9). Feature code
  must never import a vendor SDK, and never name a vendor — ask `app.providers.get_*_provider()`.
- **Metering is not optional.** `Provider._invoke` is the only way to reach a vendor; impls
  implement the private verb and report usage on the `MeterCall`. Don't add a public method that
  bypasses it.
- **Never edit a `price_book` rate in place** — add a row with a later `effective_from`. Editing
  silently re-interprets every historical cost computed at the old rate.
- **A dictated drug name is never rewritten** (S10) — `app.formulary` sets `known` on an
  exact match alone; fuzzy neighbours are `suggestions` shown to the doctor and never a
  value in a field, and `app.dictation.validate_meds` copies `name` through verbatim.
  "Auto-apply the closest match" would delete the S10 acceptance criterion.
- **A signed dictation does not change** — every mutating entry point in `app.dictation`
  raises `DictationLocked` once `status=signed`. Correcting one is an amendment, and this
  system has no amendment anywhere yet.
- **A published prompt version is immutable** — to change a prompt, add `v<N+1>.md`. Outputs are
  traced back to `id@vN`.
- **The tool contract is versioned** (`app/prompts/tools.py`) — changing a tool's shape means a
  new version, not an edit; a half-finished intake resuming against a redefined `save_answer` is
  silent data corruption.
- **The cost guard degrades, never denies.** It may lower a tier (V1→V2→V3); it must never block
  a call, deny an intake, or force `paper` (that is a human's downtime decision).
- **A `Tree` can only be built by `app.trees.schema.parse()`** — so a `Tree` in hand is
  validated by construction. Reading `question_trees.tree` and using the dict directly skips
  every check, including the ones for unreachable questions and unfireable red flags.
- **No model ever decides a red flag**, on any tier. Rules are data in the tree, evaluated
  deterministically (`app/trees/rules.py`). A model-decided flag would answer "is this fever
  dangerous?" differently depending on which vendor was up, and would be unreviewable.
- **Rules can't match `free_voice` answers** — the validator rejects it. That text is ASR
  output; matching it makes a flag depend on the transcriber and fires "no blood in my stool"
  as bleeding.
- **The walker's position is derived from the answers, never stored.** Do not add a cursor:
  it becomes a second source of truth that disagrees exactly when a provider is failing over,
  and it is what makes a tier downgrade lossless. `Walk.save()` prunes the answers stranded
  on an abandoned branch — anything derived from `walk.answers` must be recomputed after a
  save, not cached.
- **Red flags are recomputed, never accumulated** — an amendment that removes the alarming
  answer removes the flag.
- **A published tree version is immutable in spirit** — bump `version` in the file rather than
  editing content that has been asked, or every intake citing `key@vN` silently re-reads.
- **Trees are seeded `draft`.** Publishing is a clinical act (doc 03 §3, S21), not a seed
  script's. `--publish-trees` is the explicit opt-in for a dev box.
- **A session stores answers, never a tier cursor** (`app/intake/state.py`). Position is
  derived by the walker; a downgrade rebuilds `Walk.from_json(tree, answers)` on the new
  tier and loses nothing. Adding a cursor "for speed" reintroduces the exact bug the ladder
  avoids — two sources of truth that disagree when a provider is failing over.
- **The intake summariser never decides a red flag**, on any tier. Flags come from
  `Walk.red_flags` (the rules) and are passed in; the LLM path overwrites the model's flag
  list with the rules', the template path just lists them. Same boundary as the tool loop.
- **The engine downgrades, never denies** — a provider outage or a cost-guard breach lowers
  the tier (V1→V2→V3); a completed V3 intake needs no vendor for its summary. Nothing in the
  engine may block or fail an intake for cost or an outage (that is a human's paper decision).
- **The queue never renumbers a token** (`app.queue`) — it wraps `allocate_token`, it does
  not re-issue. A token is a promise to a patient holding a slip; priority reorders the
  *queue*, never the number. The online/offline partition (S7) stays the no-collision guarantee.
- **Queue order is derived, never stored as a rank** — `(priority_rank, position, token_no)`.
  Urgent-jump falls out of the sort (severity from the rules, not re-decided, and not a
  coordinator's manual move); a drag only rewrites `position` and can never demote an urgent
  token below a routine one. The one place a human sets priority is a **paper** entry.
- **A WebSocket route reads app state directly, never via a `Request` dependency** — a WS
  scope has no `Request`, so `Depends(get_hub)` 500s the handshake. `/queue/ws` uses
  `ws.app.state.queue_hub`.
- **The doctor console owns no queue mutation** (`app.doctor` is two reads). Call-next,
  no-show and lab-requeue are the S8 `/queue/*` verbs, called with the doctor's own token.
  Adding a `/doctor/call-next` gives the board and the console two state machines that
  disagree the moment one is patched — and two audit trails to reconcile.
- **The doctor's card never re-derives clinical judgement** — red flags are read from
  `Intake.red_flags` (the rule engine) and the summary from
  `summary_lang_versions[...]["structured"]` (the summarizer). A doctor screen that
  recomputed either would show a different clinical picture than the kiosk told the
  patient and than the queue prioritised on. The one thing `app.doctor` *does* ask the
  tree is which nodes a **already-fired** rule's `when` condition referenced, to highlight
  the answers behind it — that reads the rule, it does not evaluate it.
- **Client components inject CSS via `dangerouslySetInnerHTML`, not a `<style>{text}` child**
  — the text child hydrates as a mismatch (quotes escape differently SSR vs client) and
  flickers the whole subtree to client rendering.

## Stubs & fakes
- **Kiosk token issuance is `max(token_no)+1` and stays that way** —
  `app.kiosk.allocate_token` allocates below the offline base (500), guarded by the
  unique constraint. S8 did **not** replace it: the queue (`app.queue`) *wraps* it
  with a `QueueEntry` (priority/urgent insertion, wait estimate, reconciliation)
  rather than re-issuing a number, because a token is a promise to a patient holding
  a slip. The offline blocks (`app.offline`, S7) still partition the number line so
  online and offline never collide. A gap-free / reserved-number scheme is not needed.
- **`seed_doctor_demo`'s structured summaries are authored fixtures**, standing in for what
  the LLM path (doc 03 §4) writes on a box with a real model — the deterministic V3
  `TemplateSummarizer` emits only "question: answer" lines and no symptom table, which
  would under-sell a screen whose whole job is a 20-second read. The **answers and red
  flags in the same seed are genuinely derived** (a real `Walk`, real `walk.red_flags()`).
  Like `seed_queue_demo` it hard-deletes its own rows to stay repeatable.
- **`FakeLLMProvider` answers `dictation_map` with a canned, contract-shaped payload**
  (S10, `_CANNED_JSON` in `app/providers/llm.py`) — a fake that says "ok" to a
  `response_format: json` prompt can only ever demonstrate the failure path, and the
  fakes exist so whole flows can be demoed without a vendor. It is a **demo** fixture:
  tests asserting on mapped content queue their own `FakeLLMScript`, which always wins.
  The canned reply carries one off-formulary drug on purpose.
- **`_was_said` is token presence, not alignment** (S10) — a drug the doctor said in a
  *different* sentence than the one the model quoted still passes. Tighter matching needs
  word timings from the STT, which we do not store.
- **Signing a dictation emits nothing** (S10) — doc 03 §7 says it generates the
  prescription (§8) and the check-in plan draft (§9); those are S11 and S17. Writing a
  half-shaped `Prescription` row now would be a migration for a later session to undo.
- **The formulary is a seed file read at boot**, not a table — a hospital adding a drug
  needs a deploy until S18's admin console owns it.
- **The doctor's day list has no appointments** — doc 03 §5 says "appointments+walk-ins",
  but the S8 queue holds walk-ins and `Appointment` has no check-in flow until S15, so the
  worklist is the queue only. Not faked; backlog for S15/S18.
- **The doctor console has no WebSocket** — it refetches after its own mutations, so a
  coordinator moving the same line elsewhere is not pushed to the doctor until they next
  act. The `/queue/ws` hub already exists to subscribe to (S18 polish).
- **The doctor's staff token is localStorage**, and deliberately the *same* key as the
  coordinator console so one staff session covers a shift — same S19/S20 httpOnly
  hardening note.
- **Symptom sparklines read a shape S17 does not write yet** — `Checkin.responses` is
  S17's, so `app.doctor._trends` picks out numeric values defensively and needs ≥2 points
  to draw. The trendline lights up when S17 starts writing check-ins; until then only
  `seed_doctor_demo` produces one.
- **The queue + downtime flag are in-memory, single-process** (`app.queue_hub`) —
  correct for the one pilot api container; a second replica would each hold their own
  WS clients and downtime flag and miss each other's. Fix is a Redis pub/sub channel
  (S19/S20), same shape as the cost-guard override store and the OSS AdmissionController.
- **`/queue/ws` is covered by the live `queue` e2e, not a unit test** — the
  ASGITransport test client can't easily drive a WebSocket; the `QueueHub` logic
  itself is unit-tested. The route reads the hub off `ws.app.state` (a WS scope has
  no `Request`, so `Depends` on a Request-typed provider 500s the handshake).
- **The coordinator staff token is localStorage, not an httpOnly cookie** — fine for
  a pilot on a trusted LAN behind Caddy; a cookie hardening pass is S19/S20. The
  minimal phone-OTP login was lifted into S9's doctor console, which shares the key.
- **Board/console reason chips + department names render in English** — the stored
  priority reason is the English clinical label; dept-name/chip localisation is S13.
- **Downtime paper sheets are browser-printed HTML, not server PDFs** —
  `app.print_sheets` returns print-optimised HTML (A4, tick-boxes, Devanagari) that
  the browser turns into a PDF, the same fallback stance as the S7 ESC/POS bridge. A
  server-side PDF with embedded Indic fonts is a deploy dependency decision (S19/S21).
- **Offline audio is the browser's Web Speech** — the voice-pack manifest + placeholder
  TTS packs were deferred (S7 backlog). No recorded packs exist (S21); the `VoicePack`
  seam (`app.intake.voicepack`) is unchanged from S5.
- **The offline TS walker/rules are a second implementation of clinical logic** — trusted
  only because `make check-tree-fixtures` + `web/e2e/conformance.spec.ts` gate them against
  the Python original (mutation-tested). Change `app/trees/` ⇒ `make tree-fixtures`.
- **Kiosk session state is in-memory locally** (`is_local` → `InMemorySessionStore`),
  so the multi-request flow only survives within one api process. Prod is Redis. A
  second uvicorn worker locally would not share sessions. (Offline sessions live in the
  browser tab, not the server, and do not survive a tab reload mid-intake by design.)
- **Server-STT is built (`POST /kiosk/stt`)** — the kiosk records the chief complaint
  (MediaRecorder) and posts the clip to `/kiosk/stt`, which runs it through `stt_chain`
  (local Whisper on a V-OSS box → audio stays on-premises). Gated by the build-time flag
  `NEXT_PUBLIC_KIOSK_SERVER_STT=1`; **off by default**, when the kiosk uses the browser's
  Web Speech (Chrome ships that audio to a cloud recogniser). Tap-to-type is always behind
  both. The endpoint is proven with the fake STT (returns "haan"); a real transcript needs
  a live Whisper on the box. `python-multipart` was added for the upload.
- **No printer has printed a slip** — `_lib/print.ts` ESC/POS bytes are built against the
  documented 58mm command set and unit-tested; the first real slip needs a human at a
  printer. Devanagari needs the printer codepage set on the box (prints `?` until then).
- **No true "back" inside a kiosk walk** — the walk has no rewind endpoint; the
  read-back "change something" restarts the intake. S9 did not add one either (the
  doctor console is read-only over the answers), so a per-node amend — and the
  summary regeneration doc 03 §4 wants after a coordinator edit — is S18.
- **The kiosk icon set is a branded subset + aliases + a neutral fallback**, not the
  full ~65-key custom duotone set doc 04 law 4 wants; the full set + human review is
  a design-asset task (S7/S21). No option is ever iconless.
- **A pure-V3 kiosk intake finalises at ~₹0** — no metered calls happen in the walk,
  and Q1's routing-classifier cost is not attributed to the intake (routing runs
  before the intake_id exists). A `usage_scope(intake_id=...)` around the classifier
  is backlog.
- **Kiosk department names render in English** on the hi flow (seeded English names);
  dept-name localisation is S13.
- **No live vendor has ever accepted a call.** Every real impl (MSG91, Exotel SMS/telephony,
  Gemini, OpenAI, Sarvam, Google, Meta) is written against documented HTTP APIs and tested
  through `httpx.MockTransport` — real request-building and response-parsing, mocked wire.
  Endpoints, DLT template ids, sender ids and auth are per-account. **The first live send of each
  needs a human watching a real handset/number.**
- **Realtime (Gemini Live / tier V1): session manager built (S5), impl still fake only.**
  `IntakeEngine._run_v1` drives the `RealtimeVoiceProvider` interface and is proven against
  the fake; the real websocket session + the Exotel↔Live audio bridge are S14.
  `REALTIME_PROVIDER=gemini-live` still raises rather than pretending.
- **V2 is a turn pipeline, not token streaming, and does not feed tool results back to the
  LLM within a turn** — the request/response `LLMProvider` has no tool-result message type, so
  the engine mediates `get_next_node` by injecting the current question into the prompt. Fine
  for kiosk/WhatsApp; S14's real-time telephony wants true streaming + a tool-result turn.
- **The intake engine is not wired to any route** — it is a service class; channel adapters
  (kiosk S6, WhatsApp S12, telephony S14) will construct it and feed it turns.
- **No node has real V3 audio** — `app/intake/voicepack.resolve` falls back to TTS for every
  prompt; the pack format is S7, recordings S21 (already noted below for the tree nodes).
- **`Intake.answers[*].text_en` is not filled during intake** — the per-answer English gloss
  for the doctor screen is left to the summariser; a translation pass per answer is future.
- `price_book` rates are **estimates**: public list prices at ~₹84/USD, rounded up. Admin-editable
  in S18; every unit-economics number depends on them.
- WhatsApp meters per message; **Meta bills per 24h conversation** — over-counts until S12.
- Cached tokens are priced at the full `token_in` rate (vendors discount ~25%) — over-estimates.
- **Nothing schedules `CostGuard.evaluate()`** — on-demand only; needs a beat job (S17).
- Sarvam STT reports no confidence (`confidence=None`); doc 03 §4's `[unclear: ...]` contract
  leans on Google's until that is solved.
- **The classifier's ≥85% AC (S4) is unmeasured.** The 60-utterance eval set, the harness and
  the 85% gate exist (`make eval-routing`), but the only provider available is the fake, and
  scoring it measures the harness. Needs one live run with a `GEMINI_API_KEY`. The tests
  deliberately do not fake the number.
- **The 11 trees are unreviewed clinical content**, seeded `draft`, pending S21. The Hindi in
  them — and the eval set's utterances — were authored by a model, not a native speaker, and
  not collected from real patients. Tests check the text is present and structurally sound;
  they cannot check it is good Hindi or good medicine.
- **No tree node has `audio`** — the field is authored-empty. V3 kiosk voice packs are S7,
  real human recordings S21; TTS covers the gap until then.
- **V-OSS is the software half only (S-OSS.0).** The local provider adapters are real HTTP
  clients tested through `httpx.MockTransport` (OpenAI-compatible vLLM/Whisper shapes, local
  TTS `/tts`, Voicebox `/api/tts`) — **no live GPU server has ever answered**; first real bring-up
  is S-OSS.1 on the box. The `local-*` `price_book` rates are **amortized placeholders** (GPU
  capital + power spread over volume, set to a tiny per-unit number), not measured — non-zero on
  purpose so the S18 dashboard shows a true V-OSS cost, admin-editable in S18.
- **`LocalPipelineVoiceProvider` (V-OSS realtime) does not exist yet** — `REALTIME_PROVIDER=local-pipecat`
  raises (needs GPU + Pipecat, S-OSS.2), the same honesty `gemini-live` keeps. V-OSS voice runs as
  the V2 pipeline backed by local providers until then.
- **`config/tiers.yaml` `ladder_for()` is not wired into the engine/voice-gw yet** — the loader,
  validation and `AdmissionController` are built and tested, but *routing* a channel down its ladder
  (and gating the live local session on admission) is S-OSS.2, when there is a live local realtime
  session to route. Today the ladder is expressed operationally via provider fallback chains
  (local primary + cloud `*_FALLBACK_PROVIDER`) plus the existing V2→V3 tier downgrade.
- **`AdmissionController` count is per-process, in-memory** — correct for the single-voice-gw pilot;
  a second voice-gw replica needs a Redis counter (noted for S-OSS.2), same shape as the cost-guard
  override store.
- **Red-flag satisfiability is only checked for `and`-rooted rules, and only in tests** —
  `or` across branches is legitimate and `unanswered` is satisfied by an off-path node, so
  a general check needs real satisfiability, not reachability (S18's editor will want it).
- **Red flags and their instruction text are per-tree** (a tree is the unit of publish and
  sign-off), so the shared ones are duplicated across the med-onc trees and can drift.
- No Surgical Oncology "new lump/lesion" tree — doc 03 §3 lists it, doc 06's S4 line did not.
  A new-lump walk-in currently gets `surg_onc_post_op`, which asks about an operation they
  have not had.
- `prompts/` text is English-only prompt *instructions*; mr/te patient-facing strings are S13.
- Enum columns have **no CHECK constraint** despite the docstring claiming so (`native_enum=False`
  + SQLAlchemy 2.0's `create_constraint=False`).
- Staff username+TOTP login is modelled on `users` but not implemented; phone-OTP is the only path.
- No IP rate limiting on OTP verify (per-challenge attempt cap only) — S20.
- `otp_codes` rows are never pruned — S17.
- Migrations applied by hand (`make migrate`); no auto-migrate on container start.
- worker/beat: placeholder `opd.ping` Celery task only.
- web route groups: on-brand scaffold pages, no component library.
- Loki/Grafana/uptime-kuma: default config, unprovisioned.
