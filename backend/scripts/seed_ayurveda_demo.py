"""An ayurveda physician's morning, for the SESSION-AYUR-3 console demo (doc 24 §6).

The counterpart to `seed_doctor_demo`, and deliberately a much smaller script:
the console is *the same console*. What this seeds exists to prove that the same
code, handed a department whose `care_system` is ayurveda, draws a different set
of sections — the cycle trend and the regimen lines gone, the assessment fields
and pathya-apathya there, the formulary checking a dictated churna against the
ayurveda shelf.

**The practitioner is created here rather than in `seeds/doctors.json`.** The
AYUR *department* is real seed data (AYUR-0 put it there); a named BAMS
physician is not, because the hospital has not hired one — seeding a fictional
doctor into the pilot dataset would put a person on the admin console's people
list who does not exist. Demo data belongs in a demo script.

Run against the dev DB, after `make seed`:
    DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
        .venv/bin/python -m scripts.seed_ayurveda_demo
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from app import queue as q
from app.db import build_engine, build_sessionmaker
from app.models.clinical import Dictation, Intake, Prescription, Visit
from app.models.enums import Channel, IntakeTier, Lang, QueueEntryState, Role, Sex, VisitStatus
from app.models.org import Department, Doctor, User
from app.models.patient import Patient
from app.models.scheduling import Queue, QueueEntry
from app.trees import bank
from app.trees.walker import Walk

TREE_KEY = "ayurveda_digestion"
DEMO_MRN_PREFIX = "AYURDEMO-"

#: The physician the demo signs in as. A real registration number format, an
#: obviously-fictional number — nobody's actual NCISM registration.
DOCTOR_REG_NO = "RMC-AYU-9001"
DOCTOR_PHONE = "+915550001901"
DOCTOR_NAME = "Dr. Sunita Sharma"

#: Two walk-ins, both answered through the real digestion tree so the stored
#: answers are exactly what the kiosk would have written. Neither fires a red
#: flag: the flags in this tree route *out* of ayurveda to urgent allopathic
#: care (doc 24 §4), and a demo whose every patient is being sent away would
#: demonstrate the routing rather than the console.
DEMO_PATIENTS: list[dict[str, Any]] = [
    {
        "name": "Shanti Devi",
        "age": 44,
        "sex": Sex.FEMALE,
        "village": "Bansur",
        "token": 31,
        "chief_complaint": "खाने के बाद पेट में जलन और खट्टी डकार",
        "chief_complaint_en": "burning and sour belching after meals",
        "answers": {
            "ayd.main": ("jalan", "जलन होती है, खट्टी डकार आती है"),
            "ayd.timing": ("khaane_baad", "खाने के बाद"),
            "ayd.duration": (90, "तीन महीने से"),
            "ayd.bowel": ("kabz", "कब्ज़ रहती है"),
            "ayd.diet": ("teekha", "तीखा और तला हुआ ज़्यादा"),
            "ayd.alarm": (["none"], "इनमें से कुछ नहीं"),
            "ayd.words": (
                "दोपहर का खाना देर से होता है, दुकान पर भीड़ रहती है",
                "दोपहर का खाना देर से होता है, दुकान पर भीड़ रहती है",
            ),
        },
    },
    {
        "name": "Ramniwas Meena",
        "age": 52,
        "sex": Sex.MALE,
        "village": "Rajgarh",
        "token": 32,
        "chief_complaint": "गैस बनती है और पेट फूलता है",
        "chief_complaint_en": "gas and bloating",
        "answers": {
            "ayd.main": ("gas", "गैस बनती है, पेट फूल जाता है"),
            "ayd.timing": ("raat", "रात को ज़्यादा"),
            "ayd.duration": (30, "एक महीने से"),
            "ayd.bowel": ("roz", "रोज़ साफ़ हो जाता है"),
            "ayd.diet": ("besamay", "समय का ठिकाना नहीं"),
            "ayd.alarm": (["none"], "इनमें से कुछ नहीं"),
            "ayd.words": ("रात को देर से खाता हूँ", "रात को देर से खाता हूँ"),
        },
    },
]


def _play(tree, scripted: dict[str, tuple[Any, str]]) -> Walk:
    """Answer the tree in ask order, exactly as the kiosk would.

    Follows `walk.current` rather than the dict's order, so a scripted answer
    that would branch past a node simply never gets asked — the stored answers
    can never contain an off-path node. Lifted from `seed_doctor_demo`, which is
    the point: the ayurveda trees are ordinary trees on the ordinary walker.
    """
    walk = Walk(tree)
    while (node := walk.current) is not None:
        if node.id not in scripted:
            break
        value, said = scripted[node.id]
        walk.save(node.id, value, text=said, lang=Lang.HI)
    return walk


async def _ensure_doctor(session, dept: Department) -> Doctor:
    """The BAMS physician, created once and reused. See the module docstring."""
    doctor = await session.scalar(select(Doctor).where(Doctor.reg_no == DOCTOR_REG_NO))
    if doctor is not None:
        # An earlier run may predate the department being switched on, or the
        # console may have moved her; keep the demo pointed at AYUR either way.
        doctor.department_id = dept.id
        doctor.active = True
        await session.flush()
        return doctor

    user = await session.scalar(select(User).where(User.phone == DOCTOR_PHONE))
    if user is None:
        user = User(
            phone=DOCTOR_PHONE,
            name=DOCTOR_NAME,
            role=Role.DOCTOR,
            lang=Lang.HI,
            hospital_id=dept.hospital_id,
            active=True,
        )
        session.add(user)
        await session.flush()

    doctor = Doctor(
        reg_no=DOCTOR_REG_NO,
        user_id=user.id,
        department_id=dept.id,
        name=DOCTOR_NAME,
        phone=DOCTOR_PHONE,
        qualification="BAMS, MD (Ayurveda)",
        active=True,
    )
    session.add(doctor)
    await session.flush()
    return doctor


async def _reset(session) -> None:
    """Clear the previous run's rows so the demo is repeatable.

    Dev-only and a hard delete, like `seed_doctor_demo`'s — a re-run should give
    a clean morning rather than an ever-growing queue. Children before parents.
    """
    patient_ids = (
        (await session.execute(select(Patient.id).where(Patient.mrn.like(f"{DEMO_MRN_PREFIX}%"))))
        .scalars()
        .all()
    )
    if not patient_ids:
        return
    visit_ids = (
        (await session.execute(select(Visit.id).where(Visit.patient_id.in_(patient_ids))))
        .scalars()
        .all()
    )
    if visit_ids:
        await session.execute(delete(QueueEntry).where(QueueEntry.visit_id.in_(visit_ids)))
        await session.execute(delete(Prescription).where(Prescription.visit_id.in_(visit_ids)))
        await session.execute(delete(Dictation).where(Dictation.visit_id.in_(visit_ids)))
        await session.execute(delete(Intake).where(Intake.visit_id.in_(visit_ids)))
        await session.execute(delete(Visit).where(Visit.id.in_(visit_ids)))
    await session.execute(delete(Patient).where(Patient.id.in_(patient_ids)))
    await session.execute(
        delete(Queue).where(Queue.date == q.today(), ~Queue.id.in_(select(QueueEntry.queue_id)))
    )
    await session.flush()


async def _clear_todays_line(session, department_id: uuid.UUID) -> int:
    """Close anything an earlier demo left waiting in this department today."""
    queue = await session.scalar(
        select(Queue).where(Queue.department_id == department_id, Queue.date == q.today())
    )
    if queue is None:
        return 0
    entries = (
        (
            await session.execute(
                select(QueueEntry).where(
                    QueueEntry.queue_id == queue.id,
                    QueueEntry.state.in_(
                        [
                            QueueEntryState.WAITING,
                            QueueEntryState.CALLED,
                            QueueEntryState.IN_CONSULT,
                        ]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for entry in entries:
        await q.set_state(session, entry_id=entry.id, state=QueueEntryState.DONE)
    return len(entries)


async def main() -> None:
    engine = build_engine()
    sm = build_sessionmaker(engine)
    tree = bank.get(TREE_KEY)

    async with sm() as session:
        dept = await session.scalar(select(Department).where(Department.code == "AYUR"))
        if dept is None:
            raise SystemExit("seed the pilot dataset first: make seed")
        if not dept.active:
            # AYUR-1's guard opens it only when it has trees to ask; AYUR-2 gave
            # it five. A box seeded before that has it dark, and the demo is
            # allowed to open it — this is a dev script, and the console's
            # Facility tab is the operator's path to the same switch.
            dept.active = True
            print("opened the Ayurveda department (it was dark on this box)")

        doctor = await _ensure_doctor(session, dept)
        await _reset(session)
        closed = await _clear_todays_line(session, dept.id)
        if closed:
            print(f"closed {closed} queue entries left in {dept.code} by earlier demos")

        for spec in DEMO_PATIENTS:
            patient = Patient(
                hospital_id=dept.hospital_id,
                mrn=f"{DEMO_MRN_PREFIX}{spec['token']}",
                name=spec["name"],
                phone=f"+9155501{spec['token']:05d}",
                age=spec["age"],
                sex=spec["sex"],
                lang=Lang.HI,
                village=spec["village"],
                district="Alwar",
            )
            session.add(patient)
            await session.flush()

            walk = _play(tree, spec["answers"])
            flags = walk.red_flags()

            visit = Visit(
                patient_id=patient.id,
                department_id=dept.id,
                doctor_id=doctor.id,
                date=q.today(),
                status=VisitStatus.INTAKE_DONE,
                channel=Channel.KIOSK,
                token_no=spec["token"],
            )
            session.add(visit)
            await session.flush()

            intake = Intake(
                visit_id=visit.id,
                tier=IntakeTier.CONVERSATIONAL,
                lang=Lang.HI,
                answers=walk.to_json(),
                red_flags=[flag.to_json() for flag in flags],
                tree_ref=tree.ref,
                chief_complaint=spec["chief_complaint"],
                chief_complaint_en=spec["chief_complaint_en"],
                confirmed_by_patient=True,
                completed_at=datetime.now(UTC),
            )
            session.add(intake)
            await session.flush()

            await q.enqueue_from_intake(session, visit=visit, intake=intake)
            names = ", ".join(flag.name(Lang.EN) for flag in flags) or "none"
            print(f"  token {spec['token']:>3}  {spec['name']:<18} flags: {names}")

        queue = await q.get_or_create_queue(session, department_id=dept.id)
        called = await q.call_next(session, queue_id=queue.id)
        if called is not None:
            await q.set_state(session, entry_id=called.id, state=QueueEntryState.IN_CONSULT)

        await session.commit()
        print(f"\nseeded {len(DEMO_PATIENTS)} walk-ins for {doctor.name} ({dept.code})")
        print(f"login: {DOCTOR_PHONE} — the OTP is echoed locally (OTP_DEBUG_ECHO=true)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
