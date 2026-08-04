"""Who this patient is, and which doctor is going to see them.

Two questions the kiosk cannot answer and a coordinator can, resolved in one
action on the kiosk's staff strip (plan: `sessions/SESSION-ASSIGN-RX-PLAN.md`).

**Identity.** A kiosk arrival always creates its own `Patient` row so the
clinical walk can start immediately. If the arrival screen was given a phone
number or a UHC ID, `find_candidate` looks for a prior record and the visit
remembers it as a *candidate* — `Visit.candidate_patient_id`, state
`CANDIDATE`. The kiosk shows the patient nothing about the match beyond "we may
have your file": a public unauthenticated terminal that prints a named oncology
history to whoever types ten digits is a disclosure incident with a queue
attached. A coordinator confirms it, and only then does `confirm_link` repoint
the visit at the prior record.

**Assignment.** `Visit.doctor_id` has existed since the first migration and was
written only by the appointment path. `assign` starts writing it for walk-ins.
It does *not* create a per-doctor queue: `Queue` stays one row per department
with `doctor_id` NULL, because the token series, its unique constraint and the
offline `OfflineTokenBlock` leases are all per-department, and the public board
shows one line per department. Assignment is an attribute on the visit and a
filter on the doctor's worklist — never a second queue.

Nothing here re-derives clinical state. Changing a visit's department does not
re-walk the tree, recompute red flags or re-rank urgency; those are the intake
engine's, written once, and a coordinator correcting a routing mistake is not a
clinical re-assessment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import Visit
from app.models.enums import PatientLinkState
from app.models.org import Department, Doctor
from app.models.patient import Patient
from app.models.scheduling import SlotTemplate


class AssignmentError(Exception):
    """The caller asked for something this visit cannot be given."""


# -- identity -----------------------------------------------------------------


def _last10(phone: str | None) -> str | None:
    """The last ten digits of a phone number, or None if there aren't ten.

    Carriers and patients between them produce `+919876543210`,
    `919876543210` and `09876543210` for one handset; the last ten digits are
    the only part all three agree on. Matches `receptionist._patient_by_phone`
    so the phone channel and the kiosk recognise the same person.
    """
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
    return digits if len(digits) == 10 else None


async def find_candidate(
    session: AsyncSession,
    *,
    hospital_id: uuid.UUID,
    phone: str | None = None,
    external_id: str | None = None,
    exclude_patient_id: uuid.UUID | None = None,
) -> Patient | None:
    """A prior patient this arrival might be, or None.

    A UHC ID is checked before a phone number: it identifies a person, while a
    phone identifies a *handset* that a household may share. Neither is treated
    as proof — the return value is a candidate for a human to confirm, which is
    why this function cannot merge anything by itself.

    Walk-in rows created by earlier kiosk sessions are excluded. They carry a
    generated `WALKIN-` MRN rather than a registered one, so matching one would
    offer the coordinator a previous anonymous arrival as though it were a file.
    """
    external_id = (external_id or "").strip()
    if external_id:
        found = await session.scalars(
            select(Patient)
            .where(
                Patient.deleted_at.is_(None),
                Patient.hospital_id == hospital_id,
                Patient.external_id == external_id,
                Patient.mrn.not_like("WALKIN-%"),
            )
            .order_by(Patient.created_at)
        )
        match = next((p for p in found if p.id != exclude_patient_id), None)
        if match is not None:
            return match

    digits = _last10(phone)
    if not digits:
        return None
    found = await session.scalars(
        select(Patient)
        .where(
            Patient.deleted_at.is_(None),
            Patient.hospital_id == hospital_id,
            Patient.phone.like(f"%{digits}"),
            Patient.mrn.not_like("WALKIN-%"),
        )
        .order_by(Patient.created_at)
    )
    return next((p for p in found if p.id != exclude_patient_id), None)


async def note_candidate(session: AsyncSession, *, visit: Visit, candidate: Patient | None) -> None:
    """Record a possible prior file against the visit. Discloses nothing."""
    if candidate is None:
        return
    visit.candidate_patient_id = candidate.id
    visit.patient_link_state = PatientLinkState.CANDIDATE
    await session.flush()


async def confirm_link(session: AsyncSession, *, visit: Visit) -> Patient:
    """The coordinator agrees: this arrival is the prior patient.

    Repoints the visit and retires the throwaway walk-in row. The walk-in is
    *soft*-deleted and its demographics are left intact — an incorrectly
    confirmed link has to be reconstructible, and `SoftDeleteMixin` is what makes
    that possible without a restore from backup.

    The token is not reissued: the patient is already holding a printed slip and
    standing in a line ordered by it, and their identity has no bearing on either.
    """
    if visit.candidate_patient_id is None:
        raise AssignmentError("this visit has no candidate to confirm")
    if visit.patient_link_state is PatientLinkState.CONFIRMED:
        prior = await session.get(Patient, visit.patient_id)
        if prior is None:  # pragma: no cover - FK-guaranteed
            raise AssignmentError("the linked patient no longer exists")
        return prior

    prior = await session.get(Patient, visit.candidate_patient_id)
    if prior is None or prior.deleted_at is not None:
        raise AssignmentError("the matched patient record is no longer available")

    walk_in_id = visit.patient_id
    visit.patient_id = prior.id
    visit.patient_link_state = PatientLinkState.CONFIRMED

    if walk_in_id != prior.id:
        walk_in = await session.get(Patient, walk_in_id)
        # Only ever retire the generated row. If a visit somehow points at a
        # registered patient, linking must not delete a real file.
        if walk_in is not None and walk_in.mrn.startswith("WALKIN-"):
            walk_in.deleted_at = datetime.now(UTC)
    await session.flush()
    return prior


async def reject_link(session: AsyncSession, *, visit: Visit) -> None:
    """The coordinator looked and it is a different person. Never re-offered."""
    visit.candidate_patient_id = None
    visit.patient_link_state = PatientLinkState.REJECTED
    await session.flush()


# -- who is in clinic ---------------------------------------------------------


@dataclass(slots=True)
class DoctorOption:
    """A doctor the coordinator may pick, and whether the roster expects them."""

    id: uuid.UUID
    name: str
    reg_no: str
    qualification: str | None
    #: True when a slot template puts this doctor in clinic on this weekday.
    on_duty: bool


async def assignable_doctors(
    session: AsyncSession, *, department_id: uuid.UUID, on: date_type
) -> list[DoctorOption]:
    """The department's doctors, roster-first.

    Ordered on-duty first, then by name. Off-duty doctors are listed rather than
    hidden — a pilot roster is often incomplete, and a coordinator who cannot
    find the consultant standing next to them will assign nobody at all. The
    honest `on_duty` flag lets the UI say which is which instead of the list
    quietly lying by omission.
    """
    doctors = list(
        await session.scalars(
            select(Doctor)
            .where(
                Doctor.department_id == department_id,
                Doctor.active.is_(True),
                Doctor.deleted_at.is_(None),
            )
            .order_by(Doctor.name)
        )
    )
    if not doctors:
        return []

    rostered = set(
        await session.scalars(
            select(SlotTemplate.doctor_id).where(
                SlotTemplate.doctor_id.in_([d.id for d in doctors]),
                SlotTemplate.weekday == on.weekday(),
                SlotTemplate.active.is_(True),
                SlotTemplate.deleted_at.is_(None),
            )
        )
    )
    options = [
        DoctorOption(
            id=d.id,
            name=d.name,
            reg_no=d.reg_no,
            qualification=d.qualification,
            on_duty=d.id in rostered,
        )
        for d in doctors
    ]
    options.sort(key=lambda o: (not o.on_duty, o.name))
    return options


def default_doctor(options: list[DoctorOption]) -> DoctorOption | None:
    """The one to pre-select, or None when the coordinator must decide.

    Exactly one doctor on duty is the pilot's ordinary day and pre-selecting them
    turns the strip into a single tap. Two or more is a real choice and guessing
    at it would be worse than asking: an unnoticed default is how a patient ends
    up on the wrong consultant's list.
    """
    on_duty = [o for o in options if o.on_duty]
    if len(on_duty) == 1:
        return on_duty[0]
    if not on_duty and len(options) == 1:
        return options[0]
    return None


# -- assignment ---------------------------------------------------------------


@dataclass(slots=True)
class Assignment:
    """The outcome of one strip confirmation, including anything the patient
    must now be told."""

    visit: Visit
    #: Set when the department changed: the patient's printed slip is stale and
    #: the coordinator has to hand them this number instead.
    old_token_no: int | None = None
    new_token_no: int | None = None

    @property
    def token_reissued(self) -> bool:
        return self.new_token_no is not None and self.new_token_no != self.old_token_no


async def assign(
    session: AsyncSession,
    *,
    visit: Visit,
    doctor_id: uuid.UUID | None,
    department: Department | None = None,
) -> Assignment:
    """Point a visit at a doctor, and optionally at a different department.

    `doctor_id=None` is a first-class outcome, not a failure: it means the
    department pool, which is where a skipped strip and every offline arrival
    land. The doctor console surfaces those in its `Unassigned` scope with a
    count that stays visible, so an unassigned patient is never invisible.

    A doctor must belong to the visit's (possibly just-changed) department.
    Assigning across departments would put a patient on a worklist filtered by a
    department they are not queued in, and they would drop out of both.

    A department change moves the queue entry and reissues the token
    (`queue.transfer_department`) — the two things that are keyed per department.
    It does **not** re-open the clinical record: the tree that was walked, the
    flags it raised and the urgency they set all stand, and the doctor's card
    shows the original routing so the note is read in the context it was taken in.
    """
    result = Assignment(visit=visit, old_token_no=visit.token_no, new_token_no=visit.token_no)

    if department is not None and department.id != visit.department_id:
        # Imported here: `app.queue` imports `app.kiosk` for token allocation and
        # `app.kiosk` imports this module for the arrival lookup.
        from app import queue as queue_svc

        try:
            transfer = await queue_svc.transfer_department(
                session, visit=visit, department=department
            )
        except queue_svc.QueueError as exc:
            raise AssignmentError(str(exc)) from exc
        result.old_token_no = transfer.old_token_no
        result.new_token_no = transfer.new_token_no

    if doctor_id is not None:
        doctor = await session.get(Doctor, doctor_id)
        if doctor is None or doctor.deleted_at is not None or not doctor.active:
            raise AssignmentError("no such doctor")
        if doctor.department_id != visit.department_id:
            raise AssignmentError("that doctor does not work in this department")
        visit.doctor_id = doctor.id
    else:
        visit.doctor_id = None

    await session.flush()
    return result
