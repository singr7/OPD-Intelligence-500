"""The adaptive-intake telemetry report (S-ADAPT.2, doc 11 §3, §6).

> "Ship a small report (per node: clarify rate, mis-map rate, enrichment hits)
> that feeds the S18 tree editor and closes the routing/adaptivity debt with data,
> not vibes." — doc 11 §3

This is what turns the operator-flagged "the questions feel under-adapted"
(HANDOFF) from a hunch into numbers. Every voice answer records one event onto
`Intake.adaptive_events` (`app.intake.dispatch._record_adaptive_turn`); this module
aggregates them per node and cross-checks the count against the priced
`usage_events`, which is the doc 11 §3 acceptance criterion ("reconciles to
usage_events on a seeded replay").

## What each outcome means

- `interpreted` — a voice answer the interpreter mapped to a value the node
  accepted. One LLM call.
- `clarify` — too vague / an adaptive follow-up; the patient was re-asked. One
  LLM call.
- `exhausted` — the clarify budget was spent (or the candidate was rejected with
  no follow-up); the kiosk fell back to taps. One LLM call.
- `prefilled` — a node auto-answered from a fact volunteered on an *earlier* turn
  (enrichment). **No LLM call** — it is the downstream effect of an earlier
  `interpreted` turn's `enriched` count, so it is excluded from the reconciliation.
- `prefill_rejected` — a stored pre-fill no longer fit when the walk reached the
  node (an amendment rerouted the branch); dropped, the node was asked. No LLM call.

`clarify rate`, `mis-map rate` and `enrichment hits` for the S18 editor fall out of
these per-node counts.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import Intake
from app.models.enums import UsagePurpose
from app.models.metering import UsageEvent

#: Outcomes that were produced by an actual interpreter (LLM) call — the ones that
#: must reconcile 1:1 to INTAKE_TURN usage_events. `prefilled`/`prefill_rejected`
#: are downstream auto-applies with no call, so they are not counted here.
_BILLABLE_OUTCOMES = frozenset({"interpreted", "clarify", "exhausted"})


@dataclass(slots=True)
class NodeStat:
    """Per-node adaptive outcomes and the rates the S18 tree editor reads."""

    node_id: str
    interpreted: int = 0
    clarify: int = 0
    exhausted: int = 0
    prefilled: int = 0
    prefill_rejected: int = 0
    #: Facts this node's answers volunteered for OTHER nodes (sum of `enriched`).
    enrichment_hits: int = 0

    @property
    def llm_turns(self) -> int:
        """Turns that made an interpreter call — reconcile to usage_events."""
        return self.interpreted + self.clarify + self.exhausted

    @property
    def clarify_rate(self) -> float:
        """Share of this node's voice turns that needed a follow-up (a poorly worded
        question clarifies a lot — the routing/adaptivity-debt signal)."""
        return (self.clarify + self.exhausted) / self.llm_turns if self.llm_turns else 0.0

    @property
    def mismap_rate(self) -> float:
        """Share that fell all the way back to taps — the interpreter could not map
        the answer even after a follow-up (a proxy for mis-mapping / bad options)."""
        return self.exhausted / self.llm_turns if self.llm_turns else 0.0

    def record(self, outcome: str, enriched: int) -> None:
        if outcome == "interpreted":
            self.interpreted += 1
        elif outcome == "clarify":
            self.clarify += 1
        elif outcome == "exhausted":
            self.exhausted += 1
        elif outcome == "prefilled":
            self.prefilled += 1
        elif outcome == "prefill_rejected":
            self.prefill_rejected += 1
        self.enrichment_hits += enriched

    def to_json(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "interpreted": self.interpreted,
            "clarify": self.clarify,
            "exhausted": self.exhausted,
            "prefilled": self.prefilled,
            "prefill_rejected": self.prefill_rejected,
            "enrichment_hits": self.enrichment_hits,
            "llm_turns": self.llm_turns,
            "clarify_rate": round(self.clarify_rate, 4),
            "mismap_rate": round(self.mismap_rate, 4),
        }


@dataclass(slots=True)
class AdaptiveReport:
    """The whole report: per-node stats + the usage_events reconciliation."""

    nodes: list[NodeStat] = field(default_factory=list)
    #: LLM-call turns recorded in `adaptive_events` across the scanned intakes.
    recorded_llm_turns: int = 0
    #: INTAKE_TURN usage_events for those same intakes.
    usage_events: int = 0

    @property
    def reconciled(self) -> bool:
        """The doc 11 §3 AC: the interpreter turns we recorded match the priced
        usage_events one-for-one. Enrichment pre-fills are excluded by design (no
        call), so a mismatch means a real accounting bug, not an enrichment."""
        return self.recorded_llm_turns == self.usage_events

    def to_json(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_json() for n in self.nodes],
            "recorded_llm_turns": self.recorded_llm_turns,
            "usage_events": self.usage_events,
            "reconciled": self.reconciled,
        }


async def adaptive_report(
    session: AsyncSession, *, since: datetime | None = None
) -> AdaptiveReport:
    """Aggregate `Intake.adaptive_events` per node and reconcile to usage_events.

    `since` bounds both sides by the intake's `created_at`, so a report window uses
    one clock. Only intakes that actually have adaptive events are scanned — a
    pure-tap pilot produces an empty report, not a query over every intake ever.
    """
    stmt = select(Intake.id, Intake.adaptive_events).where(
        func.jsonb_array_length(Intake.adaptive_events) > 0,
        Intake.deleted_at.is_(None),
    )
    if since is not None:
        stmt = stmt.where(Intake.created_at >= since)
    rows = (await session.execute(stmt)).all()

    stats: dict[str, NodeStat] = defaultdict(lambda: NodeStat(node_id=""))
    recorded_llm_turns = 0
    intake_ids: list[uuid.UUID] = []
    for intake_id, events in rows:
        intake_ids.append(intake_id)
        for event in events or []:
            node_id = str(event.get("node_id", ""))
            outcome = str(event.get("outcome", ""))
            enriched = int(event.get("enriched", 0) or 0)
            stat = stats[node_id]
            stat.node_id = node_id
            stat.record(outcome, enriched)
            if outcome in _BILLABLE_OUTCOMES:
                recorded_llm_turns += 1

    usage_events = 0
    if intake_ids:
        count_stmt = select(func.count()).select_from(UsageEvent).where(
            UsageEvent.intake_id.in_(intake_ids),
            UsageEvent.purpose == UsagePurpose.INTAKE_TURN,
        )
        usage_events = int((await session.execute(count_stmt)).scalar_one())

    return AdaptiveReport(
        nodes=sorted(stats.values(), key=lambda n: n.node_id),
        recorded_llm_turns=recorded_llm_turns,
        usage_events=usage_events,
    )
