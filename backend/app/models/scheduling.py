"""Appointments, queues, and offline token blocks (doc 02 §4/§6, doc 01 §5)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
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
from app.models.enums import AppointmentStatus, Channel, Priority, QueueEntryState


class Appointment(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "appointments"

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"), index=True)
    slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[AppointmentStatus] = mapped_column(
        enum_type(AppointmentStatus, "appointment_status"),
        default=AppointmentStatus.BOOKED,
        index=True,
    )
    source: Mapped[Channel] = mapped_column(enum_type(Channel, "channel"))
    # reminders: [{at, channel, status}] — S15/S17 own the shape.
    reminders: Mapped[list[Any]] = mapped_column(default=list)


class Queue(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "queues"
    __table_args__ = (
        UniqueConstraint("department_id", "doctor_id", "date", name="uq_queues_dept_doctor_date"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)

    entries: Mapped[list[QueueEntry]] = relationship(back_populates="queue")


class QueueEntry(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "queue_entries"
    __table_args__ = (
        UniqueConstraint("queue_id", "visit_id", name="uq_queue_entries_queue_visit"),
    )

    queue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("queues.id"), index=True)
    visit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visits.id"), index=True)
    token_no: Mapped[int] = mapped_column(Integer)
    priority: Mapped[Priority] = mapped_column(
        enum_type(Priority, "priority"), default=Priority.ROUTINE
    )
    # Why an urgent entry jumped the queue — surfaced as a chip on the board (S8).
    priority_reason: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[QueueEntryState] = mapped_column(
        enum_type(QueueEntryState, "queue_entry_state"), default=QueueEntryState.WAITING, index=True
    )
    # Manual ordering handle for the coordinator's drag-reorder (S8).
    position: Mapped[int | None] = mapped_column(Integer)
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    queue: Mapped[Queue] = relationship(back_populates="entries")


class OfflineTokenBlock(Base, UUIDPrimaryKey, TimestampMixin):
    """Pre-allocated token ranges a kiosk consumes while the API is unreachable
    (doc 01 §5). Ranges never overlap, so offline tokens can't collide with
    server-issued ones when the kiosk syncs back."""

    __tablename__ = "offline_token_blocks"
    __table_args__ = (
        UniqueConstraint("kiosk_id", "date", "start_no", name="uq_offline_blocks_kiosk_date_start"),
    )

    kiosk_id: Mapped[str] = mapped_column(String(64), index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    start_no: Mapped[int] = mapped_column(Integer)
    end_no: Mapped[int] = mapped_column(Integer)
    used_up_to: Mapped[int | None] = mapped_column(Integer)
