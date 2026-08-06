"""Storing the conversation, and who is allowed to hold one.

Thin on purpose. The interesting decisions are in `context.py` (what may leave
the box) and `assistant.py` (what has to be true before it does); this file
opens a thread, appends a turn, and refuses a doctor from another department.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import ResearchThread, ResearchTurn, Visit
from app.models.org import Doctor
from app.research.assistant import Answer, ResearchError

logger = logging.getLogger(__name__)


async def assert_visit_scope(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> Visit:
    """Your department, or an error that says so — the S9 card's boundary.

    A local copy of the check `app.notes` and `app.dictation` also carry, on
    purpose and for the M4 reason: this package imports no clinical writer (see
    the package docstring), and importing one for eight lines of scope check
    would couple the research path to the prescription path to save nothing.
    `app.doctor` already carries four copies of it, so this is the house pattern
    rather than an exception made here.
    """
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise ResearchError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise ResearchError("that patient is in another department")
    return visit


async def get_thread(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> ResearchThread | None:
    """This doctor's thread on this visit, or None if they have not asked yet.

    Per doctor, not per visit. A colleague covering the room gets their own
    conversation: a research thread is one clinician's line of reasoning, and
    merging two would attribute one doctor's question to another in a record
    that exists precisely so that attribution is answerable later.
    """
    return await session.scalar(
        select(ResearchThread).where(
            ResearchThread.visit_id == visit_id,
            ResearchThread.doctor_id == doctor.id,
            ResearchThread.deleted_at.is_(None),
        )
    )


async def open_thread(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor, include: Sequence[str] | None
) -> ResearchThread:
    """The thread for this (visit, doctor), created on first use."""
    thread = await get_thread(session, visit_id=visit_id, doctor=doctor)
    if thread is None:
        thread = ResearchThread(
            visit_id=visit_id,
            doctor_id=doctor.id,
            context_include=list(include) if include is not None else [],
        )
        session.add(thread)
        await session.flush()
    return thread


async def turns_for(session: AsyncSession, *, thread: ResearchThread) -> list[ResearchTurn]:
    """Every turn, oldest first — the order they were asked in."""
    rows = await session.scalars(
        select(ResearchTurn)
        .where(ResearchTurn.thread_id == thread.id, ResearchTurn.deleted_at.is_(None))
        .order_by(ResearchTurn.created_at.asc())
    )
    return list(rows)


async def append_turn(
    session: AsyncSession,
    *,
    thread: ResearchThread,
    question: str,
    answer: Answer,
    context_sent: Sequence[str],
    include: Sequence[str] | None,
) -> ResearchTurn:
    """One completed exchange, stored whole.

    Only ever called after an answer is in hand. A failed call writes nothing
    (see `app.research.assistant`), so a row in this table is always a question
    that got an answer — which is what makes the audit trail readable without a
    status column to interpret.
    """
    turn = ResearchTurn(
        thread_id=thread.id,
        question=question.strip(),
        answer=answer.text,
        context_sent=list(context_sent),
        provider_snapshot={"ask": {"provider": answer.provider, "model": answer.model}},
        prompt_refs=[answer.prompt_ref],
    )
    session.add(turn)
    # The doctor's latest trim, remembered so re-opening the tab mid-consult
    # does not silently restore an item they turned off.
    thread.context_include = list(include) if include is not None else []
    await session.flush()
    logger.info("research turn %s on thread %s", turn.id, thread.id)
    return turn


def stored_include(thread: ResearchThread | None) -> list[str] | None:
    """The ids the doctor last chose to send, or None if they never have.

    `None` and `[]` are different answers and the panel renders them
    differently: None is "this doctor has not touched the trim, so show
    everything ticked", and `[]` is "they unticked every line", which is a
    legitimate way to ask a general question with no patient in it. A thread
    that exists but has taken no turn yet is still None — `open_thread` only
    records a selection once one has been sent.
    """
    if thread is None:
        return None
    value = thread.context_include
    if not isinstance(value, list) or not value:
        return None
    return [str(item) for item in value]


__all__: list[str] = [
    "append_turn",
    "assert_visit_scope",
    "get_thread",
    "open_thread",
    "stored_include",
    "turns_for",
]
