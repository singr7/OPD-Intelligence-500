# OPD Intelligence Platform — developer entrypoints (doc 05 §3, doc 07).
# `make dev` up the stack · `make test` full suite · `make deploy` on the box.
.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND_PY := backend/.venv/bin/python
VOICEGW_PY := voice-gw/.venv/bin/python

.PHONY: help dev down logs test test-backend test-voicegw test-web lint \
        tf-validate build deploy venv clean

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

# --- Tests --------------------------------------------------------------------
venv: ## Create the two Python venvs and install dev deps
	python3 -m venv backend/.venv && backend/.venv/bin/pip install -q -r backend/requirements-dev.txt
	python3 -m venv voice-gw/.venv && voice-gw/.venv/bin/pip install -q -r voice-gw/requirements.txt pytest httpx

test: test-backend test-voicegw test-web ## Run the full test suite

test-backend: ## Backend pytest
	cd backend && .venv/bin/python -m pytest -q

test-voicegw: ## voice-gw pytest
	cd voice-gw && .venv/bin/python -m pytest -q

test-web: ## Web typecheck + lint (build is exercised in CI)
	cd web && npm run typecheck && npm run lint

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
