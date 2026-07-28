"""Liveness/readiness health route.

Returns a stable contract other services (compose healthchecks, uptime-kuma,
CI smoke test, the web PWAs' connectivity check) can rely on from S1 onward.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "api", "version": __version__}


@router.get("/environment")
async def environment(request: Request) -> dict[str, str]:
    """Stable, non-secret identity used before an Android client pairs."""
    settings = request.app.state.settings
    return {
        "environment_id": settings.environment_id,
        "human_name": settings.environment_name,
        "api_contract_version": settings.api_contract_version,
        "release_sha": settings.release_sha,
        "current_time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
