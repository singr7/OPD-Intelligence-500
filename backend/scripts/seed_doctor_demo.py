"""A doctor's morning, for the S9 console demo (doc 03 §5).

Builds one MEDONC clinic for the seeded Dr. Anil Gupta (`+915550001001`): five
walk-ins with completed intakes, one of them urgent, plus a returning patient
with a visit history and a check-in trend so the timeline and the sparklines have
something real to draw.

Two things are honest here and one is a fixture:

* **The answers are a real walk.** Each patient's answers are played through
  `app.trees.walker.Walk` in ask order, so the stored `Intake.answers` is exactly
  what the kiosk would have written, and refuses anything the tree would refuse.
* **The red flags are the rules'.** They come from `walk.red_flags()`, not from a
  literal — so the urgent patient is urgent because
  `mo.cyc.febrile_neutropenia` actually fires (fever ≥38 within 14 days of
  chemo), and the queue's urgent-jump follows from that by construction.
* **The structured summaries are authored fixtures**, standing in for what the
  LLM path (doc 03 §4) writes on a box with a real model. The deterministic
  V3 `TemplateSummarizer` produces only "question: answer" lines and no symptom
  table, which would under-sell a screen whose whole job is a 20-second read.
  Registered in STATE.md → Stubs & fakes.

Run against the dev DB:
    DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
        .venv/bin/python -m scripts.seed_doctor_demo
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from app import queue as q
from app.db import build_engine, build_sessionmaker
from app.models.clinical import Dictation, Intake, Visit
from app.models.content import Checkin, CheckinPlan
from app.models.enums import (
    Channel,
    CheckinPlanStatus,
    IntakeTier,
    Lang,
    QueueEntryState,
    Sex,
    VisitStatus,
)
from app.models.org import Department, Doctor
from app.models.patient import Patient
from app.models.scheduling import Queue, QueueEntry
from app.trees import bank
from app.trees.walker import Walk

TREE_KEY = "med_onc_between_cycle"
DEMO_MRN_PREFIX = "S9DEMO-"

#: One scripted patient: identity, the answers to play, and the authored §4
#: summary the LLM path would have produced from them.
DEMO_PATIENTS: list[dict[str, Any]] = [
    {
        "name": "Kamla Devi",
        "age": 58,
        "sex": Sex.FEMALE,
        "village": "Ramgarh",
        "token": 12,
        "chief_complaint": "कीमो के बाद तेज़ बुखार और कमजोरी",
        "chief_complaint_en": "high fever and weakness after chemotherapy",
        # Day 8 after chemo with a 38.6°C fever — fires febrile neutropenia.
        "answers": {
            "mo.cyc.days_since": (8, "आठ दिन पहले"),
            "mo.cyc.fever_temp": (38.6, "बुखार है, थर्मामीटर में अड़तीस छह"),
            "mo.cyc.nausea": (6, "बहुत जी मिचलाता है"),
            "mo.cyc.vomiting": ("three_five", "चार बार उल्टी हुई"),
            "mo.cyc.fluids": ("little", "थोड़ा-थोड़ा पानी"),
            "mo.cyc.mucositis": ("mild", "मुँह में हल्की जलन"),
            "mo.cyc.neuropathy": ("mild", "उँगलियों में थोड़ा सुन्नपन"),
            "mo.cyc.fatigue": (8, "बहुत कमजोरी"),
            "mo.cyc.appetite": ("much_less", "खाना नहीं जाता"),
            "mo.cyc.bowel": ("loose", "पतले दस्त"),
            "mo.cyc.bleeding": ("no", "नहीं"),
            "mo.cyc.words": ("बहुत घबराहट हो रही है", "बहुत घबराहट हो रही है"),
        },
        "summary": {
            "chief_concern": (
                "Fever 38.6°C on day 8 after chemotherapy, with fatigue and poor intake"
            ),
            "hpi": [
                "Last chemotherapy 8 days ago; fever began yesterday evening.",
                "Four episodes of vomiting in the last day; keeping only sips of water down.",
                "Marked fatigue (8/10) and loose stools; appetite much reduced.",
            ],
            "symptoms": [
                {"symptom": "Fever", "duration": "1 day", "severity": "38.6°C"},
                {"symptom": "Nausea", "duration": "3 days", "severity": "6/10"},
                {"symptom": "Vomiting", "duration": "1 day", "severity": "3–5 episodes"},
                {"symptom": "Fatigue", "duration": "5 days", "severity": "8/10"},
                {"symptom": "Loose stools", "duration": "2 days", "severity": "moderate"},
            ],
            "history_meds": [
                "Carcinoma breast, on adjuvant chemotherapy (cycle 3).",
                "Hypertension, on amlodipine.",
            ],
            "since_last_visit": [
                "Fever is new since the last review.",
                "Fatigue worse (was 5/10 last cycle).",
            ],
            "patient_words": {
                "quote": "बहुत घबराहट हो रही है",
                "english": "I feel very anxious and unwell",
            },
            "unclear": [],
        },
        "history": True,
        "trend": True,
    },
    {
        "name": "Ramesh Chand",
        "age": 64,
        "sex": Sex.MALE,
        "village": "Bansur",
        "token": 13,
        "chief_complaint": "उल्टी बंद नहीं हो रही",
        "chief_complaint_en": "vomiting will not stop",
        "answers": {
            "mo.cyc.days_since": (4, "चार दिन"),
            "mo.cyc.fever_temp": (37.1, "बुखार नहीं"),
            "mo.cyc.nausea": (8, "बहुत ज़्यादा"),
            "mo.cyc.vomiting": ("over_five", "छह-सात बार"),
            "mo.cyc.fluids": ("no", "पानी भी नहीं रुकता"),
            "mo.cyc.mucositis": ("none", "नहीं"),
            "mo.cyc.neuropathy": ("none", "नहीं"),
            "mo.cyc.fatigue": (7, "बहुत थकान"),
            "mo.cyc.appetite": ("nothing", "कुछ नहीं खा पा रहा"),
            "mo.cyc.bowel": ("normal", "ठीक"),
            "mo.cyc.bleeding": ("no", "नहीं"),
            "mo.cyc.words": ("कमजोरी से चला नहीं जाता", "कमजोरी से चला नहीं जाता"),
        },
        "summary": {
            "chief_concern": (
                "Intractable vomiting on day 4 after chemotherapy, not tolerating oral fluids"
            ),
            "hpi": [
                "Last chemotherapy 4 days ago.",
                "More than five episodes of vomiting in the last day.",
                "Unable to keep water down; eating nothing.",
            ],
            "symptoms": [
                {"symptom": "Nausea", "duration": "4 days", "severity": "8/10"},
                {"symptom": "Vomiting", "duration": "2 days", "severity": ">5 episodes"},
                {"symptom": "Fatigue", "duration": "4 days", "severity": "7/10"},
            ],
            "history_meds": [
                "Carcinoma stomach, on palliative chemotherapy.",
                "Ondansetron as needed — patient reports it is not helping.",
            ],
            "since_last_visit": ["Vomiting is new and more severe than the previous cycle."],
            "patient_words": {
                "quote": "कमजोरी से चला नहीं जाता",
                "english": "I am too weak to walk",
            },
            "unclear": ["name of the anti-sickness medicine taken at home"],
        },
        "history": False,
        "trend": False,
    },
    {
        "name": "Shakuntala Bai",
        "age": 47,
        "sex": Sex.FEMALE,
        "village": "Kishangarh",
        "token": 14,
        "chief_complaint": "हाथ-पैर में झुनझुनी",
        "chief_complaint_en": "tingling in hands and feet",
        "answers": {
            "mo.cyc.days_since": (11, "ग्यारह दिन"),
            "mo.cyc.fever_temp": (36.9, "बुखार नहीं"),
            "mo.cyc.nausea": (2, "थोड़ा"),
            "mo.cyc.vomiting": ("none", "नहीं"),
            "mo.cyc.fluids": ("yes", "हाँ"),
            "mo.cyc.mucositis": ("none", "नहीं"),
            "mo.cyc.neuropathy": ("buttons", "बटन नहीं लगा पाती"),
            "mo.cyc.fatigue": (4, "थोड़ी थकान"),
            "mo.cyc.appetite": ("less", "थोड़ा कम"),
            "mo.cyc.bowel": ("constipated", "कब्ज़"),
            "mo.cyc.bleeding": ("no", "नहीं"),
            "mo.cyc.words": ("सिलाई का काम नहीं कर पा रही", "सिलाई का काम नहीं कर पा रही"),
        },
        "summary": {
            "chief_concern": "Worsening peripheral neuropathy on day 11 after chemotherapy",
            "hpi": [
                "Numbness and tingling in hands and feet, now interfering with fine tasks.",
                "Cannot fasten buttons; unable to do her tailoring work.",
                "Mild nausea and constipation; no fever.",
            ],
            "symptoms": [
                {
                    "symptom": "Neuropathy",
                    "duration": "3 weeks",
                    "severity": "interferes with tasks",
                },
                {"symptom": "Nausea", "duration": "4 days", "severity": "2/10"},
                {"symptom": "Constipation", "duration": "1 week", "severity": "mild"},
                {"symptom": "Fatigue", "duration": "1 week", "severity": "4/10"},
            ],
            "history_meds": [
                "Carcinoma ovary, on paclitaxel-based chemotherapy.",
            ],
            "since_last_visit": ["Neuropathy has progressed from mild to interfering with work."],
            "patient_words": {
                "quote": "सिलाई का काम नहीं कर पा रही",
                "english": "I cannot do my tailoring work any more",
            },
            "unclear": [],
        },
        "history": True,
        "trend": False,
    },
    {
        "name": "Mohan Lal",
        "age": 71,
        "sex": Sex.MALE,
        "village": "Thanagazi",
        "token": 15,
        "chief_complaint": "मुँह में छाले, खाना नहीं खा पा रहा",
        "chief_complaint_en": "mouth ulcers, unable to eat",
        # appetite=nothing + mucositis=cannot_eat fires mo.cyc.not_eating (semi).
        "answers": {
            "mo.cyc.days_since": (6, "छह दिन"),
            "mo.cyc.fever_temp": (37.4, "हल्का सा"),
            "mo.cyc.nausea": (3, "थोड़ा"),
            "mo.cyc.vomiting": ("one_two", "एक-दो बार"),
            "mo.cyc.fluids": ("little", "थोड़ा"),
            "mo.cyc.mucositis": ("cannot_eat", "छालों से खा नहीं सकता"),
            "mo.cyc.neuropathy": ("none", "नहीं"),
            "mo.cyc.fatigue": (6, "थकान"),
            "mo.cyc.appetite": ("nothing", "कुछ नहीं खाया"),
            "mo.cyc.bowel": ("constipated", "कब्ज़"),
            "mo.cyc.bleeding": ("no", "नहीं"),
            "mo.cyc.words": ("दो दिन से कुछ नहीं खाया", "दो दिन से कुछ नहीं खाया"),
        },
        "summary": {
            "chief_concern": (
                "Grade 3 mucositis on day 6 after chemotherapy — not eating for two days"
            ),
            "hpi": [
                "Mouth ulcers so painful he cannot eat solid food.",
                "Nothing eaten for two days; only sips of fluid.",
                "Low-grade temperature 37.4°C, no rigors.",
            ],
            "symptoms": [
                {"symptom": "Mucositis", "duration": "4 days", "severity": "cannot eat"},
                {"symptom": "Appetite loss", "duration": "2 days", "severity": "complete"},
                {"symptom": "Fatigue", "duration": "5 days", "severity": "6/10"},
            ],
            "history_meds": [
                "Carcinoma tongue, on concurrent chemoradiation.",
                "Topical anaesthetic gel, not effective.",
            ],
            "since_last_visit": ["Mucositis has worsened; was mild at the last visit."],
            "patient_words": {
                "quote": "दो दिन से कुछ नहीं खाया",
                "english": "I have not eaten anything for two days",
            },
            "unclear": [],
        },
        "history": True,
        "trend": True,
    },
    {
        "name": "Sita Kumari",
        "age": 39,
        "sex": Sex.FEMALE,
        "village": "Malakhera",
        "token": 16,
        "chief_complaint": "सब ठीक है, बस जाँच करानी है",
        "chief_complaint_en": "feeling well, here for routine review",
        "answers": {
            "mo.cyc.days_since": (18, "अठारह दिन"),
            "mo.cyc.fever_temp": (36.8, "बुखार नहीं"),
            "mo.cyc.nausea": (1, "ना के बराबर"),
            "mo.cyc.vomiting": ("none", "नहीं"),
            "mo.cyc.fluids": ("yes", "हाँ"),
            "mo.cyc.mucositis": ("none", "नहीं"),
            "mo.cyc.neuropathy": ("none", "नहीं"),
            "mo.cyc.fatigue": (2, "ठीक हूँ"),
            "mo.cyc.appetite": ("same", "पहले जैसा"),
            "mo.cyc.bowel": ("normal", "ठीक"),
            "mo.cyc.bleeding": ("no", "नहीं"),
            "mo.cyc.words": ("अगली साइकिल कब है?", "अगली साइकिल कब है?"),
        },
        "summary": {
            "chief_concern": "Well between cycles; here for routine review before the next cycle",
            "hpi": [
                "Day 18 after chemotherapy, recovered well.",
                "No fever, vomiting or mouth ulcers; eating normally.",
                "Wants to know the date of the next cycle.",
            ],
            "symptoms": [
                {"symptom": "Nausea", "duration": "resolved", "severity": "1/10"},
                {"symptom": "Fatigue", "duration": "resolved", "severity": "2/10"},
            ],
            "history_meds": ["Carcinoma cervix, on adjuvant chemotherapy (cycle 2 of 6)."],
            "since_last_visit": ["Recovered from the nausea reported last cycle."],
            "patient_words": {
                "quote": "अगली साइकिल कब है?",
                "english": "When is my next cycle?",
            },
            "unclear": [],
        },
        "history": False,
        "trend": False,
    },
]


def _play(tree, scripted: dict[str, tuple[Any, str]]) -> Walk:
    """Answer the tree in ask order, exactly as the kiosk would.

    Follows `walk.current` rather than the dict's order, so a scripted answer
    that would branch past a node simply never gets asked — the walk stays a
    walk, and the stored answers can never contain an off-path node.
    """
    walk = Walk(tree)
    while (node := walk.current) is not None:
        if node.id not in scripted:
            break
        value, said = scripted[node.id]
        walk.save(node.id, value, text=said, lang=Lang.HI)
    return walk


async def _reset(session, hospital_id: uuid.UUID) -> None:
    """Clear the previous run's demo rows so screenshots are repeatable.

    Dev-only, and deliberately a hard delete: like `seed_queue_demo`, this steps
    outside the soft-delete invariant on purpose so a re-run gives a clean
    state rather than an ever-growing queue.
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
    plan_ids = (
        (
            await session.execute(
                select(CheckinPlan.id).where(CheckinPlan.patient_id.in_(patient_ids))
            )
        )
        .scalars()
        .all()
    )
    if plan_ids:
        await session.execute(delete(Checkin).where(Checkin.plan_id.in_(plan_ids)))
        await session.execute(delete(CheckinPlan).where(CheckinPlan.id.in_(plan_ids)))
    if visit_ids:
        await session.execute(delete(QueueEntry).where(QueueEntry.visit_id.in_(visit_ids)))
        # S10: a consult note keeps its visit alive. Without this the second
        # `seed_doctor_demo` of the day dies on a foreign key, which is a
        # confusing way to learn that yesterday's demo signed something.
        await session.execute(delete(Dictation).where(Dictation.visit_id.in_(visit_ids)))
        await session.execute(delete(Intake).where(Intake.visit_id.in_(visit_ids)))
        await session.execute(delete(Visit).where(Visit.id.in_(visit_ids)))
    await session.execute(delete(Patient).where(Patient.id.in_(patient_ids)))
    # Drop today's now-empty MEDONC queue so the demo re-enqueues from scratch.
    await session.execute(
        delete(Queue).where(Queue.date == q.today(), ~Queue.id.in_(select(QueueEntry.queue_id)))
    )
    await session.flush()


async def _history(session, patient: Patient, dept: Department, base_token: int) -> None:
    """Two earlier visits so the timeline has something to say.

    Historical tokens are derived from the patient's demo token (`base*10 + weeks`)
    because `uq_visits_dept_date_token` is per department per day: two demo
    patients whose history lands on the same past date would otherwise collide.
    The result stays well below `KIOSK_OFFLINE_TOKEN_BASE` (500), so it cannot
    stray into the offline block range either.
    """
    for weeks, complaint in ((3, "पिछली साइकिल की समीक्षा"), (7, "पहली बार दिखाने आए")):
        past = Visit(
            patient_id=patient.id,
            department_id=dept.id,
            date=q.today() - timedelta(weeks=weeks),
            status=VisitStatus.DONE,
            channel=Channel.KIOSK,
            token_no=base_token * 10 + weeks,
        )
        session.add(past)
        await session.flush()
        session.add(
            Intake(
                visit_id=past.id,
                tier=IntakeTier.PRERECORDED,
                lang=Lang.HI,
                chief_complaint=complaint,
                chief_complaint_en=(
                    "review after previous cycle" if weeks == 3 else "first oncology visit"
                ),
                confirmed_by_patient=True,
                completed_at=datetime.now(UTC) - timedelta(weeks=weeks),
            )
        )
    await session.flush()


async def _trend(session, patient: Patient) -> None:
    """Four fortnightly check-ins, so the sparklines have a real shape."""
    plan = CheckinPlan(
        patient_id=patient.id,
        protocol_key="chemo_cycle",
        status=CheckinPlanStatus.ACTIVE,
        schedule=[{"day_offset": d, "channel": "whatsapp"} for d in (7, 21, 35, 49)],
    )
    session.add(plan)
    await session.flush()
    # Pain easing, fatigue climbing — a shape a doctor can read at a glance.
    series = [(6, 3), (5, 5), (4, 6), (3, 8)]
    for index, (pain, fatigue) in enumerate(series):
        at = datetime.now(UTC) - timedelta(days=42 - index * 14)
        session.add(
            Checkin(
                plan_id=plan.id,
                due_at=at,
                sent_at=at,
                channel=Channel.WHATSAPP,
                responses={"pain": pain, "fatigue": fatigue},
            )
        )
    await session.flush()


async def main() -> None:
    engine = build_engine()
    sm = build_sessionmaker(engine)
    tree = bank.get(TREE_KEY)

    async with sm() as session:
        doctor = await session.scalar(select(Doctor).where(Doctor.reg_no == "RMC-ONC-1001"))
        if doctor is None:
            raise SystemExit("seed the pilot dataset first: make seed")
        dept = await session.get(Department, doctor.department_id)
        assert dept is not None

        await _reset(session, dept.hospital_id)

        for spec in DEMO_PATIENTS:
            patient = Patient(
                hospital_id=dept.hospital_id,
                mrn=f"{DEMO_MRN_PREFIX}{spec['token']}",
                name=spec["name"],
                phone=f"+9155500{spec['token']:05d}",
                age=spec["age"],
                sex=spec["sex"],
                lang=Lang.HI,
                village=spec["village"],
                district="Alwar",
            )
            session.add(patient)
            await session.flush()

            if spec["history"]:
                await _history(session, patient, dept, spec["token"])
            if spec["trend"]:
                await _trend(session, patient)

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
                summary_lang_versions={
                    "hi": {
                        "structured": spec["summary"],
                        "readback": spec["chief_complaint"],
                    }
                },
                confirmed_by_patient=True,
                completed_at=datetime.now(UTC),
            )
            session.add(intake)
            await session.flush()

            await q.enqueue_from_intake(session, visit=visit, intake=intake)
            names = ", ".join(flag.name(Lang.EN) for flag in flags) or "none"
            print(f"  token {spec['token']:>3}  {spec['name']:<18} flags: {names}")

        # Call the front of the line so the console opens mid-morning, with
        # someone already in the room.
        queue = await q.get_or_create_queue(session, department_id=dept.id)
        called = await q.call_next(session, queue_id=queue.id)
        if called is not None:
            await q.set_state(session, entry_id=called.id, state=QueueEntryState.IN_CONSULT)

        await session.commit()
        print(f"\nseeded {len(DEMO_PATIENTS)} walk-ins for {doctor.name} ({dept.code})")
        print("login: +915550001001 — the OTP is echoed locally (OTP_DEBUG_ECHO=true)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
