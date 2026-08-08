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


class NoteStatus(StrEnum):
    """Where one ambient consult note is (plan §3.1).

    Two states, on purpose. `draft` is a capture the doctor has not read back;
    `confirmed` is one they have. Nothing here is `signed`, and the word is
    avoided deliberately: signing is the prescription boundary in
    `app.dictation`, and a note that looked like it had crossed a signature
    would be the first step toward a second prescription writer.
    """

    DRAFT = "draft"
    CONFIRMED = "confirmed"


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


class DocumentKind(StrEnum):
    """What the coordinator said they were photographing (doc 21 §1.2).

    A short list, chosen at capture with one tap, because a coordinator standing
    beside a patient will not fill in a taxonomy. It steers the extraction prompt
    and nothing clinical: a mislabelled document still stores, still extracts,
    and still shows the doctor its original pages.
    """

    LAB = "lab"
    HISTOPATH = "histopath"
    IMAGING_REPORT = "imaging_report"
    DISCHARGE = "discharge"
    OUTSIDE_RX = "outside_rx"
    OTHER = "other"


class DocumentStatus(StrEnum):
    """Where one scanned document is in the pipeline (doc 21 §1.1).

    Every one of these is a state the doctor's Reports tab can render honestly.
    There is deliberately no state meaning "we tried and will say nothing about
    it": a document whose machine reading failed is `extraction_failed`, and its
    original pages are still one tap away — the pipeline degrades to a photo
    viewer, never to a blank.
    """

    #: Pages are being uploaded. Not yet a whole document.
    CAPTURING = "capturing"
    #: All pages in, waiting for the extractor to claim it.
    CAPTURED = "captured"
    #: Claimed by one worker. The claim is what stops two workers paying twice.
    EXTRACTING = "extracting"
    #: Values are out and flagged; the written summary may still be coming.
    EXTRACTED = "extracted"
    #: Values, flags and summary are all present. The terminal happy state.
    SUMMARIZED = "summarized"
    #: The model was unreachable, refused, or answered unusably, `attempts` times.
    #: Retryable by a human; the pages are unaffected.
    EXTRACTION_FAILED = "extraction_failed"


class ValueFlag(StrEnum):
    """Where one measured value sits against its reference range.

    Computed in Python from the range on the report (`app.mrd.flags`), never
    asked of the model — the extraction contract has no field for it. A model
    may read a number off a page; deciding that the number is alarming is a
    clinical judgement, and the invariant in CODEBASE_MEMORY is that those are
    deterministic.
    """

    NORMAL = "normal"
    LOW = "low"
    HIGH = "high"
    #: Past a critical threshold in the curated table. Still not an urgency
    #: decision: it changes the order values are shown in, and nothing else.
    CRITICAL_LOW = "critical_low"
    CRITICAL_HIGH = "critical_high"
    #: No usable range for this test — the value is shown plainly, unjudged.
    UNKNOWN = "unknown"


class CaregiverLinkStatus(StrEnum):
    """A family member's access to one patient's file (doc 03 §1c.6, S16).

    `invited` is the caregiver asking; `active` is the patient having said yes.
    Nothing is readable while a link is merely invited — consent is a state, not
    a checkbox on the invitation.
    """

    INVITED = "invited"
    ACTIVE = "active"
    REVOKED = "revoked"


class AllergyKind(StrEnum):
    """What one allergy statement actually says (plan §4.2, SESSION-ALLERGY).

    Two kinds, because **"the patient named penicillin" and "the patient said
    there are none" are the same kind of act** — somebody was asked, at a
    knowable time, and answered. Modelling only the first would leave "asked and
    told there are none" indistinguishable from "never asked", which is the
    exact confusion the console has been refusing to make in words ever since
    Session B.

    So `none_known` is a **row**, not the absence of rows. Absence of rows means
    nobody has asked, and every surface must keep saying so.
    """

    SUBSTANCE = "substance"
    NONE_KNOWN = "none_known"


class AllergySeverity(StrEnum):
    """How bad the reaction was, when anyone knows.

    `unknown` is the default and is *not* a synonym for mild: a patient who says
    "penicillin" at a kiosk has told us the substance and nothing about the
    reaction, and a console that renders that as mild has invented the reassuring
    half. Only a clinician who asked can set anything else.
    """

    UNKNOWN = "unknown"
    MILD = "mild"
    SEVERE = "severe"


class AllergySource(StrEnum):
    """Who said it. Never inferred — it is stamped by the route that wrote it.

    This is the difference between a fact the record can stand behind and a
    thing a frightened patient said through a tablet at 9am, and both surfaces
    that render an allergy state it. A doctor's statement outranks a kiosk one
    for display order; it never deletes it.
    """

    PATIENT_KIOSK = "patient_kiosk"
    #: The kiosk, in caregiver mode — a family member answering for the patient.
    #: Worth its own value rather than folding into the above: "her son said she
    #: is allergic to sulfa" is weaker evidence than the patient saying it, and
    #: the doctor should be able to see which one they have.
    CAREGIVER_KIOSK = "caregiver_kiosk"
    DOCTOR = "doctor"


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
    #: Reading a scanned medical record (SESSION-MRD1) — both the vision call and
    #: the summary written from its output. Its own purpose rather than `summary`
    #: because a document is priced per page of image and an intake summary is
    #: not, so averaging them together makes the S18 cost-per-intake number a
    #: fiction. The column is a plain varchar(11) with no check constraint, so
    #: this needed no migration; a value longer than that would.
    DOCUMENT = "document"
    #: An ambient consult note (SESSION-M4) — its transcription and its mapping.
    #: Its own purpose rather than `dictation` for the reason `document` is its
    #: own: `analytics._per_dictation` divides DICTATION spend by the number of
    #: *signed dictations*, and notes produce no dictation to divide by. Sharing
    #: the purpose would inflate cost-per-prescription by however many
    #: observations a doctor happened to mutter that day, which is a number
    #: nobody could reconcile against an invoice.
    NOTE = "note"
    #: A research-assistant turn (SESSION-M5). Its own purpose for the reason
    #: `note` and `document` are: this is the only prose-output pathway in the
    #: system and the only one whose cost scales with how curious a doctor is
    #: rather than with how many patients came through the door. Folded into
    #: `summary` it would make cost-per-intake move when nobody's intake
    #: changed. Eight characters, so the varchar(11) column still needs no
    #: migration — `research_assist` would have needed one.
    RESEARCH = "research"
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
    #: Somebody looked at clinical data that never passes through this database
    #: (SESSION-M3). The four above are all *writes*, because the audit hook
    #: keys off a flush — but a doctor opening a patient's imaging from the PACS
    #: leaves no row to flush, and "who viewed this patient's scans" is exactly
    #: the question an access review asks. It is a distinct action rather than a
    #: `create` of an access record, because `create` is counted by existing
    #: audit queries and a read is not a change to anything.
    #:
    #: Deliberately **not** used for ordinary reads. Every list and card in this
    #: system reads patient data and logging all of it would drown the log that
    #: matters. This is for clinical content fetched from outside the box.
    #:
    #: No migration: `enum_type` builds a VARCHAR with `create_constraint`
    #: defaulted off (SQLAlchemy 1.4+), so `audit_log.action` is a plain
    #: varchar(11) with no CHECK to widen — "read" is four characters and
    #: "soft_delete" already set the ceiling. Verified against the live column
    #: rather than assumed; a value longer than eleven would need one.
    READ = "read"


class OtpPurpose(StrEnum):
    LOGIN = "login"
