"""Runtime provider credentials: `.env` is the floor, a stored row overlays it (S-GL.1).

Doc 12 §4's problem, precisely: "nothing lets you enable a vendor without editing
`.env` and restarting". Provider selection and every credential are boot-time
settings, so the day the Exotel number arrives, opening the phone channel is a
deploy — on a box, by whoever has the ssh key, which is not the person the number
was given to.

This module is the seam that fixes it, and it holds the same line the trees, the
protocol bank and the channel document hold: **the file is the floor**. A
`provider_secrets` row overlays the environment; delete the row and the box is
back to whatever `.env` said. Nothing is ever *deleted from* `.env` by a console.

**Three rules, all structural rather than remembered:**

1. **Write-only.** `overlay` decrypts credentials for the process that must use
   them; nothing in `app.routes.admin` returns them. There is no read path to
   grow a "reveal" button onto later.
2. **Only known fields.** A row can set the credential fields named here for its
   own provider and nothing else, so a compromised console cannot repoint
   `database_url`, turn on `otp_debug_echo`, or select a different vendor.
   Selecting a vendor stays a deployment decision; supplying its credentials does
   not.
3. **A short TTL, not an invalidation protocol.** Three processes (api, voice-gw,
   beat) read this, and a credential change must reach all of them. It does,
   within `OVERLAY_TTL_SECONDS`, because each process re-reads on a timer rather
   than listening for a message it might miss while restarting. "No restart"
   honestly means "live within a few seconds", which is what the console says.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.content import ProviderSecret
from app.providers.secrets import SecretUnreadable, decrypt

logger = logging.getLogger(__name__)

#: How long a process may serve a cached overlay. Short enough that "set the
#: credentials and test them" feels immediate, long enough that a busy kiosk does
#: not query this table on every read-aloud.
OVERLAY_TTL_SECONDS = 10.0

#: Which `Settings` fields each provider owns. This mapping is the allow-list from
#: rule 2 — a stored row may write these keys and no others. Keyed by
#: `kind:vendor` so both vendors of a kind can be credentialed before either is
#: selected (Meta configured while the box still runs the fake, say).
CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "messaging:meta": (
        "meta_whatsapp_token",
        "meta_phone_number_id",
        "meta_verify_token",
        "meta_app_secret",
    ),
    "telephony:exotel": (
        "exotel_sid",
        "exotel_api_key",
        "exotel_token",
        "exotel_subdomain",
        "exotel_caller_id",
        "exotel_applet_url",
        "exotel_status_callback_url",
        "exotel_webhook_token",
    ),
}

#: The fields whose *presence* means "this vendor is configured". A credential set
#: missing one of these is incomplete, and the console says so rather than letting
#: a channel look ready and fail on the first message.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "messaging:meta": ("meta_whatsapp_token", "meta_phone_number_id"),
    "telephony:exotel": ("exotel_sid", "exotel_api_key", "exotel_token"),
}


class UnknownProviderSecret(ValueError):
    """A name outside `CREDENTIAL_FIELDS`. Refused rather than stored: a row
    nothing reads is a credential an operator believes is in force."""


def known(name: str) -> str:
    if name not in CREDENTIAL_FIELDS:
        raise UnknownProviderSecret(
            f"{name!r} has no credential fields; expected one of {sorted(CREDENTIAL_FIELDS)}"
        )
    return name


def sanitise(name: str, values: dict[str, Any]) -> dict[str, str]:
    """Keep only the fields this provider owns, as strings, dropping blanks.

    A blank is dropped rather than stored so "leave this one alone" and "set this
    one to empty" are the same harmless thing: the floor shows through, which is
    what an operator clearing a field means.
    """
    allowed = CREDENTIAL_FIELDS[known(name)]
    return {k: str(v).strip() for k, v in values.items() if k in allowed and str(v).strip()}


def missing_fields(name: str, values: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_FIELDS.get(name, ()) if not values.get(field)]


# -- the overlay ---------------------------------------------------------------

_cache: dict[str, str] | None = None
_cached_at: float = 0.0


def invalidate() -> None:
    """Drop this process's cached overlay — called after a save, so the admin who
    just entered a credential can test it in the same breath."""
    global _cache, _cached_at
    _cache = None
    _cached_at = 0.0


async def overlay(session: AsyncSession, settings: Settings | None = None) -> dict[str, str]:
    """Every stored credential field, decrypted and merged. Cached for the TTL.

    A row that will not decrypt is logged and skipped, not fatal: one unreadable
    credential set (a rotated key, say) must not take the other vendor down with
    it, and the channel it belongs to reports itself unconfigured — which is true.
    """
    global _cache, _cached_at
    settings = settings or get_settings()
    now = time.monotonic()
    if _cache is not None and now - _cached_at < OVERLAY_TTL_SECONDS:
        return _cache

    rows = (
        (await session.execute(select(ProviderSecret).where(ProviderSecret.deleted_at.is_(None))))
        .scalars()
        .all()
    )

    merged: dict[str, str] = {}
    for row in rows:
        if row.provider not in CREDENTIAL_FIELDS:
            continue
        try:
            values = decrypt(row.secret, row.key_id, settings)
        except SecretUnreadable as exc:
            logger.warning("provider credentials for %s are unreadable: %s", row.provider, exc)
            continue
        merged.update(sanitise(row.provider, values))

    _cache, _cached_at = merged, now
    return merged


async def effective_settings(session: AsyncSession, settings: Settings | None = None) -> Settings:
    """`Settings` with the stored credentials applied over the environment.

    The object every provider accessor should be built from once a console can
    supply credentials. `model_copy` rather than mutation: the process-wide
    `get_settings()` stays the environment's own view, so nothing can be surprised
    by a setting changing under it mid-request.
    """
    settings = settings or get_settings()
    values = await overlay(session, settings)
    return settings.model_copy(update=values) if values else settings


async def stored_names(session: AsyncSession) -> set[str]:
    """Which providers have a stored credential set — for the console's status,
    which is the only thing it is ever told about them beyond "configured"."""
    rows = (
        (
            await session.execute(
                select(ProviderSecret.provider).where(ProviderSecret.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return set(rows)
