"""Doctor console read models + routes (S9, doc 03 §4/§5).

The service tests drive `app.doctor` against the rolled-back session; the route
tests go through HTTP with a real doctor JWT. The behaviours that matter here are
the ones a doctor screen can get quietly wrong: showing a patient from another
room, re-deciding a red flag, or rendering an option id where a clinician expects
a label.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import doctor as doc
from app import queue as q
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.audit import AuditLog
from app.models.content import Checkin, CheckinPlan
from app.models.enums import Channel, QueueEntryState, Role, VisitStatus

TODAY = q.today()

URGENT_FLAG = {
    "id": "skin_ulcer",
    "severity": "urgent",
    "label": {"en": "Non-healing ulcer", "hi": "न भरने वाला घाव"},
    "instruction": {"en": "Show the doctor today."},
    "source_node": "de.problem",
}

# A real tree from the bank, so the answers accordion is exercised against the
# same content the kiosk asks rather than a fixture that can drift from it.
TREE_KEY = "dermatology_routing"
TREE_REF = f"{TREE_KEY}@v1"
ANSWERS = {
    "de.problem": {"value": "lump", "text": "गाँठ", "text_en": "a lump"},
    "de.duration": {"value": 20, "text": "बीस दिन"},
}
STRUCTURED = {
    "chief_concern": "Non-healing skin ulcer for 20 days",
    "hpi": ["Ulcer on the left forearm", "Not healing despite ointment"],
    "symptoms": [{"symptom": "ulcer", "duration": "20 days", "severity": "moderate"}],
    "red_flags": ["Non-healing ulcer"],
    "history_meds": ["Diabetes, on metformin"],
    "since_last_visit": ["Wound is larger than last month"],
    "patient_words": {"quote": "घाव ठीक नहीं हो रहा", "english": "the wound is not healing"},
    "unclear": ["ointment name"],
    "readback": "आपको बीस दिन से घाव है।",
}


async def _seed_visit(
    session: AsyncSession,
    clinic: dict,
    *,
    token_no: int,
    red_flags: list[dict] | None = None,
    with_summary: bool = True,
    department=None,
    patient=None,
    date=TODAY,
):
    """A walk-in with a finished intake, enqueued the way the kiosk does it."""
    department = department or clinic["department"]
    patient = patient or clinic["patient"]
    visit = f.make_visit(patient, department, date=date, token_no=token_no)
    session.add(visit)
    await session.flush()
    intake = f.make_intake(
        visit,
        red_flags=red_flags or [],
        answers=dict(ANSWERS),
        tree_ref=TREE_REF,
        summary_md="**Non-healing skin ulcer**",
        summary_lang_versions=(
            {"hi": {"structured": STRUCTURED, "readback": STRUCTURED["readback"]}}
            if with_summary
            else {}
        ),
        completed_at=datetime.now(UTC),
    )
    session.add(intake)
    await session.flush()
    entry = await q.enqueue_from_intake(session, visit=visit, intake=intake)
    return visit, intake, entry


# -- day list -----------------------------------------------------------------


async def test_day_list_shows_the_department_queue_with_the_patient(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=7)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY)

    assert day.doctor_name == clinic["doctor"].name
    assert day.department_key == clinic["department"].code
    assert day.date == TODAY
    row = day.rows[0]
    assert row.token_no == 7
    assert row.visit_id == visit.id
    assert row.entry_id == entry.id
    assert row.patient_name == clinic["patient"].name
    assert row.chief_complaint == "पेट में दर्द"
    assert row.state == "waiting"


async def test_day_list_keeps_the_queues_urgent_first_order(session: AsyncSession) -> None:
    """The doctor sees the same order as the board — severity is not re-decided
    here, it arrives already sorted by `department_queue`."""
    clinic = await f.build_clinic(session)
    await _seed_visit(session, clinic, token_no=1)
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    await _seed_visit(session, clinic, token_no=2, red_flags=[URGENT_FLAG], patient=other)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY)

    assert [row.token_no for row in day.rows] == [2, 1]
    assert day.rows[0].priority == "urgent"
    assert day.rows[0].priority_reason == "Non-healing ulcer"
    assert day.rows[0].red_flag_count == 1


async def test_day_list_is_empty_when_nothing_is_queued(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY)
    assert day.rows == []


async def test_day_list_excludes_another_departments_queue(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    await _seed_visit(session, clinic, token_no=11, department=other_dept)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY)

    assert day.rows == []


async def test_resolve_doctor_refuses_a_login_with_no_doctor_record(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    stray = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add(stray)
    await session.flush()

    with pytest.raises(doc.DoctorError):
        await doc.resolve_doctor(session, user_id=stray.id)


# -- patient card -------------------------------------------------------------


async def test_patient_card_carries_the_stored_summary_contract(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    visit, intake, entry = await _seed_visit(session, clinic, token_no=4)

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    assert card.name == clinic["patient"].name
    assert card.token_no == 4
    assert card.intake_id == intake.id
    assert card.entry_id == entry.id
    assert card.entry_state == "waiting"
    assert card.summary.chief_concern == "Non-healing skin ulcer for 20 days"
    assert card.summary.symptoms[0]["duration"] == "20 days"
    assert card.summary.history_meds == ["Diabetes, on metformin"]
    assert card.summary.patient_words["quote"] == "घाव ठीक नहीं हो रहा"
    assert card.summary.unclear == ["ointment name"]


async def test_patient_card_reads_red_flags_from_the_rules_not_the_summary(
    session: AsyncSession,
) -> None:
    """`Intake.red_flags` is the rule engine's output; the strip renders that and
    never the summarizer's prose list."""
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=5, red_flags=[URGENT_FLAG])

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    assert len(card.red_flags) == 1
    flag = card.red_flags[0]
    assert flag.id == "skin_ulcer"
    assert flag.severity == "urgent"
    assert flag.label == "Non-healing ulcer"
    assert flag.instruction == "Show the doctor today."
    assert flag.source_node == "de.problem"


async def test_patient_card_renders_answers_as_english_questions_and_labels(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=6, red_flags=[URGENT_FLAG])

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    by_node = {row.node_id: row for row in card.answers}
    problem = by_node["de.problem"]
    assert problem.question == "What is troubling you most today?"
    assert problem.answer == "A lump or growth on the skin"  # the label, not "lump"
    assert problem.said == "a lump"
    assert problem.flagged is True  # the flag's source_node
    duration = by_node["de.duration"]
    assert duration.answer == "20 days"  # number + unit
    assert duration.flagged is False


async def test_patient_card_still_renders_answers_when_the_tree_is_unknown(
    session: AsyncSession,
) -> None:
    """An answer set outlives its tree file; showing node ids beats dropping
    clinical content the patient actually gave."""
    clinic = await f.build_clinic(session)
    visit, intake, _ = await _seed_visit(session, clinic, token_no=8)
    intake.tree_ref = "a_tree_that_was_deleted@v3"
    await session.flush()

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    by_node = {row.node_id: row for row in card.answers}
    assert by_node["de.problem"].question == "de.problem"
    assert by_node["de.problem"].answer == "lump"


async def test_patient_card_survives_an_intake_with_no_summary(
    session: AsyncSession,
) -> None:
    """A V3 intake that never reached a summarizer still has to render."""
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=9, with_summary=False)

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    assert card.summary.chief_concern is None
    assert card.summary.hpi == []
    assert card.chief_complaint == "पेट में दर्द"


async def test_patient_card_refuses_a_visit_in_another_department(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    visit, _, _ = await _seed_visit(session, clinic, token_no=12, department=other_dept)

    with pytest.raises(doc.DoctorError):
        await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])


async def test_patient_card_timeline_lists_past_visits_newest_first(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    old = f.make_visit(
        clinic["patient"], clinic["department"], date=TODAY - timedelta(days=30), token_no=3
    )
    session.add(old)
    await session.flush()
    session.add(f.make_intake(old, chief_complaint_en="earlier wound review"))
    await session.flush()
    visit, _, _ = await _seed_visit(session, clinic, token_no=13)

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    assert [t.date for t in card.timeline] == [TODAY, TODAY - timedelta(days=30)]
    assert card.timeline[0].is_current is True
    assert card.timeline[1].is_current is False
    assert card.timeline[1].chief_complaint == "earlier wound review"


async def test_patient_card_trends_need_more_than_one_point(
    session: AsyncSession,
) -> None:
    """A single check-in draws as a dot and reads as noise, so it is not a trend."""
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=14)
    plan = CheckinPlan(patient_id=clinic["patient"].id, protocol_key="chemo_cycle")
    session.add(plan)
    await session.flush()
    session.add(
        Checkin(
            plan_id=plan.id,
            due_at=datetime.now(UTC) - timedelta(days=7),
            channel=Channel.WHATSAPP,
            responses={"pain": 6, "nausea": 2, "note": "felt tired"},
        )
    )
    await session.flush()

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])
    assert card.trends == []

    session.add(
        Checkin(
            plan_id=plan.id,
            due_at=datetime.now(UTC),
            channel=Channel.WHATSAPP,
            responses={"pain": 3, "nausea": 1, "note": "better"},
        )
    )
    await session.flush()

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])
    series = {t.symptom: [p.value for p in t.points] for t in card.trends}
    assert series == {"nausea": [2.0, 1.0], "pain": [6.0, 3.0]}  # non-numeric "note" skipped


# -- routes -------------------------------------------------------------------


def _headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def test_day_route_requires_authentication(client: AsyncClient) -> None:
    resp = await client.get("/doctor/day")
    assert resp.status_code == 401


async def test_day_route_refuses_a_coordinator(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """A coordinator moves the line; they do not get the name+phone+answers card."""
    clinic = await f.build_clinic(session)
    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()

    resp = await client.get("/doctor/day", headers=_headers(settings, coordinator))
    assert resp.status_code == 403


async def test_day_route_returns_the_doctors_worklist(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    await _seed_visit(session, clinic, token_no=21, red_flags=[URGENT_FLAG])

    resp = await client.get("/doctor/day", headers=_headers(settings, clinic["user"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["department_key"] == clinic["department"].code
    assert body["rows"][0]["token_no"] == 21
    assert body["rows"][0]["priority"] == "urgent"
    assert body["rows"][0]["red_flag_count"] == 1


async def test_patient_route_returns_the_card(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=22, red_flags=[URGENT_FLAG])

    resp = await client.get(
        f"/doctor/patients/{visit.id}", headers=_headers(settings, clinic["user"])
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == clinic["patient"].name
    assert body["summary"]["chief_concern"] == "Non-healing skin ulcer for 20 days"
    assert body["red_flags"][0]["label"] == "Non-healing ulcer"
    assert any(a["question"] == "What is troubling you most today?" for a in body["answers"])


async def test_patient_route_refuses_another_departments_patient(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    visit, _, _ = await _seed_visit(session, clinic, token_no=23, department=other_dept)

    resp = await client.get(
        f"/doctor/patients/{visit.id}", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 403


# -- the actions are the queue's, and they audit ------------------------------


async def test_doctor_drives_the_queue_verbs_and_the_change_is_audited(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """S9 adds no action endpoints: the console calls the S8 queue routes with the
    doctor's own token, and the visit-status write lands in the audit trail."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=31)
    headers = _headers(settings, clinic["user"])
    await session.commit()

    called = await client.post(
        "/queue/call-next",
        headers=headers,
        json={"department_key": clinic["department"].code},
    )
    assert called.status_code == 200
    assert called.json()["state"] == "called"

    seen = await client.post(
        f"/queue/entries/{entry.id}/state", headers=headers, json={"state": "in_consult"}
    )
    assert seen.status_code == 200

    rows = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.entity == "visits", AuditLog.entity_id == visit.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows, "the doctor's queue action must be audited"
    assert any("status" in ((row.meta or {}).get("changed") or {}) for row in rows)


async def test_lab_requeue_sends_the_patient_to_the_back(session: AsyncSession) -> None:
    """ "Send to lab & re-queue" is a queue verb, not a doctor-console invention."""
    clinic = await f.build_clinic(session)
    _, _, first = await _seed_visit(session, clinic, token_no=41)
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    await _seed_visit(session, clinic, token_no=42, patient=other)

    await q.set_state(session, entry_id=first.id, state=QueueEntryState.CALLED)
    await q.set_state(session, entry_id=first.id, state=QueueEntryState.LAB_REQUEUE)
    await q.set_state(session, entry_id=first.id, state=QueueEntryState.WAITING)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY)
    assert [row.token_no for row in day.rows] == [42, 41]


async def test_no_show_drops_off_the_worklist(session: AsyncSession) -> None:
    """The worklist is a list of people still to be seen, not a log of the day."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=51)

    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.NO_SHOW)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY)
    assert day.rows == []
    assert visit.status is VisitStatus.NO_SHOW
