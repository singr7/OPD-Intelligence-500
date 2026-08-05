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
from app.models.clinical import Dictation
from app.models.content import Checkin, CheckinPlan
from app.models.enums import (
    Channel,
    DictationStatus,
    QueueEntryState,
    Role,
    RxMode,
    VisitStatus,
)

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


async def _colleague(session: AsyncSession, clinic: dict):
    """A second doctor in the same department, for cover and handover."""
    user = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add(user)
    await session.flush()
    doctor = f.make_doctor(user, clinic["department"])
    session.add(doctor)
    await session.flush()
    return doctor


# -- day list -----------------------------------------------------------------


async def test_day_list_shows_the_department_queue_with_the_patient(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=7)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="department")

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


async def test_the_worklist_scopes_to_the_assigned_doctor(session: AsyncSession) -> None:
    """Two patients in one department: one assigned to this doctor, one left in
    the pool. `scope="mine"` shows only the first, and the unassigned one is
    still reachable and *countable* — an unassigned waiting patient that no
    console surfaces is worse than the over-broad list it replaced."""
    clinic = await f.build_clinic(session)
    mine, _, _ = await _seed_visit(session, clinic, token_no=1)
    mine.doctor_id = clinic["doctor"].id

    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    pooled, _, _ = await _seed_visit(session, clinic, token_no=2, patient=other)
    assert pooled.doctor_id is None
    await session.flush()

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="mine")

    assert [r.visit_id for r in day.rows] == [mine.id]
    assert day.rows[0].is_mine is True
    assert day.rows[0].assigned_doctor_name == clinic["doctor"].name
    assert day.counts.unassigned == 1


async def test_the_unassigned_scope_is_the_department_pool(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    mine, _, _ = await _seed_visit(session, clinic, token_no=1)
    mine.doctor_id = clinic["doctor"].id
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    pooled, _, _ = await _seed_visit(session, clinic, token_no=2, patient=other)
    await session.flush()

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="unassigned")

    assert [r.visit_id for r in day.rows] == [pooled.id]
    assert day.rows[0].is_mine is False
    assert day.rows[0].assigned_doctor_id is None


async def test_the_department_scope_shows_a_colleagues_patient_too(
    session: AsyncSession,
) -> None:
    """Cover and handover: the whole room, with the colleague named. Naming them
    is the point — an unlabelled row in the department list is indistinguishable
    from an unassigned one, which is exactly the confusion the pool exists to
    resolve."""
    clinic = await f.build_clinic(session)
    colleague = await _colleague(session, clinic)
    theirs, _, _ = await _seed_visit(session, clinic, token_no=3)
    theirs.doctor_id = colleague.id
    await session.flush()

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="department")

    assert [r.visit_id for r in day.rows] == [theirs.id]
    assert day.rows[0].assigned_doctor_name == colleague.name
    assert day.rows[0].is_mine is False
    assert day.counts.mine == 0
    assert day.counts.unassigned == 0
    assert day.counts.department == 1


async def test_counts_are_returned_whatever_scope_was_asked_for(
    session: AsyncSession,
) -> None:
    """The `Unassigned` badge has to be truthful while its tab is closed — that
    is the whole reason the counts ride on every response."""
    clinic = await f.build_clinic(session)
    mine, _, _ = await _seed_visit(session, clinic, token_no=1)
    mine.doctor_id = clinic["doctor"].id
    for token_no in (2, 3):
        patient = f.make_patient(clinic["hospital"])
        session.add(patient)
        await session.flush()
        await _seed_visit(session, clinic, token_no=token_no, patient=patient)
    await session.flush()

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="mine")

    assert len(day.rows) == 1
    assert day.counts.mine == 1
    assert day.counts.unassigned == 2
    assert day.counts.department == 3
    assert day.counts.unassigned_waiting == 2


async def test_unassigned_waiting_matches_the_coordinators_metric(
    session: AsyncSession,
) -> None:
    """A called patient is no longer *waiting*, so the attention count drops even
    though the pool has not. The coordinator console counts it the same way; the
    desk and the consulting room must not quote different numbers."""
    clinic = await f.build_clinic(session)
    pooled, _, entry = await _seed_visit(session, clinic, token_no=1)
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.CALLED)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="department")

    assert day.counts.unassigned == 1
    assert day.counts.unassigned_waiting == 0


async def test_day_list_refuses_a_scope_it_does_not_have(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    with pytest.raises(doc.DoctorError):
        await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="everybody")  # type: ignore[arg-type]


async def test_day_list_keeps_the_queues_urgent_first_order(session: AsyncSession) -> None:
    """The doctor sees the same order as the board — severity is not re-decided
    here, it arrives already sorted by `department_queue`. Scoping filters rows;
    it never reorders them, so the urgent token leads every scope it appears in."""
    clinic = await f.build_clinic(session)
    await _seed_visit(session, clinic, token_no=1)
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    await _seed_visit(session, clinic, token_no=2, red_flags=[URGENT_FLAG], patient=other)

    for scope in ("department", "unassigned"):
        day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope=scope)

        assert [row.token_no for row in day.rows] == [2, 1]
        assert day.rows[0].priority == "urgent"
        assert day.rows[0].priority_reason == "Non-healing ulcer"
        assert day.rows[0].red_flag_count == 1


async def test_day_list_is_empty_when_nothing_is_queued(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="department")
    assert day.rows == []
    assert day.counts.department == 0


async def test_day_list_excludes_another_departments_queue(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    await _seed_visit(session, clinic, token_no=11, department=other_dept)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="department")

    assert day.rows == []


# -- take this patient --------------------------------------------------------


async def test_take_visit_puts_the_doctors_name_on_an_unassigned_patient(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    pooled, _, _ = await _seed_visit(session, clinic, token_no=1)
    assert pooled.doctor_id is None

    await doc.take_visit(session, visit_id=pooled.id, doctor=clinic["doctor"])

    assert pooled.doctor_id == clinic["doctor"].id
    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="mine")
    assert [r.visit_id for r in day.rows] == [pooled.id]
    assert day.counts.unassigned == 0


async def test_take_visit_covers_a_colleagues_patient(session: AsyncSession) -> None:
    """Cover is routine in an OPD. Making it need a coordinator turns one
    doctor's absence into a stalled line, so this is allowed, not refused."""
    clinic = await f.build_clinic(session)
    colleague = await _colleague(session, clinic)
    theirs, _, _ = await _seed_visit(session, clinic, token_no=1)
    theirs.doctor_id = colleague.id
    await session.flush()

    await doc.take_visit(session, visit_id=theirs.id, doctor=clinic["doctor"])

    assert theirs.doctor_id == clinic["doctor"].id


async def test_take_visit_refuses_another_departments_patient(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    elsewhere, _, _ = await _seed_visit(session, clinic, token_no=1, department=other_dept)

    with pytest.raises(doc.DoctorError, match="another department"):
        await doc.take_visit(session, visit_id=elsewhere.id, doctor=clinic["doctor"])

    assert elsewhere.doctor_id is None


async def test_take_visit_is_audited(session: AsyncSession) -> None:
    """`Visit` is a `Clinical` model, so the change lands in the audit trail via
    the `before_flush` hook — no route-level call anyone can forget to add. Taking
    a colleague's patient must be reconstructible afterwards."""
    clinic = await f.build_clinic(session)
    pooled, _, _ = await _seed_visit(session, clinic, token_no=1)

    await doc.take_visit(session, visit_id=pooled.id, doctor=clinic["doctor"])
    await session.flush()

    logs = (
        await session.scalars(
            select(AuditLog).where(AuditLog.entity == "visits", AuditLog.entity_id == pooled.id)
        )
    ).all()
    assert any("doctor_id" in (log.meta.get("changed") or {}) for log in logs)


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


async def test_patient_card_flags_every_answer_a_multi_node_rule_used(
    session: AsyncSession,
) -> None:
    """The clinically interesting flags span several nodes and carry no
    `source_node`, so highlighting on that alone would leave the febrile
    -neutropenia patient's fever unmarked. The nodes come from the fired rule's
    own `when` condition instead."""
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, token_no=61)
    session.add(visit)
    await session.flush()
    session.add(
        f.make_intake(
            visit,
            tree_ref="med_onc_between_cycle@v1",
            answers={
                "mo.cyc.days_since": {"value": 8},
                "mo.cyc.fever_temp": {"value": 38.6},
                "mo.cyc.nausea": {"value": 6},
            },
            # Fired by the rules; note it has no source_node of its own.
            red_flags=[
                {
                    "id": "mo.cyc.febrile_neutropenia",
                    "severity": "urgent",
                    "label": {"en": "Fever 38°C+ within 14 days of chemotherapy"},
                    "instruction": {"en": "Call the nurse now."},
                    "source_node": None,
                }
            ],
        )
    )
    await session.flush()

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    flagged = {row.node_id for row in card.answers if row.flagged}
    assert flagged == {"mo.cyc.fever_temp", "mo.cyc.days_since"}


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


# -- concluding the consult (plan §5.3b) --------------------------------------
#
# The interesting cases are the two lossy ones. A paper prescription is a real
# and common outcome, and today it leaves a visit that looks exactly like one the
# doctor abandoned halfway — same status, same queue entry, same nothing.


async def _in_the_room(session: AsyncSession, entry) -> None:
    """Get an entry to where a consult is actually happening."""
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.CALLED)
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.IN_CONSULT)


async def test_a_paper_prescription_is_recorded_rather_than_left_blank(
    session: AsyncSession,
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=71)
    await _in_the_room(session, entry)

    result = await doc.conclude_visit(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        rx_mode=RxMode.EXTERNAL_MANUAL,
        note="Written on the OPD pad; patient taking it to the hospital pharmacy.",
    )

    assert visit.rx_mode is RxMode.EXTERNAL_MANUAL
    assert visit.concluded_by == clinic["doctor"].id
    assert visit.concluded_at is not None
    assert visit.conclusion_note.startswith("Written on the OPD pad")
    # …and the queue moved, through the S8 verb rather than a second machine.
    assert entry.state is QueueEntryState.DONE
    assert result.entry_state == "done"
    assert visit.status is VisitStatus.DONE


async def test_an_unconcluded_visit_is_not_the_same_as_one_that_prescribed_nothing(
    session: AsyncSession,
) -> None:
    """Null `rx_mode` is "nobody has said yet". `none` is a doctor saying it."""
    clinic = await f.build_clinic(session)
    untouched, _, _ = await _seed_visit(session, clinic, token_no=72)
    advice_only, _, entry = await _seed_visit(session, clinic, token_no=73)
    await _in_the_room(session, entry)

    await doc.conclude_visit(
        session, visit_id=advice_only.id, doctor=clinic["doctor"], rx_mode=RxMode.NONE
    )

    assert untouched.rx_mode is None
    assert advice_only.rx_mode is RxMode.NONE


async def test_concluding_as_system_needs_a_signed_note(session: AsyncSession) -> None:
    """The one mode this function can check, so it checks it: `system` claims a
    digital prescription exists, and the pharmacy would go looking for it."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=74)
    await _in_the_room(session, entry)

    with pytest.raises(doc.ConclusionRefused, match="no signed consult note"):
        await doc.conclude_visit(
            session, visit_id=visit.id, doctor=clinic["doctor"], rx_mode=RxMode.SYSTEM
        )

    assert visit.rx_mode is None
    assert entry.state is not QueueEntryState.DONE

    session.add(_signed_note(visit, clinic["doctor"], diagnosis="x", signed_at=datetime.now(UTC)))
    await session.flush()
    await doc.conclude_visit(
        session, visit_id=visit.id, doctor=clinic["doctor"], rx_mode=RxMode.SYSTEM
    )
    assert visit.rx_mode is RxMode.SYSTEM


async def test_concluding_refuses_another_departments_visit(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    elsewhere, _, _ = await _seed_visit(session, clinic, token_no=75, department=other_dept)

    with pytest.raises(doc.DoctorError, match="another department"):
        await doc.conclude_visit(
            session, visit_id=elsewhere.id, doctor=clinic["doctor"], rx_mode=RxMode.NONE
        )

    assert elsewhere.rx_mode is None


async def test_concluding_does_not_drag_a_no_show_back_through_done(
    session: AsyncSession,
) -> None:
    """Both terminal states stay put. The conclusion is still worth recording —
    what must not happen is the queue rewriting what happened to that patient."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=76)
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.NO_SHOW)

    result = await doc.conclude_visit(
        session, visit_id=visit.id, doctor=clinic["doctor"], rx_mode=RxMode.NONE
    )

    assert entry.state is QueueEntryState.NO_SHOW
    assert result.entry_state == "no_show"
    assert visit.rx_mode is RxMode.NONE


async def test_concluding_a_called_patient_walks_the_queue_rather_than_jumping_it(
    session: AsyncSession,
) -> None:
    """A doctor who concludes a called patient has seen them. The entry takes
    the path it would have taken anyway, inside the S8 table, so `started_at`
    still means what it says."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=79)
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.CALLED)

    await doc.conclude_visit(
        session, visit_id=visit.id, doctor=clinic["doctor"], rx_mode=RxMode.EXTERNAL_MANUAL
    )

    assert entry.state is QueueEntryState.DONE
    assert entry.started_at is not None and entry.ended_at is not None


async def test_a_patient_nobody_called_cannot_be_concluded(session: AsyncSession) -> None:
    """Waiting is not a consult. Marking them done would take them off the board
    without anyone having seen them, so nothing is written at all."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=80)

    with pytest.raises(doc.ConclusionRefused, match="not been called in"):
        await doc.conclude_visit(
            session, visit_id=visit.id, doctor=clinic["doctor"], rx_mode=RxMode.EXTERNAL_MANUAL
        )

    assert visit.rx_mode is None
    assert entry.state is QueueEntryState.WAITING


async def test_a_conclusion_is_audited(session: AsyncSession) -> None:
    """The whole value of `external_manual` is that it is on the record. A
    conclusion nobody can reconstruct afterwards is not a clinical record."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=77)
    await _in_the_room(session, entry)

    await doc.conclude_visit(
        session, visit_id=visit.id, doctor=clinic["doctor"], rx_mode=RxMode.EXTERNAL_MANUAL
    )
    await session.flush()

    logs = (
        await session.scalars(
            select(AuditLog).where(AuditLog.entity == "visits", AuditLog.entity_id == visit.id)
        )
    ).all()
    assert any("rx_mode" in (log.meta.get("changed") or {}) for log in logs)


async def test_the_card_carries_the_conclusion(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=78)
    await _in_the_room(session, entry)
    await doc.conclude_visit(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        rx_mode=RxMode.EXTERNAL_MANUAL,
        note="paper script",
    )

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    assert card.rx_mode == "external_manual"
    assert card.conclusion_note == "paper script"
    assert card.concluded_at is not None


# -- the diagnosis line on the context spine ----------------------------------


def _signed_note(visit, doctor, *, diagnosis: str | None, signed_at: datetime) -> Dictation:
    return Dictation(
        visit_id=visit.id,
        doctor_id=doctor.id,
        transcript="…",
        structured={"diagnosis": diagnosis} if diagnosis else {},
        status=DictationStatus.SIGNED,
        signed_at=signed_at,
        signed_by=doctor.id,
    )


async def test_the_spine_carries_the_latest_signed_diagnosis(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=61)
    session.add(
        _signed_note(
            visit,
            clinic["doctor"],
            diagnosis="Stage IIIA breast carcinoma",
            signed_at=datetime.now(UTC),
        )
    )
    await session.flush()

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    assert card.diagnosis is not None
    assert card.diagnosis.text == "Stage IIIA breast carcinoma"
    assert card.diagnosis.is_current_visit is True


async def test_the_diagnosis_survives_from_an_earlier_visit_and_says_so(
    session: AsyncSession,
) -> None:
    """A diagnosis made last month is what makes today's presentation legible.
    The spine renders it — with its date, so it is never read as today's."""
    clinic = await f.build_clinic(session)
    past, _, _ = await _seed_visit(session, clinic, token_no=60, date=TODAY - timedelta(days=30))
    session.add(
        _signed_note(
            past,
            clinic["doctor"],
            diagnosis="Invasive ductal carcinoma",
            signed_at=datetime.now(UTC) - timedelta(days=30),
        )
    )
    today_visit, _, _ = await _seed_visit(session, clinic, token_no=61)
    await session.flush()

    card = await doc.patient_card(session, visit_id=today_visit.id, doctor=clinic["doctor"])

    assert card.diagnosis is not None
    assert card.diagnosis.text == "Invasive ductal carcinoma"
    assert card.diagnosis.on == past.date
    assert card.diagnosis.is_current_visit is False


async def test_an_unsigned_note_never_becomes_the_diagnosis(session: AsyncSession) -> None:
    """A draft is a doctor thinking out loud mid-consult. Promoting one to the
    permanent line at the top of every later screen would put an unreviewed
    machine transcription where a clinician reads a diagnosis."""
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=62)
    session.add(
        Dictation(
            visit_id=visit.id,
            doctor_id=clinic["doctor"].id,
            transcript="…",
            structured={"diagnosis": "probably TB"},
            status=DictationStatus.DRAFT,
        )
    )
    await session.flush()

    card = await doc.patient_card(session, visit_id=visit.id, doctor=clinic["doctor"])

    assert card.diagnosis is None


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
    visit, _, _ = await _seed_visit(session, clinic, token_no=21, red_flags=[URGENT_FLAG])
    visit.doctor_id = clinic["doctor"].id
    await session.flush()

    resp = await client.get("/doctor/day", headers=_headers(settings, clinic["user"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["department_key"] == clinic["department"].code
    assert body["scope"] == "mine"
    assert body["rows"][0]["token_no"] == 21
    assert body["rows"][0]["priority"] == "urgent"
    assert body["rows"][0]["red_flag_count"] == 1
    assert body["rows"][0]["is_mine"] is True


async def test_day_route_defaults_to_mine_and_still_counts_the_pool(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """The unassigned patient is not in the default list, but the caller is told
    they exist. That is the compensating control for a kiosk `Skip`."""
    clinic = await f.build_clinic(session)
    await _seed_visit(session, clinic, token_no=21)

    resp = await client.get("/doctor/day", headers=_headers(settings, clinic["user"]))

    body = resp.json()
    assert body["rows"] == []
    assert body["counts"] == {
        "mine": 0,
        "unassigned": 1,
        "department": 1,
        "unassigned_waiting": 1,
        "waiting": 1,
    }


async def test_day_route_serves_the_unassigned_scope(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    await _seed_visit(session, clinic, token_no=21)

    resp = await client.get(
        "/doctor/day", params={"scope": "unassigned"}, headers=_headers(settings, clinic["user"])
    )

    body = resp.json()
    assert body["scope"] == "unassigned"
    assert [row["token_no"] for row in body["rows"]] == [21]
    assert body["rows"][0]["assigned_doctor_name"] is None


async def test_day_route_refuses_a_scope_that_does_not_exist(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    resp = await client.get(
        "/doctor/day", params={"scope": "everybody"}, headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 422


async def test_take_route_assigns_the_visit_to_the_caller(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=23)

    resp = await client.post(
        f"/doctor/visits/{visit.id}/take", headers=_headers(settings, clinic["user"])
    )

    assert resp.status_code == 200
    assert resp.json()["assigned_doctor_name"] == clinic["doctor"].name

    day = await client.get("/doctor/day", headers=_headers(settings, clinic["user"]))
    body = day.json()
    assert [row["token_no"] for row in body["rows"]] == [23]
    assert body["counts"]["unassigned"] == 0


async def test_take_route_refuses_a_coordinator(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=24)
    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()

    resp = await client.post(
        f"/doctor/visits/{visit.id}/take", headers=_headers(settings, coordinator)
    )
    assert resp.status_code == 403


async def test_take_route_refuses_another_departments_visit(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    visit, _, _ = await _seed_visit(session, clinic, token_no=25, department=other_dept)

    resp = await client.post(
        f"/doctor/visits/{visit.id}/take", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 403


async def test_conclude_route_records_a_paper_script_and_closes_the_queue(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=26)
    await _in_the_room(session, entry)

    resp = await client.post(
        f"/doctor/visits/{visit.id}/conclude",
        json={"rx_mode": "external_manual", "note": "paper script"},
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rx_mode"] == "external_manual"
    assert body["conclusion_note"] == "paper script"
    assert body["entry_state"] == "done"
    assert body["concluded_at"]

    # And the patient has left the worklist, like anyone else who is done.
    day = await client.get(
        "/doctor/day", params={"scope": "department"}, headers=_headers(settings, clinic["user"])
    )
    assert [row["token_no"] for row in day.json()["rows"]] == []


async def test_conclude_route_refuses_system_without_a_signature(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """400, not 403 — the doctor has every right to be here, they just have not
    signed anything, and a permission error would send them hunting for the
    wrong problem."""
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=27)

    resp = await client.post(
        f"/doctor/visits/{visit.id}/conclude",
        json={"rx_mode": "system"},
        headers=_headers(settings, clinic["user"]),
    )

    assert resp.status_code == 400
    assert "signed consult note" in resp.json()["detail"]


async def test_conclude_route_requires_a_mode_and_refuses_a_coordinator(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await f.build_clinic(session)
    visit, _, _ = await _seed_visit(session, clinic, token_no=28)
    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()

    # No default for `rx_mode`: a default would be a guess about a clinical fact.
    resp = await client.post(
        f"/doctor/visits/{visit.id}/conclude",
        json={"note": "…"},
        headers=_headers(settings, clinic["user"]),
    )
    assert resp.status_code == 422

    resp = await client.post(
        f"/doctor/visits/{visit.id}/conclude",
        json={"rx_mode": "none"},
        headers=_headers(settings, coordinator),
    )
    assert resp.status_code == 403


async def test_the_card_is_not_narrowed_to_the_assigned_doctor(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """Filtering a list is UX; narrowing access is a clinical-continuity
    decision this codebase made the other way, on the record. A colleague's
    patient opens, and the card says whose patient it is."""
    clinic = await f.build_clinic(session)
    colleague = await _colleague(session, clinic)
    visit, _, _ = await _seed_visit(session, clinic, token_no=26)
    visit.doctor_id = colleague.id
    await session.flush()

    resp = await client.get(
        f"/doctor/patients/{visit.id}", headers=_headers(settings, clinic["user"])
    )

    assert resp.status_code == 200
    assert resp.json()["assigned_doctor_name"] == colleague.name


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

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="department")
    assert [row.token_no for row in day.rows] == [42, 41]


async def test_no_show_drops_off_the_worklist(session: AsyncSession) -> None:
    """The worklist is a list of people still to be seen, not a log of the day."""
    clinic = await f.build_clinic(session)
    visit, _, entry = await _seed_visit(session, clinic, token_no=51)

    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.NO_SHOW)

    day = await doc.day_list(session, doctor=clinic["doctor"], on=TODAY, scope="department")
    assert day.rows == []
    assert visit.status is VisitStatus.NO_SHOW
