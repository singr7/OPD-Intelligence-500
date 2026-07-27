"""Staff onboarding — users, doctors, and letting somebody in (doc 03 §10, S-GL.2).

> "Departments, doctors, rooms, slot templates; …" — doc 03 §10

Until this module, onboarding a doctor meant editing `seeds/doctors.json` on the
box and re-running `make seed`. That is not something a hospital administrator
can do, and it made "we hired an oncologist" a deploy.

## Three shapes worth knowing

**A doctor is two rows.** `User` is the login identity (one OTP flow, one JWT
shape, one place to revoke); `Doctor` is the clinical profile that hangs off it —
registration number, department, qualification. `create_doctor` writes both in one
transaction, because a `Doctor` with no `User` cannot log in and a doctor-role
`User` with no profile breaks every screen that joins on it.

**An invite is not a credential.** The OTP login already exists, so "invite this
person" means exactly *"this phone number can now sign in"* — which is what
creating an active `User` row already accomplished. `send_invite` therefore sends
an SMS telling them so and records that it went; it mints nothing, and there is no
token to leak, expire or reset. Re-sending is free and idempotent by nature.

**Deactivation is two steps on purpose.** A doctor with patients booked into next
Tuesday's clinic is the normal case, not the exception, and a one-click
deactivation would quietly leave those patients holding an appointment with
somebody who has left. `deactivation_impact` is the first step and lists them by
name; `deactivate` refuses without an explicit acknowledgement. What it then does
is deliberately asymmetric:

- future slots **with nobody in them** are `blocked` — nobody new can book,
- future slots **with bookings** are left alone and reported back — the
  appointments stay findable, and a human rings those patients.

Blocking rather than deleting is the same choice `app.scheduling` makes
everywhere: an unwanted clinic keeps the bookings that happened.

## Not versioned

Trees, the protocol bank and the channel document are all draft→publish→resolve,
and people deliberately are not. A doctor is not authored content with a review
cycle; forcing hiring somebody through a two-step publish would add a way to get
it half-done and buy no safety. The audit trail (`record_admin_action`) is what
makes these edits accountable instead.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_admin_action
from app.config import Settings
from app.models.enums import AuditAction, Lang, Role, UsagePurpose
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import Patient
from app.models.scheduling import Appointment, AppointmentSlot, SlotTemplate
from app.providers.base import ProviderError
from app.providers.registry import get_sms_provider
from app.providers.sms import SmsMessage
from app.scheduling import LIVE_STATUSES

logger = logging.getLogger(__name__)

#: Roles an admin may create from the console. `patient` and `caregiver` are not
#: staff — they are created by registration and by a consented grant (S16), and
#: offering them here would let a console mint a patient identity with no MRN.
STAFF_ROLES: tuple[Role, ...] = (Role.COORDINATOR, Role.NURSE, Role.DOCTOR, Role.ADMIN)

_DIGITS = re.compile(r"[^\d+]")


class PeopleError(Exception):
    """A refusal an admin can act on. The message is shown verbatim."""


def normalise_phone(raw: str) -> str:
    """`+91` E.164, from what an administrator actually types.

    A desk types "98765 43210" or "098765-43210"; the login flow looks up
    `users.phone` by exact string match, so a number stored in any other shape is
    an account that silently cannot log in. Ten digits are assumed Indian —
    the pilot is one hospital in Alwar — and anything already carrying a `+` is
    left in whatever country it names.
    """
    cleaned = _DIGITS.sub("", (raw or "").strip())
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit() or not 8 <= len(digits) <= 15:
            raise PeopleError(f"{raw!r} is not a phone number")
        return "+" + digits
    if len(cleaned) == 10 and cleaned.isdigit():
        return "+91" + cleaned
    if len(cleaned) == 12 and cleaned.startswith("91"):
        return "+" + cleaned
    if len(cleaned) == 11 and cleaned.startswith("0"):
        return "+91" + cleaned[1:]
    raise PeopleError(
        f"{raw!r} is not a phone number — enter ten digits, "
        "or the full number with its country code"
    )


# -- reading -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Person:
    """One member of staff as the console lists them."""

    user_id: uuid.UUID
    name: str
    phone: str
    role: Role
    lang: Lang
    active: bool
    last_login_at: datetime | None
    #: Doctor profile, when there is one.
    doctor_id: uuid.UUID | None
    reg_no: str | None
    qualification: str | None
    department_code: str | None
    department_name: str | None
    #: Clinic templates and live future bookings — what makes deactivation a
    #: decision rather than a toggle, shown before anybody clicks it.
    clinics: int
    upcoming_appointments: int


async def list_people(session: AsyncSession) -> list[Person]:
    """Every staff account, doctors carrying their profile and their workload."""
    rows = (
        await session.execute(
            select(User, Doctor, Department)
            .outerjoin(Doctor, (Doctor.user_id == User.id) & Doctor.deleted_at.is_(None))
            .outerjoin(Department, Department.id == Doctor.department_id)
            .where(User.deleted_at.is_(None), User.role != Role.PATIENT)
            .order_by(User.role, User.name)
        )
    ).all()

    doctor_ids = [doctor.id for _, doctor, _ in rows if doctor is not None]
    clinics = await _counts(
        session,
        select(SlotTemplate.doctor_id, func.count())
        .where(
            SlotTemplate.doctor_id.in_(doctor_ids),
            SlotTemplate.active.is_(True),
            SlotTemplate.deleted_at.is_(None),
        )
        .group_by(SlotTemplate.doctor_id),
    )
    booked = await _counts(
        session,
        select(Appointment.doctor_id, func.count())
        .where(
            Appointment.doctor_id.in_(doctor_ids),
            Appointment.deleted_at.is_(None),
            Appointment.status.in_(LIVE_STATUSES),
            Appointment.slot_at >= datetime.now(UTC),
        )
        .group_by(Appointment.doctor_id),
    )

    return [
        Person(
            user_id=user.id,
            name=user.name,
            phone=user.phone,
            role=user.role,
            lang=user.lang,
            active=user.active,
            last_login_at=user.last_login_at,
            doctor_id=doctor.id if doctor else None,
            reg_no=doctor.reg_no if doctor else None,
            qualification=doctor.qualification if doctor else None,
            department_code=department.code if department else None,
            department_name=department.name if department else None,
            clinics=clinics.get(doctor.id, 0) if doctor else 0,
            upcoming_appointments=booked.get(doctor.id, 0) if doctor else 0,
        )
        for user, doctor, department in rows
    ]


async def _counts(session: AsyncSession, stmt) -> dict[uuid.UUID, int]:
    return {key: count for key, count in (await session.execute(stmt)).all()}


# -- creating ------------------------------------------------------------------


async def _hospital(session: AsyncSession) -> Hospital:
    hospital = (
        await session.execute(select(Hospital).where(Hospital.deleted_at.is_(None)).limit(1))
    ).scalar_one_or_none()
    if hospital is None:
        raise PeopleError("no hospital is configured on this box")
    return hospital


async def _user_by_phone(session: AsyncSession, phone: str) -> User | None:
    return (await session.execute(select(User).where(User.phone == phone))).scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    name: str,
    phone: str,
    role: Role,
    lang: Lang = Lang.HI,
) -> User:
    """A staff login. Active immediately — the OTP flow is the whole of the invite."""
    if role not in STAFF_ROLES:
        raise PeopleError(f"{role} is not a staff role this console creates")
    if not (name or "").strip():
        raise PeopleError("a name is required")
    phone = normalise_phone(phone)

    existing = await _user_by_phone(session, phone)
    if existing is not None:
        # Deliberately explicit rather than "phone already exists": the number an
        # admin is typing usually belongs to somebody they can now go and find.
        raise PeopleError(f"{phone} already signs in as {existing.name} ({existing.role})")

    user = User(
        hospital_id=(await _hospital(session)).id,
        name=name.strip(),
        phone=phone,
        role=role,
        lang=lang,
        active=True,
    )
    session.add(user)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=User.__tablename__,
        entity_id=user.id,
        meta={"role": str(role), "created_from": "console"},
    )
    logger.info("created %s user %s", role, user.id)
    return user


async def create_doctor(
    session: AsyncSession,
    *,
    name: str,
    phone: str,
    department_code: str,
    reg_no: str,
    qualification: str | None = None,
    lang: Lang = Lang.HI,
) -> Doctor:
    """A doctor: the login identity and the clinical profile, in one transaction.

    An existing doctor-role user with the same phone is *adopted* rather than
    refused — a coordinator who created the login first and the profile second is
    doing the same thing in two steps, and making them delete a row to continue
    would be a worse answer than joining them up.
    """
    phone = normalise_phone(phone)
    reg_no = (reg_no or "").strip()
    if not reg_no:
        raise PeopleError("a registration number is required — it is the doctor's natural key")

    department = (
        await session.execute(
            select(Department).where(
                Department.code == department_code, Department.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if department is None:
        raise PeopleError(f"no department with code {department_code!r}")

    clash = (
        await session.execute(select(Doctor).where(Doctor.reg_no == reg_no))
    ).scalar_one_or_none()
    if clash is not None:
        raise PeopleError(f"registration number {reg_no} already belongs to {clash.name}")

    user = await _user_by_phone(session, phone)
    if user is None:
        user = await create_user(session, name=name, phone=phone, role=Role.DOCTOR, lang=lang)
    else:
        if user.role is not Role.DOCTOR:
            raise PeopleError(f"{phone} already signs in as {user.name} ({user.role})")
        profile = (
            await session.execute(select(Doctor).where(Doctor.user_id == user.id))
        ).scalar_one_or_none()
        if profile is not None:
            raise PeopleError(f"{user.name} is already a doctor in this hospital")
        user.active = True

    doctor = Doctor(
        user_id=user.id,
        department_id=department.id,
        name=name.strip(),
        phone=phone,
        reg_no=reg_no,
        qualification=(qualification or None),
        active=True,
    )
    session.add(doctor)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=Doctor.__tablename__,
        entity_id=doctor.id,
        meta={"reg_no": reg_no, "department": department.code, "created_from": "console"},
    )
    logger.info("created doctor %s in %s", doctor.id, department.code)
    return doctor


# -- the invite ----------------------------------------------------------------

#: The invite text, in the four pilot languages. Short on purpose: it is an SMS,
#: it carries no code and no link, and the whole content is "your number works
#: now". Owed the same native review as every other patient-facing string (S21) —
#: staff-facing, but the same reviewer.
INVITE_SMS: dict[Lang, str] = {
    Lang.EN: (
        "{name}, your OPD account at {hospital} is active. "
        "Sign in with this phone number — you will get a one-time code."
    ),
    Lang.HI: (
        "{name}, {hospital} में आपका OPD खाता चालू है। "
        "इसी फ़ोन नंबर से साइन इन करें — आपको एक बार का कोड मिलेगा।"
    ),
    Lang.MR: (
        "{name}, {hospital} मध्ये तुमचे OPD खाते सुरू आहे. "
        "याच फोन नंबरने साइन इन करा — तुम्हाला एकदाच वापरायचा कोड मिळेल."
    ),
    Lang.TE: (
        "{name}, {hospital}లో మీ OPD ఖాతా పనిచేస్తోంది. ఇదే ఫోన్ నంబర్‌తో సైన్ ఇన్ చేయండి — మీకు ఒకసారి వాడే కోడ్ వస్తుంది."
    ),
}


@dataclass(frozen=True, slots=True)
class InviteResult:
    sent: bool
    to: str
    detail: str


async def send_invite(
    session: AsyncSession, *, user_id: uuid.UUID, settings: Settings
) -> InviteResult:
    """Tell somebody their number now signs in. Mints nothing.

    A failed send is reported, not raised: the account works either way, and an
    admin who can see "MSG91 said the number is on DND" can pick up a phone. The
    one thing this refuses is inviting a deactivated account, which would be a
    text promising a login that the OTP flow will decline.
    """
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise PeopleError("no such person")
    if not user.can_login:
        raise PeopleError(f"{user.name} is deactivated — reactivate before inviting")

    hospital = await _hospital(session)
    body = INVITE_SMS[user.lang].format(name=user.name, hospital=hospital.name)

    detail = "sent"
    sent = True
    try:
        await get_sms_provider(settings).send(
            SmsMessage(to=user.phone, body=body, template_key="staff_invite"),
            purpose=UsagePurpose.OTHER,
        )
    except ProviderError as exc:
        sent, detail = False, str(exc)
        logger.warning("staff invite to %s failed: %s", user.id, exc)

    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=User.__tablename__,
        entity_id=user.id,
        meta={"invite": "sms", "sent": sent},
    )
    return InviteResult(sent=sent, to=user.phone, detail=detail)


# -- deactivation --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BookedAppointment:
    appointment_id: uuid.UUID
    patient_name: str
    patient_phone: str
    at: datetime
    slot_type: str | None


@dataclass(frozen=True, slots=True)
class DeactivationImpact:
    """What deactivating this person would leave behind.

    Everything here is a *future* fact. Past appointments and signed notes are
    history and are never touched — a doctor who leaves still signed what she
    signed.
    """

    user_id: uuid.UUID
    name: str
    role: Role
    is_doctor: bool
    active_clinics: int
    open_future_slots: int
    booked: list[BookedAppointment] = field(default_factory=list)

    @property
    def needs_a_decision(self) -> bool:
        return bool(self.booked)


async def deactivation_impact(session: AsyncSession, *, user_id: uuid.UUID) -> DeactivationImpact:
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise PeopleError("no such person")

    doctor = (
        await session.execute(
            select(Doctor).where(Doctor.user_id == user.id, Doctor.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if doctor is None:
        return DeactivationImpact(
            user_id=user.id,
            name=user.name,
            role=user.role,
            is_doctor=False,
            active_clinics=0,
            open_future_slots=0,
        )

    now = datetime.now(UTC)
    clinics = await session.scalar(
        select(func.count())
        .select_from(SlotTemplate)
        .where(
            SlotTemplate.doctor_id == doctor.id,
            SlotTemplate.active.is_(True),
            SlotTemplate.deleted_at.is_(None),
        )
    )
    open_slots = await session.scalar(
        select(func.count())
        .select_from(AppointmentSlot)
        .where(
            AppointmentSlot.doctor_id == doctor.id,
            AppointmentSlot.deleted_at.is_(None),
            AppointmentSlot.blocked.is_(False),
            AppointmentSlot.booked == 0,
            AppointmentSlot.starts_at >= now,
        )
    )

    rows = (
        await session.execute(
            select(Appointment, Patient)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                Appointment.doctor_id == doctor.id,
                Appointment.deleted_at.is_(None),
                Appointment.status.in_(LIVE_STATUSES),
                Appointment.slot_at >= now,
            )
            .order_by(Appointment.slot_at)
        )
    ).all()

    return DeactivationImpact(
        user_id=user.id,
        name=user.name,
        role=user.role,
        is_doctor=True,
        active_clinics=int(clinics or 0),
        open_future_slots=int(open_slots or 0),
        booked=[
            BookedAppointment(
                appointment_id=appointment.id,
                patient_name=patient.name,
                patient_phone=patient.phone,
                at=appointment.slot_at,
                slot_type=str(appointment.slot_type) if appointment.slot_type else None,
            )
            for appointment, patient in rows
        ],
    )


@dataclass(frozen=True, slots=True)
class DeactivationResult:
    user_id: uuid.UUID
    name: str
    clinics_retired: int
    slots_blocked: int
    #: Left standing, and now somebody's phone call.
    appointments_left: list[BookedAppointment]


async def deactivate(
    session: AsyncSession, *, user_id: uuid.UUID, acknowledge: bool = False
) -> DeactivationResult:
    """Stop somebody signing in, and close their future clinic — carefully.

    Refuses while future appointments are booked unless the caller acknowledges
    them, which is the whole point of the two-step: the console shows the admin
    the list of patients first, and the acknowledgement is them saying "yes, we
    will ring those five".
    """
    impact = await deactivation_impact(session, user_id=user_id)
    if impact.needs_a_decision and not acknowledge:
        raise PeopleError(
            f"{impact.name} has {len(impact.booked)} patient(s) booked in the future. "
            "Review them and confirm — deactivating does not cancel or move an appointment."
        )

    user = await session.get(User, user_id)
    assert user is not None  # deactivation_impact already refused a missing one
    user.active = False

    clinics_retired = slots_blocked = 0
    doctor = (
        await session.execute(
            select(Doctor).where(Doctor.user_id == user.id, Doctor.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if doctor is not None:
        doctor.active = False
        retired = await session.execute(
            update(SlotTemplate)
            .where(
                SlotTemplate.doctor_id == doctor.id,
                SlotTemplate.active.is_(True),
                SlotTemplate.deleted_at.is_(None),
            )
            .values(active=False)
        )
        clinics_retired = retired.rowcount or 0
        # Empty future slots are blocked so nobody books a doctor who has left;
        # slots with somebody in them are left exactly as they are, because the
        # appointment is a promise made to a patient and only a human unmakes it.
        blocked = await session.execute(
            update(AppointmentSlot)
            .where(
                AppointmentSlot.doctor_id == doctor.id,
                AppointmentSlot.deleted_at.is_(None),
                AppointmentSlot.blocked.is_(False),
                AppointmentSlot.booked == 0,
                AppointmentSlot.starts_at >= datetime.now(UTC),
            )
            .values(blocked=True)
        )
        slots_blocked = blocked.rowcount or 0

    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=User.__tablename__,
        entity_id=user.id,
        meta={
            "active": False,
            "clinics_retired": clinics_retired,
            "slots_blocked": slots_blocked,
            "appointments_left": len(impact.booked),
        },
    )
    logger.info(
        "deactivated %s: %d clinics retired, %d slots blocked, %d appointments left standing",
        user.id,
        clinics_retired,
        slots_blocked,
        len(impact.booked),
    )
    return DeactivationResult(
        user_id=user.id,
        name=user.name,
        clinics_retired=clinics_retired,
        slots_blocked=slots_blocked,
        appointments_left=impact.booked,
    )


async def activate(session: AsyncSession, *, user_id: uuid.UUID) -> Person:
    """Let somebody back in. Their clinic does **not** come back with them.

    Deactivation retired the templates and blocked the empty slots; reactivating
    restores the login and the profile only. Re-opening a clinic is an explicit
    act on the roster, because "she is back" and "she is back on Tuesdays at ten"
    are different facts and only one of them is knowable from this button.
    """
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise PeopleError("no such person")
    user.active = True
    doctor = (
        await session.execute(
            select(Doctor).where(Doctor.user_id == user.id, Doctor.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if doctor is not None:
        doctor.active = True
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=User.__tablename__,
        entity_id=user.id,
        meta={"active": True},
    )
    person = next((p for p in await list_people(session) if p.user_id == user.id), None)
    assert person is not None
    return person
