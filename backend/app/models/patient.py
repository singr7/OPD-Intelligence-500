"""Patients (doc 02 §4).

Free-text captured from patients is stored in the original language *and* in
English side by side (doc 02 §4 notes) — hence the `_en` companions rather than
translating in place and losing the source.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    Clinical,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKey,
    enum_type,
)
from app.models.enums import CaregiverLinkStatus, Lang, Sex


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
