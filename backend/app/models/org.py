"""Organisation + identity: hospitals, departments, users, doctors.

`users` is the single auth principal for every human who logs in (doc 02 §7
RBAC). `doctors` is the clinical profile that hangs off a user — a doctor has a
registration number and a department; a coordinator does not. Keeping the login
identity in one table means one OTP flow, one JWT shape, and one place to
revoke access.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey, enum_type
from app.models.enums import CareSystem, Lang, Role


class Hospital(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "hospitals"

    #: The hospital's name in English, and the fallback for every language that
    #: has no entry in `name_i18n`. Never null — a facility with no name has no
    #: prescription letterhead and no intake pass.
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(32), unique=True)  # natural key for seeds
    city: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(120))
    default_lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"), default=Lang.HI)

    #: `{"hi": "…", "mr": "…", "te": "…"}` — the hospital's name in each pilot
    #: language it has one for. Read it through `name_in()`, never directly.
    #:
    #: A hospital's name is patient-facing in a way its city is not: it is the
    #: first line of the kiosk, the letterhead of the prescription, and the top
    #: band of the boarding pass a patient carries out of the building. A woman
    #: who chose हिंदी at the kiosk and is then shown a Latin-script name has
    #: been told, in the first second, that this screen is not really for her.
    #:
    #: JSONB rather than three columns for the reason the tree bank is JSON: the
    #: set of languages is content, and adding a fifth must not be a migration.
    #: Absent or empty means "this hospital has one name" — the honest state for
    #: a facility nobody has translated yet, and what every row predating this
    #: column genuinely is.
    name_i18n: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")

    departments: Mapped[list[Department]] = relationship(back_populates="hospital")

    def name_in(self, lang: Lang | str | None) -> str:
        """What to call this hospital to somebody reading in `lang`.

        The single derivation, and the reason there is no `hospital.name` left
        at a call site that knows a language. Falls back to `name` — which is
        always populated — so a language nobody has translated shows the real
        name rather than a blank letterhead or a key.
        """
        if lang is None:
            return self.name
        translated = (self.name_i18n or {}).get(str(lang))
        if isinstance(translated, str) and translated.strip():
            return translated.strip()
        return self.name


class Department(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("hospital_id", "code", name="uq_departments_hospital_code"),)

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(32))  # natural key for seeds
    icon: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    #: The system of medicine practised here (doc 24 §3.1). This is the **only**
    #: place the platform stores it: an intake tree, a doctor console section and
    #: a formulary scope are all derived from the department a visit is in, never
    #: recorded a second time on the visit, the note or the prescription. A
    #: department that changes its system therefore changes what it offers from
    #: the next intake onward, and nothing already written is retroactively
    #: reclassified.
    #:
    #: Read it through `app.care_system.capabilities_for`, not directly.
    care_system: Mapped[CareSystem] = mapped_column(
        enum_type(CareSystem, "care_system"),
        default=CareSystem.ALLOPATHY,
        server_default=CareSystem.ALLOPATHY.value,
    )

    hospital: Mapped[Hospital] = relationship(back_populates="departments")
    doctors: Mapped[list[Doctor]] = relationship(back_populates="department")


class User(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    """Auth principal. Phone is the login handle (OTP); TOTP/password are the
    optional staff path (doc 02 §2 "staff via username+TOTP option")."""

    __tablename__ = "users"

    hospital_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    role: Mapped[Role] = mapped_column(enum_type(Role, "role"), index=True)
    lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"), default=Lang.HI)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    username: Mapped[str | None] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Argon2 of a numeric PIN that unlocks the kiosk's staff strip. Deliberately
    #: *not* the staff login: a PIN is a handful of digits typed on a screen a
    #: queue can see, so it buys a narrow, short-lived kiosk-only token and never
    #: a full staff session (`app.auth.kiosk_pin`). Null means this user cannot
    #: unlock a kiosk, which is the correct default for everyone.
    kiosk_pin_hash: Mapped[str | None] = mapped_column(String(255))
    kiosk_pin_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Set when the attempt cap trips. A shoulder-surfed PIN is guessable in a few
    #: hundred tries; the lockout is what makes that expensive rather than free.
    kiosk_pin_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    doctor: Mapped[Doctor | None] = relationship(back_populates="user", uselist=False)

    @property
    def can_login(self) -> bool:
        return self.active and self.deleted_at is None


class Doctor(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "doctors"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    reg_no: Mapped[str] = mapped_column(String(64), unique=True)  # natural key for seeds
    qualification: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="doctor")
    department: Mapped[Department] = relationship(back_populates="doctors")
