"""FastAPI application factory for the `api` service.

Kept intentionally thin in S1: an app factory, a versioned health route, and
permissive-but-explicit CORS for the local web PWAs. Feature routers
(intake, queue, doctor, rx, checkins, admin, webhooks) are mounted in later
sessions via `include_router` on the app returned here.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import Settings, get_settings
from app.routes.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

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

    app.state.settings = settings
    app.include_router(health_router)
    return app


app = create_app()
