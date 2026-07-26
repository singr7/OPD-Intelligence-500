"""Authored content and continuity: question trees, check-in plans, check-ins.

Question trees are DATA, not code (doc 02 §4) — versioned, draft/published, and
editable from the admin console (S18) without a deploy. S4 defines the `tree`
JSONB schema and its validator; this table only has to store and version it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    Clinical,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKey,
    enum_type,
)
from app.models.enums import (
    Channel,
    CheckinGrade,
    CheckinPlanStatus,
    CheckinState,
    Lang,
    TreeStatus,
)


class QuestionTree(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """One version of one authored tree. **Every language lives in the JSONB.**

    Doc 02 §4 sketched this table with a `lang` column and a (key, lang, version)
    key — one row per language. S4 dropped it, because doc 03 §3's node schema
    carries `text:{en,hi,mr,te}` inside the node and that is the shape that works:

    - Doc 03 §1 makes the patient's language **switchable at any time**, and
      `Intake.lang` is per-intake. With text in the node that is a re-render of
      the same node id; with per-language rows it is a mid-session swap onto a
      different tree row that has to happen to share node ids and branching.
    - Branching and red flags stay single-sourced. Four rows per tree means four
      copies of `red_flag_if` free to drift, under one clinical sign-off (S21)
      that only ever covered the copy the reviewer opened.
    - S13 (mr/te) becomes additive — fill in text keys, touch no structure.

    The `tree` JSONB schema and its validator are `app.trees.schema`; nothing here
    knows the shape beyond "it is an object".
    """

    __tablename__ = "question_trees"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_question_trees_key_version"),)

    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id"), index=True
    )
    key: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "med_onc_new_patient"
    version: Mapped[int] = mapped_column(Integer, default=1)
    tree: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[TreeStatus] = mapped_column(
        enum_type(TreeStatus, "tree_status"), default=TreeStatus.DRAFT, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CheckinPlan(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    """One patient's follow-up after one treatment (doc 03 §9, S17).

    Drafted by `app.checkins.plan` the moment a dictation is signed, approved by
    the doctor in one tap, then materialised into `Checkin` rows. The row keeps
    **what was decided**, not what the protocol bank currently says: `schedule`
    is a frozen list of `{day_offset, question_set, channel, message}`, so
    re-authoring a protocol next month cannot change a plan already approved —
    the same reason S11's prescription stores a `meds` snapshot rather than a
    view over the dictation.
    """

    __tablename__ = "checkin_plans"

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visits.id"), index=True)
    #: The signed note this plan came out of. A plan without one would be a
    #: follow-up nobody prescribed.
    dictation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dictations.id"), index=True)
    protocol_key: Mapped[str] = mapped_column(String(64), index=True)
    #: The instant the day offsets count from — the treatment date the doctor
    #: dictated, falling back to the signature. Stored because "D+2" is
    #: meaningless once the plan is a row in a table.
    treatment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"), default=Lang.HI)
    # schedule: [{day_offset, question_set, channel, message, due_at}] — the
    # frozen plan. `app.checkins.plan` owns the shape.
    schedule: Mapped[list[Any]] = mapped_column(default=list)
    #: What the LLM personalisation did, or why it did not run: model, prompt_ref,
    #: notes_for_doctor, error. Kept so "why does this message say that?" is
    #: answerable months later, like every other model output in this codebase.
    personalisation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: When the next cycle of this regimen is due, if it is a cycled one — the
    #: anchor for the D-2 / D-0 reminders (doc 03 §9).
    next_cycle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: [{at, rung, channel, status}] — which cycle reminders have gone out, so a
    #: job that runs hourly does not remind the same patient hourly.
    cycle_reminders: Mapped[list[Any]] = mapped_column(default=list)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CheckinPlanStatus] = mapped_column(
        enum_type(CheckinPlanStatus, "checkin_plan_status"),
        default=CheckinPlanStatus.DRAFT,
        index=True,
    )

    checkins: Mapped[list[Checkin]] = relationship(back_populates="plan")


class Checkin(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    """One rung of a plan: one set of questions, asked once, graded once.

    `asked` freezes the questions as they were sent. A patient answers "2" three
    days later and the answer has to mean what the question meant when she read
    it, not what the bank says today.
    """

    __tablename__ = "checkins"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkin_plans.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    day_offset: Mapped[int] = mapped_column(Integer, default=0)
    question_set: Mapped[str] = mapped_column(String(64), default="")
    #: The frozen question snapshot: `[Question.to_json(), ...]`.
    asked: Mapped[list[Any]] = mapped_column(default=list)
    #: The personalised covering line, in the patient's language.
    message: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"), default=Lang.HI)
    #: The channel currently being tried. The ladder rewrites it on each rung.
    channel: Mapped[Channel] = mapped_column(enum_type(Channel, "channel"))
    state: Mapped[CheckinState] = mapped_column(
        enum_type(CheckinState, "checkin_state"), default=CheckinState.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    #: When the ladder may try again. NULL = nothing pending (sent, answered, or
    #: given up on) — the same shape as `OutboundCall.next_attempt_at` (S15).
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    #: [{at, channel, status, detail}] — every rung attempted, in order.
    delivery: Mapped[list[Any]] = mapped_column(default=list)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responses: Mapped[dict[str, Any]] = mapped_column(default=dict)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grade: Mapped[CheckinGrade | None] = mapped_column(
        enum_type(CheckinGrade, "checkin_grade"), index=True
    )
    #: [{id, grade, reason, source}] — which rules fired, so a nurse reading the
    #: queue sees the clinical reason and not just a colour.
    grade_reasons: Mapped[list[Any]] = mapped_column(default=list)
    escalated_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolution_note: Mapped[str] = mapped_column(Text, default="")

    plan: Mapped[CheckinPlan] = relationship(back_populates="checkins")
