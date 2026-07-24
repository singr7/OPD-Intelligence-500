"""The Meta WhatsApp webhook (S12, doc 03 §1d) — the bot's only HTTP surface.

Two endpoints, both at `/whatsapp/webhook`:

    GET   the one-time subscription handshake (Meta echoes a challenge)
    POST  every inbound message (text, a button/list tap, or a voice note)

The route is deliberately thin: it verifies the request is really Meta's, turns
Meta's payload into the bot's normalised `Inbound`, and lets `WhatsAppBot` do the
thinking. Sending the replies and the single DB commit happen here, so the bot
stays a pure function of (state, message) — see `app.whatsapp.bot`.

**Authentication is the webhook's job, not the bot's.** A GET is checked against
the shared `meta_verify_token`; a POST body is signed by Meta with the app secret
(`X-Hub-Signature-256`) and verified here. With no secret configured (a local fake
stack) signature checking is skipped — there is nothing to verify against, and the
fake never signs.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session
from app.intake import IntakeEngine
from app.providers.base import ProviderError
from app.providers.messaging import OutboundMessage
from app.providers.registry import get_messaging_provider
from app.queue_hub import QueueHub
from app.whatsapp import ConversationStore
from app.whatsapp.bot import Inbound, WhatsAppBot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


# -- dependencies -------------------------------------------------------------


def get_engine(request: Request) -> IntakeEngine:
    engine = getattr(request.app.state, "intake_engine", None)
    if engine is None:  # pragma: no cover - lifespan always sets it
        raise HTTPException(status_code=503, detail="intake engine not ready")
    return engine


def get_conversations(request: Request) -> ConversationStore:
    store = getattr(request.app.state, "wa_conversation_store", None)
    if store is None:  # pragma: no cover - lifespan always sets it
        raise HTTPException(status_code=503, detail="whatsapp store not ready")
    return store


# -- GET: subscription handshake ----------------------------------------------


@router.get("/webhook")
async def verify(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Meta's one-time subscription check: echo `hub.challenge` back as plain text
    only when `hub.verify_token` matches ours. A mismatch is a 403 — anyone can hit
    this URL, and echoing the challenge to a stranger would subscribe an impostor.
    """
    if mode == "subscribe" and token and token == settings.meta_verify_token:
        # Meta wants the challenge echoed verbatim, as text/plain.
        return Response(content=challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="verification failed")


# -- POST: inbound messages ---------------------------------------------------


@router.post("/webhook")
async def inbound(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
    engine: IntakeEngine = Depends(get_engine),
    conversations: ConversationStore = Depends(get_conversations),
) -> dict[str, str]:
    """Handle a batch of inbound events, always answering 200.

    Meta retries on any non-200, so a parse error or a downstream hiccup on one
    message must not make it redeliver the whole batch forever — we log, skip, and
    acknowledge. Genuine duplicates are dropped by message id in the bot.
    """
    raw = await request.body()
    _verify_signature(raw, x_hub_signature_256, settings.meta_app_secret)

    try:
        payload = await request.json()
    except ValueError:
        logger.warning("whatsapp webhook: body was not JSON")
        return {"status": "ignored"}

    messages = _parse_inbound(payload)
    if not messages:
        # Delivery receipts / status callbacks land here too — nothing to do.
        return {"status": "ok"}

    bot = WhatsAppBot(engine=engine, conversations=conversations, settings=settings)
    provider = get_messaging_provider(settings)
    hub: QueueHub | None = getattr(request.app.state, "queue_hub", None)

    for message in messages:
        try:
            reply = await bot.handle(session, message)
            await session.commit()  # persist visit/token before we tell the patient
            await bot.synthesize_pending(reply)
            await _send_all(provider, reply.messages)
            if reply.queue_changed and hub is not None:
                await hub.notify_queue_changed()
        except Exception:  # noqa: BLE001 — one bad message must not 500 the batch
            await session.rollback()
            logger.exception("whatsapp webhook: failed handling a message from %s", message.wa_id)

    return {"status": "ok"}


# -- helpers ------------------------------------------------------------------


def _verify_signature(raw: bytes, header: str | None, app_secret: str) -> None:
    """Reject a body that Meta did not sign (doc 03 §1d).

    Skipped only when no `meta_app_secret` is configured — a local fake stack has
    nothing to verify against. When a secret *is* set, a missing or wrong signature
    is a 403: a webhook that accepts unsigned bodies lets anyone inject an intake.
    """
    if not app_secret:
        return
    if not header or not header.startswith("sha256="):
        raise HTTPException(status_code=403, detail="missing signature")
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header.removeprefix("sha256=")):
        raise HTTPException(status_code=403, detail="bad signature")


def _parse_inbound(payload: dict[str, Any]) -> list[Inbound]:
    """Meta's nested envelope → a flat list of normalised messages.

    Defensive throughout: Meta mixes message events with status callbacks in the
    same shape, fields are optional, and a malformed entry must be skipped, not
    crash the batch.
    """
    out: list[Inbound] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts") or []
            }
            for raw in value.get("messages") or []:
                parsed = _parse_one(raw, names)
                if parsed is not None:
                    out.append(parsed)
    return out


def _parse_one(raw: dict[str, Any], names: dict[str, str]) -> Inbound | None:
    wa_id = raw.get("from")
    if not wa_id:
        return None
    kind = raw.get("type")
    common = {
        "wa_id": wa_id,
        "message_id": raw.get("id"),
        "profile_name": names.get(wa_id),
    }

    if kind == "text":
        return Inbound(kind="text", text=(raw.get("text") or {}).get("body", ""), **common)
    if kind == "interactive":
        interactive = raw.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return Inbound(kind="reply", reply_id=reply.get("id"), **common)
    if kind in {"audio", "voice"}:
        media = raw.get(kind) or {}
        return Inbound(kind="audio", media_id=media.get("id"), **common)
    if kind == "button":
        # A quick-reply from a *template* button comes back as a plain "button".
        return Inbound(kind="text", text=(raw.get("button") or {}).get("text", ""), **common)
    # Location, contacts, reactions, stickers — not part of intake. Acknowledge and
    # drop; the bot never sees them.
    return None


async def _send_all(provider, messages: list[OutboundMessage]) -> None:
    for message in messages:
        try:
            await provider.send(message)
        except ProviderError as exc:
            # A failed outbound is logged, not raised: we already 200 the webhook,
            # and Meta redelivering the inbound would re-run the step. The patient
            # can re-send; the desk sees the log.
            logger.warning("whatsapp outbound to %s failed: %s", message.to, exc)
