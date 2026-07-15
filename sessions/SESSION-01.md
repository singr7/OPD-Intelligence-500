# SESSION-01 — Repo, infra skeleton, CI

**Date:** 2026-07-15 · **Scope ref:** docs/06-BUILD-PLAN.md → S1

## Acceptance criteria checklist
- [x] AC1 — `make dev` brings the full stack up locally: all 11 compose
      services (caddy, api, voice-gw, web, worker, beat, postgres, redis, loki,
      grafana, uptime-kuma) report healthy; every health route verified both
      directly and through the Caddy reverse proxy.
- [x] AC2 — CI defined (GitHub Actions, 5 jobs: backend, voice-gw, web, infra,
      images). Each job's commands were run locally and pass; the workflow
      itself has not executed on GitHub yet (no runs until first push). See
      "Deviations".
- [x] AC3 — `terraform validate` passes; `terraform fmt -check` clean.

## What was built
- **Monorepo layout**: `backend/ voice-gw/ web/ infra/` plus root `Makefile`,
  `docker-compose.yml`, `.pre-commit-config.yaml`, `.gitignore`, `.env.example`.
  (`android/` and `seeds/` are created in S16 and S2 respectively — not needed
  to satisfy S1's ACs; noted in Deviations.)
- **backend (api)**: FastAPI app factory ([backend/app/main.py](backend/app/main.py)),
  versioned `/health` route, pydantic-settings config, shared Dockerfile
  (also runs worker/beat), pytest suite (2 tests).
- **voice-gw**: separate FastAPI service + Dockerfile + health test — isolated
  so a telephony crash never takes down HTTP (doc 05 §3).
- **worker/beat**: Celery app skeleton with module-level `celery_app` + `opd.ping`
  task; celery imported only in those images.
- **web**: Next.js 14 App Router, five route groups
  `(kiosk)(board)(doctor)(coordinator)(admin)`, design tokens from doc 04 §1 in
  [web/app/globals.css](web/app/globals.css), `/api/health` route, on-brand
  scaffold shell per surface.
- **infra**: Terraform for the single-box pilot — VPC/subnet/SG (80/443 world,
  SSM-only, no SSH), t4g.xlarge + gp3 root + separate 100GB data EBS, EIP, IAM
  (S3+SSM+CloudWatch), S3 media (90d audio lifecycle) + backups (35d), DLM daily
  snapshots (14d), SNS + CPU alarm, `user_data.sh`. Plan-only, not applied.
- **CI**: [.github/workflows/ci.yml](.github/workflows/ci.yml) — lint+test per
  service, web build, terraform validate, docker image builds.
- **Caddy**: reverse proxy — `/api/*`→api, `/voice/*`→voice-gw (prefix
  stripped), `/`→web.

## Decisions made
- **Python deps via pip + requirements.txt** (not poetry/uv — neither installed;
  keeps CI and Docker simple). `pyproject.toml` kept for tooling config.
- **One backend image serves api + worker + beat** (celery installed in image,
  imported lazily) — fewer images, single build.
- **voice-gw host port 8090** (container stays 8080) to avoid a real clash with
  another local project holding 8080. Internal Caddy routing uses `voice-gw:8080`.
- **Health contract** `{status, service, version}` is shared across api,
  voice-gw, web — reused by compose healthchecks, uptime-kuma, and CI smoke.
- **Design tokens committed in S1** (doc 04 §1) even though real screens are
  later, so S6+ build on a fixed palette rather than reinventing it.

## Deviations from spec
- Build plan lists `android/` and `seeds/` in the layout; deferred to their
  owning sessions (S16, S2) rather than shipping empty dirs. No AC depends on
  them in S1.
- AC2 "CI green": verified by running every job's commands locally (ruff,
  pytest, `npm run build`, `terraform validate`, `docker build ×3`). The Actions
  run will only appear after the first push to GitHub; nothing GitHub-specific is
  expected to fail.
- Terraform is **not** on this machine's PATH (Homebrew blocked by outdated
  Xcode); a standalone `terraform_1.9.8` binary was used for validate/fmt. CI
  uses `hashicorp/setup-terraform`. `make tf-validate` assumes `terraform` on
  PATH.

## Tests & evidence
- `make test`: **green** — backend 2 passed, voice-gw 1 passed, web
  typecheck + lint clean.
- `terraform validate`: **Success! The configuration is valid.**
- Full-stack smoke (all through Caddy :80): `/api/health`, `/voice/health`,
  `/` (web, HTTP 200) all OK; 11/11 services healthy.
- New tests: `backend/tests/test_health.py` (2), `voice-gw/tests/test_health.py` (1).
- Screenshots (scaffold UI, self-critiqued vs doc 04 §5):
  - `sessions/screenshots/index.png` — dev surface directory; calm/spacious,
    deep-green label, no generic admin table. Not patient-facing.
  - `sessions/screenshots/board.png` — commits to the board's dark deep-green +
    marigold direction; real 8-meter token numerals are S8.
  - `sessions/screenshots/kiosk.png`, `doctor.png` — branded placeholders;
    real audio-first kiosk is S6, doctor summary hero is S9.
  - Critique: all avoid the generic-AI-dashboard trap; none are the deliverable
    screens, so the full §2 audio-first laws are exercised starting S6.

## Known gaps / stubs introduced
- Provider layer, DB models/migrations, auth — none yet (S2/S3).
- worker/beat run a placeholder `opd.ping` task only; no real jobs.
- Route-group pages are scaffolds; no component library yet (S6).
- Loki/Grafana run with default config; no dashboards/datasource provisioning
  (S19). uptime-kuma unconfigured (manual first-run).
- No `.env` committed (gitignored); `make dev` / `make .env` generates it.

## Commits
- e723b3a — S 01: scaffold backend api + voice-gw services with health routes and tests
- ff39104 — S 01: scaffold Next.js web app with five route groups and design tokens
- 27a3a84 — S 01: add Terraform pilot infra (single EC2 box, S3, DLM, SNS) — validates
- 2095d72 — S 01: add docker-compose stack, Caddy, Makefile, pre-commit, GitHub Actions CI
- 58b802f — S 01: fix stack runtime — module-level celery app, web/voice-gw healthchecks, port remap
- (session close commit follows)
