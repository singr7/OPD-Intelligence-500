"""The doctor's console read models (doc 03 §5).

Two reads and two writes. The doctor's *queue* actions — call next,
no-show, send to lab and re-queue — are still the S8 verbs
(`app.queue.call_next` / `set_state`) that the coordinator console already
drives, so this module owns no copy of the queue state machine. A second
implementation of "call the next token" is how a queue ends up with two sources
of truth that disagree the moment one of them is patched.

* `day_list` — the doctor's worklist for a day, in one of three scopes: their
  own patients, their department's unassigned pool, or the whole department.
  The coordinator's `department_queue` already orders the line (urgent first, by
  construction); this adds identity, the red-flag count and who the visit is
  assigned to, and refuses to leave the doctor's own department.
* `patient_card` — one patient's story, assembled for a 20-second read (doc 04
  §3): the §4 summary, the red-flag strip, the answers as asked, the visit
  timeline and the check-in trendline.
* `take_visit` — the one write. Cover is routine in an OPD, and making a doctor
  find a coordinator to pick up an unassigned patient turns one absence into a
  stalled line. It delegates to `app.assignment.assign` rather than writing
  `Visit.doctor_id` itself, so there is one implementation of "point this visit
  at a doctor" and one set of rules about which doctors are eligible.
* `conclude_visit` — the other write, and the reason it is here rather than on
  the queue: a paper prescription is a clinical fact about the consult, not a
  position in a line. It records *how* the consult ended and then moves the
  queue entry through `queue.set_state` like everyone else.

**The card never re-derives clinical judgement.** Red flags are read from
`Intake.red_flags`, which the rule engine wrote (`app.trees.rules`); the summary
is read from `Intake.summary_lang_versions[...]["structured"]`, which the
summarizer wrote under the doc 03 §4 contract. Nothing here recomputes either —
a doctor screen that re-decided a flag would show a different clinical picture
than the kiosk told the patient, and than the queue prioritised on.

**Authorization stays at department scope.** `day_list`'s `scope` filters a
*list*; it does not narrow *access*, and `patient_card` is deliberately not
scoped to the assigned doctor. A covering colleague, a lab re-queue picked up by
whoever is free and a second opinion all have to be able to open the card. That
is a clinical-continuity decision, not an oversight — a console that hid a
colleague's patient would leave a red flag visible to exactly one person who may
be in theatre.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as date_type
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import allergies as allergy_svc
from app import queue as queue_svc
from app.allergies import NEVER_ASKED, AllergyView
from app.models.clinical import Dictation, Intake, Visit
from app.models.content import Checkin, CheckinPlan
from app.models.enums import AllergySeverity, DictationStatus, Lang, QueueEntryState, RxMode
from app.models.org import Department, Doctor
from app.models.patient import Patient
from app.trees import bank
from app.trees.schema import Tree, TreeError


class DoctorError(Exception):
    """The caller is a doctor, but not one who may see this."""


class ConclusionRefused(DoctorError):
    """The conclusion contradicts the record — not a permission problem.

    Split out because everything else in this module raises `DoctorError` for
    "not your department", which the routes answer with a 403. A doctor being
    told "you have not signed a note yet" through a permission error would go
    looking for the wrong thing entirely.
    """


# -- day list -----------------------------------------------------------------


#: The three worklists a doctor works from. `mine` is the default because the
#: kiosk now assigns essentially every arrival (AR3), which is what makes
#: "assigned to me" a reliable list rather than a guess.
DayScope = Literal["mine", "unassigned", "department"]

DAY_SCOPES: tuple[DayScope, ...] = ("mine", "unassigned", "department")


@dataclass(slots=True)
class DayRow:
    """One patient on the doctor's worklist, in queue order."""

    entry_id: uuid.UUID
    visit_id: uuid.UUID
    token_no: int
    state: str
    priority: str
    priority_reason: str | None
    patient_name: str
    patient_age: int | None
    patient_sex: str | None
    chief_complaint: str | None
    red_flag_count: int
    called_at: datetime | None
    #: Who is going to see them. `None` is the department pool — a `Skip` at the
    #: kiosk strip, or an offline arrival that synced with no roster to pick
    #: from. It is a legitimate state, and the one `take_visit` resolves.
    assigned_doctor_id: uuid.UUID | None = None
    assigned_doctor_name: str | None = None
    #: True when this row is the reading doctor's own patient. Computed here
    #: rather than compared in the UI so "mine" means one thing on every screen.
    is_mine: bool = False


@dataclass(slots=True)
class DayCounts:
    """How many patients sit in each scope, for badges that do not lie.

    Returned on *every* response, whichever scope was asked for, so the console
    can render all three tabs truthfully without a second round trip. That is
    the whole reason the counts live here: a badge fetched separately from the
    list it describes is a badge that goes stale between two requests, and the
    one it would go stale on is `unassigned`.
    """

    mine: int
    unassigned: int
    department: int
    #: Unassigned *and still waiting* — the same definition the coordinator
    #: console's "Waiting, unassigned" metric uses, deliberately, so the desk and
    #: the consulting room never quote different numbers at each other. This is
    #: what drives the console's attention state; `unassigned` is what the tab's
    #: badge shows, because a badge that disagrees with the list under it is
    #: worse than a badge that disagrees with a metric on another screen.
    unassigned_waiting: int
    #: Everyone in the department still waiting to be called, whatever scope is
    #: open and whoever they are assigned to. The encounter bar states it when
    #: the room is free, and it has to mean "the line outside" — a figure that
    #: shrank because the doctor switched to their own list would be answering a
    #: different question than the one being asked.
    waiting: int


@dataclass(slots=True)
class DayList:
    """The doctor's morning: who they are, which room, and the line."""

    doctor_name: str
    department_key: str
    department_name: str
    date: date_type
    rows: list[DayRow]
    scope: DayScope
    counts: DayCounts
    doctor_id: uuid.UUID


async def resolve_doctor(session: AsyncSession, *, user_id: uuid.UUID) -> Doctor:
    """The `Doctor` row behind an authenticated user.

    A `doctor` role with no doctor row is a seeding/admin mistake, not a
    permission question — it is surfaced rather than silently showing an empty
    day, because an empty worklist and "you are not registered as a doctor" are
    very different things at 9am.
    """
    doctor = await session.scalar(
        select(Doctor).where(Doctor.user_id == user_id, Doctor.deleted_at.is_(None))
    )
    if doctor is None:
        raise DoctorError("this login is not linked to a doctor record")
    return doctor


async def day_list(
    session: AsyncSession,
    *,
    doctor: Doctor,
    on: date_type | None = None,
    scope: DayScope = "mine",
) -> DayList:
    """The doctor's worklist for a day, in one scope, with counts for all three.

    Order is the queue's, not ours (`department_queue`): now-serving first, then
    the waiting line sorted `(priority_rank, position, token_no)`, then the lab
    round-trips. An urgent red-flag intake is already at the top by construction
    — the doctor sees the same order the board and the coordinator do.

    **Scoping filters rows; it never reorders them.** Two doctors comparing
    screens, or a doctor comparing theirs against the board, must be reading the
    same line in the same order — otherwise "who is next" becomes a question with
    a per-screen answer.

    The counts are always computed over the *whole* department worklist, whatever
    scope was asked for. `unassigned` in particular has to be visible while its
    tab is closed: it is the compensating control for every kiosk `Skip` and
    every offline arrival, and a number nobody is shown is a number nobody acts
    on.
    """
    on = on or queue_svc.today()
    if scope not in DAY_SCOPES:
        raise DoctorError(f"no such worklist scope {scope!r}")
    dept = await session.get(Department, doctor.department_id)
    if dept is None:  # pragma: no cover - FK guarantees it
        raise DoctorError("this doctor has no department")

    views = await queue_svc.department_queue(session, department_id=dept.id, on=on)
    patients = await _patients_for_visits(session, [view.visit_id for view in views])

    rows = []
    for view in views:
        patient = patients.get(view.visit_id)
        rows.append(
            DayRow(
                entry_id=view.id,
                visit_id=view.visit_id,
                token_no=view.token_no,
                state=str(view.state),
                priority=str(view.priority),
                priority_reason=view.priority_reason,
                patient_name=patient.name if patient else "—",
                patient_age=patient.age if patient else None,
                patient_sex=str(patient.sex) if patient and patient.sex else None,
                chief_complaint=view.chief_complaint,
                red_flag_count=view.red_flag_count,
                called_at=view.called_at,
                assigned_doctor_id=view.assigned_doctor_id,
                assigned_doctor_name=view.assigned_doctor_name,
                is_mine=view.assigned_doctor_id == doctor.id,
            )
        )

    counts = DayCounts(
        mine=sum(1 for row in rows if row.is_mine),
        unassigned=sum(1 for row in rows if row.assigned_doctor_id is None),
        department=len(rows),
        unassigned_waiting=sum(
            1 for row in rows if row.assigned_doctor_id is None and row.state == "waiting"
        ),
        waiting=sum(1 for row in rows if row.state == "waiting"),
    )

    return DayList(
        doctor_name=doctor.name,
        department_key=dept.code,
        department_name=dept.name,
        date=on,
        rows=[row for row in rows if _in_scope(row, scope)],
        scope=scope,
        counts=counts,
        doctor_id=doctor.id,
    )


def _in_scope(row: DayRow, scope: DayScope) -> bool:
    if scope == "mine":
        return row.is_mine
    if scope == "unassigned":
        return row.assigned_doctor_id is None
    return True


async def take_visit(session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor) -> Visit:
    """Take this patient: the doctor puts their own name on a visit.

    Legal on an unassigned patient *and* on a colleague's: cover is routine in an
    OPD, and a doctor who has to find a coordinator to pick up the patient in
    front of them will simply see them without the record following. Taking a
    colleague's patient is not silently benign, so it is neither hidden nor
    blocked — it lands in the audit trail like every other write to `Visit`
    (`app.audit`'s `before_flush` hook), where the previous assignment is
    recoverable.

    The department check comes first and is worded from the doctor's side.
    `assignment.assign` would also refuse — it will not put a doctor on a visit
    outside their department — but its message is about the doctor being wrong
    for the department, and here it is the *visit* that is in the wrong room.
    """
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise DoctorError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise DoctorError("that patient is in another department")

    # Imported here rather than at module scope: `app.assignment` reaches into
    # `app.queue` for department transfers, and `app.queue` imports this module's
    # neighbours in turn.
    from app import assignment as assignment_svc

    try:
        await assignment_svc.assign(session, visit=visit, doctor_id=doctor.id)
    except assignment_svc.AssignmentError as exc:
        raise DoctorError(str(exc)) from exc
    return visit


@dataclass(slots=True)
class Conclusion:
    """What concluding did: the visit's own record, and where the queue landed."""

    visit: Visit
    entry_state: str | None


async def conclude_visit(
    session: AsyncSession,
    *,
    visit_id: uuid.UUID,
    doctor: Doctor,
    rx_mode: RxMode,
    note: str | None = None,
) -> Conclusion:
    """Close the consult and record how it ended (plan §5.3b).

    The lossy conclusions are the reason this exists. A doctor who writes a paper
    script has finished the consult, but nothing in this system knows it: the
    queue entry sits in `in_consult` until someone clears it, and the visit looks
    identical to one that was abandoned halfway. So the conclusion is written
    down — which mode, by whom, when, with whatever the doctor wants to add —
    and it is audited like every other write to `Visit`.

    `system` is refused without a signed note, because that is what `system`
    claims. A conclusion that says "there is a digital prescription" when there
    is not would send the pharmacy looking for a document nobody produced, and
    it is the one of the three modes this function can actually check.

    The queue moves through `queue.set_state`, not through an assignment here: a
    second way to mark an entry `done` is a second state machine, and the board
    and the coordinator would start disagreeing with the consulting room within a
    session or two. Which means this verb lives inside the S8 transition table
    rather than around it:

    * `in_consult` / `lab_requeue` → `done`, directly.
    * `called` → `in_consult` → `done`. Concluding a called patient is the
      doctor stating the consult happened, so the entry walks the same path it
      would have if they had pressed the button on the way in.
    * `waiting` → refused. Nobody called this patient, so there is no consult to
      conclude, and marking them done would take them off the board without
      anyone having seen them.
    * `done` / `no_show` → left alone. Both are terminal, and dragging a no-show
      back through `done` would rewrite what happened to that patient. The
      conclusion itself is still recorded.
    """
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise DoctorError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise DoctorError("that patient is in another department")

    if rx_mode is RxMode.SYSTEM:
        if not await _has_signed_note(session, visit_id=visit_id):
            raise ConclusionRefused(
                "this visit has no signed consult note, so it cannot be concluded "
                "as a system prescription"
            )

    # Everything that can refuse, refuses before anything is written. A rejected
    # conclusion that had already stamped `rx_mode` on the visit would leave the
    # record saying a consult ended in a way it did not.
    entry = await _entry_for_visit(session, visit_id=visit_id)
    if entry is not None and entry.state is QueueEntryState.WAITING:
        raise ConclusionRefused(
            "this patient has not been called in yet, so there is no consult to conclude"
        )

    visit.rx_mode = rx_mode
    visit.conclusion_note = (note or "").strip() or None
    visit.concluded_at = datetime.now(UTC)
    visit.concluded_by = doctor.id

    if entry is not None and entry.state not in _ALREADY_GONE:
        # A called patient who is being concluded was, demonstrably, seen. Walk
        # the entry through `in_consult` rather than widening the S8 transition
        # table for us: the table is what stops the board representing a patient
        # who is both seen and waiting, and `started_at` stays truthful this way.
        if entry.state is QueueEntryState.CALLED:
            await queue_svc.set_state(session, entry_id=entry.id, state=QueueEntryState.IN_CONSULT)
        entry = await queue_svc.set_state(session, entry_id=entry.id, state=QueueEntryState.DONE)
    await session.flush()
    return Conclusion(visit=visit, entry_state=str(entry.state) if entry else None)


# -- allergies (SESSION-ALLERGY) ----------------------------------------------
#
# These three sit here rather than in `app.allergies` for one reason: department
# scope. `app.allergies` knows about patients and statements and deliberately
# knows nothing about who is allowed to write one — it is called by the kiosk,
# which has no doctor at all. Authorization for the console is this module's job
# and it is the same check `take_visit`, `conclude_visit` and `patient_card`
# already make, worded the same way.


async def _visit_in_scope(session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor) -> Visit:
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise DoctorError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise DoctorError("that patient is in another department")
    return visit


async def record_allergy(
    session: AsyncSession,
    *,
    visit_id: uuid.UUID,
    doctor: Doctor,
    substance: str | None,
    reaction: str | None = None,
    severity: AllergySeverity = AllergySeverity.UNKNOWN,
    none_known: bool = False,
) -> AllergyView:
    """A doctor states an allergy — or states that they asked and there are none.

    Returns the whole recomputed view rather than the row, because the console's
    spine renders a *state*, and a client that patched a returned row into its
    own list would be re-deriving that state in TypeScript. One derivation, in
    `app.allergies`, on the server.
    """
    visit = await _visit_in_scope(session, visit_id=visit_id, doctor=doctor)
    await allergy_svc.record_by_doctor(
        session,
        patient_id=visit.patient_id,
        visit_id=visit.id,
        doctor=doctor,
        substance=substance,
        reaction=reaction,
        severity=severity,
        none_known=none_known,
    )
    return await allergy_svc.for_patient(session, patient_id=visit.patient_id)


async def confirm_allergy(
    session: AsyncSession, *, visit_id: uuid.UUID, allergy_id: uuid.UUID, doctor: Doctor
) -> AllergyView:
    visit = await _visit_in_scope(session, visit_id=visit_id, doctor=doctor)
    await allergy_svc.confirm(
        session, allergy_id=allergy_id, patient_id=visit.patient_id, doctor=doctor
    )
    return await allergy_svc.for_patient(session, patient_id=visit.patient_id)


async def retract_allergy(
    session: AsyncSession,
    *,
    visit_id: uuid.UUID,
    allergy_id: uuid.UUID,
    doctor: Doctor,
    reason: str | None = None,
) -> AllergyView:
    visit = await _visit_in_scope(session, visit_id=visit_id, doctor=doctor)
    await allergy_svc.retract(
        session,
        allergy_id=allergy_id,
        patient_id=visit.patient_id,
        doctor=doctor,
        reason=reason,
    )
    return await allergy_svc.for_patient(session, patient_id=visit.patient_id)


async def _has_signed_note(session: AsyncSession, *, visit_id: uuid.UUID) -> bool:
    return (
        await session.scalar(
            select(Dictation.id).where(
                Dictation.visit_id == visit_id,
                Dictation.status == DictationStatus.SIGNED,
                Dictation.deleted_at.is_(None),
            )
        )
    ) is not None


#: Queue states a conclusion does not move. Both are terminal, and dragging a
#: no-show back through `done` would rewrite what happened to that patient.
_ALREADY_GONE = frozenset({QueueEntryState.DONE, QueueEntryState.NO_SHOW})


async def _patients_for_visits(
    session: AsyncSession, visit_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Patient]:
    """Patient per visit, in one round trip (the worklist is a page, not a row)."""
    if not visit_ids:
        return {}
    result = await session.execute(
        select(Visit.id, Patient)
        .join(Patient, Visit.patient_id == Patient.id)
        .where(Visit.id.in_(visit_ids))
    )
    return {visit_id: patient for visit_id, patient in result.all()}


# -- patient card -------------------------------------------------------------


@dataclass(slots=True)
class RedFlagView:
    """One fired rule, as the strip renders it (doc 04 §3: danger tokens, top)."""

    id: str
    severity: str
    label: str
    instruction: str
    source_node: str | None


@dataclass(slots=True)
class AnswerRow:
    """One answered node: the question in English, the patient in their own words."""

    node_id: str
    question: str
    answer: str
    said: str | None
    flagged: bool


@dataclass(slots=True)
class TimelineVisit:
    """A past visit, for the "has this been going on?" glance."""

    visit_id: uuid.UUID
    date: date_type
    department_name: str
    status: str
    token_no: int | None
    chief_complaint: str | None
    is_current: bool


@dataclass(slots=True)
class TrendPoint:
    at: datetime
    value: float


@dataclass(slots=True)
class SymptomTrend:
    """One symptom's check-in trendline (doc 03 §5's "sparkline across cycles")."""

    symptom: str
    points: list[TrendPoint]


@dataclass(slots=True)
class SummaryView:
    """doc 03 §4's contract, as stored by the summarizer. All fields optional —
    a V3 intake that never reached a summarizer still has to render."""

    chief_concern: str | None = None
    hpi: list[str] = field(default_factory=list)
    symptoms: list[dict[str, str]] = field(default_factory=list)
    history_meds: list[str] = field(default_factory=list)
    since_last_visit: list[str] = field(default_factory=list)
    patient_words: dict[str, str] = field(default_factory=dict)
    unclear: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DiagnosisView:
    """The working diagnosis, and where it came from.

    Read off the most recent **signed** consult note for this patient, across
    visits — a diagnosis made last month is the one that makes today's
    presentation legible, and a card that only ever showed today's would be blank
    for exactly the patients whose history matters most.

    So the provenance rides along and the spine states it: an unqualified
    diagnosis line that silently belongs to a note from March is worse than no
    line at all. There is no `stage` field in this schema yet — the spine renders
    the diagnosis alone rather than inventing a staging vocabulary the record
    cannot support.
    """

    text: str
    #: The date of the visit the signed note belongs to.
    on: date_type
    is_current_visit: bool


@dataclass(slots=True)
class PatientCard:
    patient_id: uuid.UUID
    visit_id: uuid.UUID
    intake_id: uuid.UUID | None
    mrn: str
    name: str
    age: int | None
    sex: str | None
    lang: str
    village: str | None
    phone: str
    token_no: int | None
    department_name: str
    visit_date: date_type
    entry_id: uuid.UUID | None
    entry_state: str | None
    chief_complaint: str | None
    chief_complaint_en: str | None
    summary: SummaryView
    summary_md: str | None
    red_flags: list[RedFlagView]
    answers: list[AnswerRow]
    timeline: list[TimelineVisit]
    trends: list[SymptomTrend]
    tier: str | None
    intake_lang: str | None
    completed_at: datetime | None
    #: Who this visit is assigned to. The spine states it so a doctor reading a
    #: colleague's patient knows that is what they are doing before they write.
    assigned_doctor_id: uuid.UUID | None = None
    assigned_doctor_name: str | None = None
    diagnosis: DiagnosisView | None = None
    #: Whether a family member answered the intake instead of the patient. Part
    #: of the provenance line that replaced the old "88% confidence" number: a
    #: confidence percentage nobody can calibrate is false precision, while "a
    #: caregiver answered this, in Hindi, at 09:14, on the conversational tier"
    #: is four facts a doctor can actually weigh.
    caregiver_answered: bool = False
    #: How this consult ended, once a doctor has said (plan §5.3b). Null means
    #: it has not been concluded — which the console must not render as "nothing
    #: was prescribed", because those are different facts.
    rx_mode: str | None = None
    concluded_at: datetime | None = None
    conclusion_note: str | None = None
    #: What this record knows about what she reacts to (SESSION-ALLERGY). The
    #: whole view rather than a list, because the *state* is the clinical fact:
    #: "nobody asked" and "asked, told none" are different situations and the
    #: console must be unable to render them the same way.
    allergies: AllergyView = field(default_factory=lambda: AllergyView(state=NEVER_ASKED))
    #: Whether this visit already has a signed consult note. The console used to
    #: remember this per session, which meant a reload forgot which notes it had
    #: watched get signed — and it is what decides whether completing the consult
    #: is a one-tap `system` conclusion or a question about where the
    #: prescription went.
    note_signed: bool = False


async def patient_card(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> PatientCard:
    """Everything the doctor reads about one patient, in one payload.

    Scoped to the doctor's department: a visit in another room raises rather than
    returning a 404-shaped empty card, because "not yours" and "not there" are
    different answers and the console should not paper over the first.
    """
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise DoctorError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise DoctorError("that patient is in another department")

    patient = await session.get(Patient, visit.patient_id)
    if patient is None:  # pragma: no cover - FK guarantees it
        raise DoctorError("that visit has no patient")
    dept = await session.get(Department, visit.department_id)

    intake = await _latest_intake(session, visit_id=visit.id)
    entry = await _entry_for_visit(session, visit_id=visit.id)
    assigned = await session.get(Doctor, visit.doctor_id) if visit.doctor_id else None

    return PatientCard(
        patient_id=patient.id,
        visit_id=visit.id,
        intake_id=intake.id if intake else None,
        mrn=patient.mrn,
        name=patient.name,
        age=patient.age,
        sex=str(patient.sex) if patient.sex else None,
        lang=str(patient.lang),
        village=patient.village,
        phone=patient.phone,
        token_no=visit.token_no,
        department_name=dept.name if dept else "—",
        visit_date=visit.date,
        entry_id=entry.id if entry else None,
        entry_state=str(entry.state) if entry else None,
        chief_complaint=intake.chief_complaint if intake else None,
        chief_complaint_en=intake.chief_complaint_en if intake else None,
        summary=_summary_view(intake),
        summary_md=intake.summary_md if intake else None,
        red_flags=_red_flag_views(intake),
        answers=_answer_rows(intake),
        timeline=await _timeline(session, patient_id=patient.id, current_visit_id=visit.id),
        trends=await _trends(session, patient_id=patient.id),
        tier=str(intake.tier) if intake else None,
        intake_lang=str(intake.lang) if intake else None,
        completed_at=intake.completed_at if intake else None,
        assigned_doctor_id=assigned.id if assigned else None,
        assigned_doctor_name=assigned.name if assigned else None,
        diagnosis=await _diagnosis(session, patient_id=patient.id, current_visit_id=visit.id),
        caregiver_answered=bool(intake.caregiver_answered) if intake else False,
        rx_mode=str(visit.rx_mode) if visit.rx_mode else None,
        concluded_at=visit.concluded_at,
        conclusion_note=visit.conclusion_note,
        allergies=await allergy_svc.for_patient(session, patient_id=patient.id),
        note_signed=await _has_signed_note(session, visit_id=visit.id),
    )


async def _diagnosis(
    session: AsyncSession, *, patient_id: uuid.UUID, current_visit_id: uuid.UUID
) -> DiagnosisView | None:
    """The latest signed note's diagnosis for this patient, or None.

    Only *signed* notes count. A draft dictation is a doctor thinking out loud
    mid-consult, and promoting one to the permanent line at the top of every
    later screen would put an unreviewed machine transcription where a clinician
    reads a diagnosis. Nothing is re-derived: this reads the field
    `app.dictation` already wrote under the doc 03 §7 contract.
    """
    result = await session.execute(
        select(Dictation.structured, Visit.date, Visit.id)
        .join(Visit, Dictation.visit_id == Visit.id)
        .where(
            Visit.patient_id == patient_id,
            Dictation.signed_at.is_not(None),
            Dictation.deleted_at.is_(None),
            Visit.deleted_at.is_(None),
        )
        .order_by(Dictation.signed_at.desc())
        .limit(5)
    )
    for structured, on, visit_id in result.all():
        text = (structured or {}).get("diagnosis") if isinstance(structured, dict) else None
        if not text:
            continue
        return DiagnosisView(text=str(text), on=on, is_current_visit=visit_id == current_visit_id)
    return None


async def _latest_intake(session: AsyncSession, *, visit_id: uuid.UUID) -> Intake | None:
    """The visit's most recent intake. A visit normally has exactly one; an
    amendment (S18) would add a second, and the newest is the one that counts."""
    return await session.scalar(
        select(Intake)
        .where(Intake.visit_id == visit_id, Intake.deleted_at.is_(None))
        .order_by(Intake.created_at.desc())
        .limit(1)
    )


async def _entry_for_visit(session: AsyncSession, *, visit_id: uuid.UUID):
    from app.models.scheduling import QueueEntry

    return await session.scalar(
        select(QueueEntry).where(QueueEntry.visit_id == visit_id, QueueEntry.deleted_at.is_(None))
    )


def _summary_view(intake: Intake | None) -> SummaryView:
    """The stored §4 contract, whichever language version carries it.

    `summary_lang_versions` is keyed by the *patient's* language because that is
    what the read-back was spoken in, but the structured body inside is the
    doctor's English (doc 03 §4: "generated in English for doctor"). So any
    version's `structured` is the right one to render; we take the first.
    """
    if intake is None:
        return SummaryView()
    for version in (intake.summary_lang_versions or {}).values():
        structured = (version or {}).get("structured") if isinstance(version, dict) else None
        if not isinstance(structured, dict):
            continue
        return SummaryView(
            chief_concern=structured.get("chief_concern"),
            hpi=[str(item) for item in structured.get("hpi") or []],
            symptoms=[
                {str(k): str(v) for k, v in row.items()}
                for row in structured.get("symptoms") or []
                if isinstance(row, dict)
            ],
            history_meds=[str(item) for item in structured.get("history_meds") or []],
            since_last_visit=[str(item) for item in structured.get("since_last_visit") or []],
            patient_words={
                str(k): str(v) for k, v in (structured.get("patient_words") or {}).items()
            },
            unclear=[str(item) for item in structured.get("unclear") or []],
        )
    return SummaryView()


def _red_flag_views(intake: Intake | None) -> list[RedFlagView]:
    """The rule engine's flags, English-labelled for the strip.

    Read straight off `Intake.red_flags` — the shape `RedFlagHit.to_json()`
    wrote. Nothing is recomputed here (see the module docstring).
    """
    if intake is None:
        return []
    views = []
    for flag in intake.red_flags or []:
        if not isinstance(flag, dict):
            continue
        label = flag.get("label") or {}
        instruction = flag.get("instruction") or {}
        views.append(
            RedFlagView(
                id=str(flag.get("id", "")),
                severity=str(flag.get("severity", "urgent")),
                label=_pick_en(label) or str(flag.get("id", "")),
                instruction=_pick_en(instruction),
                source_node=flag.get("source_node"),
            )
        )
    return views


def _pick_en(mapping: Any) -> str:
    if not isinstance(mapping, dict):
        return ""
    return str(mapping.get(str(Lang.EN)) or next(iter(mapping.values()), "") or "")


def _flagged_nodes(tree: Tree | None, fired: list[Any]) -> set[str]:
    """Which answers made this patient dangerous.

    `source_node` alone is not enough: it is only populated for flags authored as
    node-level sugar (`red_flag_if` / `flag: true`). The clinically interesting
    rules are the multi-node ones — "fever ≥38 **and** within 14 days of chemo" —
    and those carry no source node at all, so highlighting on `source_node` would
    silently leave the febrile-neutropenia patient's fever unmarked.

    So we read the *fired* flags back against the tree's own `RedFlagSpec.when`
    and collect every node the condition references. Nothing is re-evaluated here
    — which flags fired was decided by the rule engine (`app.trees.rules`) and is
    read from `Intake.red_flags`; this only asks the tree which questions each
    fired rule was about.
    """
    if tree is None:
        return set()
    fired_ids = {str(flag.get("id")) for flag in fired if isinstance(flag, dict) and flag.get("id")}
    nodes: set[str] = set()
    for flag in fired:
        if isinstance(flag, dict) and flag.get("source_node"):
            nodes.add(str(flag["source_node"]))
    for spec in tree.red_flags:
        if spec.id in fired_ids:
            nodes |= _nodes_in_condition(spec.when)
    return nodes


def _nodes_in_condition(condition: Any) -> set[str]:
    """Every `node` referenced by a rule condition, at any nesting depth."""
    found: set[str] = set()
    if isinstance(condition, Mapping):
        node = condition.get("node")
        if isinstance(node, str):
            found.add(node)
        for value in condition.values():
            found |= _nodes_in_condition(value)
    elif isinstance(condition, list | tuple):
        for item in condition:
            found |= _nodes_in_condition(item)
    return found


def _answer_rows(intake: Intake | None) -> list[AnswerRow]:
    """The intake's answers as asked, in tree order where the tree is known.

    `Intake.tree_ref` (`key@vN`) is what makes this readable at all: node ids are
    stable across versions by design, so the same JSONB means different questions
    depending on which version was asked (S7). If the tree is missing from the
    bank we still render the answers — with the node id as the question — rather
    than dropping clinical content the patient actually gave.
    """
    if intake is None or not intake.answers:
        return []
    tree = _tree_for(intake.tree_ref)
    flagged_nodes = _flagged_nodes(tree, intake.red_flags or [])

    node_order = list(tree.nodes) if tree else []
    stored = intake.answers

    def sort_key(node_id: str) -> tuple[int, str]:
        return (node_order.index(node_id) if node_id in node_order else len(node_order), node_id)

    rows = []
    for node_id in sorted(stored, key=sort_key):
        answer = stored.get(node_id)
        if not isinstance(answer, dict):
            continue
        question = node_id
        rendered = str(answer.get("value", ""))
        if tree is not None:
            try:
                node = tree.node(node_id)
            except TreeError:
                node = None
            if node is not None:
                question = node.ask(Lang.EN) or node_id
                rendered = _render_value(node, answer.get("value"))
        said = answer.get("text_en") or answer.get("text")
        rows.append(
            AnswerRow(
                node_id=node_id,
                question=question,
                answer=rendered,
                said=str(said) if said else None,
                flagged=node_id in flagged_nodes,
            )
        )
    return rows


def _render_value(node: Any, value: Any) -> str:
    """An answer in the doctor's English: option labels, not option ids."""
    if isinstance(value, list):
        return ", ".join(_render_value(node, item) for item in value)
    option = node.option(str(value)) if value is not None else None
    if option is not None:
        return option.text.get(str(Lang.EN)) or str(value)
    if value is None:
        return ""
    if node.unit:
        return f"{value} {node.unit}"
    return str(value)


def _tree_for(tree_ref: str | None) -> Tree | None:
    if not tree_ref:
        return None
    key = tree_ref.split("@", 1)[0]
    try:
        return bank.get(key)
    except TreeError:
        return None


async def _timeline(
    session: AsyncSession, *, patient_id: uuid.UUID, current_visit_id: uuid.UUID
) -> list[TimelineVisit]:
    """This patient's visits, newest first, with the chief complaint of each.

    The doctor's question is "have they been here before, and for what?" — so
    every visit is listed, not just this department's: a palliative patient who
    was in surgical oncology last month is exactly the context that matters.
    """
    result = await session.execute(
        select(Visit, Department.name)
        .join(Department, Visit.department_id == Department.id)
        .where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
        .order_by(Visit.date.desc(), Visit.created_at.desc())
        .limit(20)
    )
    rows = list(result.all())
    complaints = await _complaints_for_visits(session, [visit.id for visit, _ in rows])
    return [
        TimelineVisit(
            visit_id=visit.id,
            date=visit.date,
            department_name=dept_name,
            status=str(visit.status),
            token_no=visit.token_no,
            chief_complaint=complaints.get(visit.id),
            is_current=visit.id == current_visit_id,
        )
        for visit, dept_name in rows
    ]


async def _complaints_for_visits(
    session: AsyncSession, visit_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    if not visit_ids:
        return {}
    result = await session.execute(
        select(Intake.visit_id, Intake.chief_complaint_en, Intake.chief_complaint).where(
            Intake.visit_id.in_(visit_ids), Intake.deleted_at.is_(None)
        )
    )
    out: dict[uuid.UUID, str | None] = {}
    for visit_id, chief_en, chief in result.all():
        out.setdefault(visit_id, chief_en or chief)
    return out


async def _trends(session: AsyncSession, *, patient_id: uuid.UUID) -> list[SymptomTrend]:
    """Check-in scores over time, one series per symptom (doc 03 §5).

    `Checkin.responses` is S17's shape and is not built yet, so this reads it
    defensively: any numeric value keyed by a symptom name becomes a point, and
    anything else is skipped. That way the sparklines light up the moment S17
    starts writing real check-ins, without this module guessing a schema now.
    """
    result = await session.execute(
        select(Checkin)
        .join(CheckinPlan, Checkin.plan_id == CheckinPlan.id)
        .where(
            CheckinPlan.patient_id == patient_id,
            Checkin.deleted_at.is_(None),
            Checkin.responses != {},
        )
        .order_by(Checkin.due_at)
    )
    series: dict[str, list[TrendPoint]] = {}
    for checkin in result.scalars().all():
        at = checkin.sent_at or checkin.due_at
        for symptom, value in (checkin.responses or {}).items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            series.setdefault(str(symptom), []).append(TrendPoint(at=at, value=float(value)))
    # A single point is not a trend — it draws as a dot and reads as noise.
    return [
        SymptomTrend(symptom=symptom, points=points)
        for symptom, points in sorted(series.items())
        if len(points) > 1
    ]
