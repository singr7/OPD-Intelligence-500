"""Patients (doc 02 §4).

Free-text captured from patients is stored in the original language *and* in
English side by side (doc 02 §4 notes) — hence the `_en` companions rather than
translating in place and losing the source.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    Clinical,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKey,
    enum_type,
)
from app.models.enums import Lang, Sex


class Patient(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin, Clinical):
    __tablename__ = "patients"

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), index=True)
    mrn: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # natural key
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
