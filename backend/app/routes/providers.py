"""`/providers/health` — is every external dependency working right now (doc 02 §9).

Distinct from `/health`, which answers "is this process alive" for compose and
uptime-kuma. This answers "can we still run a V1 intake", which is the question
the coordinator console's banner (S8) and an on-call human actually have.

Unauthenticated on purpose, and it must stay boring enough to deserve that: it
reports vendor *names* and health, never keys, never request bodies, never a
patient. If it ever grows a detail that would embarrass us in a screenshot, it
needs auth first.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.providers.pricing import get_price_book
from app.providers.registry import all_providers

router = APIRouter(prefix="/providers", tags=["providers"])

# Worst-first, so `status` is the max over providers.
_RANK = {"ok": 0, "unconfigured": 1, "degraded": 2, "down": 3}


@router.get("/health")
async def providers_health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    providers = all_providers(settings)
    entries = [p.health.as_dict() for p in providers]

    overall = max((e["status"] for e in entries), key=lambda s: _RANK.get(str(s), 0), default="ok")

    prices = get_price_book()
    return {
        "status": overall,
        "providers": entries,
        # Usage we metered but could not price — an empty list is the healthy
        # state. A non-empty one means the cost dashboard is understating spend
        # and the cost-guard is reading ₹0 for a provider that is charging us,
        # which is exactly the failure the guard exists to prevent.
        "unpriced": [
            {"provider": provider, "model": model, "unit": unit.value}
            for provider, model, unit in sorted(
                prices.unpriced, key=lambda k: (k[0], k[1] or "", k[2].value)
            )
        ],
    }
