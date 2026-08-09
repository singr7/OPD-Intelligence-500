"""Idempotent seed loader for the pilot dataset (`make seed`).

Reads `seeds/*.json` and generates fake patients. Every entity is matched on a
natural key (see seeds/README.md), so running this twice is the same as running
it once — which is what makes it safe to wire into a box rebuild rather than a
one-shot bootstrap.

## `seeds/*.json` describes a box nobody has set up yet

For the rows a person can edit from a console — the hospital, its departments,
staff users, doctors, clinic templates — this file **creates what is missing and
never overwrites what it finds**. Adding a department or a doctor to the file and
re-running is still how new reference data arrives; editing one that already
exists changes nothing on a box where somebody has already set it up.

That is a reversal, and a deliberate one. Overwriting was harmless while this
file was the only thing that could write those rows. It stopped being harmless
when the console could: `PATCH /admin/hospital` and the department editor
(SESSION-AYUR-1), staff onboarding and the two-step deactivation (S-GL.2). A
hand-run `make seed` would have put back the seeded hospital name — taking the
letterhead and every intake pass with it — reopened a department an
administrator had closed, and reactivated a doctor they had retired, silently.

Rows it leaves alone *because they differ* are reported as `kept`, so the
operator sees that the run noticed and stood down. The file is still validated on
every run whether or not it is written, so a typo in an existing department's
`care_system` is a loud failure and not a quiet no-op.

Patients are exempt (generated demo data, no console). So are the price book, the
tree bank and the protocol bank — versioned or append-only content with editors
of their own.

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
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from faker import Faker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import facility
from app.audit import Actor, acting_as
from app.care_system import care_system_of
from app.checkins import protocols
from app.config import get_settings
from app.db import build_engine, build_sessionmaker
from app.models.content import ProtocolBankVersion, QuestionTree
from app.models.enums import Lang, PriceUnit, Role, Sex, SlotType, TreeStatus
from app.models.metering import PriceBook
from app.models.org import Department, Doctor, Hospital, User
from app.models.patient import Patient
from app.models.scheduling import SlotTemplate
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
    #: Rows this file describes differently from the database, and did **not**
    #: touch, because a person had set them up by hand (see `_console_owned`).
    #: Reported rather than silent: an operator who renamed the hospital in the
    #: console needs to see that `make seed` noticed and stood down, not wonder
    #: afterwards whether it had quietly won.
    kept: dict[str, int]

    @classmethod
    def empty(cls) -> SeedReport:
        return cls(created={}, updated={}, unchanged={}, kept={})

    def record(self, bucket: dict[str, int], entity: str) -> None:
        bucket[entity] = bucket.get(entity, 0) + 1

    @property
    def changed_anything(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        def fmt(bucket: dict[str, int]) -> str:
            return ", ".join(f"{k}={v}" for k, v in sorted(bucket.items())) or "none"

        lines = [
            f"created: {fmt(self.created)}",
            f"updated: {fmt(self.updated)}",
            f"unchanged: {fmt(self.unchanged)}",
        ]
        if self.kept:
            lines.append(f"kept (yours, not this file's): {fmt(self.kept)}")
        return "\n".join(lines)


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


def _differs(obj: object, values: dict[str, Any]) -> list[str]:
    """Which of `values` this row disagrees with. Reads; never writes."""
    return sorted(field for field, value in values.items() if getattr(obj, field) != value)


def _console_owned(
    existing: object | None, values: dict[str, Any], entity: str, report: SeedReport
) -> bool:
    """True when this row exists already and the seed must leave it alone.

    **The rule: `seeds/*.json` describes a box that has not been set up yet.**
    It creates what is missing — including a department or a member of staff
    added to the file later, which is why re-running it is still how new
    reference data arrives — and it never overwrites a row that is already
    there.

    Before this, every run rewrote these rows from the file. That was harmless
    while the file was the only thing that could write them. It stopped being
    harmless the moment a console could: SESSION-AYUR-1 gave an administrator
    `PATCH /admin/hospital` and a department editor, S-GL.2 gave them staff
    onboarding and a two-step deactivation, and a hand-run `make seed` would
    have put back the seeded hospital name, reopened a department they closed,
    and reactivated a doctor they had retired — silently, and with the
    deactivation's whole point (patients booked with that doctor) undone.

    Applies to the rows a person can edit from a console: the hospital, its
    departments, staff users, doctors and clinic templates. **Not** to patients
    (generated demo data with no console), nor to the price book, the tree bank
    or the protocol bank, which are versioned or append-only content with
    editors of their own.

    The file is still *validated* on every run whether or not it is written —
    the caller parses its values before asking this — so a typo introduced for
    an existing department is still a loud failure rather than a quiet no-op.
    """
    if existing is None:
        return False
    differs = _differs(existing, values)
    if differs:
        report.record(report.kept, entity)
        logger.info(
            "seed: keeping the %s already set up here; %s differ from seeds/*.json",
            entity,
            ", ".join(differs),
        )
    else:
        report.record(report.unchanged, entity)
    return True


async def _upsert_hospital(
    session: AsyncSession, data: dict[str, Any], report: SeedReport
) -> Hospital:
    result = await session.execute(select(Hospital).where(Hospital.code == data["code"]))
    hospital = result.scalar_one_or_none()
    values = {
        "name": data["name"],
        # Parsed through the same validator the admin console writes behind, so
        # a seed file cannot put a name in this column that the console would
        # have refused — an English placeholder sitting in the `hi` slot, say.
        "name_i18n": facility.parse_name_i18n(data.get("name_i18n")),
        "city": data["city"],
        "district": data["district"],
        "default_lang": Lang(data["default_lang"]),
    }

    if _console_owned(hospital, values, "hospital", report):
        assert hospital is not None
        return hospital

    hospital = Hospital(code=data["code"], **values)
    session.add(hospital)
    await session.flush()
    report.record(report.created, "hospital")
    return hospital


async def _upsert_departments(
    session: AsyncSession, hospital: Hospital, rows: list[dict[str, Any]], report: SeedReport
) -> dict[str, Department]:
    result = await session.execute(select(Department).where(Department.hospital_id == hospital.id))
    existing = {d.code: d for d in result.scalars()}

    departments: dict[str, Department] = {}
    for row in rows:
        values = {
            "name": row["name"],
            "icon": row["icon"],
            # Was hard-coded `True`. A seed file may now say otherwise, and doc
            # 24's AYUR department is the first that does: a department is
            # offered on the kiosk chooser the moment it is active, and one
            # offered before its intake trees exist is a card that routes a
            # patient into a 500. Absent, this still reads `True`, so the nine
            # oncology departments are seeded exactly as before.
            "active": row.get("active", True),
            # Absent means allopathy (doc 24 §3.4) — that is what every
            # department authored before the second system of medicine is, and
            # it keeps a third-party `hospital.json` loading unchanged. A
            # *misspelt* value raises rather than defaulting.
            "care_system": care_system_of(row.get("care_system")),
        }
        dept = existing.get(row["code"])
        if not _console_owned(dept, values, "department", report):
            dept = Department(hospital_id=hospital.id, code=row["code"], **values)
            session.add(dept)
            report.record(report.created, "department")
        assert dept is not None
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

    if not _console_owned(user, values, "user", report):
        user = User(phone=row["phone"], **values)
        session.add(user)
        report.record(report.created, "user")
    assert user is not None

    await session.flush()
    await _seed_kiosk_pin(session, user, row.get("kiosk_pin"))
    return user


async def _seed_kiosk_pin(session: AsyncSession, user: User, pin: str | None) -> None:
    """Give a seeded coordinator the pilot's kiosk PIN, once and never again.

    Two rules, both deliberate:

    * **Only when the user has none.** The seed is idempotent and re-run on every
      deploy; overwriting would silently reset a PIN an operator had rotated and
      hand the corridor back a value that is printed in this repository.
    * **Never in production.** This PIN is committed and world-readable. A box
      serving real patients gets its PIN from `scripts/set_kiosk_pin.py`, typed by
      a human who is not this file.
    """
    if not pin or user.kiosk_pin_hash is not None:
        return

    from app.auth import kiosk_pin as kp

    # `is_local` is the repository's existing boundary (local/test). Inverting it
    # rather than testing for "production" by name means staging and pilot boxes
    # are covered too — anywhere a real patient could walk up to the kiosk.
    if not get_settings().is_local:
        logger.warning(
            "refusing to seed a kiosk PIN for %s outside local/test — "
            "set one with scripts/set_kiosk_pin.py",
            user.phone,
        )
        return
    try:
        await kp.set_pin(session, user=user, pin=pin)
    except kp.PinError as exc:
        logger.warning("seeded kiosk PIN for %s rejected: %s", user.phone, exc)


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

        if not _console_owned(doctor, values, "doctor", report):
            session.add(Doctor(reg_no=row["reg_no"], **values))
            report.record(report.created, "doctor")

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


async def _upsert_slot_templates(
    session: AsyncSession, rows: list[dict[str, Any]], report: SeedReport
) -> None:
    """The pilot's OPD clinic grid (S15, doc 03 §2).

    Templates only — no `appointment_slots` are generated here. Inventory is
    materialised for a date range by `app.scheduling.generate_slots` (the nightly
    beat job, or `python -m app.scheduling` by hand), because a seed that also
    wrote 60 days of slots would be a seed that quietly ages.
    """
    result = await session.execute(select(Doctor))
    doctors = {doctor.reg_no: doctor for doctor in result.scalars()}

    for row in rows:
        doctor = doctors.get(row["doctor_reg_no"])
        if doctor is None:
            raise ValueError(
                f"slot_templates.json references doctor {row['doctor_reg_no']!r}, "
                f"which is not in doctors.json"
            )
        for clinic in row["clinics"]:
            start_time = time.fromisoformat(clinic["start_time"])
            slot_type = SlotType(clinic["slot_type"])
            found = await session.execute(
                select(SlotTemplate).where(
                    SlotTemplate.doctor_id == doctor.id,
                    SlotTemplate.weekday == clinic["weekday"],
                    SlotTemplate.start_time == start_time,
                    SlotTemplate.slot_type == slot_type,
                )
            )
            template = found.scalar_one_or_none()
            values = {
                "department_id": doctor.department_id,
                "end_time": time.fromisoformat(clinic["end_time"]),
                "slot_minutes": clinic["slot_minutes"],
                "capacity": clinic["capacity"],
                "active": True,
            }
            if not _console_owned(template, values, "slot_template", report):
                session.add(
                    SlotTemplate(
                        doctor_id=doctor.id,
                        weekday=clinic["weekday"],
                        start_time=start_time,
                        slot_type=slot_type,
                        **values,
                    )
                )
                report.record(report.created, "slot_template")

    await session.flush()


async def _upsert_protocol_bank(session: AsyncSession, report: SeedReport) -> None:
    """Seed `protocol_banks` from `seeds/protocols.json` (doc 03 §9, S18-late).

    The bank moved into a table so the admin console can edit it; the file stays
    the authored source and the runtime floor (`app.checkins.store.resolve_bank`),
    exactly as `seeds/trees/` does. Loaded through `protocols.parse`, so a seed
    run is also a validation run.

    **Seeded as draft**, for the same reason the trees are: publishing is a
    clinical act. This bank rings a doctor's phone at thresholds no oncologist has
    signed off yet (S21), and a seed script marking it published would be this
    system asserting a review that has not happened. Until somebody publishes,
    the resolver falls through to the identical file — so nothing changes.
    """
    payload = _load("protocols.json")
    protocols.parse(payload)  # refuse to seed a bank the grader could not read
    version = int(payload["version"])

    row = await session.scalar(
        select(ProtocolBankVersion).where(ProtocolBankVersion.version == version)
    )
    if row is None:
        session.add(ProtocolBankVersion(version=version, bank=payload))
        report.record(report.created, "protocol_bank")
    elif _apply(row, {"bank": payload}):
        report.record(report.updated, "protocol_bank")
    else:
        report.record(report.unchanged, "protocol_bank")
    await session.flush()


async def seed(
    session: AsyncSession, *, patients: int = 50, publish_trees: bool = False
) -> SeedReport:
    """Load the pilot dataset into `session`. Caller owns the commit."""
    report = SeedReport.empty()

    hospital_data = _load("hospital.json")
    staff_data = _load("doctors.json")
    price_data = _load("price_book.json")
    slot_data = _load("slot_templates.json")

    with acting_as(SEED_ACTOR):
        hospital = await _upsert_hospital(session, hospital_data, report)
        departments = await _upsert_departments(
            session, hospital, hospital_data["departments"], report
        )
        for row in staff_data["staff"]:
            await _upsert_user(session, hospital, row, report)
        await _upsert_doctors(session, hospital, departments, staff_data["doctors"], report)
        await _upsert_slot_templates(session, slot_data["templates"], report)
        await _upsert_patients(session, hospital, patients, report)
        await _upsert_price_book(session, price_data["entries"], report)
        await _upsert_trees(session, departments, report, publish=publish_trees)
        await _upsert_protocol_bank(session, report)

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
