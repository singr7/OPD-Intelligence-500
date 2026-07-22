"""The doctor's console read models (doc 03 §5).

Two reads, no writes. That is the whole design: the doctor's *actions* — call
next, no-show, send to lab and re-queue — are the S8 queue verbs
(`app.queue.call_next` / `set_state`) that the coordinator console already
drives, so this module deliberately owns no mutation. A second implementation of
"call the next token" is how a queue ends up with two sources of truth that
disagree the moment one of them is patched.

* `day_list` — the doctor's worklist for a day: their department's queue, with
  the patient behind each token. The coordinator's `department_queue` already
  orders the line (urgent first, by construction); this adds identity and the
  red-flag count, and refuses to leave the doctor's own department.
* `patient_card` — one patient's story, assembled for a 20-second read (doc 04
  §3): the §4 summary, the red-flag strip, the answers as asked, the visit
  timeline and the check-in trendline.

**The card never re-derives clinical judgement.** Red flags are read from
`Intake.red_flags`, which the rule engine wrote (`app.trees.rules`); the summary
is read from `Intake.summary_lang_versions[...]["structured"]`, which the
summarizer wrote under the doc 03 §4 contract. Nothing here recomputes either —
a doctor screen that re-decided a flag would show a different clinical picture
than the kiosk told the patient, and than the queue prioritised on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import queue as queue_svc
from app.models.clinical import Intake, Visit
from app.models.content import Checkin, CheckinPlan
from app.models.enums import Lang
from app.models.org import Department, Doctor
from app.models.patient import Patient
from app.trees import bank
from app.trees.schema import Tree, TreeError


class DoctorError(Exception):
    """The caller is a doctor, but not one who may see this."""


# -- day list -----------------------------------------------------------------


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


@dataclass(slots=True)
class DayList:
    """The doctor's morning: who they are, which room, and the line."""

    doctor_name: str
    department_key: str
    department_name: str
    date: date_type
    rows: list[DayRow]


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
    session: AsyncSession, *, doctor: Doctor, on: date_type | None = None
) -> DayList:
    """The doctor's department queue for a day, with the patient behind each token.

    Order is the queue's, not ours (`department_queue`): now-serving first, then
    the waiting line sorted `(priority_rank, position, token_no)`, then the lab
    round-trips. An urgent red-flag intake is already at the top by construction
    — the doctor sees the same order the board and the coordinator do.
    """
    on = on or queue_svc.today()
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
            )
        )

    return DayList(
        doctor_name=doctor.name,
        department_key=dept.code,
        department_name=dept.name,
        date=on,
        rows=rows,
    )


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
    )


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
    flagged_nodes = {
        str(flag.get("source_node"))
        for flag in intake.red_flags or []
        if isinstance(flag, dict) and flag.get("source_node")
    }

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
