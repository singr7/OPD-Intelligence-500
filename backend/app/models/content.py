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
    ContentStatus,
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


class ProtocolBankVersion(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """One version of the **whole** check-in protocol bank (doc 03 §9/§10, S18-late).

    The trees moved from disk to this table one *tree* at a time, because a tree
    is self-contained. A protocol bank is not: `parse()` cross-checks the whole
    document — that no two protocols share a precedence, that no question set is
    orphaned, that every rung names a set that exists. Storing a protocol per row
    would let a half-edit pass row-level validation and fail those checks only at
    load, on a box, at the moment a doctor signs a note. So the versioned unit is
    the document, exactly as the file is.

    `bank` is the same JSON as `seeds/protocols.json`, and it reaches a
    `ProtocolBank` only through `app.checkins.protocols.parse` — the invariant the
    whole S17 session hangs off. The disk file stays the floor
    (`app.checkins.store.resolve_bank`), so a database with nothing published
    behaves exactly as it did before this table existed.
    """

    __tablename__ = "protocol_banks"
    __table_args__ = (UniqueConstraint("version", name="uq_protocol_banks_version"),)

    version: Mapped[int] = mapped_column(Integer, index=True)
    bank: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[ContentStatus] = mapped_column(
        enum_type(ContentStatus, "tree_status"), default=ContentStatus.DRAFT, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Free text from the editor — "added D+5 fever check for taxane", the line a
    #: clinical reviewer reads next to a version number.
    notes: Mapped[str | None] = mapped_column(Text)


class ChannelConfigVersion(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """One version of the **whole** channel document (doc 12 §1, S-GL.1).

    The third instance of the versioned draft→publish→resolve pattern, and
    deliberately the same shape as the second: a document rather than a row per
    channel, because the checks that matter are document-wide (a channel cannot
    reserve more GPU seats than the box has; a campaign mix must sum to 100).
    `app.tiers.parse_tier_config` is the only constructor, so a document typed
    into the console is validated exactly as `config/tiers.yaml` is, and
    `config/tiers.yaml` stays the floor (`app.channels.store.resolve_config`).

    What it decides is not clinical, but it is the loudest switch in the system:
    publishing a document with `whatsapp.enabled = false` is how a hospital says
    "that number is not answered yet" — and the alternative today is a patient
    messaging a bot that fails per message (doc 12 §4).
    """

    __tablename__ = "channel_configs"
    __table_args__ = (UniqueConstraint("version", name="uq_channel_configs_version"),)

    version: Mapped[int] = mapped_column(Integer, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[ContentStatus] = mapped_column(
        enum_type(ContentStatus, "tree_status"), default=ContentStatus.DRAFT, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: "closed whatsapp until Meta approves the templates" — the line the next
    #: person reads when they wonder why a channel is dark.
    notes: Mapped[str | None] = mapped_column(Text)


class ProviderSecret(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """A vendor credential set, entered in the console and encrypted at rest (S-GL.1).

    The first secret this codebase keeps in its database, and it is kept under
    three rules that the rest of the system does not need:

    1. **Write-only over the wire.** No route returns `secret`, ever. The console
       shows whether a credential is set, when, by whom, and what the vendor said
       the last time we tested it — never the value. There is no "reveal" button
       and no GET that could grow one.
    2. **Encrypted at rest** with a key that is *not* in the database
       (`app.providers.secrets`), so a database dump is not a set of live vendor
       credentials.
    3. **`.env` stays the floor**, exactly as the seed files are the floor for
       trees and protocols: a row overlays the environment, and deleting the row
       returns the box to whatever `.env` said.

    `provider` is the registry kind + vendor (`messaging:meta`, `telephony:exotel`)
    so two vendors of the same kind can be configured before either is selected.
    """

    __tablename__ = "provider_secrets"
    __table_args__ = (UniqueConstraint("provider", name="uq_provider_secrets_provider"),)

    provider: Mapped[str] = mapped_column(String(64), index=True)
    #: Fernet ciphertext over the JSON credential mapping. Never rendered.
    secret: Mapped[str] = mapped_column(Text)
    #: Which key encrypted it, so a rotated key can tell "cannot decrypt" from
    #: "wrong key" and say so instead of failing as though nothing were stored.
    key_id: Mapped[str] = mapped_column(String(32), default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    #: The last `POST /admin/providers/{name}/test`: {at, ok, detail}. The vendor's
    #: own error, kept verbatim — "the token is expired" is the whole value of a
    #: test button, and paraphrasing it loses the only actionable part.
    last_test: Mapped[dict[str, Any]] = mapped_column(default=dict)


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

    `grading_rules` freezes the rules for the same reason, and S18-late is why it
    has to. While the bank was a file in a pull request, "the rules moved under a
    live check-in" meant a deploy; now that an admin can publish a new bank from
    a console, it means an afternoon. A grade is recomputed on every answer and
    on every correction, so an unfrozen rule set would re-grade answers already
    given — quietly, and in either direction.
    """

    __tablename__ = "checkins"

    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkin_plans.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    day_offset: Mapped[int] = mapped_column(Integer, default=0)
    question_set: Mapped[str] = mapped_column(String(64), default="")
    #: The frozen question snapshot: `[Question.to_json(), ...]`.
    asked: Mapped[list[Any]] = mapped_column(default=list)
    #: The frozen grading rules: `[GradingRule.to_json(), ...]`, the set's rules
    #: as they stood when this check-in was created. NULL on rows written before
    #: S18-late, which grade against the bank as they always did.
    grading_rules: Mapped[list[Any] | None] = mapped_column(nullable=True)
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
