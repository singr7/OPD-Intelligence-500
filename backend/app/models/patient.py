"""Patients (doc 02 §4).

Free-text captured from patients is stored in the original language *and* in
English side by side (doc 02 §4 notes) — hence the `_en` companions rather than
translating in place and losing the source.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    Clinical,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKey,
    enum_type,
)
from app.models.enums import (
    AllergyKind,
    AllergySeverity,
    AllergySource,
    CaregiverLinkStatus,
    Lang,
    Sex,
)


class Patient(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "patients"

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), index=True)
    mrn: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # natural key
    #: A health ID the patient already carries (the pilot site calls it a UHC ID).
    #: Deliberately *not* modelled as ABHA: a real ABHA integration brings ABDM
    #: registration, consent artefacts and linkage duties, which is a programme
    #: rather than a column. `external_id_kind` labels whatever the deployment
    #: actually issues. Optional everywhere — it never gates an intake or a token,
    #: and it is not unique, because a mistyped ID must not block a second patient.
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)
    external_id_kind: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    alt_phone: Mapped[str | None] = mapped_column(String(20))
    age: Mapped[int | None] = mapped_column(Integer)
    dob: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[Sex | None] = mapped_column(enum_type(Sex, "sex"))
    lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"), default=Lang.HI)
    village: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(120))

    caregiver_name: Mapped[str | None] = mapped_column(String(200))
    caregiver_phone: Mapped[str | None] = mapped_column(String(20))

    # Consent capture at registration (doc 02 §7). `consent_audio_url` is set by
    # the phone channel, which records a spoken consent line instead.
    consent_given_at: Mapped[date | None] = mapped_column(Date)
    consent_audio_url: Mapped[str | None] = mapped_column(String(500))


class PatientAllergy(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    """One statement somebody made about this patient's allergies.

    Session B left this as the largest hole in the doctor's context spine: nothing
    in the product captured an allergy, so the spine and the History tab both said
    so in words, and neither would say "no known allergies" — a clinical claim the
    record could not make and a doctor would act on. This table is what lets those
    surfaces stop apologising, and it is shaped by that same refusal.

    **It is a log of statements, not a list of allergies.** One row is one act:
    somebody was asked at a knowable moment and said something. The current
    picture is derived from the rows (`app.allergies.for_patient`), never stored,
    because the derivation is where the honesty lives — three states that must
    never collapse into each other:

    * no rows at all → **nobody has asked**. Still the commonest state, and the
      one the spine has been describing accurately for six sessions.
    * a `none_known` row and no live `substance` rows → **asked, and told there
      are none** — rendered with its source and date, never bare.
    * one or more live `substance` rows → **known allergies**, whatever else was
      said before. A later "none" never suppresses an earlier named substance;
      it is far likelier that the second asking was rushed than that a penicillin
      anaphylaxis stopped being true.

    Hung off the **patient**, like `MedicalDocument` and for the same reason: an
    allergy stated in March is true in August. `visit_id` records which arrival
    it was stated at and is nullable, so a doctor can record one outside a visit.

    **Nothing here is ever edited or deleted.** A correction is `retracted_at`
    plus a new row — the one place in this system that has a correction path
    (HANDOFF item 6), and deliberately the first, because a wrong allergy is the
    single most dangerous stale fact this record can carry. A hard edit would
    lose the fact that the record once told a prescribing doctor something false.
    """

    __tablename__ = "patient_allergies"

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    #: The arrival it was stated at. Null for a statement made outside any visit.
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visits.id"), index=True)

    kind: Mapped[AllergyKind] = mapped_column(enum_type(AllergyKind, "allergy_kind"), index=True)

    #: What they said, in the words they said it in — the doc 02 §4 convention,
    #: original beside English rather than translated in place. Both null on a
    #: `none_known` row, which names no substance by definition.
    substance: Mapped[str | None] = mapped_column(String(200))
    substance_en: Mapped[str | None] = mapped_column(String(200))

    #: What happened to them, if anyone asked. Free text and clinician-entered:
    #: "throat closed", "rash". No vocabulary, because a kiosk cannot offer one
    #: and a doctor typing between patients will not use one.
    reaction: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[AllergySeverity] = mapped_column(
        enum_type(AllergySeverity, "allergy_severity"),
        default=AllergySeverity.UNKNOWN,
        server_default=AllergySeverity.UNKNOWN.value,
    )

    source: Mapped[AllergySource] = mapped_column(enum_type(AllergySource, "allergy_source"))
    #: Set only when `source` is `doctor`. The kiosk is unauthenticated — a
    #: patient-stated allergy has no clinician's name on it, and pretending
    #: otherwise is what would make an unreviewed statement look reviewed.
    recorded_by_doctor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))

    #: A clinician has asked about this specific statement and stands behind it.
    #: Separate from `source=doctor`: a doctor confirming what the patient said
    #: at the kiosk is the commonest and most valuable act here, and it must not
    #: require re-typing the substance to record it.
    confirmed_by_doctor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Struck out by a clinician — wrong drug, wrong patient, mis-heard at the
    #: kiosk. The row stays and stops counting; `retracted_reason` is shown to
    #: anyone who opens the history, because "who un-said this, and why" is the
    #: question a review asks first.
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retracted_by_doctor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("doctors.id"))
    retracted_reason: Mapped[str | None] = mapped_column(Text)

    @property
    def is_live(self) -> bool:
        """Still part of the current picture: neither retracted nor soft-deleted."""
        return self.retracted_at is None and self.deleted_at is None


class CaregiverLink(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    """One family member's standing permission to see one patient's file (S16).

    `Patient.caregiver_phone` is a *contact* — who to call about this patient.
    This table is an *access grant*, which is a different thing and must not be
    inferred from the first: a number written on a registration form has not
    consented to read a cancer file, and the patient has not agreed to let it.
    The grant is therefore always the patient's own act (`consented_at`, set by
    `POST /patient/caregivers/{id}/approve` on the patient's own login), and
    revoking is a state change rather than a delete so the history survives.

    Deliberately not a role on `users`: a caregiver is not staff, has no login of
    her own beyond her phone, and must never be reachable by the RBAC guards that
    admit `Role.CAREGIVER` to clinical routes.
    """

    __tablename__ = "caregiver_links"
    __table_args__ = (
        # One live grant per (patient, phone). A second invitation for the same
        # number is the same grant being re-offered, not a new one.
        UniqueConstraint("patient_id", "phone", name="uq_caregiver_links_patient_id_phone"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), index=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    relation: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[CaregiverLinkStatus] = mapped_column(
        enum_type(CaregiverLinkStatus, "caregiver_link_status"),
        default=CaregiverLinkStatus.INVITED,
        index=True,
    )
    #: Set the moment the patient approves, cleared never — a revoked link keeps
    #: the date it was once granted.
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def grants_access(self) -> bool:
        return self.status is CaregiverLinkStatus.ACTIVE and self.deleted_at is None
