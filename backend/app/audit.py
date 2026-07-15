"""Append-only audit trail for every clinical write (doc 02 §4/§7).

Design: audit rows are produced by a SQLAlchemy `before_flush` hook, not by
feature code. Any INSERT/UPDATE/soft-delete of a model marked `Clinical` yields
an `audit_log` row in the *same transaction* — so a write and its audit row
commit or roll back together, and there is no route-level call anyone can forget
to add. `tests/test_audit.py` asserts coverage over the mapper registry, so a new
clinical table without the marker fails CI.

`before_flush` (not `after_flush`) is what lets the audit row join the same
flush — objects added during `after_flush` would need a second one. The cost is
that pending rows have no PK yet, so `_entity_id` materialises it.

The actor comes from a `ContextVar` set per request by `AuditMiddleware`.
Background work (Celery, webhooks, seeds) runs with the default system actor
rather than silently attributing writes to a user.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.base import Clinical
from app.models.enums import AuditAction, Role

# Values never copied into the audit log. The log records *that* a field changed
# (doc 02 §7: audit is immutable and exported to S3 daily) — replicating names,
# phone numbers or clinical free-text into a second, longer-lived table would
# widen the PII blast radius for no investigative gain.
REDACTED_FIELDS = frozenset(
    {
        "name",
        "phone",
        "alt_phone",
        "caregiver_name",
        "caregiver_phone",
        "village",
        "transcript",
        "answers",
        "summary_md",
        "summary_lang_versions",
        "chief_complaint",
        "chief_complaint_en",
        "responses",
        "structured",
        "password_hash",
        "totp_secret",
        "code_hash",
        "consent_audio_url",
        "audio_url",
    }
)

# Bookkeeping columns — noise in a change log, since `at` already records when.
_IGNORED_FIELDS = frozenset({"created_at", "updated_at"})


@dataclass(frozen=True)
class Actor:
    """Who is responsible for a write."""

    id: uuid.UUID | None = None
    role: Role | None = None
    label: str = "system"
    request_id: str | None = None
    ip: str | None = None


SYSTEM_ACTOR = Actor(label="system")

_current_actor: ContextVar[Actor] = ContextVar("current_actor", default=SYSTEM_ACTOR)


def get_actor() -> Actor:
    return _current_actor.get()


def set_actor(actor: Actor) -> object:
    return _current_actor.set(actor)


def reset_actor(token: object) -> None:
    _current_actor.reset(token)  # type: ignore[arg-type]


@contextlib.contextmanager
def acting_as(actor: Actor) -> Iterator[Actor]:
    """Scope a block of work to an actor. Used by the middleware, Celery tasks,
    the seed script, and tests."""
    token = _current_actor.set(actor)
    try:
        yield actor
    finally:
        _current_actor.reset(token)


def _redact(value: Any) -> Any:
    """Keep change detection useful without copying PII: booleans and short enum
    codes are safe to record verbatim; everything else becomes a marker."""
    if value is None or isinstance(value, bool):
        return value
    return "<redacted>"


def _changed_fields(obj: object) -> dict[str, dict[str, Any]]:
    state = inspect(obj)
    changes: dict[str, dict[str, Any]] = {}
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in _IGNORED_FIELDS:
            continue
        history = state.attrs[key].history
        if not history.has_changes():
            continue
        before = history.deleted[0] if history.deleted else None
        after = history.added[0] if history.added else None
        if before == after:
            continue
        if key in REDACTED_FIELDS:
            changes[key] = {"from": _redact(before), "to": _redact(after)}
        else:
            changes[key] = {"from": _stringify(before), "to": _stringify(after)}
    return changes


def _stringify(value: Any) -> Any:
    """JSONB-safe rendering of a column value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _entity_id(obj: object) -> uuid.UUID | None:
    """The PK of the row being written, materialised if it doesn't exist yet.

    A column `default=uuid.uuid4` is evaluated by the INSERT during flush — which
    is *after* this hook runs, so a pending object still has `id=None` here.
    Assigning the UUID now produces exactly the value the default would have, and
    it is the only way the audit row can name the row it describes.
    """
    current = getattr(obj, "id", None)
    if current is None and hasattr(obj, "id"):
        current = uuid.uuid4()
        obj.id = current  # type: ignore[attr-defined]
    return current


def _entry(obj: object, action: AuditAction, actor: Actor, meta: dict[str, Any]) -> AuditLog:
    return AuditLog(
        actor_id=actor.id,
        actor_role=actor.role,
        actor_label=actor.label,
        action=action,
        entity=obj.__tablename__,  # type: ignore[attr-defined]
        entity_id=_entity_id(obj),
        request_id=actor.request_id,
        ip=actor.ip,
        meta=meta,
    )


def _collect(session: Session) -> list[AuditLog]:
    actor = get_actor()
    entries: list[AuditLog] = []

    for obj in session.new:
        if isinstance(obj, Clinical):
            entries.append(_entry(obj, AuditAction.CREATE, actor, {}))

    for obj in session.dirty:
        if not isinstance(obj, Clinical) or not session.is_modified(obj, include_collections=False):
            continue
        changes = _changed_fields(obj)
        if not changes:
            continue
        # Soft deletes are the only deletes on clinical tables (doc 02 §4), so a
        # newly-set deleted_at is logged as a deletion rather than a field edit.
        soft_deleted = changes.get("deleted_at", {}).get("to") is not None
        action = AuditAction.SOFT_DELETE if soft_deleted else AuditAction.UPDATE
        entries.append(_entry(obj, action, actor, {"changed": changes}))

    # Hard deletes shouldn't happen on clinical tables, but if one does, it is
    # logged rather than passing silently. The coverage test enforces the policy.
    for obj in session.deleted:
        if isinstance(obj, Clinical):
            entries.append(_entry(obj, AuditAction.DELETE, actor, {"hard_delete": True}))

    return entries


class AuditedSession(Session):
    """A Session that audits clinical writes.

    Auditing is bound to the session *class* rather than installed onto each
    factory: any sessionmaker built with `sync_session_class=AuditedSession`
    audits by construction, and there is no setup call to forget. Async sessions
    reach this through their sync session — `before_flush` is a sync-session
    event, and `async_sessionmaker` is not a valid event target.

    Scoping to a subclass, rather than listening on `Session` globally, keeps the
    hook off sessions that are not ours (Alembic's, for one).
    """


@event.listens_for(AuditedSession, "before_flush")
def _before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    for entry in _collect(session):
        session.add(entry)


def audited_models() -> list[type]:
    """Every mapped class marked `Clinical`. Used by the coverage test."""
    from app.models.base import Base

    return [
        mapper.class_ for mapper in Base.registry.mappers if issubclass(mapper.class_, Clinical)
    ]
