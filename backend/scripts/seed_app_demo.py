"""One patient's phone, for the S16 app demo (doc 03 §1c).

Gives the first seeded patient (`+915551900001`) the things the app is built to
show, so a screen walk on an emulator has something real on it:

* a **signed prescription** with a schedule the doctor's words actually stated
  (`1-0-1`) and one whose words did not (`SOS`) — the pair that proves the
  reminder screen never invents a dose time,
* a **completed intake** with a Hindi read-back summary, so the care file has a
  visit summary next to the prescription,
* a **chemo-review appointment**, so the calendar has a cycle,
* a **caregiver link**, so family access is not an empty screen.

The prescription rows are written in the frozen shape `app.prescription` writes
at signing (name as dictated, plus the parsed schedule) rather than by running a
dictation through the model — the dictation path has its own fixtures and tests
(S10/S11), and this script's job is to furnish a phone, not to re-prove that.
Registered in STATE.md → Stubs & fakes as demo data.

Run against the dev DB:
    DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
        .venv/bin/python -m scripts.seed_app_demo
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.clinical import Intake, Prescription, Visit
from app.models.enums import (
    AppointmentStatus,
    CaregiverLinkStatus,
    Channel,
    IntakeTier,
    Lang,
    SlotType,
    VisitStatus,
)
from app.models.org import Department, Doctor
from app.models.patient import CaregiverLink, Patient
from app.models.scheduling import Appointment

DEMO_PHONE = os.getenv("DEMO_PHONE", "+915551900001")
CAREGIVER_PHONE = os.getenv("DEMO_CAREGIVER_PHONE", "+915551900099")

MEDS = [
    {
        "name": "Tab Ondansetron",
        "dose": "4mg",
        "route": "PO",
        "freq": "1-0-1",
        "duration": "5 days",
        "as_spoken": "ondansetron four mg one zero one for five days",
        "known": True,
        "generic": "ondansetron",
        "schedule": {
            "morning": True,
            "afternoon": False,
            "night": True,
            "per_day": 2,
            "slots_known": True,
            "source": "1-0-1",
        },
        "flagged": False,
    },
    {
        "name": "Tab Paracetamol",
        "dose": "500mg",
        "route": "PO",
        # Unreadable as a schedule on purpose: this is the line the app must
        # refuse to put an alarm on.
        "freq": "SOS",
        "duration": "as needed",
        "as_spoken": "paracetamol five hundred SOS",
        "known": True,
        "generic": "paracetamol",
        "schedule": None,
        "flagged": False,
    },
]

SUMMARY_HI = (
    "मुख्य शिकायत: पेट में दर्द, तीन दिन से।\n"
    "दर्द मध्यम है और खाने के बाद बढ़ता है। उल्टी नहीं हुई। बुखार नहीं है।\n"
    "पिछली कीमो के बाद दो दिन कमज़ोरी रही।"
)


async def main() -> None:
    url = os.getenv("DATABASE_URL", "postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd")
    engine = create_async_engine(url)

    # `expire_on_commit=False` so the summary line below can still read the
    # patient's name after the commit — an expired attribute would trigger a
    # lazy refresh outside the greenlet and blow up on the last line.
    async with AsyncSession(engine, expire_on_commit=False) as session:
        patient = (
            await session.execute(select(Patient).where(Patient.phone == DEMO_PHONE))
        ).scalars().first()
        if patient is None:
            raise SystemExit(f"no seeded patient on {DEMO_PHONE} — run `make seed` first")

        department = (
            await session.execute(select(Department).where(Department.code == "MEDONC"))
        ).scalars().first()
        if department is None:
            raise SystemExit("no MEDONC department — run `make seed` first")

        doctor = (
            await session.execute(
                select(Doctor).where(Doctor.department_id == department.id)
            )
        ).scalars().first()

        now = datetime.now(UTC)

        # -- a past visit with a summary and a prescription --------------------
        # Matched on exactly what is created below, or a second run mints a
        # second visit and the patient's file grows a duplicate prescription.
        visit = (
            await session.execute(
                select(Visit).where(
                    Visit.patient_id == patient.id,
                    Visit.channel == Channel.KIOSK,
                    Visit.status == VisitStatus.DONE,
                )
            )
        ).scalars().first()
        if visit is None:
            visit = Visit(
                patient_id=patient.id,
                department_id=department.id,
                doctor_id=doctor.id if doctor else None,
                date=(now - timedelta(days=6)).date(),
                token_no=None,
                status=VisitStatus.DONE,
                channel=Channel.KIOSK,
            )
            session.add(visit)
            await session.flush()

        intake = (
            await session.execute(select(Intake).where(Intake.visit_id == visit.id))
        ).scalars().first()
        if intake is None:
            session.add(
                Intake(
                    visit_id=visit.id,
                    tier=IntakeTier.PRERECORDED,
                    lang=Lang.HI,
                    chief_complaint="पेट में दर्द",
                    chief_complaint_en="abdominal pain",
                    summary_md=SUMMARY_HI,
                    summary_lang_versions={"hi": SUMMARY_HI},
                    confirmed_by_patient=True,
                    completed_at=now - timedelta(days=6),
                    answers={},
                    red_flags=[],
                )
            )

        prescription = (
            await session.execute(
                select(Prescription).where(Prescription.visit_id == visit.id)
            )
        ).scalars().first()
        if prescription is None:
            session.add(Prescription(visit_id=visit.id, meds=MEDS))

        # -- a chemo cycle to put on the calendar ------------------------------
        cycle = (
            await session.execute(
                select(Appointment).where(
                    Appointment.patient_id == patient.id,
                    Appointment.slot_type == SlotType.CHEMO_REVIEW,
                )
            )
        ).scalars().first()
        if cycle is None:
            session.add(
                Appointment(
                    patient_id=patient.id,
                    department_id=department.id,
                    doctor_id=doctor.id if doctor else None,
                    slot_at=now + timedelta(days=8),
                    status=AppointmentStatus.BOOKED,
                    source=Channel.PHONE,
                    slot_type=SlotType.CHEMO_REVIEW,
                )
            )

        # -- a daughter who may see the file -----------------------------------
        link = (
            await session.execute(
                select(CaregiverLink).where(
                    CaregiverLink.patient_id == patient.id,
                    CaregiverLink.phone == CAREGIVER_PHONE,
                )
            )
        ).scalars().first()
        if link is None:
            session.add(
                CaregiverLink(
                    patient_id=patient.id,
                    phone=CAREGIVER_PHONE,
                    name="Meena",
                    relation="daughter",
                    status=CaregiverLinkStatus.ACTIVE,
                    consented_at=now,
                )
            )

        await session.commit()
        print(f"seeded the app demo for {patient.name} ({DEMO_PHONE})")
        print(f"  caregiver login: {CAREGIVER_PHONE}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
