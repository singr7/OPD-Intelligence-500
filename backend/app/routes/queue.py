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
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from app import queue as queue_svc
from app.auth.rbac import Principal, require_staff
from app.db import get_session
from app.models.clinical import Intake, Visit
from app.models.enums import Lang, QueueEntryState
from app.models.org import Department
from app.queue_hub import QueueHub

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


class BoardDeptOut(BaseModel):
    department_key: str
    department_name: str
    now_serving: int | None
    now_serving_reason: str | None
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


class ConsoleDeptOut(BaseModel):
    department_key: str
    department_name: str
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
                now_serving=b.now_serving,
                now_serving_reason=b.now_serving_reason,
                next=[
                    BoardEntryOut(
                        token_no=e.token_no,
                        priority=e.priority.value,
                        priority_reason=e.priority_reason,
                        red_flag=e.red_flag_count > 0,
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
async def queue_ws(ws: WebSocket, hub: QueueHub = Depends(get_hub)) -> None:
    """The live-sync socket for board + console. Public (the board has no login).

    Carries only change *pings* and the downtime flag — no PII crosses it, so a
    wall-mounted TV can hold it open with no credential. Clients re-fetch their
    own snapshot on a ping.
    """
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
                entries=[
                    ConsoleEntryOut(
                        id=e.id,
                        token_no=e.token_no,
                        priority=e.priority.value,
                        priority_reason=e.priority_reason,
                        state=e.state.value,
                        chief_complaint=e.chief_complaint,
                        red_flag_count=e.red_flag_count,
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
