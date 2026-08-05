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
from app.models.enums import (
    Channel,
    DictationStatus,
    DocumentKind,
    DocumentStatus,
    DoseStatus,
    IntakeTier,
    Lang,
    PatientLinkState,
    RxMode,
    VisitStatus,
)


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

    #: A prior patient this arrival may be, pending a coordinator's confirmation.
    #: Never merged automatically: a wrong merge in an oncology record is worse
    #: than a duplicate, and only the duplicate is repairable without argument.
    candidate_patient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patients.id"), index=True
    )
    patient_link_state: Mapped[PatientLinkState] = mapped_column(
        enum_type(PatientLinkState, "patient_link_state"),
        default=PatientLinkState.NONE,
        server_default=PatientLinkState.NONE.value,
        index=True,
    )

    #: How the consult ended (plan §5.3b). Null until a doctor concludes it, so
    #: "not concluded" and "concluded with nothing" stay distinguishable — they
    #: are different clinical situations and only one of them needs chasing.
    rx_mode: Mapped[RxMode | None] = mapped_column(enum_type(RxMode, "rx_mode"), index=True)
    conclusion_note: Mapped[str | None] = mapped_column(Text)
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))

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

    # Adaptive-intake telemetry (S-ADAPT.2, doc 11 §3): one record per interpret
    # event — [{node_id, outcome, enriched, at}]. Feeds the S18 tree-improvement
    # report (per-node clarify / mis-map / enrichment rates); the LLM-call turns
    # reconcile to this intake's INTAKE_TURN usage_events. Empty for a pure-tap
    # or non-adaptive intake, so existing rows and the offline floor are unaffected.
    adaptive_events: Mapped[list[Any]] = mapped_column(default=list)

    chief_complaint: Mapped[str | None] = mapped_column(Text)
    chief_complaint_en: Mapped[str | None] = mapped_column(Text)
    summary_md: Mapped[str | None] = mapped_column(Text)
    summary_lang_versions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    confirmed_by_patient: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Caregiver mode (doc 03 §1: "I am answering for the patient"). Promoted to a
    # real column in S16 — the app needs to record it on a *known* patient, where
    # S6's workaround (writing a marker into `Patient.caregiver_name`) would
    # overwrite the real caregiver's name on a real registration record.
    caregiver_answered: Mapped[bool] = mapped_column(Boolean, default=False)

    # `key@vN` of the tree that produced `answers` (S7). Without it a stored
    # answer set cannot be read back with certainty: node ids are stable across
    # versions by design, so the same JSONB means different questions depending
    # on which version was asked, and S18 can publish a new version while a kiosk
    # is offline with the old one cached.
    tree_ref: Mapped[str | None] = mapped_column(String(120))

    # The offline kiosk's own id for this intake (S7, doc 01 §5). It is the
    # idempotency key for sync: the network usually returns *during* a batch, so
    # a retry re-sends intakes that already landed, and without a unique key each
    # retry mints a second visit for one patient. Null for anything created
    # online — a server-side intake never needs one.
    client_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

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


class MedicalDocument(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    """One scanned paper document: a batch of page images (doc 21 §1.3).

    Hung off the **patient**, not the visit. A lab report brought to today's OPD
    is part of this patient's record for good; `visit_id` records which arrival
    it came in with, and is nullable so a record can be scanned outside a visit
    without inventing one.

    The page bytes are not here — `object_keys` points into the object store, in
    page order. Postgres keeps the index; the disk keeps the JPEGs.
    """

    __tablename__ = "medical_documents"

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visits.id"), index=True)
    kind: Mapped[DocumentKind] = mapped_column(enum_type(DocumentKind, "document_kind"))
    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "document_status"),
        default=DocumentStatus.CAPTURING,
        index=True,
    )

    #: Object-store keys, page order significant. `pages` is len(object_keys),
    #: denormalised so a list view does not have to unpack the JSONB.
    object_keys: Mapped[list[Any]] = mapped_column(default=list)
    pages: Mapped[int] = mapped_column(Integer, default=0)

    #: Who stood there with the phone. A staff user, always — `/scan` is
    #: authenticated, unlike the kiosk.
    captured_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)

    #: The exact vision/summary providers and models this document was read by,
    #: snapshotted at extraction (the VOICE1 pattern). Config moves; a stored
    #: reading must stay attributable to what actually produced it.
    provider_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict)

    #: Extraction attempts so far, and why the last one failed. The counter is
    #: what stops a vendor outage from re-billing the same document all day;
    #: the reason is what the coordinator's retry screen shows.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    #: Set when a worker claims the document. Also the staleness clock: a worker
    #: killed mid-extraction leaves `extracting` behind, and the sweep reclaims
    #: it after `app.mrd.CLAIM_TIMEOUT` rather than leaving it stuck forever.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extraction: Mapped[DocumentExtraction | None] = relationship(
        back_populates="document", uselist=False
    )


class DocumentExtraction(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    """What the machine read off one document, and what we computed from it.

    Split from `MedicalDocument` because they have different lifetimes and
    different authors: the pages are what the coordinator captured and are
    immutable; this is what a model said about them, and it can be re-run,
    superseded by a better prompt version, or corrected by a doctor. Keeping
    them in one row would make "re-extract" a destructive edit of a capture.

    `payload` holds the doc 21 §1.4 shape: `tests[]` as read, each carrying the
    reference range *and where the range came from*, plus a computed `flag`.
    """

    __tablename__ = "document_extractions"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medical_documents.id"), index=True, unique=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    summary_text: Mapped[str | None] = mapped_column(Text)

    #: How many `tests[]` came out other than `normal`/`unknown`. Denormalised so
    #: the doctor's spine can badge "4 values flagged" without opening the JSONB
    #: for every patient on the worklist.
    outlier_count: Mapped[int] = mapped_column(Integer, default=0)

    #: `id@vN` of every prompt that contributed — extract and summarise. Same
    #: contract as `Dictation`: an output must be traceable to its exact prompt
    #: version months later.
    prompt_refs: Mapped[list[Any]] = mapped_column(default=list)

    #: A doctor has read this against the original pages. Until then every
    #: surface that shows it must say so (doc 21 §1.5) — an unverified machine
    #: reading of a lab report is a draft, and drafts are labelled.
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[MedicalDocument] = relationship(back_populates="extraction")


class DoseEvent(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    """What the patient did about one scheduled dose (doc 03 §1c.4, S16).

    Adherence, reported by the app: the phone owns the alarms (WorkManager +
    exact alarms) and tells the server what happened, because a phone that was
    offline all evening still knows its reminder went unanswered and the server
    never would.

    The row is keyed by `(prescription_id, med_index, scheduled_for)` rather than
    by a client-generated id, which is what makes reporting idempotent: a phone
    that syncs the same evening twice — or a caregiver's phone reporting the same
    missed dose the patient's phone already reported — updates one row instead of
    creating a second, and so cannot trigger a second caregiver ping.
    `med_index` points into `Prescription.meds`, the frozen snapshot; the drug is
    never re-identified by name here.
    """

    __tablename__ = "dose_events"
    __table_args__ = (
        UniqueConstraint(
            "prescription_id",
            "med_index",
            "scheduled_for",
            name="uq_dose_events_prescription_id_med_index_scheduled_for",
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    prescription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prescriptions.id"), index=True)
    med_index: Mapped[int] = mapped_column(Integer)
    #: The dose's own clock time, as the app scheduled it.
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[DoseStatus] = mapped_column(enum_type(DoseStatus, "dose_status"))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: When the caregiver was actually pinged about a miss. Null on a taken dose,
    #: and on a missed one whose patient has no active caregiver link.
    caregiver_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
