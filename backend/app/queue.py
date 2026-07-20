"""Queue management (doc 03 §6) — the coordinator's live queue over the visits.

The kiosk (S6) and the offline sync (S7) already put a **token** on a `Visit`:
`allocate_token` owns the number line and its online/offline partition, and this
session does not touch that — a token is a promise made to a patient holding a
slip, and renumbering it is the one thing the whole offline design exists to
prevent. What S8 adds is the *queue*: the ordered list of who is waiting for a
room, who is being seen, who jumped ahead and why.

## One `QueueEntry` per visit, ordering derived from three things

A visit joins the queue once (`enqueue`), keyed unique per (queue, visit). Its
place in line is decided, in order, by:

1. **priority** — `urgent` (a red flag fired) sorts ahead of `semi` ahead of
   `routine`. This is the "urgent red-flag intake auto-jumps" behaviour (doc 03
   §6): it falls out of the sort, it is not a manual move a coordinator must make.
2. **position** — a manual handle the coordinator's drag-reorder rewrites.
   Defaults to the token number, so an untouched queue is in token order.
3. **token_no** — the final tiebreak, so ordering is always total and stable.

Waiting entries sort by `(priority_rank, position, token_no)`; called / in-consult
entries are "now serving" and leave the waiting line. Nothing here decides
priority from clinical judgement — `urgent` comes from the **rules'** red flags
(`priority_from_red_flags`), the same deterministic source as everywhere else
(STATE.md invariant: no model, and here no coordinator either, invents a flag —
though a coordinator *may* manually escalate with a written reason).

## Wait estimate

`estimate_wait` multiplies the number of people ahead by the mean consult time
*observed today* for that department, falling back to a configured seed before
there is anything to measure. It is deliberately a coarse range, not a promise —
doc 01 §7's success metric is ±15 min.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.clinical import Intake, Visit
from app.models.enums import Channel, IntakeTier, Lang, Priority, QueueEntryState, VisitStatus
from app.models.org import Department
from app.models.patient import Patient
from app.models.scheduling import Queue, QueueEntry

#: Lower sorts earlier. Urgent first — the whole point of the priority column.
PRIORITY_RANK: dict[Priority, int] = {
    Priority.URGENT: 0,
    Priority.SEMI: 1,
    Priority.ROUTINE: 2,
}

#: A waiting entry that has left the line is one of these — "now serving" on the
#: board, or already gone.
_ACTIVE_STATES = (QueueEntryState.CALLED, QueueEntryState.IN_CONSULT)
_GONE_STATES = (QueueEntryState.DONE, QueueEntryState.NO_SHOW)


class QueueError(Exception):
    """A queue operation that cannot proceed — an unknown entry, a bad transition."""


def today() -> date_type:
    return datetime.now(UTC).date()


def _sort_key(entry: QueueEntry) -> tuple[int, int, int]:
    return (
        PRIORITY_RANK.get(entry.priority, 2),
        entry.position if entry.position is not None else entry.token_no,
        entry.token_no,
    )


def priority_from_red_flags(
    red_flags: list[dict] | None, *, lang: Lang | str = Lang.EN
) -> tuple[Priority, str | None]:
    """Map an intake's fired red flags to a queue priority + a reason chip.

    The severity is the flags' own (`RedFlagHit.severity`, from the rules — doc
    03 §1), never re-decided here. The reason is the flag labels joined for the
    chip the board and coordinator show ("why did this token jump?").
    """
    if not red_flags:
        return Priority.ROUTINE, None

    ranked = sorted(
        red_flags,
        key=lambda f: PRIORITY_RANK.get(Priority(f.get("severity", "routine")), 2),
    )
    top = Priority(ranked[0].get("severity", "routine"))
    if top is Priority.ROUTINE:
        return Priority.ROUTINE, None

    labels = []
    for flag in ranked:
        label = flag.get("label") or {}
        text = label.get(str(lang)) or label.get("en") or flag.get("id")
        if text and text not in labels:
            labels.append(text)
    reason = " · ".join(labels[:2]) if labels else None
    return top, reason


# -- read models (what the board and console render) --------------------------


@dataclass(slots=True)
class EntryView:
    """One row in the coordinator's ordered list."""

    id: uuid.UUID
    visit_id: uuid.UUID
    token_no: int
    priority: Priority
    priority_reason: str | None
    state: QueueEntryState
    position: int | None
    chief_complaint: str | None
    red_flag_count: int
    called_at: datetime | None


@dataclass(slots=True)
class DepartmentBoard:
    """One department's slice of the TV board (doc 03 §6)."""

    department_key: str
    department_name: str
    now_serving: int | None
    now_serving_reason: str | None
    next_tokens: list[EntryView]
    waiting_count: int
    est_wait_low: int
    est_wait_high: int


# -- queue lifecycle ----------------------------------------------------------


async def get_or_create_queue(
    session: AsyncSession, *, department_id: uuid.UUID, on: date_type | None = None
) -> Queue:
    """The one queue for a department for a day (doctor-agnostic at pilot scale).

    Keyed `uq(department_id, doctor_id, date)`; we run one queue per department
    with `doctor_id` null — a walk-in kiosk does not choose a doctor, and the
    board is per room/department (doc 03 §6). Per-doctor queues are a later split
    when rooms are assigned (backlog, S9/S18).
    """
    on = on or today()
    existing = await session.scalar(
        select(Queue).where(
            Queue.department_id == department_id,
            Queue.doctor_id.is_(None),
            Queue.date == on,
        )
    )
    if existing is not None:
        return existing

    queue = Queue(department_id=department_id, doctor_id=None, date=on)
    try:
        async with session.begin_nested():
            session.add(queue)
            await session.flush()
        return queue
    except IntegrityError:
        # Another request created it between the select and the flush — take theirs.
        return await session.scalar(  # type: ignore[return-value]
            select(Queue).where(
                Queue.department_id == department_id,
                Queue.doctor_id.is_(None),
                Queue.date == on,
            )
        )


async def enqueue(
    session: AsyncSession,
    *,
    visit: Visit,
    priority: Priority = Priority.ROUTINE,
    priority_reason: str | None = None,
) -> QueueEntry:
    """Put a visit in its department's queue (idempotent per visit).

    Requires a token — a queue entry without a number has nothing to call. The
    visit's status moves to IN_QUEUE. Re-enqueuing the same visit returns the
    existing entry and, if a red flag has since escalated it, raises its priority
    (a routine entry can become urgent; it never silently drops back to routine).
    """
    if visit.token_no is None:
        raise QueueError("cannot enqueue a visit with no token")

    queue = await get_or_create_queue(session, department_id=visit.department_id, on=visit.date)

    existing = await session.scalar(
        select(QueueEntry).where(
            QueueEntry.queue_id == queue.id, QueueEntry.visit_id == visit.id
        )
    )
    if existing is not None:
        if PRIORITY_RANK.get(priority, 2) < PRIORITY_RANK.get(existing.priority, 2):
            existing.priority = priority
            existing.priority_reason = priority_reason
            await session.flush()
        return existing

    entry = QueueEntry(
        queue_id=queue.id,
        visit_id=visit.id,
        token_no=visit.token_no,
        priority=priority,
        priority_reason=priority_reason,
        # Default the manual handle to the token so an untouched queue reads in
        # token order; the coordinator's drag rewrites it (`reorder`).
        position=visit.token_no,
        state=QueueEntryState.WAITING,
    )
    session.add(entry)
    visit.status = VisitStatus.IN_QUEUE
    await session.flush()
    return entry


async def enqueue_from_intake(
    session: AsyncSession, *, visit: Visit, intake: Intake
) -> QueueEntry:
    """Enqueue a confirmed intake, taking its priority from its red flags.

    This is the seam the kiosk confirm (and offline sync) calls: an intake that
    tripped a red flag lands as `urgent` with a reason chip and jumps the queue,
    with no coordinator action (doc 03 §6 AC).

    The reason is stored in English: it is a staff-facing clinical label on the
    coordinator console, not patient copy (the board shows it as an urgent chip,
    not a sentence the patient reads).
    """
    priority, reason = priority_from_red_flags(intake.red_flags, lang=Lang.EN)
    return await enqueue(session, visit=visit, priority=priority, priority_reason=reason)


async def _entries_for_queue(
    session: AsyncSession, queue_id: uuid.UUID
) -> list[QueueEntry]:
    result = await session.execute(
        select(QueueEntry).where(QueueEntry.queue_id == queue_id)
    )
    return list(result.scalars().all())


def _waiting_sorted(entries: list[QueueEntry]) -> list[QueueEntry]:
    waiting = [e for e in entries if e.state == QueueEntryState.WAITING]
    waiting.sort(key=_sort_key)
    return waiting


async def call_next(
    session: AsyncSession, *, queue_id: uuid.UUID
) -> QueueEntry | None:
    """Call the front of the line: mark it CALLED, stamp `called_at`.

    Returns the called entry, or None if nobody is waiting. Any entry already
    CALLED (called but not yet seen) is left as-is — calling next does not skip a
    patient who was just announced; the coordinator marks them seen / no-show
    explicitly (`set_state`).
    """
    entries = await _entries_for_queue(session, queue_id)
    waiting = _waiting_sorted(entries)
    if not waiting:
        return None
    entry = waiting[0]
    entry.state = QueueEntryState.CALLED
    entry.called_at = datetime.now(UTC)
    await session.flush()
    return entry


#: The transitions a coordinator/doctor may drive. Everything else raises rather
#: than silently corrupting a queue's state (e.g. DONE → WAITING).
_ALLOWED_TRANSITIONS: dict[QueueEntryState, set[QueueEntryState]] = {
    QueueEntryState.WAITING: {QueueEntryState.CALLED, QueueEntryState.NO_SHOW},
    QueueEntryState.CALLED: {
        QueueEntryState.IN_CONSULT,
        QueueEntryState.NO_SHOW,
        QueueEntryState.WAITING,  # re-queue a no-answer without penalty
        QueueEntryState.LAB_REQUEUE,
    },
    QueueEntryState.IN_CONSULT: {
        QueueEntryState.DONE,
        QueueEntryState.LAB_REQUEUE,
    },
    QueueEntryState.LAB_REQUEUE: {QueueEntryState.WAITING, QueueEntryState.DONE},
    QueueEntryState.NO_SHOW: {QueueEntryState.WAITING},  # they turned up after all
    QueueEntryState.DONE: set(),
}

_VISIT_STATUS_FOR: dict[QueueEntryState, VisitStatus] = {
    QueueEntryState.WAITING: VisitStatus.IN_QUEUE,
    QueueEntryState.CALLED: VisitStatus.IN_QUEUE,
    QueueEntryState.IN_CONSULT: VisitStatus.IN_CONSULT,
    QueueEntryState.DONE: VisitStatus.DONE,
    QueueEntryState.NO_SHOW: VisitStatus.NO_SHOW,
    QueueEntryState.LAB_REQUEUE: VisitStatus.IN_QUEUE,
}


async def set_state(
    session: AsyncSession, *, entry_id: uuid.UUID, state: QueueEntryState
) -> QueueEntry:
    """Transition one entry, keeping the visit status and timestamps in step.

    Refuses an illegal transition (e.g. a DONE entry back to WAITING) — the
    board and console must not be able to represent a patient who is both seen
    and waiting. LAB_REQUEUE → WAITING re-joins the line at the back of its
    priority (its position is bumped past the current tail) so a lab round-trip
    does not send someone straight to the front.
    """
    entry = await session.get(QueueEntry, entry_id)
    if entry is None:
        raise QueueError(f"no such queue entry {entry_id}")
    if state != entry.state and state not in _ALLOWED_TRANSITIONS.get(entry.state, set()):
        raise QueueError(f"cannot move a {entry.state.value} entry to {state.value}")

    now = datetime.now(UTC)
    if state == QueueEntryState.CALLED and entry.called_at is None:
        entry.called_at = now
    if state == QueueEntryState.IN_CONSULT and entry.started_at is None:
        entry.started_at = now
    if state in _GONE_STATES:
        entry.ended_at = now
    if state == QueueEntryState.WAITING and entry.state == QueueEntryState.LAB_REQUEUE:
        entry.position = await _tail_position(session, entry.queue_id)
        entry.ended_at = None

    entry.state = state
    visit = await session.get(Visit, entry.visit_id)
    if visit is not None:
        visit.status = _VISIT_STATUS_FOR[state]
    await session.flush()
    return entry


async def _tail_position(session: AsyncSession, queue_id: uuid.UUID) -> int:
    highest = await session.scalar(
        select(func.max(QueueEntry.position)).where(QueueEntry.queue_id == queue_id)
    )
    return int(highest or 0) + 1


async def reorder(
    session: AsyncSession, *, queue_id: uuid.UUID, ordered_ids: list[uuid.UUID]
) -> list[QueueEntry]:
    """Rewrite `position` to the given order (the coordinator's drag-reorder).

    Only the manual handle moves; priority still wins the sort, so a drag cannot
    demote an urgent red-flag token below a routine one (the board would then
    disagree with the rules). Ids not in `ordered_ids` keep their positions,
    numbered after the listed ones.
    """
    entries = {e.id: e for e in await _entries_for_queue(session, queue_id)}
    unknown = [i for i in ordered_ids if i not in entries]
    if unknown:
        raise QueueError(f"entries not in this queue: {unknown}")

    for rank, entry_id in enumerate(ordered_ids, start=1):
        entries[entry_id].position = rank
    await session.flush()
    return [entries[i] for i in ordered_ids]


# -- estimation + snapshots ---------------------------------------------------


async def _mean_consult_minutes(
    session: AsyncSession, *, queue_id: uuid.UUID
) -> float:
    """Observed mean consult time today, or the configured seed if none yet."""
    result = await session.execute(
        select(QueueEntry.started_at, QueueEntry.ended_at).where(
            QueueEntry.queue_id == queue_id,
            QueueEntry.state == QueueEntryState.DONE,
            QueueEntry.started_at.is_not(None),
            QueueEntry.ended_at.is_not(None),
        )
    )
    durations = [
        (ended - started).total_seconds() / 60
        for started, ended in result.all()
        if ended and started and ended > started
    ]
    if not durations:
        return float(get_settings().queue_default_consult_minutes)
    return sum(durations) / len(durations)


def estimate_wait(*, ahead: int, mean_minutes: float) -> tuple[int, int]:
    """A coarse ±20% range in whole minutes for `ahead` people at `mean_minutes`.

    Coarse on purpose (doc 01 §7: ±15 min is success) — a precise single number
    on a TV board reads as a promise the OPD cannot keep.
    """
    expected = ahead * mean_minutes
    low = int(expected * 0.8)
    high = max(low + 5, int(round(expected * 1.2 / 5) * 5))
    return low, high


async def _view(entry: QueueEntry, *, chief: str | None, flags: int) -> EntryView:
    return EntryView(
        id=entry.id,
        visit_id=entry.visit_id,
        token_no=entry.token_no,
        priority=entry.priority,
        priority_reason=entry.priority_reason,
        state=entry.state,
        position=entry.position,
        chief_complaint=chief,
        red_flag_count=flags,
        called_at=entry.called_at,
    )


async def _chief_and_flags(
    session: AsyncSession, visit_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[str | None, int]]:
    """One query for the chief complaint + red-flag count per visit's intake."""
    if not visit_ids:
        return {}
    result = await session.execute(
        select(Intake.visit_id, Intake.chief_complaint, Intake.red_flags).where(
            Intake.visit_id.in_(visit_ids)
        )
    )
    out: dict[uuid.UUID, tuple[str | None, int]] = {}
    for visit_id, chief, red_flags in result.all():
        out[visit_id] = (chief, len(red_flags or []))
    return out


async def department_queue(
    session: AsyncSession, *, department_id: uuid.UUID, on: date_type | None = None
) -> list[EntryView]:
    """The coordinator's full ordered list for a department (all non-gone states).

    Now-serving (called / in consult) first, then the sorted waiting line. Done
    and no-show entries are dropped — the console is a worklist, not a log.
    """
    on = on or today()
    queue = await session.scalar(
        select(Queue).where(
            Queue.department_id == department_id,
            Queue.doctor_id.is_(None),
            Queue.date == on,
        )
    )
    if queue is None:
        return []
    entries = await _entries_for_queue(session, queue.id)
    meta = await _chief_and_flags(session, [e.visit_id for e in entries])

    active = [e for e in entries if e.state in _ACTIVE_STATES]
    active.sort(key=lambda e: (e.called_at or datetime.min.replace(tzinfo=UTC)))
    waiting = _waiting_sorted(entries)
    lab = [e for e in entries if e.state == QueueEntryState.LAB_REQUEUE]
    lab.sort(key=_sort_key)

    views = []
    for entry in [*active, *waiting, *lab]:
        chief, flags = meta.get(entry.visit_id, (None, 0))
        views.append(await _view(entry, chief=chief, flags=flags))
    return views


async def board(
    session: AsyncSession, *, on: date_type | None = None
) -> list[DepartmentBoard]:
    """The whole TV board: every active department, now-serving + next 3 + wait.

    A department with nothing queued today is omitted — the board shows rooms
    that are actually running (doc 04 §3: "absolutely no clutter").
    """
    on = on or today()
    result = await session.execute(
        select(Queue)
        .where(Queue.date == on, Queue.doctor_id.is_(None))
        .options(selectinload(Queue.entries))
    )
    queues = list(result.scalars().all())
    dept_ids = [q.department_id for q in queues]
    if not dept_ids:
        return []

    depts = {
        d.id: d
        for d in (
            await session.execute(select(Department).where(Department.id.in_(dept_ids)))
        )
        .scalars()
        .all()
    }
    all_visit_ids = [e.visit_id for q in queues for e in q.entries]
    meta = await _chief_and_flags(session, all_visit_ids)

    boards: list[DepartmentBoard] = []
    for queue in queues:
        dept = depts.get(queue.department_id)
        if dept is None:
            continue
        entries = list(queue.entries)
        active = [e for e in entries if e.state in _ACTIVE_STATES]
        active.sort(key=lambda e: (e.called_at or datetime.min.replace(tzinfo=UTC)))
        waiting = _waiting_sorted(entries)
        if not active and not waiting:
            continue  # department is done for the day — keep it off the board

        now = active[-1] if active else None
        mean = await _mean_consult_minutes(session, queue_id=queue.id)
        next_views = []
        for idx, entry in enumerate(waiting[:3]):
            chief, flags = meta.get(entry.visit_id, (None, 0))
            next_views.append(await _view(entry, chief=chief, flags=flags))
        # Wait for the person at the back of "next 3" — the useful number for a
        # patient reading the board is "how long until roughly me".
        low, high = estimate_wait(ahead=len(waiting), mean_minutes=mean)
        boards.append(
            DepartmentBoard(
                department_key=dept.code,
                department_name=dept.name,
                now_serving=now.token_no if now else None,
                now_serving_reason=now.priority_reason if now else None,
                next_tokens=next_views,
                waiting_count=len(waiting),
                est_wait_low=low,
                est_wait_high=high,
            )
        )
    boards.sort(key=lambda b: b.department_key)
    return boards


# -- downtime paper entry (doc 01 §5 point 3) ---------------------------------


@dataclass(slots=True)
class PaperEntryResult:
    visit_id: uuid.UUID
    intake_id: uuid.UUID
    token_no: int
    priority: Priority


async def paper_entry(
    session: AsyncSession,
    *,
    department: Department,
    token_no: int,
    lang: Lang,
    chief_complaint: str | None,
    patient_name: str | None = None,
    urgent: bool = False,
    urgent_reason: str | None = None,
) -> PaperEntryResult:
    """Batch-enter one paper intake sheet after a total blackout (doc 01 §5 pt 3).

    "The hospital ran on paper yesterday; downtime mode is paper with a memory."
    On recovery the coordinator types the paper sheets in: this creates the same
    `Visit` + `Intake` a kiosk would (channel=PAPER), keeps the **token the
    patient is already holding** from the printed paper block, and enqueues it.
    The coordinator can mark it urgent by hand (a nurse's judgement on a paper
    sheet) — the one place a human, not the rules, may set priority, and it
    carries a written reason for the audit trail.
    """
    patient = Patient(
        hospital_id=department.hospital_id,
        mrn=f"PAPER-{uuid.uuid4().hex[:10].upper()}",
        name=patient_name or "Paper intake",
        phone="",
        lang=lang,
    )
    session.add(patient)
    await session.flush()

    visit = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=today(),
        status=VisitStatus.REGISTERED,
        channel=Channel.PAPER,
        token_no=token_no,
    )
    session.add(visit)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise QueueError(
            f"token {token_no} is already issued for {department.code} today"
        ) from exc

    intake = Intake(
        visit_id=visit.id,
        tier=IntakeTier.PAPER,
        lang=lang,
        chief_complaint=chief_complaint,
        confirmed_by_patient=True,
        completed_at=datetime.now(UTC),
    )
    session.add(intake)
    await session.flush()

    priority = Priority.URGENT if urgent else Priority.ROUTINE
    reason = urgent_reason if urgent else None
    await enqueue(session, visit=visit, priority=priority, priority_reason=reason)
    return PaperEntryResult(
        visit_id=visit.id, intake_id=intake.id, token_no=token_no, priority=priority
    )
