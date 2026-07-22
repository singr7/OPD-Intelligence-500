"""FastAPI application factory for the `api` service.

S1 shipped the factory + health route. S2 added persistence, auth, and the audit
trail. Auditing needs nothing here: sessions come from `app.db`, which builds
them on the audited session class, so clinical writes are logged no matter which
router does them. The middleware only binds *who* is acting (`AuditMiddleware`).

S3 adds the provider layer's two background pieces, both on the lifespan: the
usage meter's drain task (doc 02 §8 — metering is async, batched, and must never
block a call) and the cost guard that owns the tier override. Feature routers
(intake, queue, doctor, rx, checkins, admin, webhooks) mount here in later
sessions.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.db import build_sessionmaker, get_engine
from app.intake import IntakeEngine, build_session_store
from app.middleware import AuditMiddleware
from app.providers.costguard import CostGuard, build_override_store, set_guard
from app.providers.metering import UsageMeter, set_meter
from app.providers.pricing import get_price_book
from app.queue_hub import QueueHub
from app.routes.health import router as health_router
from app.routes.kiosk import router as kiosk_router
from app.routes.providers import router as providers_router
from app.routes.queue import router as queue_router


def _build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Built here rather than at import: an engine created at import time
        # binds to whichever event loop imported it and fails on first use.
        sessionmaker = build_sessionmaker(get_engine())

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

        # One IntakeEngine per process — it holds no per-intake state (the session
        # store does), so channel routers (kiosk now, WhatsApp S12) share it.
        # Adaptive intake (S-ADAPT.1, doc 11) is enabled only when the flag is on
        # AND a real LLM is wired: a fake provider answering itself is exactly what
        # the interpreter must never do, so a fake keeps the kiosk on taps.
        adaptive = settings.intake_adaptive and settings.llm_provider != "fake"
        app.state.intake_engine = IntakeEngine(
            build_session_store(settings), adaptive=adaptive
        )

        # The live-queue fan-out hub (S8): board + coordinator sockets and the
        # in-memory downtime flag. In-process — one api container at pilot scale
        # (see app/queue_hub.py for the multi-replica caveat).
        app.state.queue_hub = QueueHub()

        try:
            yield
        finally:
            # Flushes the buffer, so a clean restart keeps its cost rows.
            await meter.stop()
            set_meter(None)
            set_guard(None)

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Refuses to boot a non-local env still carrying dev secrets (see config.py).
    settings.assert_production_safe()

    app = FastAPI(
        title="OPD Intelligence Platform API",
        version=__version__,
        docs_url="/docs",
        lifespan=_build_lifespan(settings),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # tightened to known PWA origins in S19 deploy hardening
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuditMiddleware, settings=settings)

    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(providers_router)
    app.include_router(kiosk_router)
    app.include_router(queue_router)
    return app


app = create_app()
