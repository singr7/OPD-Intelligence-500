"""Print the adaptive-intake telemetry report (doc 11 §3) as a table.

`app.intake.adaptive_report.adaptive_report` has no HTTP route yet — the tree
editor that consumes it is S18. This is the read path for the omen validation:
turn the flags on, run scripted intakes answering by voice, then run this to see
per-node clarify / mis-map / enrichment rates against the real on-box model and
the usage_events reconciliation.

    python -m scripts.adaptive_report            # everything with adaptive events
    python -m scripts.adaptive_report --hours 2  # only the last 2 hours

A pure-tap pilot (flags off) prints an empty report — that is the correct answer,
not a bug.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.db import build_engine, build_sessionmaker
from app.intake.adaptive_report import adaptive_report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours", type=float, default=None, help="only intakes from the last N hours"
    )
    args = parser.parse_args()
    since = datetime.now(UTC) - timedelta(hours=args.hours) if args.hours else None

    engine = build_engine()
    sm = build_sessionmaker(engine)
    try:
        async with sm() as session:
            report = await adaptive_report(session, since=since)
    finally:
        await engine.dispose()

    if not report.nodes:
        print("No adaptive events recorded.")
        print("(Flags off ⇒ this is expected. With flags on, answer a node by voice first.)")
        return

    header = (
        f"{'node':<28} {'interp':>6} {'clarify':>7} {'exhaust':>7} "
        f"{'prefill':>7} {'reject':>6} {'enrich':>6} {'clarify%':>8} {'mismap%':>7}"
    )
    print(header)
    print("-" * len(header))
    for n in report.nodes:
        attempts = n.interpreted + n.clarify + n.exhausted
        clarify_pct = (100 * n.clarify / attempts) if attempts else 0.0
        # A mis-map here is a turn that never resolved to a value the node accepts —
        # the interpreter gave up (exhausted) rather than clarifying successfully.
        mismap_pct = (100 * n.exhausted / attempts) if attempts else 0.0
        print(
            f"{n.node_id:<28} {n.interpreted:>6} {n.clarify:>7} {n.exhausted:>7} "
            f"{n.prefilled:>7} {n.prefill_rejected:>6} {n.enrichment_hits:>6} "
            f"{clarify_pct:>7.0f}% {mismap_pct:>6.0f}%"
        )

    print()
    print(
        f"interpreter turns recorded: {report.recorded_llm_turns}   "
        f"INTAKE_TURN usage_events: {report.usage_events}   "
        f"reconciled: {'yes' if report.reconciled else 'NO — accounting bug'}"
    )


if __name__ == "__main__":
    asyncio.run(main())
