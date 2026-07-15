# SESSION-02 — Data model, auth, RBAC, audit

**Date:** 2026-07-15 · **Scope ref:** docs/06-BUILD-PLAN.md → S2

## Acceptance criteria checklist
- [x] **pytest CRUD+auth suite green** — 87 backend tests, green on three consecutive runs.
- [x] **Audit row on every clinical write** — enforced structurally (see below), proven by
      `test_every_clinical_write_is_audited`, which walks the mapper registry rather than a
      hand-kept list, and by the seed writing 50 `create` rows attributed to actor `seed`.
- [x] **Seeds load idempotently** — second run reports `created: none, updated: none`,
      writes no rows and no audit entries. Verified via CLI against the real DB and in tests.

## What was built
- `backend/app/models/` — the full doc 02 §4 schema, 21 tables across org / patient /
  clinical / scheduling / content / metering / auth / audit modules, plus `enums.py`.
- `backend/alembic/` + initial migration `75dc12335238` — all 21 tables, round-trips
  (downgrade → upgrade) cleanly, and `alembic check` reports no drift from the models.
- `backend/app/audit.py` — `AuditedSession` + `before_flush` hook; `Actor` ContextVar;
  PII redaction. `app/middleware.py` binds the actor from the JWT per request.
- `backend/app/auth/` — phone-OTP challenge/verify, JWT access+refresh with rotation and
  revocation, Argon2 hashing, RBAC guards (`require_roles`, `require_staff`, …).
  Routes: `/auth/otp/request`, `/auth/otp/verify`, `/auth/refresh`, `/auth/logout`, `/auth/me`.
- `backend/app/providers/` — `SMSProvider` interface + `FakeSMSProvider` + config-driven
  registry. First slice of the doc 02 §9 provider layer; S3 fills in the rest.
- `seeds/` + `backend/app/seed.py` — idempotent loader, `make seed`, `--patients N`, `--dry-run`.
- `make migrate`, `make migration m="…"`, `make seed`; CI gains a Postgres service.

## Decisions made
- **Audit is structural, not conventional.** Writes to any model marked `Clinical` produce an
  `audit_log` row from a `before_flush` hook, in the same transaction. There is no per-route
  audit call to forget, and `tests/test_audit.py` fails if a new clinical table escapes the
  marker. Do not replace this with per-route logging.
- **Append-only is enforced in Postgres**, not the ORM: triggers reject UPDATE/DELETE/TRUNCATE
  on `audit_log` for every client including psql. An audit trail an admin can quietly edit is
  not an audit trail. Retention (doc 02 §7 daily S3 export) must drop the triggers, prune, and
  recreate them — in a migration that says so.
- **Audit records that a field changed, never the PII.** `REDACTED_FIELDS` covers names,
  phones, transcripts, summaries, secrets. The log outlives the record and is exported daily;
  copying PII into it would widen the blast radius for no investigative gain.
- **`users` is the single auth principal**; `doctors` is the clinical profile hanging off it.
  One OTP flow, one JWT shape, one place to revoke.
- **Tests run on real Postgres and build the schema via `alembic upgrade head`.** SQLite has no
  JSONB or PL/pgSQL, and `create_all` omits the triggers — either would report green on exactly
  the append-only guarantee it cannot enforce.
- **Enums are VARCHAR + CHECK** (`native_enum=False`), storing values (`in_queue`), not names.
  Adding a value later is a constraint swap, not a non-transactional `ALTER TYPE`.
- **Seed/staff phone numbers are unroutable by construction** (`+91 5xxxxxxxxx`; real Indian
  mobiles start 6-9). Seeds land on demo boxes that may have a live SMS provider from S3 on.
- **Access tokens are re-checked against the DB each request** — deactivation and role changes
  take effect immediately rather than at token expiry.
- Postgres published on **host 5433** (see Deviations).

## Deviations from spec
- **9 departments, not the 8 the build plan says.** Doc 03 §3 names exactly nine — 4 oncology
  (MEDONC, RADONC, SURGONC, PALL) + 5 routing (GENMED, GYNAE, ENT, PULM, DERM). S4 authors trees
  for exactly those, so the clinical spec wins over the round number. Non-material; no doc change.
- **Postgres host port 5432 → 5433.** A native Postgres already listens on 127.0.0.1:5432 on
  this machine and wins over Docker's bind, so `localhost:5432` silently reached the *wrong*
  database (this cost the first migration attempt). In-cluster traffic is unchanged
  (`postgres:5432`). Same precedent as voice-gw → 8090 in S1.
- **`refresh_tokens` table added** beyond doc 02 §4's list. Without a server-side handle, logout
  is cosmetic until the JWT expires. Cheap and it is the right thing.
- **passlib dropped in favour of argon2-cffi directly** — passlib is unmaintained and its bcrypt
  backend breaks on bcrypt 4.x.

## Tests & evidence
- `make test`: **backend 87 passed**, voice-gw 1 passed, web typecheck + lint clean.
  Backend suite run 3× consecutively to confirm no flakes.
- `terraform validate`: not run locally — terraform still not on PATH (S1 gotcha, unchanged);
  CI covers it and the infra job is untouched by this session.
- New tests: `test_crud.py` (17), `test_auth.py` (29), `test_audit.py` (18), `test_seed.py` (9),
  `test_schema.py` (5), `test_config.py` (7), plus the S1 health tests.
- **End-to-end against the live stack** (not just tests): `make dev` → 11 healthy services →
  OTP request for a seeded doctor → verify → JWT → `GET /auth/me` returned
  `{"name":"Dr. Anil Gupta","role":"doctor"}`; `audit_log` showed `seed|create|patients|50`.
- Append-only verified against a direct `psql` connection: UPDATE, DELETE and TRUNCATE all
  raise `audit_log is append-only`.
- No UI work this session, so no screenshots.

### Two real bugs the tests caught
1. **`intakes.cost_inr` was float.** It is summed into invoices that must reconcile exactly
   against `usage_events` (doc 02 §8) and binary floats don't sum exactly. Now `Numeric(12,4)`;
   `test_money_is_never_stored_as_float` guards every money column.
2. **OTP "only the newest code verifies" was resting on `ORDER BY created_at DESC`.** Postgres
   `now()` is the *transaction* timestamp, so codes issued in one transaction tie and an older
   code could win — the single-outstanding-code guarantee was not actually guaranteed. Issuing a
   code now retires the previous one, making the invariant true on write.

Also fixed while wiring: `Depends(get_sms_provider)` made FastAPI try to parse `Settings` from
the request body (every call 422'd) — routes now use `sms_provider_dependency`.

## Known gaps / stubs introduced
(Mirrored into STATE.md → Stubs & fakes)
- `FakeSMSProvider` is the only SMS impl; real MSG91/Exotel + usage metering land in S3.
  A provider without metering fails review per doc 02 §9 — the fake is exempt only until S3.
- Staff username+TOTP login (doc 02 §2) is modelled (`users.username/password_hash/totp_secret`)
  but not implemented; phone-OTP is the only working path.
- No rate limiting by IP on OTP verify — belongs at the edge, S20.
- OTP rows are never pruned (a Celery job is the natural home, S17).
- `question_trees`, `price_book`, `usage_events` are tables with no writers yet (S3/S4).
- Migrations are applied by hand (`make migrate`); no auto-migrate on container start.

## Commits
- `5bcce0a` — S 02: add data model, Alembic migration, auth, RBAC, audit trail
- `cad412e` — S 02: add seeds, test suite, and CI Postgres service
- (this file + HANDOFF/STATE in the session-close commit)
