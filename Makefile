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
        slots campaign-dryrun app-demo checkin-demo \
        android-test android-test-device android-apk android-emulator android-install \
        tree-fixtures check-tree-fixtures care-system-fixtures check-care-system-fixtures

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

kiosk-pin: ## Kiosk staff PINs: no ARGS lists them; ARGS="--phone +91... --set|--clear|--unlock"
	cd backend && DATABASE_URL=$(HOST_DB_URL) .venv/bin/python -m scripts.set_kiosk_pin $(ARGS)

# --- Appointments (S15) -------------------------------------------------------
slots: ## Materialise bookable slots from the seeded templates (idempotent)
	cd backend && DATABASE_URL=$(HOST_DB_URL) .venv/bin/python -m app.worker opd.slots.generate

campaign-dryrun: ## Print tomorrow's D-1 intake call list. Dials nobody.
	cd backend && DATABASE_URL=$(HOST_DB_URL) .venv/bin/python -m app.campaign

# --- Evals --------------------------------------------------------------------
eval-routing: ## Score the routing classifier against its 60-utterance eval set
	@echo "Scores whatever LLM_PROVIDER is set to. With the default (fake) this"
	@echo "measures the harness, not a model — set a real provider + key first."
	cd backend && .venv/bin/python -m app.evals --set routing

lang-qa: ## Language QA harness (S13): completeness + script + glossary + audio round-trip
	cd backend && .venv/bin/python -m app.lang_qa

# --- Tests --------------------------------------------------------------------
venv: ## Create the two Python venvs and install dev deps
	python3 -m venv backend/.venv && backend/.venv/bin/pip install -q -r backend/requirements-dev.txt
	python3 -m venv voice-gw/.venv && voice-gw/.venv/bin/pip install -q -r voice-gw/requirements.txt pytest httpx

test: test-backend test-voicegw test-web android-test ## Run the full test suite

test-backend: ## Backend pytest
	cd backend && .venv/bin/python -m pytest -q

test-voicegw: ## voice-gw pytest (runs on the backend venv — voice-gw shares the engine, S14)
	cd voice-gw && PYTHONPATH="$(CURDIR)/backend:$(CURDIR)/voice-gw" $(CURDIR)/$(BACKEND_PY) -m pytest -q

test-web: check-tree-fixtures check-care-system-fixtures ## Web typecheck + lint + conformance (build is exercised in CI)
	cd web && npm run typecheck && npm run lint && npm run conformance

# --- Android (S16) ------------------------------------------------------------
# JAVA_HOME is pinned: AGP 8.7 wants a JDK 17, and the machine's default `java`
# is newer. ANDROID_HOME comes from the SDK the operator installed.
ANDROID_JAVA_HOME ?= /opt/homebrew/opt/openjdk@17
GRADLEW := JAVA_HOME=$(ANDROID_JAVA_HOME) ./gradlew

android-test: ## Android JVM unit tests (no device needed) — part of `make test`
	cd android && $(GRADLEW) testDebugUnitTest

android-test-device: ## Instrumented tests: offline care file, reminders, home intake. Needs a booted emulator.
	cd android && $(GRADLEW) connectedDebugAndroidTest

android-apk: ## Release APK + the 15MB size gate (doc 03 §1c.7)
	cd android && $(GRADLEW) checkApkSize

android-emulator: ## Boot the pilot AVD headless (Ctrl-C to stop)
	$${ANDROID_HOME:-$$HOME/Library/Android/sdk}/emulator/emulator -avd opd_pilot -no-window -no-audio -no-boot-anim

android-install: ## Install the debug app on a booted device, pointed at the local stack
	cd android && $(GRADLEW) installDebug -PopdApiBase=http://10.0.2.2:8000

app-demo: ## Give the first seeded patient a prescription, a cycle and a caregiver (S16 demo)
	cd backend && DATABASE_URL=$(HOST_DB_URL) .venv/bin/python -m scripts.seed_app_demo

checkin-demo: ## Sign a chemo note, approve the plan, answer D+2 red (S17 demo)
	cd backend && DATABASE_URL=$(HOST_DB_URL) .venv/bin/python -m scripts.seed_checkin_demo

tree-fixtures: ## Regenerate the Python→TS walker conformance fixtures (S7)
	cd backend && .venv/bin/python -m app.tree_fixtures

check-tree-fixtures: ## Fail if the conformance fixtures are stale vs the Python walker
	cd backend && .venv/bin/python -m app.tree_fixtures --check

care-system-fixtures: ## Regenerate the Python→TS care-system capabilities fixture (doc 24)
	cd backend && .venv/bin/python -m app.care_system_fixtures

check-care-system-fixtures: ## Fail if the care-system fixture is stale vs app/care_system.py
	cd backend && .venv/bin/python -m app.care_system_fixtures --check

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

preflight: ## Build the api + voice-gw images and prove they can actually import (run before any box deploy)
	@# `make test` runs in the venvs, which install from pyproject; the images
	@# install from backend/requirements.txt. When those two drift, every test
	@# passes and the container crash-loops on boot. It has happened twice —
	@# python-multipart (1e4f0ce) and cryptography (S-GL.1) — so this is the
	@# check that belongs between "tests green" and "pull on the box".
	docker build -q -t opd-preflight-api ./backend
	docker run --rm opd-preflight-api python -c "import app.main" && echo "  api image imports OK"
	docker build -q -t opd-preflight-vgw -f voice-gw/Dockerfile .
	docker run --rm opd-preflight-vgw python -c "import sys; sys.path[:0]=['/app/backend','/app/voice-gw']; import gw.main" \
		&& echo "  voice-gw image imports OK"

# --- Deploy (runs on the EC2 box via SSM in S19) ------------------------------
deploy: ## git pull -> build -> up -> smoke (doc 05 §3)
	git pull --ff-only
	docker compose build
	docker compose up -d --wait
	curl -fsS http://localhost:8000/health && echo " api ok"

clean: ## Remove venvs, build artifacts, terraform cache
	rm -rf backend/.venv voice-gw/.venv web/node_modules web/.next infra/.terraform
