"""Asking the question, and everything that has to be true before it is asked.

The provider-chain adapter for `research_assist`, plus the three refusals that
sit in front of it: the department scope, the daily turn budget, and the check
that the client did not try to put text in the context.

## `json_output=False`, and why that is the safety property

Every other LLM pathway in this system parses what comes back — dictation into
drug lines, notes into S/O/A/P, MRD into values with computed flags. Each of
those has a contract, and each contract is where that module states what it
refuses to accept.

This one has no parser at all. The reply is stored as prose and rendered as
prose, so there is no field on any clinical record it can reach, no formulary
lookup it can trigger and no printed sheet it can appear on. That is a stronger
guarantee than a schema with the dangerous fields left out, and it is the reason
this module can be the one prose surface in the plan without being the
dangerous one.

## The budget is a count of turns, not a sum of rupees

The plan says "cost-guarded per-doctor-per-day like every metered pathway", and
this deviates from `app.providers.costguard` in shape. That guard watches
`usage_events` on a schedule and flips a tier flag; it exists to degrade intake
under load, it is keyed by channel, and `UsageEvent` has no doctor column to
group by in the first place.

More fundamentally, **the rupee is not knowable at the moment this guard has to
decide.** Metering is async and batched by design — "this path must never block
a patient-facing call" — so at the instant a doctor taps send, the cost of their
previous turn may not have been priced and written yet. A guard that read a
number that lags by an unbounded interval would be a guard that lets a runaway
client through and then reports, accurately, that the budget was fine when it
checked.

A count of turns is knowable exactly, from a table this module owns, in the same
transaction. And it bounds spend honestly because every turn has the same
bounded shape: a context capped at four assembled items, a question capped at
`research_max_question`, a history capped at `research_history_turns`, and
`max_tokens` on the reply. The number of turns is the only thing that varies, so
it is the thing to cap.

## Nothing queues

A note whose mapping fails keeps the transcript, because the recording is the
half nobody can recreate. There is no equivalent here: an unanswered question is
a question the doctor still has, and they can ask it again in four seconds. So a
provider outage writes **no turn at all** — the panel says the assistant is
unavailable and closes. A greyed-out pending row that might answer later would
be a promise this module cannot keep, and worse, would leave a question sitting
in a clinical audit trail with no answer beside it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import ResearchThread, ResearchTurn
from app.models.enums import UsagePurpose
from app.prompts import load
from app.providers import LLMProvider, LLMRequest, ProviderError, with_fallback
from app.research.context import ResearchContext

logger = logging.getLogger(__name__)

#: Pinned, not "latest" — a prompt edit must not quietly change the register of
#: answers a doctor has already been reading all week (`app.prompts.loader`).
PROMPT_VERSION = 1

#: Long enough for the six-to-twelve lines the prompt asks for, with room for a
#: refusal that also answers the rest of the question.
MAX_TOKENS = 900

#: Higher than the mapping paths (0.0) and lower than a chat product. Those map
#: a doctor's words and must not vary; this writes prose, where a
#: deterministic-to-the-token register reads stilted. Still low: the failure
#: mode of a creative temperature here is a confidently phrased trial that does
#: not exist.
TEMPERATURE = 0.2


class ResearchError(Exception):
    """The caller may not do this."""


class ResearchUnavailable(ResearchError):
    """The LLM chain is down. Nothing is stored and nothing queues."""


class BudgetExhausted(ResearchError):
    """This doctor has used their turns for today."""


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    model: str
    provider: str
    prompt_ref: str


class Assistant:
    """`research_assist` on the configured LLM chain.

    Same adapter seam as `NoteMapper` and `DictationMapper`, and for the same
    reason: it takes a provider chain and nothing else, so `LLM_PROVIDER=
    local_vllm` runs the whole thing on the box. A doctor's research questions
    are a record of what they were unsure about, and being able to keep that on
    the premises by changing one setting matters more here than almost anywhere
    else in this system.
    """

    def __init__(
        self, providers: Sequence[LLMProvider], *, prompt_version: int | None = PROMPT_VERSION
    ):
        self._providers = list(providers)
        self._prompt = load("research_assist", prompt_version)

    async def ask(
        self, question: str, *, context: str, history: Sequence[tuple[str, str]] = ()
    ) -> Answer:
        if not question.strip():
            raise ResearchError("nothing to ask: the question is empty")

        request = LLMRequest(
            prompt=self._prompt.render(
                context=context or "Nothing — the doctor sent no patient context with this.",
                question=question.strip(),
            ),
            system=self._prompt.system,
            prompt_ref=self._prompt.ref,
            # The one prose surface in the plan. See the module docstring: the
            # absence of a parser is what makes this module unable to write to a
            # clinical record.
            json_output=False,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            history=tuple(history),
        )

        try:
            result = await with_fallback(
                self._providers,
                lambda provider: provider.complete(request, purpose=UsagePurpose.RESEARCH),
            )
        except ProviderError as exc:
            raise ResearchUnavailable(str(exc)) from exc

        text = result.text.strip()
        if not text:
            # An empty completion is an outage wearing a 200. Treating it as an
            # answer would put a blank turn in a clinical audit trail.
            raise ResearchUnavailable("the model returned an empty answer")

        return Answer(
            text=text,
            model=result.model,
            provider=_provider_name(self._providers, result),
            prompt_ref=self._prompt.ref,
        )


def _provider_name(providers: Sequence[LLMProvider], result: Any) -> str:
    """Which provider in the chain actually answered, by the model it returned.

    The identification `app.notes` and `app.mrd.pipeline` both use: `LLMResult`
    carries the model but not the provider, and a fallback chain means the first
    link is not necessarily the one that replied.
    """
    for provider in providers:
        if provider.model == result.model:
            return provider.name
    return providers[0].name if providers else "unknown"


# -- the daily budget ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Budget:
    """What this doctor has left today, as the panel states it."""

    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


async def budget_for(
    session: AsyncSession, *, doctor_id: Any, limit: int, now: datetime | None = None
) -> Budget:
    """Turns this doctor has taken since the start of the operating day.

    The day boundary is **`app.queue.today()`'s** — UTC midnight — because that
    is what "today" already means everywhere else in this system: the worklist,
    the board and the analytics all bucket by it, and a budget that reset at a
    different moment from the worklist would be a second definition of the
    operating day for a doctor to hold in their head.

    It is a rolling *calendar* day rather than a rolling 24 hours, so a doctor
    who asked thirty questions at yesterday's clinic starts today with a full
    budget. That UTC midnight falls at 05:30 in the clinic's own time is a
    property this platform already has and this module should not be the one
    place that fixes it — it is recorded in the handoff, and changing it is a
    platform-wide decision about `queue.today()`, not a research-assistant one.
    """
    moment = now or datetime.now(UTC)
    start = datetime.combine(moment.astimezone(UTC).date(), time.min, tzinfo=UTC)

    used = await session.scalar(
        select(func.count(ResearchTurn.id))
        .join(ResearchThread, ResearchTurn.thread_id == ResearchThread.id)
        .where(
            ResearchThread.doctor_id == doctor_id,
            ResearchTurn.created_at >= start,
            ResearchTurn.deleted_at.is_(None),
        )
    )
    return Budget(used=int(used or 0), limit=limit)


def render_context(
    context: ResearchContext, include: Sequence[str] | None
) -> tuple[str, list[str]]:
    """The prompt block, and the same lines frozen for the stored turn.

    One function so the two can never disagree: what a reader sees in
    `ResearchTurn.context_sent` months later is character-for-character what the
    vendor was sent, not a re-render against a lab value that has since been
    re-flagged.
    """
    lines = [item.text for item in context.select(include)]
    return "\n".join(f"- {line}" for line in lines), lines


def history_for(turns: Sequence[ResearchTurn], *, depth: int) -> tuple[tuple[str, str], ...]:
    """The last `depth` exchanges as `LLMRequest.history` wants them.

    Question and answer as separate user/assistant turns, oldest first. The
    context block is **not** replayed — it rides on the current prompt, where it
    reflects what the doctor has the panel set to *now*. Replaying an older
    turn's context would mean an item the doctor unticked five minutes ago
    coming back with every subsequent question, which would make the trim
    control a lie.
    """
    out: list[tuple[str, str]] = []
    for turn in list(turns)[-depth:]:
        out.append(("user", turn.question))
        out.append(("assistant", turn.answer))
    return tuple(out)


def operating_day(now: datetime | None = None) -> date:
    """The day the budget is counted over — `app.queue.today()`'s day."""
    return (now or datetime.now(UTC)).astimezone(UTC).date()


__all__ = [
    "MAX_TOKENS",
    "PROMPT_VERSION",
    "TEMPERATURE",
    "Answer",
    "Assistant",
    "Budget",
    "BudgetExhausted",
    "ResearchError",
    "ResearchUnavailable",
    "budget_for",
    "history_for",
    "operating_day",
    "render_context",
]
