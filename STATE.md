# STATE

**Built (S1):** Monorepo skeleton — `backend/` (FastAPI api + Celery worker/beat), `voice-gw/`
(FastAPI), `web/` (Next.js 14, 5 route groups, design tokens), `infra/` (Terraform pilot,
plan-only + Caddyfile). Full docker-compose stack (11 services) runs healthy via `make dev`. CI
(GitHub Actions), Makefile, pre-commit.

**Built (S2):** Full doc 02 §4 schema — 21 SQLAlchemy models + one Alembic migration that
round-trips and matches the models. Phone-OTP login → JWT (access + rotating refresh with
revocation), Argon2 hashing, RBAC guards. Append-only audit trail covering every clinical write.
`SMSProvider` interface + fake. Idempotent seed (1 hospital, 9 departments, 5 doctors + 3 staff,
50 deterministic patients). 87 backend tests. `make test` green.

**Not built yet:** provider layer beyond SMS (voice/LLM/STT/TTS/messaging/telephony), usage
metering + cost-guard, question-tree engine, intake logic, any real UI.

## How to run
```
make dev                 # full stack (11 services)
make migrate             # apply migrations to the local DB
make seed                # load the pilot dataset (idempotent, safe to re-run)
make test                # backend pytest + voice-gw pytest + web typecheck/lint
make migration m="..."   # autogenerate a revision from model changes
```
Local login: `POST /auth/otp/request {"phone": "+915550001001"}` (seeded doctor) returns
`debug_code` when `OTP_DEBUG_ECHO=true`; POST it to `/auth/otp/verify` for a JWT.

## Environment gotchas
- **Postgres: host port 5433**, not 5432 — a native Postgres owns 127.0.0.1:5432 on this dev
  machine and wins over Docker's bind, so 5432 silently reaches the wrong database. In-cluster
  URLs are unchanged (`postgres:5432`). Tests default to `localhost:5433/opd_test`
  (`TEST_DATABASE_URL` to override); `ALEMBIC_DATABASE_URL` overrides for alembic by hand.
- **voice-gw on host port 8090** (8080 taken by another local project).
- **`.env` is gitignored and does not auto-update.** `make .env` only copies `.env.example` when
  the file is missing — after a session that adds keys, append them by hand. Without
  `OTP_DEBUG_ECHO=true` you cannot log in locally.
- `terraform` is not on PATH (brew blocked by old Xcode); CI covers `terraform validate`.
- Tests require a real Postgres and build the schema via `alembic upgrade head` — SQLite would
  not have JSONB or the audit triggers.

## Env vars
See `.env.example` (authoritative). Notable: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET` (≥32
chars; the app refuses to boot with `ENV!=local` on the dev default), `SMS_PROVIDER` (`fake`
until S3), `OTP_DEBUG_ECHO` (local only — refused outside `ENV=local`), `OTP_*` tuning, provider
key slots (unused until S3).

## Invariants (don't quietly break these)
- **Anything patient-affecting subclasses `Clinical`** (`app/models/base.py`) — that alone makes
  writes audited, via a `before_flush` hook on `AuditedSession`. There is no per-route audit call.
- **`audit_log` is append-only in the database** — triggers reject UPDATE/DELETE/TRUNCATE for
  every client, including psql. Pruning requires an explicit migration that drops them.
- **Audit records that a field changed, never the PII** (`REDACTED_FIELDS` in `app/audit.py`).
- **Soft deletes only** on clinical tables; set `deleted_at`, never DELETE.
- **Money is `Numeric`, never float** — costs must reconcile exactly against `usage_events`.
- **New model ⇒ import it in `app/models/__init__.py`**, or it is silently missing from migrations.
- **Every external call behind a provider interface**, each with a fake (doc 02 §9).

## Stubs & fakes
- `FakeSMSProvider` is the only SMS implementation; MSG91/Exotel + metering land in S3.
  (Doc 02 §9: a provider without usage metering fails review — the fake is exempt until S3.)
- Staff username+TOTP login is modelled on `users` but not implemented; phone-OTP is the only path.
- `question_trees`, `price_book`, `usage_events` are migrated tables with **no writers** (S3/S4).
- No IP rate limiting on OTP verify (per-challenge attempt cap only) — S20.
- `otp_codes` rows are never pruned — S17.
- Migrations applied by hand (`make migrate`); no auto-migrate on container start.
- worker/beat: placeholder `opd.ping` Celery task only.
- web route groups: on-brand scaffold pages, no component library.
- Loki/Grafana/uptime-kuma: default config, unprovisioned.
