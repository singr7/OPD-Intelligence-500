"""Append-only audit log (doc 02 §4/§7).

Append-only is enforced in the database, not in Python: the initial migration
installs triggers that raise on UPDATE or DELETE against this table. Application
bugs, a psql session, and a future ORM refactor all hit the same wall.

Rows are written by `app.audit` from a `before_flush` hook — no route calls this
model directly. Deliberately no `TimestampMixin`: an `updated_at` column on an
append-only table would be a lie, and its `onupdate` would trip the trigger.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey, enum_type
from app.models.enums import AuditAction, Role


class AuditLog(Base, UUIDPrimaryKey):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity_entity_id", "entity", "entity_id"),
        Index("ix_audit_log_actor_id_at", "actor_id", "at"),
    )

    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # Nullable: system actors (Celery beat, webhook callbacks, the seed script)
    # have no user row. `actor_label` always says who it was in words.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    actor_role: Mapped[Role | None] = mapped_column(enum_type(Role, "role"))
    actor_label: Mapped[str] = mapped_column(String(120), default="system")

    action: Mapped[AuditAction] = mapped_column(enum_type(AuditAction, "audit_action"), index=True)
    entity: Mapped[str] = mapped_column(String(64), index=True)  # table name
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # Correlates every row written by one request; set by the audit middleware.
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ip: Mapped[str | None] = mapped_column(String(64))

    # meta: {changed: {field: {from, to}}} for updates; {} for creates.
    # Values are redacted by `app.audit.REDACTED_FIELDS` — the log records that a
    # field changed, never the PII itself.
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
