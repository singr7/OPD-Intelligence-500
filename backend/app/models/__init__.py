"""SQLAlchemy models for the full domain schema (doc 02 §4).

Importing this package registers every mapper on `Base.metadata`. Alembic's
`env.py` and the audit layer both depend on that being complete, so a new model
module must be imported here or it will be silently missing from migrations.
"""

from app.models.audit import AuditLog
from app.models.auth import OtpCode, RefreshToken
from app.models.base import Base, Clinical, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey
from app.models.clinical import (
    Dictation,
    DocumentExtraction,
    DoseEvent,
    Intake,
    MedicalDocument,
    Prescription,
    Visit,
)
from app.models.content import (
    ChannelConfigVersion,
    Checkin,
    CheckinPlan,
    ProtocolBankVersion,
    ProviderSecret,
    QuestionTree,
)
from app.models.enums import (
    AppointmentStatus,
    AuditAction,
    CaregiverLinkStatus,
    Channel,
    CheckinGrade,
    CheckinPlanStatus,
    DictationStatus,
    DoseStatus,
    IntakeTier,
    Lang,
    OtpPurpose,
    OutboundCallState,
    PriceUnit,
    Priority,
    QueueEntryState,
    Role,
    Sex,
    SlotType,
    TreeStatus,
    UsagePurpose,
    VisitStatus,
)
from app.models.metering import PriceBook, UsageEvent
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import CaregiverLink, Patient
from app.models.scheduling import (
    Appointment,
    AppointmentSlot,
    OfflineTokenBlock,
    OutboundCall,
    Queue,
    QueueEntry,
    SlotTemplate,
)

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
    "DoseEvent",
    "MedicalDocument",
    "DocumentExtraction",
    "CaregiverLink",
    # scheduling
    "Appointment",
    "SlotTemplate",
    "AppointmentSlot",
    "OutboundCall",
    "Queue",
    "QueueEntry",
    "OfflineTokenBlock",
    # content / continuity
    "QuestionTree",
    "CheckinPlan",
    "ChannelConfigVersion",
    "ProtocolBankVersion",
    "ProviderSecret",
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
    "CaregiverLinkStatus",
    "Channel",
    "CheckinGrade",
    "CheckinPlanStatus",
    "DictationStatus",
    "DoseStatus",
    "IntakeTier",
    "Lang",
    "OtpPurpose",
    "OutboundCallState",
    "PriceUnit",
    "Priority",
    "QueueEntryState",
    "Role",
    "Sex",
    "SlotType",
    "TreeStatus",
    "UsagePurpose",
    "VisitStatus",
]
