"""The clinical record: visits, intakes, dictations, prescriptions (doc 02 §4).

Every table here is `Clinical` — writes land in `audit_log` automatically.
JSONB carries the shapes that later sessions own (tree answers in S4/S5,
structured dictation in S10), so this migration does not have to be revisited
each time those contracts firm up.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    Base,
    Clinical,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKey,
    enum_type,
)
from app.models.enums import Channel, DictationStatus, IntakeTier, Lang, VisitStatus


class Visit(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "visits"
    __table_args__ = (
        # Token numbers are unique per department per day; the offline kiosk blocks
        # in `offline_token_blocks` carve out non-overlapping ranges so a sync after
        # downtime can never collide with a server-issued token (doc 01 §5).
        UniqueConstraint("department_id", "date", "token_no", name="uq_visits_dept_date_token"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    token_no: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[VisitStatus] = mapped_column(
        enum_type(VisitStatus, "visit_status"), default=VisitStatus.REGISTERED, index=True
    )
    channel: Mapped[Channel] = mapped_column(enum_type(Channel, "channel"))

    intakes: Mapped[list[Intake]] = relationship(back_populates="visit")
    dictations: Mapped[list[Dictation]] = relationship(back_populates="visit")


class Intake(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "intakes"

    visit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visits.id"), index=True)
    tier: Mapped[IntakeTier] = mapped_column(enum_type(IntakeTier, "intake_tier"))
    lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"))

    # transcript: [{role, text, text_en, at, audio_url}] — S5 owns the shape.
    transcript: Mapped[list[Any]] = mapped_column(default=list)
    # answers: {node_id: {value, text, text_en, at}} — S4/S5 own the shape.
    answers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    red_flags: Mapped[list[Any]] = mapped_column(default=list)

    chief_complaint: Mapped[str | None] = mapped_column(Text)
    chief_complaint_en: Mapped[str | None] = mapped_column(Text)
    summary_md: Mapped[str | None] = mapped_column(Text)
    summary_lang_versions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    confirmed_by_patient: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Cost attribution is finalised on completion in S5 by summing the
    # usage_events that share this intake_id (doc 02 §8). Numeric, not float:
    # this is a sum of per-event costs that has to reconcile exactly against
    # usage_events on the S18 dashboard, and binary floats don't sum exactly.
    cost_inr: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    visit: Mapped[Visit] = relationship(back_populates="intakes")


class Dictation(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "dictations"

    visit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visits.id"), index=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id"), index=True)
    audio_url: Mapped[str | None] = mapped_column(String(500))
    transcript: Mapped[str | None] = mapped_column(Text)
    # structured: {diagnosis, plan, meds[], advice, follow_up, treatment_events[]} — S10.
    structured: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[DictationStatus] = mapped_column(
        enum_type(DictationStatus, "dictation_status"), default=DictationStatus.DRAFT, index=True
    )
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))

    visit: Mapped[Visit] = relationship(back_populates="dictations")


class Prescription(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "prescriptions"

    visit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visits.id"), index=True)
    dictation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("dictations.id"), index=True)
    meds: Mapped[list[Any]] = mapped_column(default=list)
    pdf_url: Mapped[str | None] = mapped_column(String(500))
    # delivered_via: {whatsapp: {at, status}, sms: {...}, print: {...}} — S11.
    delivered_via: Mapped[dict[str, Any]] = mapped_column(default=dict)
