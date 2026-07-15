"""Object builders for tests.

Every builder takes overrides, so a test states only the field it cares about
and the rest is plausible. Values are unique per call where the schema demands
uniqueness, so tests can't collide on a natural key.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import Dictation, Intake, Visit
from app.models.enums import Channel, IntakeTier, Lang, Role, Sex, VisitStatus
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import Patient

_counter = itertools.count(1)


def _n() -> int:
    return next(_counter)


def make_hospital(**overrides: Any) -> Hospital:
    n = _n()
    return Hospital(
        **{
            "code": f"H{n:04d}",
            "name": f"Test Hospital {n}",
            "city": "Alwar",
            "district": "Alwar",
            "default_lang": Lang.HI,
            **overrides,
        }
    )


def make_department(hospital: Hospital, **overrides: Any) -> Department:
    n = _n()
    return Department(
        **{
            "hospital_id": hospital.id,
            "code": f"D{n:04d}",
            "name": f"Department {n}",
            "icon": "stethoscope",
            **overrides,
        }
    )


def make_user(hospital: Hospital | None = None, **overrides: Any) -> User:
    n = _n()
    return User(
        **{
            "hospital_id": hospital.id if hospital else None,
            "name": f"User {n}",
            "phone": f"+9155500{n:05d}",
            "role": Role.COORDINATOR,
            "lang": Lang.HI,
            **overrides,
        }
    )


def make_doctor(user: User, department: Department, **overrides: Any) -> Doctor:
    n = _n()
    return Doctor(
        **{
            "user_id": user.id,
            "department_id": department.id,
            "name": user.name,
            "phone": user.phone,
            "reg_no": f"REG-{n:05d}",
            "qualification": "MD, DM (Medical Oncology)",
            **overrides,
        }
    )


def make_patient(hospital: Hospital, **overrides: Any) -> Patient:
    n = _n()
    return Patient(
        **{
            "hospital_id": hospital.id,
            "mrn": f"MRN{n:06d}",
            "name": f"Patient {n}",
            "phone": f"+9155519{n:05d}",
            "age": 52,
            "sex": Sex.FEMALE,
            "lang": Lang.HI,
            "village": "Ramgarh",
            "district": "Alwar",
            **overrides,
        }
    )


def make_visit(patient: Patient, department: Department, **overrides: Any) -> Visit:
    return Visit(
        **{
            "patient_id": patient.id,
            "department_id": department.id,
            "date": date(2026, 7, 15),
            "token_no": _n(),
            "status": VisitStatus.REGISTERED,
            "channel": Channel.KIOSK,
            **overrides,
        }
    )


def make_intake(visit: Visit, **overrides: Any) -> Intake:
    return Intake(
        **{
            "visit_id": visit.id,
            "tier": IntakeTier.CONVERSATIONAL,
            "lang": Lang.HI,
            "chief_complaint": "पेट में दर्द",
            "chief_complaint_en": "abdominal pain",
            **overrides,
        }
    )


def make_dictation(visit: Visit, doctor: Doctor, **overrides: Any) -> Dictation:
    return Dictation(
        **{
            "visit_id": visit.id,
            "doctor_id": doctor.id,
            "transcript": "Continue same regimen, review after 3 weeks.",
            **overrides,
        }
    )


async def build_clinic(session: AsyncSession) -> dict[str, Any]:
    """A minimal but complete clinic: hospital → department → doctor → patient.

    Flushes so every object has its PK and can be referenced by FK.
    """
    hospital = make_hospital()
    session.add(hospital)
    await session.flush()

    department = make_department(hospital)
    session.add(department)
    await session.flush()

    user = make_user(hospital, role=Role.DOCTOR)
    session.add(user)
    await session.flush()

    doctor = make_doctor(user, department)
    patient = make_patient(hospital)
    session.add_all([doctor, patient])
    await session.flush()

    return {
        "hospital": hospital,
        "department": department,
        "user": user,
        "doctor": doctor,
        "patient": patient,
    }


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
