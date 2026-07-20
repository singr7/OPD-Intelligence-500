"""Populate the queue with a few walk-ins for the S8 board/console demo.

Idempotent-ish: it enqueues fresh walk-in visits each run (they're anonymous
demo rows), and calls the front of two departments so the board shows a
now-serving numeral. One entry carries a red flag so the urgent-jump + reason
chip is visible on both surfaces.

Run against the dev DB:
    DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
        .venv/bin/python -m scripts.seed_queue_demo
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import delete, select

from app import queue as q
from app.db import build_engine, build_sessionmaker
from app.models.clinical import Intake, Visit
from app.models.enums import Channel, IntakeTier, Lang, QueueEntryState, VisitStatus
from app.models.org import Department
from app.models.patient import Patient
from app.models.scheduling import Queue, QueueEntry

URGENT_FLAG = {
    "id": "fever_post_chemo",
    "severity": "urgent",
    "label": {"en": "Fever after chemo", "hi": "कीमो के बाद बुखार"},
    "instruction": {"en": "Call the nurse now."},
    "source_node": "onc.fever",
}

COMPLAINTS = [
    "पेट में दर्द और उल्टी",
    "बुखार और कमजोरी",
    "साँस लेने में तकलीफ़",
    "पुराना घाव ठीक नहीं हो रहा",
    "सिर दर्द तीन दिन से",
]


async def _walk_in(session, dept: Department, token: int, complaint: str, flags):
    patient = Patient(
        hospital_id=dept.hospital_id,
        mrn=f"DEMO-{uuid.uuid4().hex[:8].upper()}",
        name="Walk-in patient",
        phone="",
        lang=Lang.HI,
    )
    session.add(patient)
    await session.flush()
    visit = Visit(
        patient_id=patient.id,
        department_id=dept.id,
        date=q.today(),
        status=VisitStatus.REGISTERED,
        channel=Channel.KIOSK,
        token_no=token,
    )
    session.add(visit)
    await session.flush()
    intake = Intake(
        visit_id=visit.id,
        tier=IntakeTier.PRERECORDED,
        lang=Lang.HI,
        chief_complaint=complaint,
        red_flags=flags or [],
        confirmed_by_patient=True,
    )
    session.add(intake)
    await session.flush()
    return await q.enqueue_from_intake(session, visit=visit, intake=intake)


async def _reset_today(session) -> None:
    """Clear this day's demo queue so a re-run is deterministic (dev-only).

    Hard deletes the anonymous demo rows and today's queues/entries — this is a
    demo seeder, not app code, so it steps outside the soft-delete invariant on
    purpose to give the screenshots a clean, repeatable state.
    """
    today = q.today()
    queue_ids = (
        (await session.execute(select(Queue.id).where(Queue.date == today))).scalars().all()
    )
    if queue_ids:
        await session.execute(delete(QueueEntry).where(QueueEntry.queue_id.in_(queue_ids)))
        await session.execute(delete(Queue).where(Queue.id.in_(queue_ids)))
    demo_visits = (
        (
            await session.execute(
                select(Visit.id)
                .join(Patient, Visit.patient_id == Patient.id)
                .where(Visit.date == today, Patient.mrn.like("DEMO-%"))
            )
        )
        .scalars()
        .all()
    )
    if demo_visits:
        await session.execute(delete(Intake).where(Intake.visit_id.in_(demo_visits)))
        await session.execute(delete(Visit).where(Visit.id.in_(demo_visits)))
    await session.flush()


async def main() -> None:
    engine = build_engine()
    sm = build_sessionmaker(engine)
    async with sm() as session:
        await _reset_today(session)
        depts = (
            (await session.execute(select(Department).order_by(Department.code))).scalars().all()
        )
        depts = list(depts)[:3]
        base = 10
        for di, dept in enumerate(depts):
            for i in range(4):
                token = base + di * 20 + i
                # First department's 3rd walk-in is the urgent one.
                flags = [URGENT_FLAG] if (di == 0 and i == 2) else None
                await _walk_in(session, dept, token, COMPLAINTS[(di + i) % len(COMPLAINTS)], flags)
            # Call the front of the first two rooms so the board has a now-serving.
            if di < 2:
                queue = await q.get_or_create_queue(session, department_id=dept.id)
                called = await q.call_next(session, queue_id=queue.id)
                if called and di == 0:
                    # Put the first room's called patient into consult for variety.
                    await q.set_state(
                        session, entry_id=called.id, state=QueueEntryState.IN_CONSULT
                    )
        await session.commit()
        print(f"seeded queue demo across {len(depts)} departments")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
