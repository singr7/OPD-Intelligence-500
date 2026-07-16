"""Idempotent seed loader for the pilot dataset (`make seed`).

Reads `seeds/*.json` and generates fake patients. Every entity is matched on a
natural key (see seeds/README.md), so running this twice is the same as running
it once — which is what makes it safe to wire into a box rebuild rather than a
one-shot bootstrap.

Writes run as the `seed` actor, so the patients it creates carry audit rows
attributed to seeding rather than to a person.

    python -m app.seed                 # 50 patients (default)
    python -m app.seed --patients 200  # load-test sized
    python -m app.seed --dry-run       # report what would change
    python -m app.seed --publish-trees # publish the trees too (they seed as draft)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from faker import Faker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import Actor, acting_as
from app.db import build_engine, build_sessionmaker
from app.models.content import QuestionTree
from app.models.enums import Lang, PriceUnit, Role, Sex, TreeStatus
from app.models.metering import PriceBook
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import Patient
from app.trees.bank import TREES_DIR, load_bank

logger = logging.getLogger("seed")

SEEDS_DIR = Path(__file__).resolve().parents[2] / "seeds"

# Fixed seed ⇒ identical patients on every machine and every run. A generated
# dataset that differs per developer makes bug reports irreproducible.
FAKER_SEED = 20260715

SEED_ACTOR = Actor(label="seed")

# Rural Alwar catchment (doc 01 §2: patients travel 50-200km, often from villages).
DISTRICTS = ["Alwar", "Bharatpur", "Dausa", "Rajgarh", "Behror", "Tijara", "Kishangarh Bas"]


@dataclass
class SeedReport:
    """What the run changed. Printed at the end and asserted on in tests."""

    created: dict[str, int]
    updated: dict[str, int]
    unchanged: dict[str, int]

    @classmethod
    def empty(cls) -> SeedReport:
        return cls(created={}, updated={}, unchanged={})

    def record(self, bucket: dict[str, int], entity: str) -> None:
        bucket[entity] = bucket.get(entity, 0) + 1

    @property
    def changed_anything(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        def fmt(bucket: dict[str, int]) -> str:
            return ", ".join(f"{k}={v}" for k, v in sorted(bucket.items())) or "none"

        return (
            f"created: {fmt(self.created)}\n"
            f"updated: {fmt(self.updated)}\n"
            f"unchanged: {fmt(self.unchanged)}"
        )


def _load(name: str) -> dict[str, Any]:
    return json.loads((SEEDS_DIR / name).read_text())


def _apply(obj: object, values: dict[str, Any]) -> bool:
    """Set only the fields that actually differ.

    Returning early on no-op keeps the object out of `session.dirty`, which is
    what stops a re-run from writing pointless audit rows.
    """
    changed = False
    for field, value in values.items():
        if getattr(obj, field) != value:
            setattr(obj, field, value)
            changed = True
    return changed


async def _upsert_hospital(
    session: AsyncSession, data: dict[str, Any], report: SeedReport
) -> Hospital:
    result = await session.execute(select(Hospital).where(Hospital.code == data["code"]))
    hospital = result.scalar_one_or_none()
    values = {
        "name": data["name"],
        "city": data["city"],
        "district": data["district"],
        "default_lang": Lang(data["default_lang"]),
    }

    if hospital is None:
        hospital = Hospital(code=data["code"], **values)
        session.add(hospital)
        await session.flush()
        report.record(report.created, "hospital")
    elif _apply(hospital, values):
        report.record(report.updated, "hospital")
    else:
        report.record(report.unchanged, "hospital")
    return hospital


async def _upsert_departments(
    session: AsyncSession, hospital: Hospital, rows: list[dict[str, Any]], report: SeedReport
) -> dict[str, Department]:
    result = await session.execute(select(Department).where(Department.hospital_id == hospital.id))
    existing = {d.code: d for d in result.scalars()}

    departments: dict[str, Department] = {}
    for row in rows:
        values = {"name": row["name"], "icon": row["icon"], "active": True}
        dept = existing.get(row["code"])
        if dept is None:
            dept = Department(hospital_id=hospital.id, code=row["code"], **values)
            session.add(dept)
            report.record(report.created, "department")
        elif _apply(dept, values):
            report.record(report.updated, "department")
        else:
            report.record(report.unchanged, "department")
        departments[row["code"]] = dept

    await session.flush()
    return departments


async def _upsert_user(
    session: AsyncSession, hospital: Hospital, row: dict[str, Any], report: SeedReport
) -> User:
    result = await session.execute(select(User).where(User.phone == row["phone"]))
    user = result.scalar_one_or_none()
    values = {
        "name": row["name"],
        "role": Role(row["role"]),
        "lang": Lang(row["lang"]),
        "hospital_id": hospital.id,
        "active": True,
        "username": row.get("username"),
    }

    if user is None:
        user = User(phone=row["phone"], **values)
        session.add(user)
        report.record(report.created, "user")
    elif _apply(user, values):
        report.record(report.updated, "user")
    else:
        report.record(report.unchanged, "user")

    await session.flush()
    return user


async def _upsert_doctors(
    session: AsyncSession,
    hospital: Hospital,
    departments: dict[str, Department],
    rows: list[dict[str, Any]],
    report: SeedReport,
) -> None:
    for row in rows:
        dept = departments[row["department_code"]]

        # A doctor is a User (login identity) plus a Doctor (clinical profile).
        user = await _upsert_user(
            session,
            hospital,
            {
                "name": row["name"],
                "phone": row["phone"],
                "role": Role.DOCTOR.value,
                "lang": row["lang"],
            },
            report,
        )

        result = await session.execute(select(Doctor).where(Doctor.reg_no == row["reg_no"]))
        doctor = result.scalar_one_or_none()
        values = {
            "user_id": user.id,
            "department_id": dept.id,
            "name": row["name"],
            "phone": row["phone"],
            "qualification": row["qualification"],
            "active": True,
        }

        if doctor is None:
            session.add(Doctor(reg_no=row["reg_no"], **values))
            report.record(report.created, "doctor")
        elif _apply(doctor, values):
            report.record(report.updated, "doctor")
        else:
            report.record(report.unchanged, "doctor")

    await session.flush()


async def _upsert_patients(
    session: AsyncSession, hospital: Hospital, count: int, report: SeedReport
) -> None:
    fake = Faker("en_IN")
    Faker.seed(FAKER_SEED)
    rng = random.Random(FAKER_SEED)

    result = await session.execute(select(Patient).where(Patient.hospital_id == hospital.id))
    existing = {p.mrn: p for p in result.scalars()}

    for i in range(1, count + 1):
        mrn = f"OPD{i:06d}"
        sex = rng.choice([Sex.MALE, Sex.FEMALE])
        name = fake.name_male() if sex is Sex.MALE else fake.name_female()
        # Caregiver as a first-class user (doc 01 §2) — most rural patients arrive
        # with one, and the caregiver usually operates the phone.
        has_caregiver = rng.random() < 0.6

        values = {
            "name": name,
            # Unroutable by construction, same reasoning as seeds/doctors.json.
            "phone": f"+9155519{i:05d}",
            "age": rng.randint(24, 78),
            "sex": sex,
            "lang": rng.choice([Lang.HI, Lang.HI, Lang.HI, Lang.EN, Lang.MR, Lang.TE]),
            "village": fake.city(),
            "district": rng.choice(DISTRICTS),
            "caregiver_name": fake.name() if has_caregiver else None,
            "caregiver_phone": f"+9155529{i:05d}" if has_caregiver else None,
            "consent_given_at": date(2026, 1, 1),
        }

        patient = existing.get(mrn)
        if patient is None:
            session.add(Patient(hospital_id=hospital.id, mrn=mrn, **values))
            report.record(report.created, "patient")
        elif _apply(patient, values):
            report.record(report.updated, "patient")
        else:
            report.record(report.unchanged, "patient")

    await session.flush()


async def _upsert_price_book(
    session: AsyncSession, rows: list[dict[str, Any]], report: SeedReport
) -> None:
    """Seed `price_book` (doc 02 §8).

    Natural key is (provider, model, unit, effective_from) — the same uniqueness
    the table enforces. Note what that means for a price change: you add a row
    with a later `effective_from`, you do not edit one. Editing in place would
    silently re-interpret every historical cost that was computed at the old rate
    (see app/providers/pricing.py), and the S18 invoice reconciliation would stop
    matching reality with no visible cause.

    Not `Clinical` — no patient is affected by a rate, so these writes are not
    audited. Admin edits in S18 will be, through the admin console's own trail.
    """
    result = await session.execute(select(PriceBook))
    existing = {(p.provider, p.model, p.unit, p.effective_from): p for p in result.scalars()}

    for row in rows:
        key = (
            row["provider"],
            row["model"],
            PriceUnit(row["unit"]),
            date.fromisoformat(row["effective_from"]),
        )
        values = {"price_inr": Decimal(row["price_inr"]), "notes": row.get("notes")}
        entry = existing.get(key)
        if entry is None:
            session.add(
                PriceBook(
                    provider=key[0], model=key[1], unit=key[2], effective_from=key[3], **values
                )
            )
            report.record(report.created, "price_book")
        elif _apply(entry, values):
            report.record(report.updated, "price_book")
        else:
            report.record(report.unchanged, "price_book")

    await session.flush()


async def _upsert_trees(
    session: AsyncSession,
    departments: dict[str, Department],
    report: SeedReport,
    *,
    publish: bool = False,
) -> None:
    """Seed `question_trees` from the authored bank in `seeds/trees/` (doc 03 §3).

    Natural key is (key, version) — the table's uniqueness since S4 dropped `lang`
    (every language lives inside the JSONB; see `app.models.content.QuestionTree`).
    Editing a tree's content and re-seeding therefore *replaces* that version in
    place, which is right while the bank is a file in a pull request and wrong the
    moment S18 lets someone edit a published tree in the console. Bump `version` in
    the file to keep the old one.

    **Seeded as draft.** Doc 03 §3: the bank is "clinically reviewed before
    go-live", and publishing is a clinical act — an oncologist's, in S21. A seed
    script asserting review happened would make `status` mean nothing. Pass
    `--publish-trees` for a dev box that wants live content; nothing in the engine
    reads `status`, because `app.trees.bank` loads the files directly.

    Not `Clinical` — authored content affects no patient by existing. S18's editor
    gets its own trail.
    """
    bank = load_bank()
    result = await session.execute(select(QuestionTree))
    existing = {(row.key, row.version): row for row in result.scalars()}

    for tree in sorted(bank.values(), key=lambda item: item.key):
        department = departments.get(tree.department) if tree.department else None
        if tree.department and department is None:
            # A tree pointing at a department that does not exist would route
            # patients to a desk with nobody at it.
            raise ValueError(
                f"tree {tree.ref} names department {tree.department!r}, "
                f"which is not in hospital.json ({sorted(departments)})"
            )

        raw = json.loads((TREES_DIR / f"{tree.key}.json").read_text())
        values: dict[str, Any] = {
            "department_id": department.id if department else None,
            "tree": raw,
        }
        if publish:
            values["status"] = TreeStatus.PUBLISHED
            values["published_at"] = datetime.now(UTC)

        row = existing.get((tree.key, tree.version))
        if row is None:
            session.add(QuestionTree(key=tree.key, version=tree.version, **values))
            report.record(report.created, "question_trees")
        elif _apply(row, values):
            report.record(report.updated, "question_trees")
        else:
            report.record(report.unchanged, "question_trees")

    await session.flush()


async def seed(
    session: AsyncSession, *, patients: int = 50, publish_trees: bool = False
) -> SeedReport:
    """Load the pilot dataset into `session`. Caller owns the commit."""
    report = SeedReport.empty()

    hospital_data = _load("hospital.json")
    staff_data = _load("doctors.json")
    price_data = _load("price_book.json")

    with acting_as(SEED_ACTOR):
        hospital = await _upsert_hospital(session, hospital_data, report)
        departments = await _upsert_departments(
            session, hospital, hospital_data["departments"], report
        )
        for row in staff_data["staff"]:
            await _upsert_user(session, hospital, row, report)
        await _upsert_doctors(session, hospital, departments, staff_data["doctors"], report)
        await _upsert_patients(session, hospital, patients, report)
        await _upsert_price_book(session, price_data["entries"], report)
        await _upsert_trees(session, departments, report, publish=publish_trees)

    return report


async def _main(patients: int, dry_run: bool, publish_trees: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    engine = build_engine()
    factory = build_sessionmaker(engine)

    try:
        async with factory() as session:
            report = await seed(session, patients=patients, publish_trees=publish_trees)
            if dry_run:
                await session.rollback()
                logger.info("dry run — rolled back\n%s", report.summary())
            else:
                await session.commit()
                logger.info(
                    "seed complete at %s\n%s", datetime.now(UTC).isoformat(), report.summary()
                )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the pilot seed dataset (idempotent).")
    parser.add_argument("--patients", type=int, default=50, help="fake patients to generate")
    parser.add_argument("--dry-run", action="store_true", help="report changes without committing")
    parser.add_argument(
        "--publish-trees",
        action="store_true",
        help="publish the seeded question trees (default: draft — publishing is a "
        "clinical decision, see doc 03 §3)",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.patients, args.dry_run, args.publish_trees))


if __name__ == "__main__":
    main()
