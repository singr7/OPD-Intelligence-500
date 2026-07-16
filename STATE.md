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

**Not built yet:** any real UI (S6+); channel adapters (kiosk S6, WhatsApp S12,
telephony S14); the real Gemini Live impl (S14).

## How to run
```
make dev                 # full stack (11 services)
make migrate             # apply migrations to the local DB
make seed                # load the pilot dataset + price book + trees (idempotent)
make test                # backend pytest + voice-gw pytest + web typecheck/lint
make migration m="..."   # autogenerate a revision from model changes
make eval-routing        # score the routing classifier (needs a real LLM key to mean anything)
```
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
`DAILY_BUDGET_INR` (per-channel cost-guard caps; a channel with no entry is uncapped).

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

## Stubs & fakes
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
