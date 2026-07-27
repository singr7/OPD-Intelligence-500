"""Kiosk channel service (doc 03 §1a) — the walk-in adapter over the intake engine.

The kiosk is a **V3 client** of `app.intake.IntakeEngine` (HANDOFF S6): it drives
the four-tool contract directly from taps, so its answers JSONB is identical to
what telephony (S14) and WhatsApp (S12) produce — the engine is the single source
of clinical truth, and this module is a thin adapter that:

1. **Routes Q1** — the spoken chief complaint through `app.routing.classify_department`,
   *honouring* `needs_human` (send to the desk / show a chooser, never guess a 0.3),
2. **Picks the tree** for the department (`app.trees.bank.for_department`),
3. **Materialises the visit** — a walk-in `Patient` + `Visit` + `Intake` row so the
   engine's `finalize_cost` has somewhere to land and the answers persist, and
4. **Allocates a token** at confirm time.

The HTTP surface (`app.routes.kiosk`) is deliberately thin over this; everything
here is unit-testable without a request. Nothing here decides anything clinical —
the department is the model's (distrusted) guess, and the questions and red flags
are the tree's.

## Not this session

Token issuance here is a provisional `max(token_no)+1` per department per day.
Real queue-managed issuance — priority/urgent insertion, offline token blocks,
reconciliation — is S8 (queue) and S7 (offline). This exists only so the kiosk's
final screen has a real number to show; S8 replaces the allocator, not the shape.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.clinical import Intake, Visit
from app.models.enums import Channel, IntakeTier, Lang, VisitStatus
from app.models.org import Department
from app.models.patient import Patient
from app.patient_names import PatientNameError, normalize_patient_name
from app.routing import DepartmentGuess, DepartmentOption, classify_department
from app.trees import bank, store
from app.trees.schema import Tree

logger = logging.getLogger(__name__)

#: The kiosk always drives V3 — deterministic taps, no model in the walk. Q1's
#: classifier is the one model call, and it never touches the tree or the flags.
KIOSK_TIER = IntakeTier.PRERECORDED

#: Where a patient goes when the classifier is unsure. The kiosk turns this into a
#: department chooser (doc 03 §1a: "call staff" / tap fallback always visible).
TRIAGE = "TRIAGE"


class KioskError(Exception):
    """A kiosk request that cannot proceed — an unknown department, no tree."""


@dataclass(slots=True)
class Routed:
    """The outcome of routing Q1: either a department + tree, or a chooser."""

    guess: DepartmentGuess
    department: Department | None
    tree: Tree | None

    @property
    def needs_department(self) -> bool:
        """True when the caller should show a chooser rather than start a walk."""
        return self.department is None or self.tree is None


async def _departments(session: AsyncSession) -> list[Department]:
    result = await session.execute(
        select(Department).where(Department.active.is_(True)).order_by(Department.code)
    )
    return list(result.scalars().all())


async def route_complaint(
    session: AsyncSession,
    *,
    complaint: str,
    lang: Lang,
    dept_key: str | None = None,
) -> Routed:
    """Decide the department + tree for a chief complaint.

    `dept_key`, when given, is a confirmed choice (staff- or patient-picked from
    the chooser) and skips the classifier entirely. Without it, the model routes;
    a `needs_human` verdict returns a `Routed` with no department, and the caller
    shows the chooser instead of guessing.
    """
    departments = await _departments(session)
    by_code = {d.code: d for d in departments}

    if dept_key is not None:
        department = by_code.get(dept_key)
        if department is None:
            raise KioskError(f"unknown department {dept_key!r}")
        return Routed(
            guess=DepartmentGuess(dept_key, 1.0, "chosen at the desk", needs_human=False),
            department=department,
            tree=await store.resolve_tree(session, dept_key),
        )

    options = [DepartmentOption(key=d.code, name=d.name) for d in departments]
    guess = await classify_department(complaint, lang=lang, departments=options)

    if guess.needs_human or guess.dept_key not in by_code:
        return Routed(guess=guess, department=None, tree=None)

    return Routed(
        guess=guess,
        department=by_code[guess.dept_key],
        tree=await store.resolve_tree(session, guess.dept_key),
    )


def select_tree(dept_key: str) -> Tree | None:
    """Pick a department's intake tree from the **on-disk** bank.

    The disk-only selector: it never sees a console publish. The live intake path
    goes through `app.trees.store.resolve_tree` (DB-published, disk floor) since
    S18; this remains for the offline pack builder and tests that want the
    authored content regardless of DB state. Selection is shared via `store.pick`
    so both paths choose the same tree.
    """
    return store.pick(bank.for_department(dept_key))


@dataclass(slots=True)
class WalkIn:
    """The rows a kiosk intake needs to exist before the engine can persist it."""

    patient: Patient
    visit: Visit
    intake: Intake


async def create_walk_in(
    session: AsyncSession,
    *,
    department: Department,
    lang: Lang,
    tree: Tree,
    caregiver: bool = False,
    patient_name: str | None = None,
) -> WalkIn:
    """Create the named walk-in patient, visit and intake for a kiosk session.

    ``patient_name=None`` retains the rollout-safe fallback for an older kiosk.
    ``mrn`` remains a generated walk-in key, not a registered hospital MRN.
    """
    try:
        name = normalize_patient_name(patient_name)
    except PatientNameError as exc:
        raise KioskError(str(exc)) from exc
    patient = Patient(
        hospital_id=department.hospital_id,
        mrn=f"WALKIN-{uuid.uuid4().hex[:10].upper()}",
        name=name,
        phone="",
        lang=lang,
        # Kept alongside `Intake.caregiver_answered` (the real flag since S16):
        # on an anonymous walk-in there is no registered caregiver name to
        # overwrite, and the marker is what the desk sees when it later attaches
        # a name to this row.
        caregiver_name="(caregiver at kiosk)" if caregiver else None,
    )
    session.add(patient)
    await session.flush()

    visit = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=datetime.now(UTC).date(),
        status=VisitStatus.REGISTERED,
        channel=Channel.KIOSK,
    )
    session.add(visit)
    await session.flush()

    intake = Intake(
        visit_id=visit.id,
        tier=KIOSK_TIER,
        lang=lang,
        caregiver_answered=caregiver,
    )
    session.add(intake)
    await session.flush()

    return WalkIn(patient=patient, visit=visit, intake=intake)


async def allocate_token(session: AsyncSession, visit: Visit) -> int:
    """Assign the next token number for this visit's department + day.

    Provisional (see the module docstring): a simple per-department-per-day
    sequence guarded by the `uq_visits_dept_date_token` unique constraint, with a
    single retry on the race. S8's queue service owns real issuance — priority
    insertion, reconciliation. Returns the allocated number.

    **Stays below `kiosk_offline_token_base`** (S7). Everything at or above the
    base belongs to the offline blocks in `app.offline`, which a kiosk may be
    handing out right now with no way to tell us. The `max()` below is therefore
    taken over online numbers only — an offline intake that synced at 500 must
    not drag the next online token to 501, straight into another kiosk's range.
    """
    if visit.token_no is not None:
        return visit.token_no

    base = get_settings().kiosk_offline_token_base
    for _ in range(5):
        current = await session.scalar(
            select(func.max(Visit.token_no)).where(
                Visit.department_id == visit.department_id,
                Visit.date == visit.date,
                Visit.token_no < base,
            )
        )
        candidate = int(current or 0) + 1
        if candidate >= base:
            # The online range is full. Refuse rather than wrap into the offline
            # blocks: a duplicate token is worse than a loud failure, and this is
            # a capacity problem a human must see (S8 widens the range).
            raise KioskError(
                f"online token range is exhausted for this department today "
                f"(reached {base - 1}); offline blocks own {base} and above"
            )
        try:
            # A savepoint, not the whole transaction: a collision must roll back
            # only this token attempt, never the walk-in rows created alongside it.
            async with session.begin_nested():
                visit.token_no = candidate
                await session.flush()
            return candidate
        except IntegrityError:
            # Another kiosk took that number between the max() and the flush; the
            # savepoint rolled the clash back — try the next number.
            visit.token_no = None
    raise KioskError("could not allocate a token — too many concurrent kiosks")
