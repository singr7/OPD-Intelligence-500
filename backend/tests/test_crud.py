"""CRUD round-trips and the schema invariants the domain depends on."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.clinical import Intake, Prescription, Visit
from app.models.content import QuestionTree
from app.models.enums import Channel, IntakeTier, TreeStatus, VisitStatus
from app.models.org import Department, Doctor, Hospital
from app.models.patient import Patient
from tests import factories as f


async def test_hospital_department_doctor_round_trip(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)

    # Relationships are loaded explicitly: an async session cannot lazy-load on
    # attribute access, so every traversal a route needs must be eager-loaded.
    result = await session.execute(
        select(Doctor)
        .where(Doctor.id == clinic["doctor"].id)
        .options(
            selectinload(Doctor.user),
            selectinload(Doctor.department).selectinload(Department.hospital),
        )
    )
    doctor = result.scalar_one()

    assert doctor.department_id == clinic["department"].id
    assert doctor.user.role.value == "doctor"
    assert doctor.department.hospital.code == clinic["hospital"].code


async def test_patient_create_read_update(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]

    patient.village = "Behror"
    patient.caregiver_name = "Sita Devi"
    patient.caregiver_phone = "+915552900001"
    await session.flush()

    # created_at/updated_at are server defaults — unloaded until refreshed.
    await session.refresh(patient)

    fetched = await session.get(Patient, patient.id)
    assert fetched is not None
    assert fetched.village == "Behror"
    assert fetched.caregiver_name == "Sita Devi"
    assert fetched.created_at is not None
    assert fetched.updated_at is not None


async def test_soft_delete_keeps_the_row(session: AsyncSession) -> None:
    """Doc 02 §4: soft deletes only — a deleted patient is still queryable."""
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]

    patient.deleted_at = datetime.now(UTC)
    await session.flush()

    still_there = await session.get(Patient, patient.id)
    assert still_there is not None
    assert still_there.is_deleted

    live = await session.execute(
        select(Patient).where(Patient.id == patient.id, Patient.deleted_at.is_(None))
    )
    assert live.scalar_one_or_none() is None


async def test_visit_intake_cascade_of_references(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)

    visit = f.make_visit(clinic["patient"], clinic["department"], doctor_id=clinic["doctor"].id)
    session.add(visit)
    await session.flush()

    intake = f.make_intake(visit, answers={"onc.pain.location": {"value": "abdomen"}})
    session.add(intake)
    await session.flush()

    result = await session.execute(
        select(Visit).where(Visit.id == visit.id).options(selectinload(Visit.intakes))
    )
    fetched = result.scalar_one()
    assert len(fetched.intakes) == 1
    assert fetched.intakes[0].answers["onc.pain.location"]["value"] == "abdomen"


async def test_jsonb_payloads_round_trip_unicode(session: AsyncSession) -> None:
    """Patient free-text is Devanagari/Telugu; JSONB must return it unchanged."""
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()

    transcript = [
        {"role": "assistant", "text": "आपको क्या तकलीफ है?", "at": "2026-07-15T09:00:00Z"},
        {"role": "patient", "text": "पेट में दर्द है", "text_en": "I have abdominal pain"},
    ]
    intake = f.make_intake(
        visit,
        transcript=transcript,
        summary_lang_versions={"hi": "पेट में दर्द", "te": "కడుపు నొప్పి"},
        red_flags=[{"rule": "onc.fever.high", "severity": "urgent"}],
    )
    session.add(intake)
    await session.flush()
    session.expunge(intake)

    fetched = await session.get(Intake, intake.id)
    assert fetched is not None
    assert fetched.transcript == transcript
    assert fetched.summary_lang_versions["te"] == "కడుపు నొప్పి"
    assert fetched.red_flags[0]["severity"] == "urgent"


async def test_enum_columns_store_values_not_names(session: AsyncSession) -> None:
    """The API and admin console speak "in_queue", not "IN_QUEUE"."""
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], status=VisitStatus.IN_QUEUE)
    session.add(visit)
    await session.flush()

    raw = await session.execute(
        select(Visit.__table__.c.status, Visit.__table__.c.channel).where(
            Visit.__table__.c.id == visit.id
        )
    )
    status, channel = raw.one()
    assert status == "in_queue"
    assert channel == "kiosk"


async def test_duplicate_mrn_is_rejected(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    session.add(f.make_patient(clinic["hospital"], mrn=clinic["patient"].mrn))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_token_number_is_unique_per_department_per_day(session: AsyncSession) -> None:
    """The constraint that keeps offline kiosk tokens from colliding on sync."""
    clinic = await f.build_clinic(session)
    day = date(2026, 7, 15)

    session.add(f.make_visit(clinic["patient"], clinic["department"], date=day, token_no=42))
    await session.flush()

    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    session.add(f.make_visit(other, clinic["department"], date=day, token_no=42))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_same_token_allowed_on_a_different_day(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)

    session.add(
        f.make_visit(clinic["patient"], clinic["department"], date=date(2026, 7, 15), token_no=7)
    )
    session.add(
        f.make_visit(clinic["patient"], clinic["department"], date=date(2026, 7, 16), token_no=7)
    )
    await session.flush()  # must not raise


async def test_question_tree_versioning(session: AsyncSession) -> None:
    """Trees are data: same key, many versions, one published (S4/S18).

    Keyed (key, version) since S4 — every language lives inside the JSONB, so a
    tree is one row per version rather than one per language (see
    `app.models.content.QuestionTree` and `app.trees.schema`).
    """
    clinic = await f.build_clinic(session)
    node = {
        "id": "onc.pain.location",
        "type": "single",
        "text": {"en": "Where is the pain?", "hi": "दर्द कहाँ है?"},
    }

    v1 = QuestionTree(
        department_id=clinic["department"].id,
        key="onc_pain",
        version=1,
        tree={"nodes": [node]},
        status=TreeStatus.PUBLISHED,
        published_at=datetime.now(UTC),
    )
    v2 = QuestionTree(
        department_id=clinic["department"].id,
        key="onc_pain",
        version=2,
        tree={"nodes": [node]},
        status=TreeStatus.DRAFT,
    )
    session.add_all([v1, v2])
    await session.flush()

    rows = await session.execute(
        select(QuestionTree).where(QuestionTree.key == "onc_pain").order_by(QuestionTree.version)
    )
    trees = list(rows.scalars())
    assert [t.version for t in trees] == [1, 2]
    assert [t.status for t in trees] == [TreeStatus.PUBLISHED, TreeStatus.DRAFT]


async def test_duplicate_tree_version_is_rejected(session: AsyncSession) -> None:
    """(key, version) is unique — publishing over a version rather than adding one
    would silently re-interpret every intake that cited it."""
    clinic = await f.build_clinic(session)
    for _ in range(2):
        session.add(
            QuestionTree(
                department_id=clinic["department"].id,
                key="onc_pain",
                version=1,
                tree={},
            )
        )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_prescription_defaults_are_empty_not_null(session: AsyncSession) -> None:
    """JSONB containers default to empty so consumers never branch on None."""
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()

    rx = Prescription(visit_id=visit.id)
    session.add(rx)
    await session.flush()
    session.expunge(rx)

    fetched = await session.get(Prescription, rx.id)
    assert fetched is not None
    assert fetched.meds == []
    assert fetched.delivered_via == {}


async def test_intake_tier_ladder_values(session: AsyncSession) -> None:
    """The V1→V2→V3 downgrade ladder (doc 02 §2) as stored values."""
    assert IntakeTier.CONVERSATIONAL.value == "conversational"
    assert IntakeTier.RULE_BASED.value == "rule_based"
    assert IntakeTier.PRERECORDED.value == "prerecorded"
    assert IntakeTier.PAPER.value == "paper"


async def test_department_code_unique_within_hospital_only(session: AsyncSession) -> None:
    """Two hospitals may both have a MEDONC; one hospital may not have two."""
    h1 = f.make_hospital()
    h2 = f.make_hospital()
    session.add_all([h1, h2])
    await session.flush()

    session.add(f.make_department(h1, code="MEDONC"))
    session.add(f.make_department(h2, code="MEDONC"))
    await session.flush()  # different hospitals: fine

    session.add(f.make_department(h1, code="MEDONC"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_hospital_lists_its_departments(session: AsyncSession) -> None:
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    session.add_all([f.make_department(hospital) for _ in range(3)])
    await session.flush()

    result = await session.execute(
        select(Hospital)
        .where(Hospital.id == hospital.id)
        .options(selectinload(Hospital.departments))
    )
    assert len(result.scalar_one().departments) == 3


async def test_channel_covers_every_intake_source(session: AsyncSession) -> None:
    """Doc 02 §4: kiosk, phone, whatsapp, app, paper — paper is the downtime path."""
    clinic = await f.build_clinic(session)
    for channel in Channel:
        session.add(f.make_visit(clinic["patient"], clinic["department"], channel=channel))
    await session.flush()

    rows = await session.execute(select(Visit).where(Visit.patient_id == clinic["patient"].id))
    assert {v.channel for v in rows.scalars()} == set(Channel)


async def test_department_relationship_to_doctors(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    result = await session.execute(
        select(Department)
        .where(Department.id == clinic["department"].id)
        .options(selectinload(Department.doctors))
    )
    assert [d.id for d in result.scalar_one().doctors] == [clinic["doctor"].id]
