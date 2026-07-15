# STATE

**Built (S1):** Monorepo skeleton — `backend/` (FastAPI api + Celery
worker/beat), `voice-gw/` (FastAPI), `web/` (Next.js 14, 5 route groups, design
tokens), `infra/` (Terraform pilot, plan-only + Caddyfile). Full docker-compose
stack (11 services) runs healthy via `make dev`. CI (GitHub Actions), Makefile,
pre-commit. `make test` green; `terraform validate` passes.

**Not built yet:** DB schema/migrations, auth/RBAC/audit, provider layer,
question-tree engine, any real UI or intake logic.

**Stubs & fakes:**
- worker/beat: placeholder `opd.ping` Celery task only.
- web route groups: on-brand scaffold pages, no component library.
- Loki/Grafana/uptime-kuma: default config, unprovisioned.
- No provider implementations; no `FakeProvider`s yet.

**Environment gotchas:** voice-gw on host port **8090** (8080 taken by another
local project); `terraform` not on PATH (standalone binary used); `.env`
gitignored, generated from `.env.example`.
