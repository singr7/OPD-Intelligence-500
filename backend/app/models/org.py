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

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKey, enum_type
from app.models.enums import Lang, Role


class Hospital(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "hospitals"

    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(32), unique=True)  # natural key for seeds
    city: Mapped[str | None] = mapped_column(String(120))
    district: Mapped[str | None] = mapped_column(String(120))
    default_lang: Mapped[Lang] = mapped_column(enum_type(Lang, "lang"), default=Lang.HI)

    departments: Mapped[list[Department]] = relationship(back_populates="hospital")


class Department(Base, UUIDPrimaryKey, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("hospital_id", "code", name="uq_departments_hospital_code"),)

    hospital_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(32))  # natural key for seeds
    icon: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

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
