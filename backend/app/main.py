"""FastAPI application factory for the `api` service.

S1 shipped the factory + health route. S2 adds persistence, auth, and the audit
trail. Auditing needs nothing here: sessions come from `app.db`, which builds
them on the audited session class, so clinical writes are logged no matter which
router does them. This middleware only binds *who* is acting (`AuditMiddleware`).
Feature routers (intake, queue, doctor, rx, checkins, admin, webhooks) are
mounted here in later sessions.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.middleware import AuditMiddleware
from app.routes.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Refuses to boot a non-local env still carrying dev secrets (see config.py).
    settings.assert_production_safe()

    app = FastAPI(
        title="OPD Intelligence Platform API",
        version=__version__,
        docs_url="/docs",
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
    return app


app = create_app()
