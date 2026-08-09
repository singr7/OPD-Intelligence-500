"""The hospital's own identity, and the departments it runs (doc 24 §7, AYUR-1).

Until this module, two facts a hospital owns about itself were only editable by
editing `seeds/hospital.json` on the box and re-running the seed: **what this
hospital is called**, and **which departments it runs**. Doc 24 needs both from a
console, because the platform's second system of medicine arrives as a
department an administrator marks `ayurveda` in a facility that may be called
"Ayurveda Hospital" — and neither of those is a deploy.

## Three shapes worth knowing

**There is one hospital.** Every other table hangs off it and the seed creates
exactly one; `identity()` reads it and `update_identity()` edits it. There is no
`POST /admin/hospital`, because a second hospital on this box is not a console
action — it is a second deployment.

**A change of system of medicine is confirmed, never silent.** Doc 24 §7 asks
for "explicit copy about what changes". That copy is *derived*: `care_system` is
turned into capability flags by `app.care_system`, so switching a department
changes exactly the flags `care_system.differences()` reports, and
`care_system_impact()` hands the console that list plus the two counts that make
it a decision — how many doctors have their console rearranged, and how many
published intake trees were authored for the system being left behind. Nothing
here writes a sentence about ayurveda: a third system's confirmation copy
arrives with its capabilities row and needs no code.

**Activating a department is guarded by its intake trees.** This is the load
bearing rule of the session. A department is offered on the kiosk chooser the
moment it is `active`, and `routes/kiosk.py` asserts a tree was resolved after
routing — so activating a department with no published tree and no tree on disk
is a patient tapping a card into a 500. `activate` therefore refuses, naming the
missing content. Doc 24's `AYUR` department is seeded dark for exactly this
reason and this is the check that keeps it dark until SESSION-AYUR-2 authors
`seeds/trees/ayurveda_*.json`.

## Not versioned

Same stance as `app.people`, and for the same reason: trees, the protocol bank
and the channel document are authored content with a review cycle, and a
hospital's name is not. The audit trail (`record_admin_action`) is what makes
these edits accountable instead.

## What this module does not fix

`app.seed` still overwrites a hospital's name and a department's row from
`seeds/hospital.json` on every run, so `make seed` reverts an edit made here.
`make deploy` does not run the seed, so this only bites an operator who runs it
by hand — but it is a real trap and it is written up in HANDOFF rather than
quietly fixed, because fixing it means changing a test that exists precisely to
assert the current behaviour.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_admin_action
from app.care_system import (
    CapabilityChange,
    CareSystemError,
    capabilities_for,
    care_system_of,
    differences,
)
from app.models.enums import AuditAction, CareSystem, Lang
from app.models.org import Department, Doctor, Hospital
from app.trees import store as tree_store

logger = logging.getLogger(__name__)

#: A department code is a natural key: it names token series, resolves intake
#: trees, and is typed into seed files and tests. Upper-case ASCII keeps it
#: legible in all of those; the length ceiling is the column's.
_CODE = re.compile(r"^[A-Z][A-Z0-9]{1,31}$")


class FacilityError(Exception):
    """A refusal an administrator can act on. The message is shown verbatim."""


# -- the hospital --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HospitalIdentity:
    """What this hospital calls itself — the facts that reach a printed page."""

    hospital_id: uuid.UUID
    code: str
    name: str
    city: str | None
    district: str | None
    default_lang: Lang


def _identity(row: Hospital) -> HospitalIdentity:
    return HospitalIdentity(
        hospital_id=row.id,
        code=row.code,
        name=row.name,
        city=row.city,
        district=row.district,
        default_lang=row.default_lang,
    )


async def hospital_row(session: AsyncSession) -> Hospital:
    row = (
        await session.execute(select(Hospital).where(Hospital.deleted_at.is_(None)).limit(1))
    ).scalar_one_or_none()
    if row is None:
        raise FacilityError("no hospital is configured on this box")
    return row


async def identity(session: AsyncSession) -> HospitalIdentity:
    return _identity(await hospital_row(session))


async def update_identity(
    session: AsyncSession,
    *,
    name: str | None = None,
    city: str | None = None,
    district: str | None = None,
    default_lang: Lang | None = None,
) -> HospitalIdentity:
    """Rename the hospital, or restate where it is.

    Every field is optional and an absent one is left alone, so a console that
    edits the name does not have to resend the district it never showed.

    The name reaches patients on paper — the prescription letterhead and the
    intake boarding pass both render whatever is stored here — so it is trimmed
    but never otherwise rewritten. A hospital's legal name is its own; this is
    not the place to impose title case on it.
    """
    row = await hospital_row(session)
    changed: dict[str, str] = {}

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise FacilityError("a hospital needs a name — it is printed on every prescription")
        if len(cleaned) > 200:
            raise FacilityError("that name is too long for the letterhead (200 characters)")
        if cleaned != row.name:
            row.name = cleaned
            changed["name"] = "set"
    for field, value in (("city", city), ("district", district)):
        if value is None:
            continue
        cleaned_or_none = value.strip() or None
        if cleaned_or_none != getattr(row, field):
            setattr(row, field, cleaned_or_none)
            changed[field] = "set"
    if default_lang is not None and default_lang != row.default_lang:
        row.default_lang = default_lang
        changed["default_lang"] = str(default_lang)

    if changed:
        # `Hospital` carries no `Clinical` marker — it is not a patient record —
        # so the `before_flush` hook does not audit it and this edit writes its
        # own trail, the way every other console edit does. The *values* stay
        # out of `meta`: which fields an administrator changed is the
        # investigable fact, and the new name is on the row itself.
        record_admin_action(
            session,
            action=AuditAction.UPDATE,
            entity=Hospital.__tablename__,
            entity_id=row.id,
            meta={"changed": sorted(changed), "edited_from": "console"},
        )
        logger.info("hospital %s edited from the console: %s", row.id, sorted(changed))
    await session.flush()
    return _identity(row)


# -- departments ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DepartmentRow:
    """One department as the console lists it, with what makes it a decision."""

    department_id: uuid.UUID
    code: str
    name: str
    icon: str | None
    care_system: CareSystem
    active: bool
    #: Doctors whose console this department's system of medicine shapes.
    doctors: int
    #: Published intake trees bound to this department. Zero is why a brand-new
    #: department cannot be activated (see `_assert_has_intake`).
    published_trees: int
    #: Whether a walk-in routed here would find something to be asked. False
    #: means activating this department sends patients into an error.
    has_intake: bool


async def _rows(session: AsyncSession) -> list[Department]:
    return list(
        (
            await session.execute(
                select(Department).where(Department.deleted_at.is_(None)).order_by(Department.code)
            )
        ).scalars()
    )


async def _doctor_counts(session: AsyncSession) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(Doctor.department_id, func.count())
        .where(Doctor.deleted_at.is_(None), Doctor.active.is_(True))
        .group_by(Doctor.department_id)
    )
    return {department_id: count for department_id, count in rows.all()}


async def _describe(
    session: AsyncSession, row: Department, doctors: dict[uuid.UUID, int]
) -> DepartmentRow:
    published = await tree_store.published_for_department(session, row.code)
    return DepartmentRow(
        department_id=row.id,
        code=row.code,
        name=row.name,
        icon=row.icon,
        care_system=row.care_system,
        active=row.active,
        doctors=doctors.get(row.id, 0),
        published_trees=len(published),
        has_intake=await tree_store.resolve_tree(session, row.code) is not None,
    )


async def list_departments(session: AsyncSession) -> list[DepartmentRow]:
    """Every department, active or not.

    Deliberately not `GET /admin/departments`, which stays active-only because
    it feeds the create-a-doctor picker and a console must not be able to hire
    somebody into a department no patient can reach. The editor is the one
    surface that has to see the dark ones — that is what it is for.
    """
    doctors = await _doctor_counts(session)
    return [await _describe(session, row, doctors) for row in await _rows(session)]


async def _by_code(session: AsyncSession, code: str) -> Department:
    row = (
        await session.execute(
            select(Department).where(Department.code == code, Department.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if row is None:
        raise FacilityError(f"no department with code {code!r}")
    return row


async def _assert_has_intake(session: AsyncSession, code: str, name: str) -> None:
    """Refuse to open a department that has nothing to ask a patient.

    The kiosk chooser lists every active department, and `routes/kiosk.py`
    asserts a tree was resolved once one is picked. A department with neither a
    published tree nor a tree in `seeds/trees/` therefore turns a patient's tap
    into a 500 — not a graceful "come back later", an error screen in a corridor.

    This is the guard doc 24's `AYUR` department exists behind: it is seeded
    inactive, and it stays that way until SESSION-AYUR-2 authors its trees, at
    which point this check passes on its own and the console's toggle works.
    """
    if await tree_store.resolve_tree(session, code) is not None:
        return
    raise FacilityError(
        f"{name} has no intake tree yet, so a patient who chose it at the kiosk "
        "would get an error instead of questions. Publish a tree for "
        f"{code} first — until then this department stays closed."
    )


async def create_department(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    icon: str | None = None,
    care_system: CareSystem | str | None = None,
    active: bool = False,
) -> DepartmentRow:
    """A new department. **Closed by default**, and that is not timidity.

    A department is offered to patients the moment it is active, and a
    department created a second ago has no intake tree — so `active=True` here
    is refused by the same guard the toggle uses. Creating it dark, authoring
    its trees, then opening it is the order the platform can actually support.
    """
    code = (code or "").strip().upper()
    if not _CODE.match(code):
        raise FacilityError(
            f"{code!r} is not a department code — use 2 to 32 letters and digits, "
            "starting with a letter (MEDONC, AYUR)"
        )
    name = (name or "").strip()
    if not name:
        raise FacilityError("a department needs a name — patients read it on the kiosk")

    hospital = await hospital_row(session)
    clash = (
        await session.execute(
            select(Department).where(Department.hospital_id == hospital.id, Department.code == code)
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise FacilityError(f"{code} already exists here as {clash.name!r}")

    try:
        system = care_system_of(care_system)
    except CareSystemError as exc:
        raise FacilityError(str(exc)) from exc

    if active:
        await _assert_has_intake(session, code, name)

    row = Department(
        hospital_id=hospital.id,
        code=code,
        name=name,
        icon=(icon or "").strip() or None,
        care_system=system,
        active=active,
    )
    session.add(row)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=Department.__tablename__,
        entity_id=row.id,
        meta={
            "code": code,
            "care_system": str(system),
            "active": active,
            "created_from": "console",
        },
    )
    logger.info("created department %s (%s)", code, system)
    return await _describe(session, row, await _doctor_counts(session))


# -- changing a department's system of medicine --------------------------------


@dataclass(frozen=True, slots=True)
class CareSystemImpact:
    """What switching one department's system of medicine would change.

    Handed to the console *before* the switch, the way `deactivation_impact`
    precedes a deactivation: an operator sees the consequences and then decides,
    rather than deciding and then discovering them.
    """

    code: str
    name: str
    from_system: CareSystem
    to_system: CareSystem
    #: The flags that differ between the two capability rows — derived, so this
    #: cannot go stale when a capability is added.
    changes: tuple[CapabilityChange, ...]
    #: Doctors in this department. Their console gains and loses sections at
    #: their next sign-in; nothing they have already written changes.
    doctors: int
    #: Published intake trees bound to this department, authored for the system
    #: it is leaving. They keep working — nothing is unpublished here — but they
    #: are now asking a department's questions in another system's register.
    published_trees: int
    #: Whether patients can be routed here right now.
    active: bool

    @property
    def is_a_change(self) -> bool:
        return self.from_system != self.to_system


async def care_system_impact(
    session: AsyncSession, *, code: str, to: CareSystem | str
) -> CareSystemImpact:
    row = await _by_code(session, code)
    try:
        target = care_system_of(to)
    except CareSystemError as exc:
        raise FacilityError(str(exc)) from exc

    published = await tree_store.published_for_department(session, row.code)
    doctors = await _doctor_counts(session)
    return CareSystemImpact(
        code=row.code,
        name=row.name,
        from_system=row.care_system,
        to_system=target,
        changes=capability_differences(row.care_system, target),
        doctors=doctors.get(row.id, 0),
        published_trees=len(published),
        active=row.active,
    )


def capability_differences(
    before: CareSystem | str, after: CareSystem | str
) -> tuple[CapabilityChange, ...]:
    """The flags two systems of medicine disagree about.

    A one-line indirection so this module never names a member of the enum: it
    hands `app.care_system` two stored values and renders whatever comes back.
    """
    return differences(capabilities_for(before), capabilities_for(after))


async def update_department(
    session: AsyncSession,
    *,
    code: str,
    name: str | None = None,
    icon: str | None = None,
    care_system: CareSystem | str | None = None,
    active: bool | None = None,
    acknowledge: bool = False,
) -> DepartmentRow:
    """Edit a department. A change of system of medicine needs `acknowledge`.

    Everything else is an ordinary edit. The system of medicine is not: it
    reaches the intake trees the department offers, four or five sections of the
    doctor's console, the formulary a dictated drug is checked against and the
    register of every prompt — so doc 24 §7 asks for it to be confirmed against
    an explicit statement of what changes, and this refuses without one.
    """
    row = await _by_code(session, code)
    changed: dict[str, object] = {}

    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise FacilityError("a department needs a name — patients read it on the kiosk")
        if cleaned != row.name:
            row.name = cleaned
            changed["name"] = "set"
    if icon is not None:
        cleaned_icon = icon.strip() or None
        if cleaned_icon != row.icon:
            row.icon = cleaned_icon
            changed["icon"] = cleaned_icon

    if care_system is not None:
        try:
            target = care_system_of(care_system)
        except CareSystemError as exc:
            raise FacilityError(str(exc)) from exc
        if target != row.care_system:
            if not acknowledge:
                raise FacilityError(
                    f"changing {row.name} from {row.care_system} to {target} changes what it "
                    "offers patients and what its doctors see. Confirm the change to proceed."
                )
            changed["care_system"] = f"{row.care_system}->{target}"
            row.care_system = target

    if active is not None and active != row.active:
        if active:
            await _assert_has_intake(session, row.code, row.name)
        changed["active"] = active
        row.active = active

    if changed:
        record_admin_action(
            session,
            action=AuditAction.UPDATE,
            entity=Department.__tablename__,
            entity_id=row.id,
            # `care_system` and `active` go in verbatim rather than as a
            # `<redacted>` marker: neither is PII, and "who switched Ayurveda on,
            # and when" is the exact question this trail exists to answer.
            meta={"code": row.code, "changed": changed, "edited_from": "console"},
        )
        logger.info("department %s edited from the console: %s", row.code, changed)
    await session.flush()
    return await _describe(session, row, await _doctor_counts(session))
