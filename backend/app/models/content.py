"""Authored content and continuity: question trees, check-in plans, check-ins.

Question trees are DATA, not code (doc 02 §4) — versioned, draft/published, and
editable from the admin console (S18) without a deploy. S4 defines the `tree`
JSONB schema and its validator; this table only has to store and version it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    Clinical,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKey,
    enum_type,
)
from app.models.enums import Channel, CheckinGrade, CheckinPlanStatus, Lang, TreeStatus


class QuestionTree(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "question_trees"
    __table_args__ = (
        UniqueConstraint("key", "lang", "version", name="uq_question_trees_key_lang_version"),
    )

    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id"), index=True
    )
    key: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "med_onc_new_patient"
    version: Mapped[int] = mapped_column(Integer, default=1)
    lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"))
    tree: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[TreeStatus] = mapped_column(
        enum_type(TreeStatus, "tree_status"), default=TreeStatus.DRAFT, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckinPlan(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "checkin_plans"

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visits.id"), index=True)
    protocol_key: Mapped[str] = mapped_column(String(64), index=True)
    # schedule: [{day_offset, channel, question_set}] — S17 owns the shape.
    schedule: Mapped[list[Any]] = mapped_column(default=list)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CheckinPlanStatus] = mapped_column(
        enum_type(CheckinPlanStatus, "checkin_plan_status"),
        default=CheckinPlanStatus.DRAFT,
        index=True,
    )

    checkins: Mapped[list[Checkin]] = relationship(back_populates="plan")


class Checkin(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "checkins"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkin_plans.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    channel: Mapped[Channel] = mapped_column(enum_type(Channel, "channel"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responses: Mapped[dict[str, Any]] = mapped_column(default=dict)
    grade: Mapped[CheckinGrade | None] = mapped_column(
        enum_type(CheckinGrade, "checkin_grade"), index=True
    )
    escalated_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    plan: Mapped[CheckinPlan] = relationship(back_populates="checkins")
