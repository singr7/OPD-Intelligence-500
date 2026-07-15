"""Domain enums (doc 02 §4).

Stored as VARCHAR + CHECK constraint (`native_enum=False`), not Postgres native
ENUM types: adding a value later is a cheap constraint swap rather than an
`ALTER TYPE` that can't run inside a transaction. Values are the wire format —
they appear in JSON payloads and the admin console, so renaming one is a
breaking change.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """RBAC roles (doc 02 §7). Ordered least → most privileged for readability only;
    privilege is decided by explicit grants in `app.auth.rbac`, never by ordering."""

    PATIENT = "patient"
    CAREGIVER = "caregiver"
    COORDINATOR = "coordinator"
    NURSE = "nurse"
    DOCTOR = "doctor"
    ADMIN = "admin"


class Lang(StrEnum):
    EN = "en"
    HI = "hi"
    MR = "mr"
    TE = "te"


class Sex(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class Channel(StrEnum):
    KIOSK = "kiosk"
    PHONE = "phone"
    WHATSAPP = "whatsapp"
    APP = "app"
    PAPER = "paper"


class VisitStatus(StrEnum):
    REGISTERED = "registered"
    INTAKE_DONE = "intake_done"
    IN_QUEUE = "in_queue"
    IN_CONSULT = "in_consult"
    DONE = "done"
    NO_SHOW = "no_show"


class IntakeTier(StrEnum):
    """Voice/intake tiers (doc 02 §2). V1→V2→V3 is the downgrade ladder."""

    CONVERSATIONAL = "conversational"  # V1 — Gemini Live
    RULE_BASED = "rule_based"  # V2 — STT → LLM → TTS
    PRERECORDED = "prerecorded"  # V3 — deterministic walker + voice packs
    PAPER = "paper"  # downtime


class TreeStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    ARRIVED = "arrived"


class Priority(StrEnum):
    ROUTINE = "routine"
    SEMI = "semi"
    URGENT = "urgent"


class QueueEntryState(StrEnum):
    WAITING = "waiting"
    CALLED = "called"
    IN_CONSULT = "in_consult"
    DONE = "done"
    NO_SHOW = "no_show"
    LAB_REQUEUE = "lab_requeue"


class DictationStatus(StrEnum):
    DRAFT = "draft"
    SIGNED = "signed"


class CheckinPlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CheckinGrade(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class UsagePurpose(StrEnum):
    INTAKE_TURN = "intake_turn"
    SUMMARY = "summary"
    ROUTING = "routing"
    DICTATION = "dictation"
    CHECKIN = "checkin"
    OTHER = "other"


class PriceUnit(StrEnum):
    """How a vendor bills. Extends doc 02 §4's list with `char` — see below.

    `char` is not in doc 02 §4's enum, which predates pricing the actual vendors:
    both TTS options (Sarvam Bulbul, Google) bill per character, not per second
    of audio produced. Without it, TTS cost would be an estimate derived from
    output duration, and S18's AC ("dashboard numbers reconcile to usage_events
    exactly" + monthly invoice reconciliation) would be unmeetable by
    construction. Added in S3 and flagged in HANDOFF for ratification.
    """

    TOKEN_IN = "token_in"
    TOKEN_OUT = "token_out"
    AUDIO_SEC = "audio_sec"
    CALL_MIN = "call_min"
    MSG = "msg"
    CHAR = "char"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SOFT_DELETE = "soft_delete"
    DELETE = "delete"


class OtpPurpose(StrEnum):
    LOGIN = "login"
