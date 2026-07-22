"""Intake session state (doc 02 §5) — one patient's live intake, across tiers.

> "SessionState (redis): language, dept, tree position, answers so far, active
> tier." — doc 02 §5

This is the state a channel adapter (kiosk WS, Exotel WS, WhatsApp webhook)
attaches to. Three processes touch it — api, worker, voice-gw — so it lives in
Redis, not a process dict, exactly like the cost-guard's tier override
(`app.providers.costguard`): a phone call handled by voice-gw and its summary
finalised by the worker must see the same answers.

## Position is *not* here — the answers are

The one field this state deliberately does **not** carry is a tree cursor. The
walker derives position from the answers (`app.trees.walker`), and duplicating
that into the session as "where we are" would be a second source of truth that
disagrees with the answers precisely when a provider is failing over — which is
the moment a downgrade rebuilds the walk on a new tier. So we store
`walk.to_json()` (the answers) and rebuild `Walk.from_json(tree, answers)` on
whichever tier picks the session up. A downgrade is then lossless by
construction, not by remembering to copy a cursor across.

`answers` is the same `{node_id: {value, text, text_en, lang, at}}` shape doc 03
§1's AC demands every tier and channel produce, and it is what lands in
`Intake.answers` on completion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from app.models.enums import Channel, IntakeTier, Lang
from app.prompts.tools import TOOL_CONTRACT_VERSION


class SessionStatus(StrEnum):
    """Where an intake is in its life. Only `active` accepts more answers."""

    ACTIVE = "active"
    COMPLETE = "complete"  # tree walked to the end, summary produced
    ENDED = "ended"  # patient stopped early; partial intake saved (doc 03 §1b)
    HANDOFF = "handoff"  # escalated to a human


@dataclass(slots=True)
class SessionState:
    """Everything needed to resume an intake on any tier, in any process.

    Serialised whole to Redis on every mutation. Kept deliberately small and
    JSON-first: the `Tree` is *not* stored (it is reloaded from `tree_key` via the
    bank), because a tree is validated content that must come through
    `schema.parse`, never a dict thawed from Redis (STATE.md invariant).
    """

    session_id: str
    channel: Channel
    lang: Lang
    tree_key: str
    tree_version: int
    #: The tier config asked for; `active_tier` is where the session actually runs
    #: after any downgrade. Keeping both is what lets recovery return to the
    #: configured tier when the day rolls over (the guard clears its override).
    configured_tier: IntakeTier
    active_tier: IntakeTier

    department: str | None = None
    intake_id: uuid.UUID | None = None
    visit_id: uuid.UUID | None = None
    #: Pinned at session start; a session that began on one tool contract keeps it
    #: even if the process is redeployed with a newer one mid-intake
    #: (`app.prompts.tools`: a redefined `save_answer` mid-intake is data corruption).
    contract_version: str = TOOL_CONTRACT_VERSION

    chief_complaint: str | None = None
    chief_complaint_en: str | None = None

    #: `walk.to_json()` — the answers, from which position is derived.
    answers: dict[str, Any] = field(default_factory=dict)
    #: [{role, text, text_en, lang, at, tier}] — what was said, for the record and
    #: the doctor screen. doc 03 §4's transcript shape (`Intake.transcript`).
    transcript: list[dict[str, Any]] = field(default_factory=list)
    #: Last computed red flags (recomputed on every save; cached here only so a
    #: reader that is not the walker — a coordinator banner — can see them).
    red_flags: list[dict[str, Any]] = field(default_factory=list)

    status: SessionStatus = SessionStatus.ACTIVE
    confirmed: bool = False
    summary_md: str | None = None
    #: {lang: {"structured": {...}, "readback": "..."}} — the doc 03 §4 contract in
    #: each language it was rendered in, keyed for `Intake.summary_lang_versions`.
    summary_lang_versions: dict[str, Any] = field(default_factory=dict)
    #: Finalised on completion by summing usage_events for this intake_id.
    cost_inr: Decimal | None = None

    #: Adaptive-intake enrichment (S-ADAPT.2, doc 11 §3): facts a patient
    #: volunteered for questions not yet asked, waiting for the walk to reach them.
    #: {node_id: {"value": ..., "text": ...}}. Each is still validated by
    #: `walk.save` when auto-applied, so nothing here bypasses the tree or a rule.
    pending_prefills: dict[str, Any] = field(default_factory=dict)
    #: One record per adaptive interpret event, for the V2 telemetry report (doc 11
    #: §3): [{node_id, outcome, enriched, at}]. Persisted onto `Intake.adaptive_events`
    #: at finalize; the LLM-call turns reconcile to the intake's INTAKE_TURN usage_events.
    adaptive_turns: list[dict[str, Any]] = field(default_factory=list)

    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # -- transcript helpers ---------------------------------------------------

    def record_turn(
        self,
        role: str,
        text: str,
        *,
        text_en: str | None = None,
        lang: Lang | str | None = None,
    ) -> None:
        self.transcript.append(
            {
                "role": role,
                "text": text,
                "text_en": text_en,
                "lang": str(lang) if lang else None,
                "at": datetime.now(UTC).isoformat(),
                "tier": str(self.active_tier),
            }
        )

    # -- serialisation --------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "channel": str(self.channel),
            "lang": str(self.lang),
            "tree_key": self.tree_key,
            "tree_version": self.tree_version,
            "configured_tier": str(self.configured_tier),
            "active_tier": str(self.active_tier),
            "department": self.department,
            "intake_id": str(self.intake_id) if self.intake_id else None,
            "visit_id": str(self.visit_id) if self.visit_id else None,
            "contract_version": self.contract_version,
            "chief_complaint": self.chief_complaint,
            "chief_complaint_en": self.chief_complaint_en,
            "answers": self.answers,
            "transcript": self.transcript,
            "red_flags": self.red_flags,
            "status": str(self.status),
            "confirmed": self.confirmed,
            "summary_md": self.summary_md,
            "summary_lang_versions": self.summary_lang_versions,
            "cost_inr": str(self.cost_inr) if self.cost_inr is not None else None,
            "pending_prefills": self.pending_prefills,
            "adaptive_turns": self.adaptive_turns,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SessionState:
        def as_uuid(value: Any) -> uuid.UUID | None:
            return uuid.UUID(value) if value else None

        return cls(
            session_id=data["session_id"],
            channel=Channel(data["channel"]),
            lang=Lang(data["lang"]),
            tree_key=data["tree_key"],
            tree_version=int(data["tree_version"]),
            configured_tier=IntakeTier(data["configured_tier"]),
            active_tier=IntakeTier(data["active_tier"]),
            department=data.get("department"),
            intake_id=as_uuid(data.get("intake_id")),
            visit_id=as_uuid(data.get("visit_id")),
            contract_version=data.get("contract_version", TOOL_CONTRACT_VERSION),
            chief_complaint=data.get("chief_complaint"),
            chief_complaint_en=data.get("chief_complaint_en"),
            answers=data.get("answers") or {},
            transcript=data.get("transcript") or [],
            red_flags=data.get("red_flags") or [],
            status=SessionStatus(data.get("status", SessionStatus.ACTIVE)),
            confirmed=bool(data.get("confirmed", False)),
            summary_md=data.get("summary_md"),
            summary_lang_versions=data.get("summary_lang_versions") or {},
            cost_inr=Decimal(data["cost_inr"]) if data.get("cost_inr") is not None else None,
            pending_prefills=data.get("pending_prefills") or {},
            adaptive_turns=data.get("adaptive_turns") or [],
            created_at=_parse_dt(data.get("created_at")),
            updated_at=_parse_dt(data.get("updated_at")),
        )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(UTC)


class SessionStore(Protocol):
    """Where a live intake's state lives between turns.

    The seam between the intake engine and its transport: an Exotel call and the
    summary worker share one intake only because they share one store.
    """

    async def get(self, session_id: str) -> SessionState | None: ...

    async def save(self, state: SessionState) -> None: ...

    async def delete(self, session_id: str) -> None: ...


class InMemorySessionStore:
    """Single-process store, for tests and `make dev` without Redis.

    Wrong for production the same way `InMemoryTierOverrideStore` is: voice-gw and
    api would each keep their own idea of the intake. `build_session_store` picks
    Redis outside local.
    """

    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {}

    async def get(self, session_id: str) -> SessionState | None:
        state = self._states.get(session_id)
        # Hand back a copy so a caller mutating its local state does not
        # retroactively change what is "stored" without a save() — matching the
        # Redis store, where get() always deserialises a fresh object.
        return replace(state) if state is not None else None

    async def save(self, state: SessionState) -> None:
        state.updated_at = datetime.now(UTC)
        self._states[state.session_id] = replace(state)

    async def delete(self, session_id: str) -> None:
        self._states.pop(session_id, None)


class RedisSessionStore:
    """Redis-backed store — the one production uses.

    A TTL bounds how long an abandoned intake (a patient who walked away from the
    kiosk, a dropped call) lingers. It is generous — an intake can pause while a
    coordinator is fetched — but finite, so Redis is not a graveyard of
    half-finished sessions.
    """

    KEY = "intake:session:{session_id}"

    def __init__(self, redis, *, ttl_seconds: int = 6 * 3600) -> None:  # redis.asyncio.Redis
        self._redis = redis
        self._ttl = ttl_seconds

    async def get(self, session_id: str) -> SessionState | None:
        import json

        raw = await self._redis.get(self.KEY.format(session_id=session_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        return SessionState.from_json(json.loads(text))

    async def save(self, state: SessionState) -> None:
        import json

        state.updated_at = datetime.now(UTC)
        await self._redis.set(
            self.KEY.format(session_id=state.session_id),
            json.dumps(state.to_json()),
            ex=self._ttl,
        )

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self.KEY.format(session_id=session_id))


def build_session_store(settings) -> SessionStore:
    """Redis outside local; in-memory for tests and single-process dev.

    Mirrors `costguard.build_override_store`: local dev gets the in-memory store
    even though compose runs Redis, so `pytest` needs no broker.
    """
    if settings.is_local:
        return InMemorySessionStore()
    from redis.asyncio import Redis

    return RedisSessionStore(Redis.from_url(settings.redis_url))
