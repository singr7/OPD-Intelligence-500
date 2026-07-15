# HANDOFF — after Session 2

**Repo state:** branch `main`, last code commit `cad412e` (session-close commit follows this
file). `make test` green (backend **87**, voice-gw 1, web typecheck+lint). `make dev` brings up
11 healthy services. Postgres now on host port **5433** — read the first bullet under "Watch
out for" before anything else.

**One paragraph:** The database and the security spine are in. The full doc 02 §4 schema (21
tables) exists as SQLAlchemy models with one Alembic migration that round-trips and matches the
models (`alembic check` clean). Phone-OTP login issues JWTs with role claims; RBAC guards are
ready for feature routes to depend on; every clinical write automatically produces an
append-only `audit_log` row, enforced by Postgres triggers rather than convention. An idempotent
seed loads the pilot hospital, 9 departments, 5 doctors + 3 staff and 50 deterministic patients.
The only provider that exists is `FakeSMSProvider` — the real provider layer, usage metering and
cost-guard are S3's whole job. No intake logic, no trees, no real UI yet.

## Next session (S3 — Provider layer, usage metering + prompt library)
- Objective: every external dependency behind an interface with a `FakeProvider`
  (RealtimeVoice/LLM/STT/TTS/SMS/Messaging/Telephony), a usage-metering decorator emitting
  priced `usage_events` from every wrapper, `price_book` + cost computation, retry/circuit
  breaker + provider health registry, cost-guard skeleton, and `prompts/` with the V1/V2 shared
  tool contract.
- Start notes:
  - `backend/app/providers/` already exists with the shape to grow into: `sms.py` (interface +
    fake), `registry.py` (config-driven selection + `sms_provider_dependency`). Extend the
    registry rather than inventing a second one. **Routes must depend on
    `sms_provider_dependency`, never on `get_sms_provider` directly** — FastAPI parses a
    dependency's `Settings` param out of the request body and 422s every call.
  - The `usage_events` and `price_book` tables are already migrated and have **no writers** —
    S3 is what fills them. `usage_events` is deliberately un-FK'd to intakes/visits so metering
    can never block or fail a patient-facing call.
  - Money columns are `Numeric`, never float (they must reconcile exactly on the S18 dashboard).
    `tests/test_schema.py::test_money_is_never_stored_as_float` enforces this on every new column.
  - New model? Import it in `app/models/__init__.py` (or it is silently absent from migrations),
    then `make migration m="…"` and check the generated file before committing.
  - Anything patient-affecting must subclass `Clinical` (`app/models/base.py`) — that is the
    entire opt-in to auditing. `tests/test_audit.py` has a deliberate hardcoded list of the
    clinical tables; if S3 legitimately adds one, update that list consciously.
- Exact first commands:
  1. `make dev` (baseline; 11 services healthy)
  2. `make migrate && make seed` (seed is idempotent — safe if already loaded)
  3. `make test`

## Watch out for
- **Port 5433, and a native Postgres on 5432.** This machine runs its own Postgres on
  127.0.0.1:5432 which *wins over Docker's bind*, so `localhost:5432` reaches the wrong database
  and fails with `role "opd" does not exist`. Compose publishes ours on **5433**; don't revert
  it. In-cluster URLs stay `postgres:5432`. Host tooling: `make migrate`/`make seed` handle it;
  pytest defaults to `localhost:5433/opd_test` (`TEST_DATABASE_URL` to override). Port 8080 is
  still taken, so voice-gw is still on 8090. Others: web 3000, api 8000, grafana 3001,
  uptime-kuma 3002, loki 3100, redis 6379.
- **`.env` is gitignored and yours is probably stale.** S2 added `JWT_SECRET`, `SMS_PROVIDER`,
  `OTP_DEBUG_ECHO` and friends to `.env.example`; `make .env` only copies when the file is
  *missing*, so append the new keys by hand. Without `OTP_DEBUG_ECHO=true` you cannot log in
  locally — the OTP goes nowhere and the fake provider won't log the body.
- **Tests need a real Postgres** (JSONB + PL/pgSQL triggers); they build the schema by running
  `alembic upgrade head`, not `create_all`, so a broken migration fails the whole suite rather
  than hiding. CI has a Postgres service on 5432 (no native one to collide with there).
- **The audit log cannot be edited or deleted** — by anyone, including psql and future
  migrations. If you write test/probe rows into a real DB you cannot clean them up; drop the
  database instead. Pruning requires dropping the triggers in an explicit migration.
- **`terraform` is still not on PATH** here (brew blocked by old Xcode); CI covers
  `terraform validate`. `make tf-validate` will fail locally until it's installed.

## Decisions needed from the human
- **SMS/OTP provider for S3: MSG91 vs Exotel SMS.** Still open, and now blocking: S3 writes the
  concrete impl. S2 stubbed it behind `SMSProvider` so either is a config change, but S3 needs
  the pick. (`.env.example` has an `MSG91_KEY` slot from S1, which is not a decision.)
- **Nothing else blocking S3.**

## Backlog additions
- Staff username+TOTP login (doc 02 §2) — columns exist on `users`, no implementation. Suggest
  S18 with the admin console, unless a pilot coordinator needs it sooner.
- Rate-limit OTP verify by IP at the edge (per-challenge attempt cap already enforced) — S20.
- Prune consumed/expired `otp_codes`; a Celery beat job — S17, when beat gets real work.
- Audit log daily S3 export + retention (doc 02 §7) — must drop/recreate the append-only
  triggers in a migration that says so — S19.
- Soft-delete filtering is manual (`.where(deleted_at.is_(None))`); consider a default query
  option once feature code has enough repetition to justify it — S8 or later.
- Carried from S1: provision Grafana datasource + dashboards (S19); pin exact dependency
  versions (S20); per-service `.dockerignore` (any session).
