"""WhatsApp conversation state (S12, doc 03 §1d) — one patient's thread with the bot.

The intake engine's `SessionState` (Redis) already holds a live intake's answers,
tier and position. This is the layer *above* that: the thing keyed by the
patient's phone (`wa_id`) rather than by an opaque session id, because that is all
a Meta webhook hands us. It carries three things `SessionState` cannot:

1. **wa_id → session_id** — the mapping from the phone Meta gives us to the intake
   session the engine knows, so the next inbound message resumes the right walk.
2. **The pre-intake step** — language pick, chief-complaint capture and the
   department chooser happen *before* an intake session exists (the kiosk does
   these in the browser; on WhatsApp the server drives them one message at a time).
3. **The 24-hour window** — `last_inbound_at`. Meta lets us send free text only
   within 24h of the patient's last message; outside it we must use a registered
   template (`app.whatsapp.templates`). This is the only channel with that rule,
   so the window state lives here, not in the engine.

Like `SessionState` and the cost-guard override, this lives in Redis in
production (three processes may touch a thread) and in-memory for tests / local.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from app.models.enums import Lang

#: Meta's customer-service window. Free-form messages are allowed only within this
#: long after the patient's most recent inbound message; outside it, a registered
#: template is the only thing the API will accept (doc 03 §1d).
WINDOW = timedelta(hours=24)


class ConversationStep(StrEnum):
    """Where the bot is in the thread. The intake *engine* owns everything from
    `INTAKE` onward; the earlier steps are the WhatsApp adapter's own small FSM,
    standing in for screens the kiosk shows in the browser."""

    IDLE = "idle"  # no active flow — a greeting or a command lands here
    LANGUAGE = "language"  # asked the patient to pick a language
    COMPLAINT = "complaint"  # asked for the chief complaint (text or voice note)
    DEPARTMENT = "department"  # showed the department chooser (classifier unsure)
    INTAKE = "intake"  # an engine session is live; walking the tree
    READBACK = "readback"  # summary shown; awaiting confirm / change
    DONE = "done"  # token issued; thread is quiet until the next message


@dataclass(slots=True)
class Conversation:
    """One patient's WhatsApp thread. Small, JSON-first, Redis-serialised whole."""

    wa_id: str
    step: ConversationStep = ConversationStep.IDLE
    lang: Lang | None = None
    #: The live intake session (engine `SessionState.session_id`), once one exists.
    session_id: str | None = None
    patient_id: uuid.UUID | None = None
    visit_id: uuid.UUID | None = None
    #: The chooser options last shown, as (dept_key, name); a button reply's id is
    #: matched against these so we never trust a key the patient's client invented.
    department_options: list[list[str]] = field(default_factory=list)
    #: The last time the *patient* messaged us — the anchor of the 24h window.
    last_inbound_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def within_window(self, *, now: datetime | None = None) -> bool:
        """True if a free-form message is allowed right now (doc 03 §1d).

        Conservative when unknown: a conversation we have never heard from is
        treated as *out* of window, so the first proactive contact is forced onto
        a template rather than a free-form send Meta would reject.
        """
        if self.last_inbound_at is None:
            return False
        now = now or datetime.now(UTC)
        anchor = self.last_inbound_at
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
        return now - anchor < WINDOW

    def mark_inbound(self, *, now: datetime | None = None) -> None:
        """Record that the patient just messaged — (re)opening the 24h window."""
        self.last_inbound_at = now or datetime.now(UTC)

    def reset_flow(self) -> None:
        """Drop the live-intake pointers when a thread finishes or is abandoned,
        keeping the identity (wa_id, lang, patient) and the window anchor."""
        self.step = ConversationStep.IDLE
        self.session_id = None
        self.visit_id = None
        self.department_options = []

    # -- serialisation --------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "wa_id": self.wa_id,
            "step": str(self.step),
            "lang": str(self.lang) if self.lang else None,
            "session_id": self.session_id,
            "patient_id": str(self.patient_id) if self.patient_id else None,
            "visit_id": str(self.visit_id) if self.visit_id else None,
            "department_options": self.department_options,
            "last_inbound_at": self.last_inbound_at.isoformat() if self.last_inbound_at else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Conversation:
        def as_uuid(value: Any) -> uuid.UUID | None:
            return uuid.UUID(value) if value else None

        def as_dt(value: Any) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        return cls(
            wa_id=data["wa_id"],
            step=ConversationStep(data.get("step", ConversationStep.IDLE)),
            lang=Lang(data["lang"]) if data.get("lang") else None,
            session_id=data.get("session_id"),
            patient_id=as_uuid(data.get("patient_id")),
            visit_id=as_uuid(data.get("visit_id")),
            department_options=[list(o) for o in data.get("department_options") or []],
            last_inbound_at=as_dt(data.get("last_inbound_at")),
            created_at=as_dt(data.get("created_at")) or datetime.now(UTC),
            updated_at=as_dt(data.get("updated_at")) or datetime.now(UTC),
        )


class ConversationStore(Protocol):
    """Where a WhatsApp thread's state lives between webhook calls."""

    async def get(self, wa_id: str) -> Conversation | None: ...

    async def save(self, conversation: Conversation) -> None: ...

    async def delete(self, wa_id: str) -> None: ...


class InMemoryConversationStore:
    """Single-process store, for tests and `make dev` without Redis. Wrong for
    production for the same reason `InMemorySessionStore` is."""

    def __init__(self) -> None:
        self._threads: dict[str, Conversation] = {}

    async def get(self, wa_id: str) -> Conversation | None:
        thread = self._threads.get(wa_id)
        return replace(thread) if thread is not None else None

    async def save(self, conversation: Conversation) -> None:
        conversation.updated_at = datetime.now(UTC)
        self._threads[conversation.wa_id] = replace(conversation)

    async def delete(self, wa_id: str) -> None:
        self._threads.pop(wa_id, None)


class RedisConversationStore:
    """Redis-backed store — production's.

    The TTL is a few days, not hours: the 24h window itself is enforced by
    `last_inbound_at`, and we want a returning patient's language and identity to
    survive between visits so we do not re-ask on every contact.
    """

    KEY = "wa:conversation:{wa_id}"

    def __init__(self, redis, *, ttl_seconds: int = 7 * 24 * 3600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def get(self, wa_id: str) -> Conversation | None:
        import json

        raw = await self._redis.get(self.KEY.format(wa_id=wa_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        return Conversation.from_json(json.loads(text))

    async def save(self, conversation: Conversation) -> None:
        import json

        conversation.updated_at = datetime.now(UTC)
        await self._redis.set(
            self.KEY.format(wa_id=conversation.wa_id),
            json.dumps(conversation.to_json()),
            ex=self._ttl,
        )

    async def delete(self, wa_id: str) -> None:
        await self._redis.delete(self.KEY.format(wa_id=wa_id))


def build_conversation_store(settings) -> ConversationStore:
    """Redis outside local; in-memory for tests and single-process dev. Mirrors
    `build_session_store`."""
    if settings.is_local:
        return InMemoryConversationStore()
    from redis.asyncio import Redis

    return RedisConversationStore(Redis.from_url(settings.redis_url))
