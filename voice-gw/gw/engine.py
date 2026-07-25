"""The engine half of voice-gw (S14): one in-process `IntakeEngine`, plus the
usage meter and cost guard it needs, stood up on the FastAPI lifespan.

This mirrors `backend/app/main.py` deliberately — the *same* engine class the api
runs, so a phone intake is byte-for-byte the same intake as a kiosk one, one tier
down or up. voice-gw is a separate process only for crash isolation (doc 05 §3),
not a separate engine.

Why the meter and guard live here too: the phone channel meters per-minute audio
into `usage_events` (doc 02 §5) and respects the cost guard from the first turn
(`IntakeEngine.start_session`). Both are process-global singletons
(`set_meter` / `set_guard`), so voice-gw must own its own — it does not share the
api process. They point at the same Postgres and the same Redis override store, so
the dashboard and the guard see phone and kiosk usage as one ledger.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings, get_settings
from app.db import build_sessionmaker, get_engine
from app.intake import IntakeEngine, build_session_store
from app.providers.costguard import CostGuard, build_override_store, set_guard
from app.providers.metering import UsageMeter, set_meter
from app.providers.pricing import get_price_book


def build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built on the running loop, not at import — an engine/sessionmaker created
        # at import binds the wrong event loop and fails on first use (see main.py).
        sessionmaker = build_sessionmaker(get_engine())
        app.state.sessionmaker = sessionmaker

        meter = UsageMeter(sessionmaker, get_price_book())
        set_meter(meter)
        await meter.start()

        guard = CostGuard(
            sessionmaker,
            build_override_store(settings),
            budgets=settings.daily_budget_inr,
            alert_fraction=settings.cost_guard_alert_fraction,
            override_ttl_seconds=settings.cost_guard_override_ttl_seconds,
            timezone=settings.timezone,
            enabled=settings.cost_guard_enabled,
        )
        set_guard(guard)

        # Adaptive intake is on only with the flag AND a real LLM — a fake provider
        # answering its own follow-ups is exactly what the interpreter must never do
        # (doc 11 §5). Same gate as the api.
        adaptive = settings.intake_adaptive and settings.llm_provider != "fake"
        app.state.intake_engine = IntakeEngine(build_session_store(settings), adaptive=adaptive)

        try:
            yield
        finally:
            await meter.stop()
            set_meter(None)
            set_guard(None)

    return lifespan


def get_intake_engine(request: Request) -> IntakeEngine:
    """The one process-wide engine, built on the lifespan."""
    return request.app.state.intake_engine


def get_sessionmaker(request: Request) -> async_sessionmaker:
    """The async sessionmaker for persistence + `finalize_cost`."""
    return request.app.state.sessionmaker


def build_engine_for_tests(settings: Settings | None = None) -> IntakeEngine:
    """A bare in-memory engine for unit tests that don't need Postgres/Redis.

    Uses the in-memory session store (no Redis) so the call-driver tests can run
    the pipeline over installed fake providers without the full stack.
    """
    settings = settings or get_settings()
    return IntakeEngine(build_session_store(settings))
