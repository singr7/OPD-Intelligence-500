"""Liveness/readiness health route.

Returns a stable contract other services (compose healthchecks, uptime-kuma,
CI smoke test, the web PWAs' connectivity check) can rely on from S1 onward.
"""

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "api", "version": __version__}
