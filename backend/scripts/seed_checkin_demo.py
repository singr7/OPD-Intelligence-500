"""The check-in engine on a screen, in one command (S17, doc 03 §9).

Walks the whole session end to end against the dev database, so the on-box pass
is a script rather than a morning of clicking:

1. Signs a **carboplatin** consult note for the first seeded patient, which
   drafts a platinum check-in plan (D+2, D+7, D+14).
2. Approves it as the seeded doctor — the one tap — so its three check-ins exist.
3. Marks the D+2 one as sent, answers it **red** (seven vomits), and lets the
   real escalation path run: the doctor and the coordinator are alerted through
   whatever SMS provider the box is configured with, and she lands on the nurse
   queue.
4. Leaves the D+7 one **pending and due now**, so `python -m app.worker
   opd.checkins.send` has something real to deliver.

Everything it writes goes through the real services (`app.dictation.sign`,
`app.checkins.plan.approve`, `app.checkins.grading.answer_one`) — nothing here
hand-writes a row that production would write differently. Like the other demo
seeds it hard-deletes its own plans first so it is repeatable.

**It sends real messages if the box has real providers configured.** On a fake
stack (the default) it sends nothing anywhere.

Run against the dev DB:
    DATABASE_URL=postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd \
        .venv/bin/python -m scripts.seed_checkin_demo
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app import dictation as dictation_svc
from app.checkins import grading as grading_svc
from app.checkins import plan as plan_svc
from app.db import build_engine, build_sessionmaker
from app.models.clinical import Dictation, Visit
from app.models.content import Checkin, CheckinPlan
from app.models.enums import Channel, CheckinState, VisitStatus
from app.models.org import Doctor
from app.models.patient import Patient

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "checkin_dictations.json"


async def run() -> str:
    engine = build_engine()
    lines: list[str] = []
    try:
        async with build_sessionmaker(engine)() as session:
            patient = await session.scalar(
                select(Patient).where(Patient.deleted_at.is_(None)).order_by(Patient.created_at)
            )
            doctor = await session.scalar(
                select(Doctor).where(Doctor.deleted_at.is_(None)).order_by(Doctor.created_at)
            )
            if patient is None or doctor is None:
                return "no seeded patient or doctor — run `make seed` first"

            # Repeatable: drop this script's own previous run.
            old = list(
                await session.scalars(
                    select(CheckinPlan.id).where(CheckinPlan.patient_id == patient.id)
                )
            )
            if old:
                await session.execute(delete(Checkin).where(Checkin.plan_id.in_(old)))
                await session.execute(delete(CheckinPlan).where(CheckinPlan.id.in_(old)))

            treated_on = datetime.now(UTC) - timedelta(days=2)
            case = next(
                c
                for c in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
                if c["id"] == "carboplatin-day-1"
            )
            mapping = json.loads(json.dumps(case["mapping"]))
            mapping["treatment_events"][0]["date"] = treated_on.date().isoformat()
            mapping["treatment_events"][0]["next_due"] = (
                (treated_on + timedelta(days=21)).date().isoformat()
            )

            visit = Visit(
                patient_id=patient.id,
                department_id=doctor.department_id,
                doctor_id=doctor.id,
                date=treated_on.date(),
                channel=Channel.KIOSK,
                status=VisitStatus.DONE,
            )
            session.add(visit)
            await session.flush()

            dictation = Dictation(
                visit_id=visit.id,
                doctor_id=doctor.id,
                transcript="Carboplatin AUC 5 aaj chadha diya, cycle one. Emeset BD paanch din.",
                structured={
                    **dictation_svc.empty_structured(),
                    "mapped": mapping,
                    "fields": mapping,
                },
            )
            session.add(dictation)
            await session.flush()

            # 1 + 2: sign (drafts the plan) and approve (materialises it).
            await dictation_svc.sign(session, dictation=dictation, doctor=doctor)
            plan = await session.scalar(
                select(CheckinPlan).where(CheckinPlan.dictation_id == dictation.id)
            )
            if plan is None:
                return "no plan drafted — check the protocol bank"
            checkins = await plan_svc.approve(session, plan=plan, doctor=doctor)
            lines.append(
                f"plan {plan.protocol_key} for {patient.name}: "
                + ", ".join(f"D+{c.day_offset} {c.question_set}" for c in checkins)
            )

            # 3: the D+2 one, answered red, through the real grading path.
            d2 = checkins[0]
            d2.state = CheckinState.SENT
            d2.sent_at = datetime.now(UTC)
            await session.flush()
            grading, _ = await grading_svc.answer_one(
                session, checkin=d2, question_id="ck.gi.vomit", raw=7
            )
            lines.append(
                f"D+{d2.day_offset} answered 7 vomits → {grading.grade}: "
                + "; ".join(r.reason for r in grading.reasons)
            )

            # 4: the D+7 one, due now, for the delivery job to pick up.
            d7 = checkins[1]
            d7.due_at = datetime.now(UTC)
            d7.next_attempt_at = datetime.now(UTC)
            await session.flush()
            lines.append(f"D+{d7.day_offset} left pending and due now")

            await session.commit()
            lines.append(f"patient {patient.phone} · doctor {doctor.phone} ({doctor.name})")
            lines.append("nurse queue: GET /checkins/review   drafts: GET /checkins/plans/drafts")
    finally:
        await engine.dispose()
    return "\n".join(lines)


def main() -> None:
    print(asyncio.run(run()))


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()
