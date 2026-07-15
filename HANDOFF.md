# HANDOFF — after Session 1

**Repo state:** branch `main`, last code commit `58b802f` (session-close commit
follows this file). `make test` green (backend 2, voice-gw 1, web typecheck+lint).
`make dev` brings up 11 healthy services. `terraform validate` passes.

**One paragraph:** The infra skeleton is done. A monorepo (`backend/ voice-gw/
web/ infra/`) builds and runs end-to-end under docker compose behind Caddy, with
health routes on every service, a Celery worker/beat pair, Postgres/Redis, and
the Loki/Grafana/uptime-kuma observability containers. CI (GitHub Actions) and a
plan-only Terraform stack for the single-EC2 pilot are in place. No product
logic yet — no DB schema, auth, providers, or real UI. Everything is scaffolding
that S2+ fills in.

## Next session (S2 — Data model, auth, RBAC, audit)
- Objective: SQLAlchemy models + Alembic for the full schema (doc 02 §4), JWT
  auth, phone-OTP flow (SMS provider stubbed behind an interface), roles,
  append-only audit middleware, seed script (1 hospital, 8 departments,
  5 doctors, 50 fake patients).
- Start notes: the api service has no DB session/engine wiring yet — add it
  (async SQLAlchemy, `DATABASE_URL` already in config + compose). Create the
  `seeds/` directory here. Put Alembic under `backend/` (e.g. `backend/alembic/`).
  Add `sqlalchemy`, `alembic`, `asyncpg`, `python-jose`/`pyjwt`, `passlib` to
  `backend/requirements.txt` (and mirror dev tools).
- Exact first commands:
  1. `make dev` (confirm baseline green — see "Watch out for" re: port 8080)
  2. `make test`
  3. `cd infra && terraform validate` (needs `terraform` on PATH — see below)

## Watch out for
- **Port 8080** is used by another local project on this machine
  (`wps-sectheta-wordpress-1`); voice-gw is therefore published on host **8090**.
  Don't revert this. Other host ports: web 3000, api 8000, grafana 3001,
  uptime-kuma 3002, loki 3100, postgres 5432, redis 6379.
- **terraform** is not on PATH here (brew blocked by old Xcode). A standalone
  `terraform_1.9.8` binary lives in the session scratchpad; CI uses
  `hashicorp/setup-terraform`. Install terraform properly before relying on
  `make tf-validate` locally.
- **web healthcheck** uses `node -e fetch(...)` because `node:22-slim` has no
  wget/curl. Keep it node-based.
- **celery** must be referenced as `app.worker:celery_app` (a module-level
  instance), not the `make_celery` factory.
- **`.env`** is gitignored; `make dev`/`make .env` copies it from `.env.example`.

## Decisions needed from the human
- SMS/OTP provider for S2 (MSG91 vs Exotel SMS) — doc 02 §4 lists both; S2 stubs
  it behind an interface either way, but the concrete impl in S3 needs a pick.
- (None blocking S2.)

## Backlog additions
- Provision Grafana datasource + starter dashboards from Loki (suggest S19).
- Pin exact dependency versions (currently `>=`) once the stack stabilizes
  (suggest S20 hardening).
- Consider a root `.dockerignore` per service to speed image builds (minor;
  any session).
