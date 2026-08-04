"""Queue HTTP + WebSocket surface (doc 03 §6).

Three audiences, three access levels:

* **Board** (`GET /queue/board`, `WS /queue/ws`) — a TV in the waiting room. No
  login: it hangs on a wall. It shows only tokens, rooms and wait ranges, never a
  name or a chief complaint, so there is nothing to authenticate to protect.
* **Console** (`GET /queue/console`, the action verbs) — the coordinator. Staff
  auth: it shows chief complaints and drives the queue.
* **Downtime / reconciliation / paper entry** — the coordinator's outage tools.

Every mutation ends by nudging the hub (`notify_queue_changed`), so the board and
any open console re-fetch and stay live (the AC1 "three browsers live-sync").
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from app import assignment as assignment_svc
from app import kiosk as kiosk_svc
from app import offline as offline_svc
from app import print_sheets
from app import queue as queue_svc
from app.auth.rbac import Principal, require_staff
from app.db import get_session
from app.models.clinical import Intake, Visit
from app.models.enums import Lang, QueueEntryState
from app.models.org import Department, Doctor, Hospital
from app.models.scheduling import QueueEntry
from app.queue_hub import QueueHub
from app.trees import bank

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


# -- dependencies -------------------------------------------------------------


def get_hub(request: Request) -> QueueHub:
    hub = getattr(request.app.state, "queue_hub", None)
    if hub is None:  # pragma: no cover - lifespan always sets it
        raise HTTPException(status_code=503, detail="queue hub not ready")
    return hub


async def _department(session: AsyncSession, dept_key: str) -> Department:
    dept = await session.scalar(select(Department).where(Department.code == dept_key))
    if dept is None:
        raise HTTPException(status_code=404, detail=f"unknown department {dept_key!r}")
    return dept


# -- wire models --------------------------------------------------------------


class BoardEntryOut(BaseModel):
    token_no: int
    priority: str
    priority_reason: str | None = None
    red_flag: bool = False
    patient_name: str | None = None


class BoardDeptOut(BaseModel):
    department_key: str
    department_name: str
    doctor_name: str | None = None
    now_serving: int | None
    now_serving_reason: str | None
    now_serving_name: str | None = None
    next: list[BoardEntryOut]
    waiting_count: int
    est_wait_low: int
    est_wait_high: int


class BoardOut(BaseModel):
    downtime: bool
    departments: list[BoardDeptOut]


class ConsoleEntryOut(BaseModel):
    id: uuid.UUID
    token_no: int
    priority: str
    priority_reason: str | None
    state: str
    chief_complaint: str | None
    red_flag_count: int
    patient_name: str | None = None


class ConsoleDeptOut(BaseModel):
    department_key: str
    department_name: str
    doctor_name: str | None = None
    entries: list[ConsoleEntryOut]


class ConsoleOut(BaseModel):
    downtime: bool
    departments: list[ConsoleDeptOut]


class CallNextIn(BaseModel):
    department_key: str


class StateIn(BaseModel):
    state: QueueEntryState


class ReorderIn(BaseModel):
    department_key: str
    ordered_ids: list[uuid.UUID]


class DowntimeIn(BaseModel):
    active: bool


class DowntimeOut(BaseModel):
    active: bool
    since: str | None = None


class PaperEntryIn(BaseModel):
    department_key: str
    token_no: int = Field(ge=1)
    lang: Lang = Lang.HI
    chief_complaint: str | None = Field(default=None, max_length=2000)
    patient_name: str | None = Field(default=None, max_length=200)
    urgent: bool = False
    urgent_reason: str | None = Field(default=None, max_length=200)


class ReconEntryOut(BaseModel):
    intake_id: uuid.UUID
    visit_id: uuid.UUID
    token_no: int | None
    department_key: str
    channel: str
    chief_complaint: str | None
    red_flag_count: int
    client_id: str | None
    completed_at: str | None


class ReconOut(BaseModel):
    count: int
    entries: list[ReconEntryOut]


class PaperEntryOut(BaseModel):
    visit_id: uuid.UUID
    intake_id: uuid.UUID
    token_no: int
    priority: str


# -- board (public) -----------------------------------------------------------


@router.get("/board", response_model=BoardOut)
async def get_board(
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
) -> BoardOut:
    """The TV board (doc 04 §3). Public — tokens and waits, no PII."""
    boards = await queue_svc.board(session)
    return BoardOut(
        downtime=hub.downtime,
        departments=[
            BoardDeptOut(
                department_key=b.department_key,
                department_name=b.department_name,
                doctor_name=b.doctor_name,
                now_serving=b.now_serving,
                now_serving_reason=b.now_serving_reason,
                now_serving_name=b.now_serving_name,
                next=[
                    BoardEntryOut(
                        token_no=e.token_no,
                        priority=e.priority.value,
                        priority_reason=e.priority_reason,
                        red_flag=e.red_flag_count > 0,
                        patient_name=e.patient_name,
                    )
                    for e in b.next_tokens
                ],
                waiting_count=b.waiting_count,
                est_wait_low=b.est_wait_low,
                est_wait_high=b.est_wait_high,
            )
            for b in boards
        ],
    )


@router.websocket("/ws")
async def queue_ws(ws: WebSocket) -> None:
    """The live-sync socket for board + console. Public (the board has no login).

    Carries only change *pings* and the downtime flag — no PII crosses it, so a
    wall-mounted TV can hold it open with no credential. Clients re-fetch their
    own snapshot on a ping.

    The hub is read off `ws.app.state` rather than via `Depends(get_hub)`: a
    WebSocket scope has no `Request`, so the Request-typed dependency can't be
    resolved here (this bit us once — the handshake 500'd).
    """
    hub: QueueHub | None = getattr(ws.app.state, "queue_hub", None)
    if hub is None:  # pragma: no cover - lifespan always sets it
        await ws.close(code=1011)
        return
    await hub.connect(ws)
    try:
        while True:
            # We do not expect messages from clients; receiving is how we notice a
            # disconnect promptly rather than only on the next failed broadcast.
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:  # noqa: BLE001
        await hub.disconnect(ws)


# -- console (staff) ----------------------------------------------------------


@router.get("/console", response_model=ConsoleOut)
async def get_console(
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
    _: Principal = Depends(require_staff),
) -> ConsoleOut:
    """The coordinator's full ordered worklist, per department."""
    boards = await queue_svc.board(session)
    departments = []
    for b in boards:
        dept = await _department(session, b.department_key)
        entries = await queue_svc.department_queue(session, department_id=dept.id)
        departments.append(
            ConsoleDeptOut(
                department_key=b.department_key,
                department_name=b.department_name,
                doctor_name=b.doctor_name,
                entries=[
                    ConsoleEntryOut(
                        id=e.id,
                        token_no=e.token_no,
                        priority=e.priority.value,
                        priority_reason=e.priority_reason,
                        state=e.state.value,
                        chief_complaint=e.chief_complaint,
                        red_flag_count=e.red_flag_count,
                        patient_name=e.patient_name,
                    )
                    for e in entries
                ],
            )
        )
    return ConsoleOut(downtime=hub.downtime, departments=departments)


@router.post("/call-next", response_model=ConsoleEntryOut | dict)
async def call_next(
    payload: CallNextIn,
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
    _: Principal = Depends(require_staff),
) -> object:
    dept = await _department(session, payload.department_key)
    queue = await queue_svc.get_or_create_queue(session, department_id=dept.id)
    entry = await queue_svc.call_next(session, queue_id=queue.id)
    await session.commit()
    await hub.notify_queue_changed()
    if entry is None:
        return {"called": None}
    return ConsoleEntryOut(
        id=entry.id,
        token_no=entry.token_no,
        priority=entry.priority.value,
        priority_reason=entry.priority_reason,
        state=entry.state.value,
        chief_complaint=None,
        red_flag_count=0,
    )


@router.post("/entries/{entry_id}/state", response_model=ConsoleEntryOut)
async def set_entry_state(
    entry_id: uuid.UUID,
    payload: StateIn,
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
    _: Principal = Depends(require_staff),
) -> ConsoleEntryOut:
    try:
        entry = await queue_svc.set_state(session, entry_id=entry_id, state=payload.state)
    except queue_svc.QueueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await hub.notify_queue_changed()
    return ConsoleEntryOut(
        id=entry.id,
        token_no=entry.token_no,
        priority=entry.priority.value,
        priority_reason=entry.priority_reason,
        state=entry.state.value,
        chief_complaint=None,
        red_flag_count=0,
    )


class EntryAssignIn(BaseModel):
    link_candidate: bool | None = None
    department_key: str | None = None
    doctor_id: uuid.UUID | None = None


class EntryAssignOut(BaseModel):
    entry_id: uuid.UUID
    visit_id: uuid.UUID
    department_key: str
    department_name: str
    assigned_doctor_id: uuid.UUID | None = None
    assigned_doctor_name: str | None = None
    link_state: str
    token_no: int | None = None
    previous_token_no: int | None = None
    token_reissued: bool = False


class AssignableDoctorOut(BaseModel):
    id: uuid.UUID
    name: str
    qualification: str | None = None
    on_duty: bool


@router.get("/entries/{entry_id}/assignable", response_model=list[AssignableDoctorOut])
async def assignable(
    entry_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_staff),
) -> list[AssignableDoctorOut]:
    """Who this entry's department can be assigned to today."""
    entry, visit = await _entry_and_visit(session, entry_id)
    options = await assignment_svc.assignable_doctors(
        session, department_id=visit.department_id, on=visit.date
    )
    return [
        AssignableDoctorOut(id=o.id, name=o.name, qualification=o.qualification, on_duty=o.on_duty)
        for o in options
    ]


@router.post("/entries/{entry_id}/assign", response_model=EntryAssignOut)
async def assign_entry(
    entry_id: uuid.UUID,
    payload: EntryAssignIn,
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
    _: Principal = Depends(require_staff),
) -> EntryAssignOut:
    """Assign, re-route or link a queued visit from the coordinator console.

    The same verbs as the kiosk's staff strip, reached from the desk instead of
    the terminal. This is the compensating control for every arrival the strip
    did not settle — a `Skip`, and every visit an **offline** kiosk synced with no
    roster to pick from. Without it those patients would sit in the department
    pool with nobody's name on them, which is exactly the state the doctor
    console's `Unassigned` count exists to make impossible to miss.
    """
    entry, visit = await _entry_and_visit(session, entry_id)

    try:
        if payload.link_candidate is True:
            await assignment_svc.confirm_link(session, visit=visit)
        elif payload.link_candidate is False:
            await assignment_svc.reject_link(session, visit=visit)

        department = None
        if payload.department_key:
            department = await kiosk_svc.department_by_code(session, payload.department_key)
            if department is None:
                raise HTTPException(status_code=422, detail="no such department")

        result = await assignment_svc.assign(
            session, visit=visit, doctor_id=payload.doctor_id, department=department
        )
    except assignment_svc.AssignmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    dept = await session.get(Department, visit.department_id)
    doctor = await session.get(Doctor, visit.doctor_id) if visit.doctor_id else None

    await session.commit()
    await hub.notify_queue_changed()

    return EntryAssignOut(
        entry_id=entry.id,
        visit_id=visit.id,
        department_key=dept.code if dept else "",
        department_name=dept.name if dept else "",
        assigned_doctor_id=doctor.id if doctor else None,
        assigned_doctor_name=doctor.name if doctor else None,
        link_state=str(visit.patient_link_state),
        token_no=result.new_token_no,
        previous_token_no=result.old_token_no if result.token_reissued else None,
        token_reissued=result.token_reissued,
    )


async def _entry_and_visit(session: AsyncSession, entry_id: uuid.UUID):
    entry = await session.get(QueueEntry, entry_id)
    if entry is None or entry.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such queue entry")
    visit = await session.get(Visit, entry.visit_id)
    if visit is None or visit.deleted_at is not None:  # pragma: no cover - FK-guaranteed
        raise HTTPException(status_code=404, detail="no such visit")
    return entry, visit


@router.post("/reorder")
async def reorder(
    payload: ReorderIn,
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
    _: Principal = Depends(require_staff),
) -> dict:
    dept = await _department(session, payload.department_key)
    queue = await queue_svc.get_or_create_queue(session, department_id=dept.id)
    try:
        entries = await queue_svc.reorder(
            session, queue_id=queue.id, ordered_ids=payload.ordered_ids
        )
    except queue_svc.QueueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    await hub.notify_queue_changed()
    return {"ordered": [str(e.id) for e in entries]}


# -- downtime + reconciliation (staff) ----------------------------------------


@router.get("/downtime", response_model=DowntimeOut)
async def get_downtime(hub: QueueHub = Depends(get_hub)) -> DowntimeOut:
    """Public: the board and kiosk read this to raise their offline banner."""
    return DowntimeOut(
        active=hub.downtime,
        since=hub.downtime_since.isoformat() if hub.downtime_since else None,
    )


@router.post("/downtime", response_model=DowntimeOut)
async def set_downtime(
    payload: DowntimeIn,
    hub: QueueHub = Depends(get_hub),
    principal: Principal = Depends(require_staff),
) -> DowntimeOut:
    """Enter / exit downtime (doc 01 §5). Broadcasts to every open screen."""
    logger.info("downtime set to %s by %s", payload.active, principal.name)
    event = await hub.set_downtime(payload.active)
    return DowntimeOut(active=event["active"], since=event["since"])


@router.get("/reconciliation", response_model=ReconOut)
async def reconciliation(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_staff),
) -> ReconOut:
    """The downtime reconciliation list (doc 01 §5 pt 5): today's intakes that
    came in *off the online path* — offline-kiosk syncs (`client_id` set) and
    paper entries (channel=paper). This is what the coordinator reviews after a
    drill or outage: "everything that happened while we were dark, now on the
    record, with the tokens the patients are holding."
    """
    on = queue_svc.today()
    result = await session.execute(
        select(Intake, Visit, Department)
        .join(Visit, Intake.visit_id == Visit.id)
        .join(Department, Visit.department_id == Department.id)
        .where(
            Visit.date == on,
            (Intake.client_id.is_not(None)) | (Visit.channel == "paper"),
        )
        .order_by(Visit.token_no)
    )
    entries = []
    for intake, visit, dept in result.all():
        entries.append(
            ReconEntryOut(
                intake_id=intake.id,
                visit_id=visit.id,
                token_no=visit.token_no,
                department_key=dept.code,
                channel=visit.channel.value,
                chief_complaint=intake.chief_complaint,
                red_flag_count=len(intake.red_flags or []),
                client_id=intake.client_id,
                completed_at=intake.completed_at.isoformat() if intake.completed_at else None,
            )
        )
    return ReconOut(count=len(entries), entries=entries)


@router.post("/downtime/paper-entry", response_model=PaperEntryOut)
async def paper_entry(
    payload: PaperEntryIn,
    session: AsyncSession = Depends(get_session),
    hub: QueueHub = Depends(get_hub),
    _: Principal = Depends(require_staff),
) -> PaperEntryOut:
    """Batch-enter one paper intake sheet (doc 01 §5 pt 3)."""
    dept = await _department(session, payload.department_key)
    try:
        result = await queue_svc.paper_entry(
            session,
            department=dept,
            token_no=payload.token_no,
            lang=payload.lang,
            chief_complaint=payload.chief_complaint,
            patient_name=payload.patient_name,
            urgent=payload.urgent,
            urgent_reason=payload.urgent_reason,
        )
    except queue_svc.QueueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await hub.notify_queue_changed()
    return PaperEntryOut(
        visit_id=result.visit_id,
        intake_id=result.intake_id,
        token_no=result.token_no,
        priority=result.priority.value,
    )


# -- printable downtime sheets (doc 01 §5 pt 3) -------------------------------


async def _hospital_name(session: AsyncSession) -> str:
    hospital = await session.scalar(select(Hospital).order_by(Hospital.created_at))
    return hospital.name if hospital else "OPD"


@router.get("/print/intake-sheets", response_class=HTMLResponse)
async def print_intake_sheets(
    session: AsyncSession = Depends(get_session),
    lang: list[Lang] | None = None,
    _: Principal = Depends(require_staff),
) -> HTMLResponse:
    """Fillable paper intake forms, one page per tree, rendered from the live tree
    bank (doc 01 §5 pt 3). Print to PDF from the browser and laminate."""
    hospital_name = await _hospital_name(session)
    result = await session.execute(select(Department).order_by(Department.code))
    dept_names = {d.code: d.name for d in result.scalars().all()}
    sheets = [
        (tree.to_json(), dept_names.get(tree.department or "", tree.department or "General"))
        for tree in sorted(bank.load_bank().values(), key=lambda t: t.key)
    ]
    html = print_sheets.render_intake_sheets(sheets, hospital_name=hospital_name, langs=lang)
    return HTMLResponse(html)


@router.get("/print/token-block", response_class=HTMLResponse)
async def print_token_block(
    kiosk_id: str,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_staff),
) -> HTMLResponse:
    """A tear-off token-numeral sheet for a kiosk's offline blocks (doc 01 §5 pt 3).

    Leasing is idempotent (`app.offline`), so printing this before an outage both
    ensures the day's blocks exist and prints exactly the numbers the kiosk will
    hand out — the same pre-allocated ranges, so a paper token can never collide.
    """
    try:
        blocks = await offline_svc.lease_blocks(session, kiosk_id=kiosk_id)
    except offline_svc.OfflineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    hospital_name = await _hospital_name(session)
    html = print_sheets.render_token_block_sheet(
        [
            {"department_name": b.department_name, "start_no": b.start_no, "end_no": b.end_no}
            for b in blocks
        ],
        hospital_name=hospital_name,
        kiosk_id=kiosk_id,
        date_str=offline_svc.today().isoformat(),
    )
    return HTMLResponse(html)
