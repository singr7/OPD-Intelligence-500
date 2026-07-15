"""SQLAlchemy models for the full domain schema (doc 02 §4).

Importing this package registers every mapper on `Base.metadata`. Alembic's
`env.py` and the audit layer both depend on that being complete, so a new model
module must be imported here or it will be silently missing from migrations.
"""

from app.models.audit import AuditLog
from app.models.auth import OtpCode, RefreshToken
from app.models.base import Base, Clinical, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey
from app.models.clinical import Dictation, Intake, Prescription, Visit
from app.models.content import Checkin, CheckinPlan, QuestionTree
from app.models.enums import (
    AppointmentStatus,
    AuditAction,
    Channel,
    CheckinGrade,
    CheckinPlanStatus,
    DictationStatus,
    IntakeTier,
    Lang,
    OtpPurpose,
    PriceUnit,
    Priority,
    QueueEntryState,
    Role,
    Sex,
    TreeStatus,
    UsagePurpose,
    VisitStatus,
)
from app.models.metering import PriceBook, UsageEvent
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import Patient
from app.models.scheduling import Appointment, OfflineTokenBlock, Queue, QueueEntry

__all__ = [
    # base
    "Base",
    "Clinical",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UUIDPrimaryKey",
    # org / identity
    "Hospital",
    "Department",
    "User",
    "Doctor",
    # patient + clinical record
    "Patient",
    "Visit",
    "Intake",
    "Dictation",
    "Prescription",
    # scheduling
    "Appointment",
    "Queue",
    "QueueEntry",
    "OfflineTokenBlock",
    # content / continuity
    "QuestionTree",
    "CheckinPlan",
    "Checkin",
    # metering
    "PriceBook",
    "UsageEvent",
    # auth
    "OtpCode",
    "RefreshToken",
    # audit
    "AuditLog",
    # enums
    "AppointmentStatus",
    "AuditAction",
    "Channel",
    "CheckinGrade",
    "CheckinPlanStatus",
    "DictationStatus",
    "IntakeTier",
    "Lang",
    "OtpPurpose",
    "PriceUnit",
    "Priority",
    "QueueEntryState",
    "Role",
    "Sex",
    "TreeStatus",
    "UsagePurpose",
    "VisitStatus",
]
