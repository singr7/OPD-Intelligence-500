"""The research surface (plan §4).

`require_doctor` on every route. Decision 7 — "the research assistant advises
the doctor and only the doctor" — is a statement about who may hold this
conversation, and a coordinator or an admin has no route into it here.

    GET  /research/visits/{visit_id}   the context, the thread so far, the budget
    POST /research/visits/{visit_id}   ask a question, get an answer back

Two routes, and the GET is the interesting one: it exists so the panel can show
the doctor **exactly what would be sent, before anything is sent**. Nothing in
this file accepts context text — `ask` takes a list of ids and the server
re-derives the words. See `app.research.context` for why that asymmetry is the
whole PHI posture rather than an implementation detail.

There is no `PATCH`, no `DELETE` and no route that marks a turn as anything. A
research answer cannot be adopted into the record.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import doctor as doctor_svc
from app import facility as facility_svc
from app.auth.rbac import Principal, require_doctor
from app.config import Settings, get_settings
from app.db import get_session
from app.providers.metering import usage_scope
from app.providers.registry import llm_chain
from app.research import assistant as assist
from app.research import context as ctx
from app.research import threads as th

router = APIRouter(prefix="/research", tags=["research"])


# -- wire models --------------------------------------------------------------


class ContextItemOut(BaseModel):
    """One line the doctor can see, drop, and hold us to.

    `text` is exactly what goes to the vendor — shown verbatim rather than
    summarised for the panel, because a "view what we send" control that
    paraphrases what it sends is worse than not having one.
    """

    id: str
    label: str
    text: str
    source: str
    caveat: str = ""


class TurnOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    #: The lines that actually left the box with this question, frozen at the
    #: time. Not re-derived — a lab value may have been re-flagged since.
    context_sent: list[str] = []
    model: str | None = None
    created_at: datetime


class BudgetOut(BaseModel):
    used: int
    limit: int
    remaining: int


class PanelOut(BaseModel):
    visit_id: uuid.UUID
    #: Everything assembled, in order. The panel ticks these.
    context: list[ContextItemOut] = []
    #: Sources that exist but produced nothing, with the reason. `[[label,
    #: why], ...]` — rendered so "no labs scanned" never looks like a source
    #: this module forgot to build.
    absent: list[list[str]] = []
    #: The ids the doctor last chose. Null means they have not trimmed, so the
    #: panel ticks everything; `[]` means they unticked every line, which is a
    #: legitimate way to ask a general question.
    include: list[str] | None = None
    suggestions: list[str] = []
    turns: list[TurnOut] = []
    budget: BudgetOut
    #: False when `RESEARCH_ENABLED=false`. The tab says so rather than 404ing.
    enabled: bool = True


class AskIn(BaseModel):
    question: str = Field(min_length=1)
    #: Context item **ids**, never text. Null means "everything you have".
    include: list[str] | None = None


class AskOut(BaseModel):
    turn: TurnOut
    budget: BudgetOut


# -- helpers ------------------------------------------------------------------


async def _doctor(session: AsyncSession, principal: Principal):
    try:
        return await doctor_svc.resolve_doctor(session, user_id=principal.id)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _fail(exc: assist.ResearchError) -> HTTPException:
    if isinstance(exc, assist.BudgetExhausted):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, assist.ResearchUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if "another department" in str(exc):
        return HTTPException(status_code=403, detail=str(exc))
    if "no such visit" in str(exc):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _turn_out(turn) -> TurnOut:
    return TurnOut(
        id=turn.id,
        question=turn.question,
        answer=turn.answer,
        context_sent=[str(line) for line in turn.context_sent or []],
        model=(turn.provider_snapshot or {}).get("ask", {}).get("model"),
        created_at=turn.created_at,
    )


def _budget_out(budget: assist.Budget) -> BudgetOut:
    return BudgetOut(used=budget.used, limit=budget.limit, remaining=budget.remaining)


async def _settle(session: AsyncSession) -> None:
    """Commit before the response goes out, so 200 means it is on the record.

    The M4 fix, applied here from the start rather than after a live stack
    404'd. FastAPI tears down `yield` dependencies *after* sending the response
    (since 0.106; this repo runs 0.139), so `get_session`'s commit lands after
    the caller already has its 200. This surface chains writes exactly the way
    the note dock does — a doctor asks a second question while the first turn's
    commit is still in flight — and the failure would be a thread that
    momentarily forgets a turn it just answered.
    """
    await session.commit()


# -- routes -------------------------------------------------------------------


@router.get("/visits/{visit_id}", response_model=PanelOut)
async def panel(
    visit_id: uuid.UUID,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PanelOut:
    """What would be sent, what has been asked, and what is left of today.

    Assembles the context on every open rather than caching it: a report
    scanned at the desk two minutes ago should appear in the panel the next time
    the doctor looks at it, and a cached context is a context that goes stale
    exactly when the consult is moving fastest.
    """
    doctor = await _doctor(session, principal)
    try:
        visit = await th.assert_visit_scope(session, visit_id=visit_id, doctor=doctor)
    except assist.ResearchError as exc:
        raise _fail(exc) from exc

    assembled = await ctx.assemble(session, visit=visit)
    thread = await th.get_thread(session, visit_id=visit_id, doctor=doctor)
    turns = await th.turns_for(session, thread=thread) if thread else []
    budget = await assist.budget_for(
        session, doctor_id=doctor.id, limit=settings.research_daily_turns
    )

    return PanelOut(
        visit_id=visit_id,
        context=[
            ContextItemOut(
                id=item.id,
                label=item.label,
                text=item.text,
                source=item.source,
                caveat=item.caveat,
            )
            for item in assembled.items
        ],
        absent=[[label, why] for label, why in assembled.absent],
        include=th.stored_include(thread),
        suggestions=list(ctx.suggestions(assembled)),
        turns=[_turn_out(turn) for turn in turns],
        budget=_budget_out(budget),
        enabled=settings.research_enabled,
    )


@router.post("/visits/{visit_id}", response_model=AskOut)
async def ask(
    visit_id: uuid.UUID,
    body: AskIn,
    principal: Principal = Depends(require_doctor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AskOut:
    """Ask, and store the exchange whole.

    The order of the refusals matters and is deliberate: scope, then the switch,
    then the question's shape, then the context ids, then the budget — every
    check that costs nothing runs before the one that touches the database, and
    all of them run before a vendor is called. A doctor whose budget is spent
    finds out without a vendor being billed for finding out.
    """
    doctor = await _doctor(session, principal)
    try:
        visit = await th.assert_visit_scope(session, visit_id=visit_id, doctor=doctor)
    except assist.ResearchError as exc:
        raise _fail(exc) from exc

    if not settings.research_enabled:
        raise HTTPException(status_code=503, detail="the research assistant is switched off here")

    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="nothing to ask: the question is empty")
    if len(question) > settings.research_max_question:
        raise HTTPException(
            status_code=400,
            detail=f"that question is longer than {settings.research_max_question} characters",
        )

    unknown = ctx.unknown_ids(body.include)
    if unknown:
        # Rejected rather than ignored: a client sending an id this module does
        # not build is a client that thinks it can put text in the context, and
        # it should find that out at the first attempt.
        raise HTTPException(status_code=400, detail=f"not context this system builds: {unknown}")

    budget = await assist.budget_for(
        session, doctor_id=doctor.id, limit=settings.research_daily_turns
    )
    if budget.exhausted:
        raise _fail(
            assist.BudgetExhausted(
                f"that is {budget.limit} research questions today — the assistant resumes tomorrow"
            )
        )

    assembled = await ctx.assemble(session, visit=visit)
    prompt_context, frozen = assist.render_context(assembled, body.include)

    # Read the existing thread for history, but do **not** create one yet. The
    # rollback in `get_session` would undo an empty thread anyway; not writing
    # it in the first place is what makes "a provider outage stores nothing"
    # true by construction rather than true because of how a dependency happens
    # to handle exceptions.
    existing = await th.get_thread(session, visit_id=visit_id, doctor=doctor)
    history = assist.history_for(
        await th.turns_for(session, thread=existing) if existing else [],
        depth=settings.research_history_turns,
    )

    assistant = assist.Assistant(
        llm_chain(settings),
        # From the visit's department, like the dictation mapper's. Framing only —
        # see `Assistant.__init__`; the refusals do not vary by pack.
        capabilities=await facility_svc.capabilities_for_visit(session, visit_id),
    )
    try:
        # Attributed to the visit, like the note mapper's: the S18 dashboard
        # wants this rupee amount next to the consult it belongs to.
        with usage_scope(visit_id=visit_id):
            answer = await assistant.ask(question, context=prompt_context, history=history)
    except assist.ResearchError as exc:
        raise _fail(exc) from exc

    thread = await th.open_thread(session, visit_id=visit_id, doctor=doctor, include=body.include)
    turn = await th.append_turn(
        session,
        thread=thread,
        question=question,
        answer=answer,
        context_sent=frozen,
        include=body.include,
    )
    await _settle(session)

    spent = await assist.budget_for(
        session, doctor_id=doctor.id, limit=settings.research_daily_turns
    )
    return AskOut(turn=_turn_out(turn), budget=_budget_out(spent))
