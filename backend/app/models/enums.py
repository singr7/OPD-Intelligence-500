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
    #: S17. Outbound only — an intake never happens over SMS, but it is the last
    #: rung of the check-in delivery ladder (doc 03 §9) and the one that reaches a
    #: feature phone. Added rather than invented locally so `Checkin.channel`
    #: says what actually carried the message.
    SMS = "sms"


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


#: Authored content — trees, protocol banks — shares one draft→published
#: lifecycle. The Postgres type is named `tree_status` for the first table that
#: needed it; an identical second type would be two things to migrate in step
#: for no gain. Use this alias where the content is not a tree.
ContentStatus = TreeStatus


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    ARRIVED = "arrived"


class SlotType(StrEnum):
    """Oncology slot types (doc 03 §2). A slot is typed because the clinics are:
    a new consult takes a different length and a different room from a chemo
    review, and a caller asking for a follow-up must not be offered a new-patient
    slot the doctor holds for referrals."""

    NEW_CONSULT = "new_consult"
    FOLLOW_UP = "follow_up"
    CHEMO_REVIEW = "chemo_review"


class OutboundCallState(StrEnum):
    """One patient's place in the D-1 outbound campaign's retry ladder (S15).

    `failed` means the ladder is exhausted (2 attempts, doc 03 §1b), not that a
    single dial failed — a dial that fails leaves the row `pending` with a later
    `next_attempt_at`.
    """

    PENDING = "pending"
    DIALING = "dialing"
    COMPLETED = "completed"
    FAILED = "failed"
    FALLBACK_SENT = "fallback_sent"
    CANCELLED = "cancelled"


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


class PatientLinkState(StrEnum):
    """Whether this visit's patient row is known to be the right one.

    A kiosk arrival always creates its own patient row so intake can begin
    without waiting for anyone. When the arrival screen recognises a phone number
    or UHC ID, the match is recorded as a `CANDIDATE` and **nothing is disclosed
    on the kiosk** — a public terminal that prints a named oncology history to
    whoever types ten digits is a disclosure incident with a queue attached. A
    coordinator confirms or rejects it on the staff strip.
    """

    #: No prior record was offered or found. The ordinary new-registration path.
    NONE = "none"
    #: A prior patient was matched but no human has agreed it is the same person.
    CANDIDATE = "candidate"
    #: A coordinator confirmed the match; the visit now points at the prior record.
    CONFIRMED = "confirmed"
    #: A coordinator looked and said it is a different person. Never re-offered.
    REJECTED = "rejected"


class DictationStatus(StrEnum):
    DRAFT = "draft"
    SIGNED = "signed"


class RxMode(StrEnum):
    """How a consult ended, prescribing-wise (plan §5.3b).

    Recorded because the alternative is a blank visit. A doctor who writes on a
    paper pad has still concluded the consult, and a record that shows nothing
    at all cannot be told apart from one where the doctor was interrupted — the
    two need very different follow-ups. This is a clinical record in its own
    right: written, audited, and never inferred from the absence of a note.
    """

    #: A signed consult note in this system. The prescription, if there is one,
    #: hangs off the signature.
    SYSTEM = "system"
    #: A paper script, or one written in another system. Nothing digital exists
    #: for the patient's app, the pharmacy or the follow-up reminders.
    EXTERNAL_MANUAL = "external_manual"
    #: No prescription was given at all — advice, reassurance, a follow-up date.
    NONE = "none"


class CaregiverLinkStatus(StrEnum):
    """A family member's access to one patient's file (doc 03 §1c.6, S16).

    `invited` is the caregiver asking; `active` is the patient having said yes.
    Nothing is readable while a link is merely invited — consent is a state, not
    a checkbox on the invitation.
    """

    INVITED = "invited"
    ACTIVE = "active"
    REVOKED = "revoked"


class DoseStatus(StrEnum):
    """What happened to one scheduled dose of one medicine (doc 03 §1c.4, S16).

    `missed` is what the caregiver gets pinged about. It is reported by the app
    (the phone knows the reminder fired and was never answered); the server
    records it and does the pinging.
    """

    TAKEN = "taken"
    MISSED = "missed"
    SNOOZED = "snoozed"


class CheckinPlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CheckinState(StrEnum):
    """Where one check-in is in its life (S17).

    Deliberately separate from `grade`: a check-in can be `expired` (the ladder
    ran out of rungs and nobody answered) with no grade at all, and "we could not
    reach her" is a different clinical fact from "she said she is fine".
    """

    PENDING = "pending"
    SENT = "sent"
    ANSWERED = "answered"
    #: Every rung of the delivery ladder failed, or the window to answer closed.
    EXPIRED = "expired"
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
    """How a vendor bills — their unit, not our measurement (doc 02 §4).

    `char` was added in S3 and ratified into doc 02 §4: both TTS options (Sarvam
    Bulbul, Google) bill per character, not per second of audio produced. Without
    it, TTS cost would be an estimate derived from output duration, and S18's AC
    ("dashboard numbers reconcile to usage_events exactly" + monthly invoice
    reconciliation) would be unmeetable by construction.

    Quanta live in `app.providers.pricing.UNIT_QUANTUM`, not here: token_in,
    token_out and char are priced per 1,000; the rest per single unit.
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
