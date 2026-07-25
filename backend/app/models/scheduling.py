"""Appointments, slot inventory, queues, and offline token blocks (doc 02 §4/§6,
doc 01 §5, doc 03 §2).

## Why double-booking is unrepresentable (S15)

doc 03 §2 asks for it as a *DB constraint*, not a service rule, because the two
things that book a slot — an AI receptionist mid-call and a coordinator in the
console — race by construction. Three constraints do it together:

1. `appointment_slots` is one row per (doctor, instant): `uq_appointment_slots_
   doctor_starts_at`. Generating inventory twice cannot produce two slots.
2. A slot carries `capacity` and `booked`, and the DB refuses `booked > capacity`
   (`ck_appointment_slots_booked_within_capacity`). Seats are claimed with an
   atomic `UPDATE ... SET booked = booked + 1 WHERE booked < capacity`, so the
   loser of a race gets zero rows back, not an overbooked clinic.
3. Each appointment holds a numbered **seat** in its slot, unique per slot
   (`uq_appointments_slot_id_seat_no`). Even if (2) were wrong, two live
   appointments could not name the same seat.

Cancelling **NULLs the seat and decrements `booked`** rather than deleting the
row: the appointment keeps its `slot_id` (history — which slot was released), and
NULL seats do not collide in a Postgres unique index, so the seat is genuinely
free again.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
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
from app.models.enums import (
    AppointmentStatus,
    Channel,
    OutboundCallState,
    Priority,
    QueueEntryState,
    SlotType,
)


class SlotTemplate(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """A doctor's recurring weekly clinic — "Dr Sharma, Tuesdays 10:00–13:00, 15
    minutes, 1 seat, follow-ups" (doc 03 §2/§10, admin-configured).

    Templates are the authored thing; `AppointmentSlot` rows are *generated* from
    them for real dates (`app.scheduling.generate_slots`). Editing a template
    never rewrites slots already booked — regeneration only adds what is missing.
    """

    __tablename__ = "slot_templates"
    __table_args__ = (
        UniqueConstraint(
            "doctor_id",
            "weekday",
            "start_time",
            "slot_type",
            name="uq_slot_templates_doctor_weekday_start_type",
        ),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="weekday_in_week"),
        CheckConstraint("slot_minutes > 0", name="slot_minutes_positive"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id"), index=True)
    #: Monday = 0, matching `datetime.date.weekday()`.
    weekday: Mapped[int] = mapped_column(Integer)
    #: Local clinic time (hospital timezone), not UTC — a clinic that starts at
    #: 10:00 starts at 10:00 in Alwar whatever the server thinks.
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=15)
    #: Seats per slot — an OPD doctor genuinely takes 2–3 patients per 15 minutes.
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    slot_type: Mapped[SlotType] = mapped_column(
        enum_type(SlotType, "slot_type"), default=SlotType.FOLLOW_UP
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AppointmentSlot(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """One bookable instant of one doctor's clinic. See the module docstring for
    why `booked`/`capacity` live here and what stops them going wrong."""

    __tablename__ = "appointment_slots"
    __table_args__ = (
        UniqueConstraint("doctor_id", "starts_at", name="uq_appointment_slots_doctor_starts_at"),
        CheckConstraint("booked >= 0 AND booked <= capacity", name="booked_within_capacity"),
    )

    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slot_templates.id"))
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctors.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_type: Mapped[SlotType] = mapped_column(enum_type(SlotType, "slot_type"), index=True)
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    booked: Mapped[int] = mapped_column(Integer, default=0)
    #: A doctor's leave, a machine down — blocks booking without deleting history.
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def seats_left(self) -> int:
        return 0 if self.blocked else max(0, self.capacity - self.booked)


class Appointment(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("slot_id", "seat_no", name="uq_appointments_slot_id_seat_no"),
    )

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
    # reminders: [{at, channel, kind, status}] — the confirmation/campaign
    # delivery log (S15 writes it; S17's check-ins append to the same shape).
    reminders: Mapped[list[Any]] = mapped_column(default=list)

    # -- slot inventory (S15) --
    #: Null only for an appointment made before slot inventory existed, or one a
    #: coordinator forced outside the clinic grid.
    slot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointment_slots.id"), index=True
    )
    #: 1..capacity. NULLed on cancel to release the seat (module docstring).
    seat_no: Mapped[int | None] = mapped_column(Integer)
    slot_type: Mapped[SlotType | None] = mapped_column(enum_type(SlotType, "slot_type"))

    slot: Mapped[AppointmentSlot | None] = relationship(lazy="raise")


class OutboundCall(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """One patient's slot in an outbound campaign, and its retry ladder (doc 01
    §4.2: the D-1 pre-visit intake call).

    This is the campaign's memory. It exists so the ladder survives a worker
    restart, so a re-run of the beat job re-dials nobody twice
    (`uq_outbound_calls_appointment_id_purpose`), and so the Exotel status
    callback — which arrives minutes later, in a different process — has a row to
    reconcile against by `call_sid`.
    """

    __tablename__ = "outbound_calls"
    __table_args__ = (
        UniqueConstraint("appointment_id", "purpose", name="uq_outbound_calls_appointment_purpose"),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointments.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    #: What the call is for. "d1_intake" today; S17's check-in ladder is the next.
    purpose: Mapped[str] = mapped_column(String(32), default="d1_intake")
    #: The clinic date the call is about (D+1 when the campaign runs).
    for_date: Mapped[date] = mapped_column(Date, index=True)
    to_phone: Mapped[str] = mapped_column(String(20))
    state: Mapped[OutboundCallState] = mapped_column(
        enum_type(OutboundCallState, "outbound_call_state"),
        default=OutboundCallState.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_call_sid: Mapped[str | None] = mapped_column(String(64), index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    #: Set when the call actually produced an intake, so the arrival desk knows
    #: the intake is already done (doc 01 §4.2).
    intake_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intakes.id"))
    #: The vendor's last word: "completed", "no-answer", "busy", "failed".
    outcome: Mapped[str | None] = mapped_column(String(32))
    fallback_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
