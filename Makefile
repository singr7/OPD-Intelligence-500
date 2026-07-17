# OPD Intelligence Platform — developer entrypoints (doc 05 §3, doc 07).
# `make dev` up the stack · `make test` full suite · `make deploy` on the box.
.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND_PY := backend/.venv/bin/python
VOICEGW_PY := voice-gw/.venv/bin/python

# Host-side DB URL. In-cluster services reach Postgres at postgres:5432; from the
# host it is published on 5433, because a native Postgres already owns 5432 on
# the dev machine (see docker-compose.yml).
HOST_DB_URL ?= postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd

.PHONY: help dev down logs test test-backend test-voicegw test-web lint \
        tf-validate build deploy venv clean migrate migration seed eval-routing \
        tree-fixtures check-tree-fixtures

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.env: ## Create local env from the example if missing
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

dev: .env ## Bring the full stack up locally (builds images, waits for health)
	docker compose up -d --build --wait
	@echo "web http://localhost:3000  ·  api http://localhost:8000/health  ·  grafana http://localhost:3001"

down: ## Stop the stack
	docker compose down

logs: ## Tail all service logs
	docker compose logs -f --tail=100

# --- Database -----------------------------------------------------------------
migrate: ## Apply migrations to the local stack's Postgres
	cd backend && ALEMBIC_DATABASE_URL=$(HOST_DB_URL) .venv/bin/alembic upgrade head

migration: ## Autogenerate a revision from model changes: make migration m="add x"
	@test -n "$(m)" || (echo 'usage: make migration m="describe the change"' && exit 1)
	cd backend && ALEMBIC_DATABASE_URL=$(HOST_DB_URL) \
		.venv/bin/alembic revision --autogenerate -m "$(m)"

seed: ## Load the pilot seed dataset (idempotent — safe to re-run)
	cd backend && DATABASE_URL=$(HOST_DB_URL) .venv/bin/python -m app.seed

# --- Evals --------------------------------------------------------------------
eval-routing: ## Score the routing classifier against its 60-utterance eval set
	@echo "Scores whatever LLM_PROVIDER is set to. With the default (fake) this"
	@echo "measures the harness, not a model — set a real provider + key first."
	cd backend && .venv/bin/python -m app.evals --set routing

# --- Tests --------------------------------------------------------------------
venv: ## Create the two Python venvs and install dev deps
	python3 -m venv backend/.venv && backend/.venv/bin/pip install -q -r backend/requirements-dev.txt
	python3 -m venv voice-gw/.venv && voice-gw/.venv/bin/pip install -q -r voice-gw/requirements.txt pytest httpx

test: test-backend test-voicegw test-web ## Run the full test suite

test-backend: ## Backend pytest
	cd backend && .venv/bin/python -m pytest -q

test-voicegw: ## voice-gw pytest
	cd voice-gw && .venv/bin/python -m pytest -q

test-web: check-tree-fixtures ## Web typecheck + lint + walker conformance (build is exercised in CI)
	cd web && npm run typecheck && npm run lint && npm run conformance

tree-fixtures: ## Regenerate the Python→TS walker conformance fixtures (S7)
	cd backend && .venv/bin/python -m app.tree_fixtures

check-tree-fixtures: ## Fail if the conformance fixtures are stale vs the Python walker
	cd backend && .venv/bin/python -m app.tree_fixtures --check

lint: ## Ruff (python) + next lint (web)
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd voice-gw && python3 -m ruff check . || true
	cd web && npm run lint

# --- Infra --------------------------------------------------------------------
tf-validate: ## terraform fmt-check + validate (plan-only in the pilot)
	cd infra && terraform init -backend=false -input=false >/dev/null && \
		terraform fmt -check -recursive && terraform validate

build: ## Build all docker images without starting
	docker compose build

# --- Deploy (runs on the EC2 box via SSM in S19) ------------------------------
deploy: ## git pull -> build -> up -> smoke (doc 05 §3)
	git pull --ff-only
	docker compose build
	docker compose up -d --wait
	curl -fsS http://localhost:8000/health && echo " api ok"

clean: ## Remove venvs, build artifacts, terraform cache
	rm -rf backend/.venv voice-gw/.venv web/node_modules web/.next infra/.terraform
