"""Seed loader: correct contents, and idempotent on re-run (S2 AC)."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.care_system import CareSystemError
from app.models.audit import AuditLog
from app.models.content import QuestionTree
from app.models.enums import CareSystem, Role, TreeStatus
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import Patient
from app.seed import SeedReport, seed
from app.trees.bank import load_bank
from app.trees.schema import parse


async def _count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_seed_loads_the_pilot_dataset(session: AsyncSession) -> None:
    report = await seed(session, patients=50)

    hospital = (await session.execute(select(Hospital))).scalar_one()
    assert hospital.code == "ALWAR01"
    assert hospital.city == "Alwar"

    # The departments doc 03 §3 names: 4 oncology + 5 routing. Plus doc 24's
    # AYUR, the platform's second system of medicine — seeded inactive from
    # AYUR-0 until AYUR-2 authored its intake trees, and open since, because a
    # department nobody can be routed to is the one thing an active card must
    # never mean.
    departments = list((await session.execute(select(Department))).scalars())
    assert len(departments) == 10
    assert len([d for d in departments if d.active]) == 10
    assert {"MEDONC", "RADONC", "SURGONC", "PALL"} <= {d.code for d in departments}

    assert await _count(session, Doctor) == 5
    assert await _count(session, Patient) == 50

    # 5 doctors + 3 non-doctor staff, each with a login identity.
    assert await _count(session, User) == 8
    doctors = list((await session.execute(select(User).where(User.role == Role.DOCTOR))).scalars())
    assert len(doctors) == 5

    assert report.created["patient"] == 50
    assert report.created["doctor"] == 5


async def test_every_seeded_department_states_its_system_of_medicine(
    session: AsyncSession,
) -> None:
    """Doc 24 §3.4. Nine allopathy, one ayurveda, and the ayurveda one is open.

    It was seeded dark from AYUR-0 to AYUR-1 for a reason worth keeping in view:
    `Department.active` is what the kiosk chooser, the classifier and the admin
    department list all filter on, and a department with no intake tree puts
    "Ayurveda" in front of a patient and then fails the assert in
    `routes/kiosk.py` when they tap it. SESSION-AYUR-2 authored the five trees of
    doc 24 §5, so the condition that held it closed no longer holds — and
    `facility._assert_has_intake` now enforces the rule itself rather than a
    comment asking the next session to remember. What this seed does **not**
    settle is the clinical review: doc 24 §9 makes BAMS sign-off a launch gate,
    and a real deployment closes this department in the console until a
    practitioner has read the trees.
    """
    await seed(session, patients=1)
    departments = {d.code: d for d in (await session.execute(select(Department))).scalars()}

    ayurveda = departments["AYUR"]
    assert ayurveda.care_system is CareSystem.AYURVEDA
    assert ayurveda.active is True
    assert [t for t in load_bank().values() if t.department == "AYUR"], (
        "an open department must have something to ask"
    )

    others = [d for code, d in departments.items() if code != "AYUR"]
    assert all(d.care_system is CareSystem.ALLOPATHY for d in others)
    assert all(d.active for d in others)


async def test_a_department_that_does_not_say_is_allopathy(session: AsyncSession) -> None:
    """A `hospital.json` written before doc 24 keeps loading, and its departments
    are what they have always been — no backfill, no reclassification."""
    from app.seed import _upsert_departments

    hospital = Hospital(code="OLD01", name="Somewhere", city=None, district=None)
    session.add(hospital)
    await session.flush()

    report = SeedReport.empty()
    departments = await _upsert_departments(
        session,
        hospital,
        [{"code": "GENMED", "name": "General Medicine", "icon": "stethoscope"}],
        report,
    )
    assert departments["GENMED"].care_system is CareSystem.ALLOPATHY
    assert departments["GENMED"].active is True


async def test_a_misspelt_system_of_medicine_is_refused_not_defaulted(
    session: AsyncSession,
) -> None:
    """The other half of the default: silence means allopathy, a typo means a
    mistake. Defaulting "ayurved" would hand an ayurveda clinic the oncology
    prompt pack and the chemo check-in machinery, and look right on every screen.
    """
    hospital = Hospital(code="OLD02", name="Somewhere", city=None, district=None)
    session.add(hospital)
    await session.flush()

    from app.seed import _upsert_departments

    with pytest.raises(CareSystemError):
        await _upsert_departments(
            session,
            hospital,
            [{"code": "AYUR", "name": "Ayurveda", "icon": "leaf", "care_system": "ayurved"}],
            SeedReport.empty(),
        )


async def test_running_seed_twice_changes_nothing(session: AsyncSession) -> None:
    """AC: seeds load idempotently — a rebuild must not duplicate the hospital."""
    await seed(session, patients=50)
    counts = {
        model: await _count(session, model)
        for model in (Hospital, Department, Doctor, User, Patient)
    }

    second = await seed(session, patients=50)

    for model, before in counts.items():
        assert await _count(session, model) == before, f"{model.__name__} was duplicated"

    assert not second.changed_anything, f"second run wrote something:\n{second.summary()}"
    assert second.unchanged["patient"] == 50


async def test_second_run_writes_no_audit_rows(session: AsyncSession) -> None:
    """A no-op re-run must not spam the append-only log — those rows are forever."""
    await seed(session, patients=10)
    before = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()

    await seed(session, patients=10)
    after = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()

    assert after == before


async def test_seeded_patients_are_audited_as_the_seed_actor(session: AsyncSession) -> None:
    await seed(session, patients=5)

    rows = list(
        (await session.execute(select(AuditLog).where(AuditLog.entity == "patients"))).scalars()
    )
    assert len(rows) == 5
    assert {r.actor_label for r in rows} == {"seed"}
    assert all(r.actor_id is None for r in rows)


async def test_seed_is_deterministic(session: AsyncSession) -> None:
    """Fixed Faker seed ⇒ the same patients everywhere, so bugs reproduce."""
    await seed(session, patients=10)

    patients = list((await session.execute(select(Patient).order_by(Patient.mrn))).scalars())
    assert [p.mrn for p in patients] == [f"OPD{i:06d}" for i in range(1, 11)]

    first_pass = [(p.mrn, p.name, p.age, p.district) for p in patients]

    # Re-running regenerates the same values, so nothing is reported as changed.
    report = await seed(session, patients=10)
    assert not report.changed_anything

    again = list((await session.execute(select(Patient).order_by(Patient.mrn))).scalars())
    assert [(p.mrn, p.name, p.age, p.district) for p in again] == first_pass


async def test_a_hand_set_up_hospital_survives_a_reseed(session: AsyncSession) -> None:
    """The seed matches on a natural key and never inserts a rival — and, since
    SESSION-AYUR-1's follow-up, never overwrites the row it found either.

    This test used to assert the opposite: that a hand-renamed hospital was put
    back from `seeds/hospital.json`. That was safe while the file was the only
    thing that could write the row. It stopped being safe when the admin console
    gained `PATCH /admin/hospital` — a hospital renamed to "Ayurveda Hospital"
    would have reverted on the next hand-run `make seed`, taking the letterhead
    and every intake pass back with it, and nothing would have said so.

    The reversal is the operator's decision, recorded in HANDOFF after AYUR-1
    asked for it: `seeds/*.json` describes a box nobody has set up yet.
    """
    await seed(session, patients=5)
    hospital = (await session.execute(select(Hospital))).scalar_one()
    hospital.name = "Renamed By Hand"
    await session.flush()

    report = await seed(session, patients=5)

    assert await _count(session, Hospital) == 1
    refreshed = (await session.execute(select(Hospital))).scalar_one()
    assert refreshed.name == "Renamed By Hand"
    # Kept, not "unchanged": the run noticed the disagreement and stood down,
    # and says so, rather than leaving an operator to wonder who won.
    assert report.kept.get("hospital") == 1
    assert not report.changed_anything


async def test_the_seed_still_creates_reference_data_added_to_the_file(
    session: AsyncSession,
) -> None:
    """The other half of the rule, and the reason re-running the seed is still
    how new reference data arrives: it creates what is missing. Only rows that
    already exist are left alone."""
    from app.seed import _upsert_departments

    hospital = Hospital(code="NEW01", name="Somewhere", city=None, district=None)
    session.add(hospital)
    await session.flush()
    report = SeedReport.empty()

    await _upsert_departments(
        session, hospital, [{"code": "AYUR", "name": "Ayurveda", "icon": "leaf"}], report
    )
    # A later edition of the file adds one, and renames the first.
    await _upsert_departments(
        session,
        hospital,
        [
            {"code": "AYUR", "name": "Ayurveda (renamed in the file)", "icon": "leaf"},
            {"code": "PANCH", "name": "Panchakarma", "icon": "leaf"},
        ],
        report,
    )

    rows = {
        d.code: d
        for d in (
            await session.execute(select(Department).where(Department.hospital_id == hospital.id))
        ).scalars()
    }
    assert set(rows) == {"AYUR", "PANCH"}
    assert rows["AYUR"].name == "Ayurveda", "an existing department is left alone"
    assert report.created["department"] == 2


async def test_a_misspelt_system_of_medicine_is_refused_even_for_a_row_it_will_not_write(
    session: AsyncSession,
) -> None:
    """The file is validated on every run whether or not it is written.

    Otherwise "create only" would have turned the seed into a silent no-op for
    exactly the rows most likely to carry a typo — the ones that have been in
    the file long enough for somebody to edit them.
    """
    from app.seed import _upsert_departments

    hospital = Hospital(code="NEW02", name="Somewhere", city=None, district=None)
    session.add(hospital)
    await session.flush()
    report = SeedReport.empty()
    await _upsert_departments(
        session, hospital, [{"code": "AYUR", "name": "Ayurveda", "icon": "leaf"}], report
    )

    with pytest.raises(CareSystemError):
        await _upsert_departments(
            session,
            hospital,
            [{"code": "AYUR", "name": "Ayurveda", "icon": "leaf", "care_system": "ayurved"}],
            report,
        )


async def test_patient_count_is_configurable(session: AsyncSession) -> None:
    await seed(session, patients=5)
    assert await _count(session, Patient) == 5

    # Growing the dataset adds only the new patients.
    report = await seed(session, patients=8)
    assert await _count(session, Patient) == 8
    assert report.created["patient"] == 3
    assert report.unchanged["patient"] == 5


async def test_doctors_are_linked_to_users_and_departments(session: AsyncSession) -> None:
    await seed(session, patients=1)

    doctors = list((await session.execute(select(Doctor))).scalars())
    assert len(doctors) == 5
    for doctor in doctors:
        user = await session.get(User, doctor.user_id)
        assert user is not None
        assert user.role is Role.DOCTOR
        assert user.phone == doctor.phone
        assert await session.get(Department, doctor.department_id) is not None


async def test_seeded_phone_numbers_cannot_reach_a_real_handset(session: AsyncSession) -> None:
    """Seeds land on demo boxes that may have a live SMS provider from S3 on.

    Indian mobile numbers start 6-9, so a +91 5xxxxxxxxx number is unroutable by
    construction — no stranger gets an OTP because someone seeded staging.
    """
    await seed(session, patients=20)

    users = list((await session.execute(select(User))).scalars())
    patients = list((await session.execute(select(Patient))).scalars())
    assert users and patients

    for phone in [u.phone for u in users] + [p.phone for p in patients]:
        assert phone.startswith("+915"), f"{phone} could route to a real person"

    for patient in patients:
        if patient.caregiver_phone:
            assert patient.caregiver_phone.startswith("+915")


# -- question trees (S4) -------------------------------------------------------


async def test_seed_loads_the_tree_bank(session: AsyncSession) -> None:
    await seed(session, patients=5)

    rows = list((await session.execute(select(QuestionTree))).scalars())
    assert {row.key for row in rows} == set(load_bank())
    # The seeded version is the tree's own, not a constant: a clinical revision
    # (S-UX.6 rewrote the gynae and general-medicine routing) bumps it, and a
    # row that still claimed v1 would be a published tree lying about what an
    # oncologist reviewed.
    bank = load_bank()
    assert {row.key: row.version for row in rows} == {
        key: tree.version for key, tree in bank.items()
    }


async def test_seeded_trees_are_still_valid_trees_after_the_round_trip(
    session: AsyncSession,
) -> None:
    """The JSONB in the database must parse back into the tree it came from — the
    column is what S18's editor and the eventual live path read, not the file."""
    await seed(session, patients=5)

    for row in (await session.execute(select(QuestionTree))).scalars():
        tree = parse(row.tree)
        assert tree.key == row.key
        assert tree.version == row.version


async def test_trees_seed_as_draft_because_publishing_is_a_clinical_act(
    session: AsyncSession,
) -> None:
    """doc 03 §3: the bank is "clinically reviewed before go-live" (S21). A seed
    script that published would make `status` mean nothing."""
    await seed(session, patients=5)

    rows = list((await session.execute(select(QuestionTree))).scalars())
    assert rows
    assert all(row.status is TreeStatus.DRAFT for row in rows)
    assert all(row.published_at is None for row in rows)


async def test_trees_can_be_published_explicitly(session: AsyncSession) -> None:
    await seed(session, patients=5, publish_trees=True)

    rows = list((await session.execute(select(QuestionTree))).scalars())
    assert rows
    assert all(row.status is TreeStatus.PUBLISHED for row in rows)
    assert all(row.published_at is not None for row in rows)


async def test_every_tree_is_linked_to_its_department(session: AsyncSession) -> None:
    """A tree pointing at no department is a patient routed to a desk with nothing
    to ask them."""
    await seed(session, patients=5)

    bank = load_bank()
    for row in (await session.execute(select(QuestionTree))).scalars():
        department = await session.get(Department, row.department_id)
        assert department is not None, f"{row.key} has no department"
        assert department.code == bank[row.key].department


async def test_reseeding_trees_changes_nothing(session: AsyncSession) -> None:
    await seed(session, patients=5)
    report = await seed(session, patients=5)

    assert "question_trees" not in report.created
    assert "question_trees" not in report.updated
    assert report.unchanged["question_trees"] == len(load_bank())
