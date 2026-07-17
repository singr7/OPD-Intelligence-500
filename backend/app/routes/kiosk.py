"""Kiosk intake HTTP surface (doc 03 §1a) — REST over the intake tool contract.

The intake engine had no channel until now (STATE.md: "not wired to any route").
This is the first one. It is deliberately **thin REST that mirrors the four-tool
contract** rather than a websocket, for one reason the HANDOFF called out: keep
the wire shape the same as the tool contract so S14's telephony and S12's WhatsApp
reuse the vocabulary. One request = one tool call over the dispatcher:

    POST /kiosk/start    -> route Q1, create the visit, get_next_node (first screen)
    GET  /kiosk/{sid}/next   -> get_next_node (re-render / resume)
    POST /kiosk/{sid}/answer -> save_answer, returns the next node
    POST /kiosk/{sid}/finish -> finish_and_summarize (the read-back screen)
    POST /kiosk/{sid}/confirm -> mark confirmed, allocate token, finalize cost

The kiosk is a V3 client (taps, no model in the walk); the one model call is Q1's
department classifier, and `needs_human` is honoured — `/start` then returns a
department chooser instead of a session, and the kiosk re-calls `/start` with the
chosen `dept_key`. Nothing here is authenticated: a kiosk is a public terminal and
the intake carries no credential (the visit is an anonymous walk-in). It must stay
that boring — no patient lookup, no PII in a path.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import kiosk as kiosk_svc
from app import offline as offline_svc
from app.db import get_session
from app.intake import IntakeEngine, SessionState, ToolError
from app.models.enums import Channel, Lang
from app.providers.metering import get_meter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kiosk", tags=["kiosk"])


# -- dependency ---------------------------------------------------------------


def get_engine(request: Request) -> IntakeEngine:
    """The one process-wide `IntakeEngine`, built on the lifespan (it holds no
    per-intake state; the session store does)."""
    engine = getattr(request.app.state, "intake_engine", None)
    if engine is None:  # pragma: no cover - lifespan always sets it
        raise HTTPException(status_code=503, detail="intake engine not ready")
    return engine


async def _load_state(engine: IntakeEngine, session_id: str) -> SessionState:
    state = await engine.store.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="no such intake session")
    if state.channel is not Channel.KIOSK:
        # This route only speaks for kiosk sessions; a phone session must not be
        # advanced by taps.
        raise HTTPException(status_code=409, detail="session is not a kiosk session")
    return state


# -- wire models --------------------------------------------------------------


class StartIn(BaseModel):
    lang: Lang
    chief_complaint: str = Field(min_length=1, max_length=2000)
    caregiver: bool = False
    #: A confirmed department (staff- or patient-picked from the chooser). When
    #: present the classifier is skipped entirely.
    dept_key: str | None = None


class NodeOut(BaseModel):
    id: str
    type: str
    text: str
    options: list[dict[str, Any]]
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    audio: str | None = None


class DeptOut(BaseModel):
    key: str
    name: str


class StartOut(BaseModel):
    #: "routed" — a session started; "needs_department" — show the chooser.
    status: str
    session_id: str | None = None
    lang: Lang | None = None
    tier: str | None = None
    department: DeptOut | None = None
    tree_key: str | None = None
    node: NodeOut | None = None
    complete: bool = False
    #: Populated only on "needs_department": the chooser's options + why.
    departments: list[DeptOut] = Field(default_factory=list)
    reason: str | None = None


class AnswerIn(BaseModel):
    node_id: str
    value: Any = None
    raw_text: str | None = None


class AnswerOut(BaseModel):
    ok: bool
    node_id: str
    complete: bool
    #: Present when the answer did not fit the node — the kiosk re-asks.
    error: str | None = None
    red_flags: list[dict[str, Any]] = Field(default_factory=list)
    #: The next screen (None once the tree completes).
    node: NodeOut | None = None


class FinishOut(BaseModel):
    readback: str
    summary_md: str | None
    red_flags: list[dict[str, Any]]
    complete: bool


class ConfirmOut(BaseModel):
    token_no: int | None
    department: DeptOut | None
    red_flags: list[dict[str, Any]]
    cost_inr: str | None


# -- routes -------------------------------------------------------------------


@router.post("/start", response_model=StartOut)
async def start(
    payload: StartIn,
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
) -> StartOut:
    """Route the chief complaint, open the intake, return the first question.

    Honours the classifier's `needs_human`: an uncertain route yields
    `status="needs_department"` and the chooser, not a guessed session.
    """
    try:
        routed = await kiosk_svc.route_complaint(
            session,
            complaint=payload.chief_complaint,
            lang=payload.lang,
            dept_key=payload.dept_key,
        )
    except kiosk_svc.KioskError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if routed.needs_department:
        departments = await kiosk_svc._departments(session)
        return StartOut(
            status="needs_department",
            departments=[DeptOut(key=d.code, name=d.name) for d in departments],
            reason=routed.guess.reason or "Let's confirm the right doctor for you.",
        )

    assert routed.department is not None and routed.tree is not None
    walk_in = await kiosk_svc.create_walk_in(
        session,
        department=routed.department,
        lang=payload.lang,
        tree=routed.tree,
        caregiver=payload.caregiver,
    )

    state = await engine.start_session(
        tree=routed.tree,
        channel=Channel.KIOSK,
        lang=payload.lang,
        configured_tier=kiosk_svc.KIOSK_TIER,
        intake_id=walk_in.intake.id,
        visit_id=walk_in.visit.id,
        chief_complaint=payload.chief_complaint,
    )

    dispatcher = engine.dispatcher(state, routed.tree)
    first = await dispatcher.get_next_node()
    return StartOut(
        status="routed",
        session_id=state.session_id,
        lang=state.lang,
        tier=state.active_tier.value,
        department=DeptOut(key=routed.department.code, name=routed.department.name),
        tree_key=routed.tree.key,
        node=_node_out(first),
        complete=first.get("complete", False),
    )


@router.get("/{session_id}/next", response_model=NodeOut | dict)
async def next_node(
    session_id: str,
    engine: IntakeEngine = Depends(get_engine),
) -> Any:
    """The current question — for a resumed kiosk (idle reset) or a re-render."""
    state = await _load_state(engine, session_id)
    dispatcher = engine.dispatcher(state)
    result = await dispatcher.get_next_node()
    node = _node_out(result)
    return node.model_dump() if node else {"complete": True, "node": None}


@router.post("/{session_id}/answer", response_model=AnswerOut)
async def answer(
    session_id: str,
    payload: AnswerIn,
    engine: IntakeEngine = Depends(get_engine),
) -> AnswerOut:
    """Record one tap/answer, then hand back the next screen.

    A `Walk.save` prunes answers stranded on an abandoned branch, so the next node
    and the red flags are recomputed here from the fresh walk — never cached on the
    client (STATE.md invariant).
    """
    state = await _load_state(engine, session_id)
    dispatcher = engine.dispatcher(state)
    try:
        saved = await dispatcher.save_answer(
            payload.node_id, payload.value, raw_text=payload.raw_text
        )
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not saved["ok"]:
        return AnswerOut(
            ok=False,
            node_id=payload.node_id,
            complete=False,
            error=saved.get("error"),
            node=_node_out(await dispatcher.get_next_node()),
        )

    nxt = await dispatcher.get_next_node()
    return AnswerOut(
        ok=True,
        node_id=payload.node_id,
        complete=saved["complete"],
        red_flags=saved.get("red_flags", []),
        node=_node_out(nxt),
    )


@router.post("/{session_id}/finish", response_model=FinishOut)
async def finish(
    session_id: str,
    engine: IntakeEngine = Depends(get_engine),
) -> FinishOut:
    """Summarise the intake and return the patient read-back (the confirm screen).

    Does not yet allocate a token or finalise cost — the patient has not confirmed
    the read-back. That is `/confirm`.
    """
    state = await _load_state(engine, session_id)
    dispatcher = engine.dispatcher(state)
    result = await dispatcher.finish_and_summarize("complete")
    return FinishOut(
        readback=result["readback"],
        summary_md=result["summary_md"],
        red_flags=result["red_flags"],
        complete=result["complete"],
    )


@router.post("/{session_id}/confirm", response_model=ConfirmOut)
async def confirm(
    session_id: str,
    engine: IntakeEngine = Depends(get_engine),
    session: AsyncSession = Depends(get_session),
) -> ConfirmOut:
    """The patient confirmed the read-back: allocate a token and finalise the cost.

    The token screen is the kiosk's last screen (doc 03 §1a). Cost finalisation
    sums this intake's `usage_events` (the classifier's routing call, mostly) onto
    the `Intake` row.
    """
    state = await _load_state(engine, session_id)
    state.confirmed = True
    await engine.store.save(state)

    token_no: int | None = None
    department: DeptOut | None = None
    if state.visit_id is not None:
        from app.models.clinical import Visit

        visit = await session.get(Visit, state.visit_id)
        if visit is not None:
            token_no = await kiosk_svc.allocate_token(session, visit)
            dept = await session.get(kiosk_svc.Department, visit.department_id)
            if dept is not None:
                department = DeptOut(key=dept.code, name=dept.name)

    # Drain the batched meter first so the cost sums a complete set of
    # usage_events — the classifier's routing call is metered async, and without a
    # flush finalize_cost would read ₹0 for a call that did cost (STATE.md).
    meter = get_meter()
    if meter is not None:
        await meter.flush()
    cost = await engine.finalize_cost(state, session)
    return ConfirmOut(
        token_no=token_no,
        department=department,
        red_flags=state.red_flags,
        cost_inr=str(cost) if cost is not None else None,
    )


# -- offline (S7, doc 01 §5) --------------------------------------------------


class BlockOut(BaseModel):
    department: DeptOut
    start_no: int
    end_no: int
    #: The highest number the *server* knows this kiosk has issued. The kiosk's
    #: own store is ahead of this during an outage — it is a resume hint after a
    #: reboot, not an instruction.
    used_up_to: int | None
    next_free: int


class LeaseOut(BaseModel):
    kiosk_id: str
    date: str
    blocks: list[BlockOut]


class SyncIntakeIn(BaseModel):
    #: The kiosk's id for this intake; the idempotency key (see `app.offline`).
    client_id: str = Field(min_length=8, max_length=64)
    department_key: str
    tree_key: str
    lang: Lang
    token_no: int
    #: `{node_id: {value, text, text_en, lang, at}}` — the walker's shape, from
    #: the offline TS walker. The server re-walks it; red flags are recomputed
    #: here and the kiosk's own list is never read.
    answers: dict[str, Any]
    chief_complaint: str | None = None
    caregiver: bool = False
    completed_at: datetime | None = None


class SyncIn(BaseModel):
    kiosk_id: str = Field(min_length=1, max_length=64)
    intakes: list[SyncIntakeIn] = Field(max_length=200)


class SyncResultOut(BaseModel):
    client_id: str
    #: "synced" | "duplicate" | "rejected"
    status: str
    token_no: int | None = None
    red_flags: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class SyncOut(BaseModel):
    results: list[SyncResultOut]
    synced: int
    duplicates: int
    rejected: int


@router.post("/blocks/lease", response_model=LeaseOut)
async def lease_blocks(
    kiosk_id: str,
    session: AsyncSession = Depends(get_session),
) -> LeaseOut:
    """Lease this kiosk's offline token blocks for today (doc 01 §5).

    Called while the network is *up* — that is the whole point. The kiosk holds
    one block per department (offline it cannot classify, so the patient picks
    from the chooser and any department may be needed) and consumes them from
    IndexedDB during an outage.

    Idempotent: re-leasing returns the same ranges. It never hands out a fresh
    one, because the old one is already on paper slips in patients' hands.
    """
    try:
        blocks = await offline_svc.lease_blocks(session, kiosk_id=kiosk_id)
    except offline_svc.OfflineError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return LeaseOut(
        kiosk_id=kiosk_id,
        date=offline_svc.today().isoformat(),
        blocks=[
            BlockOut(
                department=DeptOut(key=block.department_key, name=block.department_name),
                start_no=block.start_no,
                end_no=block.end_no,
                used_up_to=block.used_up_to,
                next_free=block.next_free,
            )
            for block in blocks
        ],
    )


@router.post("/sync", response_model=SyncOut)
async def sync(
    payload: SyncIn,
    session: AsyncSession = Depends(get_session),
) -> SyncOut:
    """Take back the intakes a kiosk completed while the API was unreachable.

    Per-intake results rather than all-or-nothing: one bad intake in a batch of
    twenty must not strand the other nineteen on a kiosk, and the kiosk needs to
    know exactly which ones to stop retrying. A `duplicate` is a success — it
    means an earlier attempt landed before the network dropped again.
    """
    results: list[SyncResultOut] = []
    for item in payload.intakes:
        outcome = await offline_svc.sync_intake(
            session,
            kiosk_id=payload.kiosk_id,
            client_id=item.client_id,
            department_key=item.department_key,
            tree_key=item.tree_key,
            lang=item.lang,
            token_no=item.token_no,
            answers=item.answers,
            chief_complaint=item.chief_complaint,
            caregiver=item.caregiver,
            completed_at=item.completed_at,
        )
        results.append(
            SyncResultOut(
                client_id=outcome.client_id,
                status=outcome.status,
                token_no=outcome.token_no,
                red_flags=outcome.red_flags or [],
                error=outcome.error,
            )
        )

    return SyncOut(
        results=results,
        synced=sum(1 for r in results if r.status == "synced"),
        duplicates=sum(1 for r in results if r.status == "duplicate"),
        rejected=sum(1 for r in results if r.status == "rejected"),
    )


# -- helpers ------------------------------------------------------------------


def _node_out(result: dict[str, Any]) -> NodeOut | None:
    """The dispatcher's `get_next_node` result → the wire node, or None if done."""
    if result.get("complete") or result.get("node") is None:
        return None
    node = result["node"]
    return NodeOut(
        id=node["id"],
        type=node["type"],
        text=node["text"],
        options=node["options"],
        min=node.get("min"),
        max=node.get("max"),
        unit=node.get("unit"),
        audio=node.get("audio"),
    )
