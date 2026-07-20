"""Queue service + routes (S8, doc 03 §6).

The service tests drive `app.queue` directly against the rolled-back session; the
route tests go through the HTTP surface with a real coordinator JWT, and cover
the two behaviours the AC calls out: an urgent red-flag token jumps the queue
with a reason chip, and the board/console read models stay consistent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import queue as q
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.enums import Priority, QueueEntryState, Role, VisitStatus

# asyncio_mode = "auto" (pyproject) runs the coroutine tests; no module-level
# mark, so the handful of pure-sync unit tests here don't warn.

TODAY = q.today()

URGENT_FLAG = {
    "id": "fever_post_chemo",
    "severity": "urgent",
    "label": {"en": "Fever after chemo", "hi": "कीमो के बाद बुखार"},
    "instruction": {"en": "Call the nurse now."},
    "source_node": "onc.fever",
}


async def _clinic(session: AsyncSession) -> dict:
    clinic = await f.build_clinic(session)
    return clinic


async def _enqueue_visit(
    session: AsyncSession,
    clinic: dict,
    *,
    token_no: int,
    red_flags: list[dict] | None = None,
) -> q.QueueEntry:
    patient = f.make_patient(clinic["hospital"])
    session.add(patient)
    await session.flush()
    visit = f.make_visit(patient, clinic["department"], date=TODAY, token_no=token_no)
    session.add(visit)
    await session.flush()
    intake = f.make_intake(visit, red_flags=red_flags or [])
    session.add(intake)
    await session.flush()
    return await q.enqueue_from_intake(session, visit=visit, intake=intake)


# -- service: enqueue + priority ----------------------------------------------


async def test_enqueue_puts_visit_in_queue(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    patient = clinic["patient"]
    visit = f.make_visit(patient, clinic["department"], date=TODAY, token_no=3)
    session.add(visit)
    await session.flush()

    entry = await q.enqueue(session, visit=visit)

    assert entry.token_no == 3
    assert entry.position == 3  # defaults to the token
    assert entry.state is QueueEntryState.WAITING
    assert entry.priority is Priority.ROUTINE
    assert visit.status is VisitStatus.IN_QUEUE


async def test_enqueue_is_idempotent_per_visit(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, token_no=5)
    session.add(visit)
    await session.flush()

    first = await q.enqueue(session, visit=visit)
    second = await q.enqueue(session, visit=visit)
    assert first.id == second.id


async def test_enqueue_without_token_refuses(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, token_no=None)
    session.add(visit)
    await session.flush()
    with pytest.raises(q.QueueError):
        await q.enqueue(session, visit=visit)


def test_priority_from_red_flags_maps_severity() -> None:
    priority, reason = q.priority_from_red_flags([URGENT_FLAG], lang="en")
    assert priority is Priority.URGENT
    assert reason == "Fever after chemo"


def test_priority_from_no_flags_is_routine() -> None:
    priority, reason = q.priority_from_red_flags([])
    assert priority is Priority.ROUTINE
    assert reason is None


async def test_red_flag_intake_enters_urgent_with_reason(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    entry = await _enqueue_visit(session, clinic, token_no=8, red_flags=[URGENT_FLAG])
    assert entry.priority is Priority.URGENT
    assert entry.priority_reason == "Fever after chemo"


# -- service: ordering / jump-the-queue (AC2) ---------------------------------


async def test_urgent_token_jumps_ahead_of_earlier_routine(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    await _enqueue_visit(session, clinic, token_no=1)  # routine, earlier
    await _enqueue_visit(session, clinic, token_no=2)  # routine, earlier
    await _enqueue_visit(session, clinic, token_no=9, red_flags=[URGENT_FLAG])  # urgent, later

    views = await q.department_queue(session, department_id=clinic["department"].id, on=TODAY)
    assert [v.token_no for v in views] == [9, 1, 2]
    assert views[0].priority is Priority.URGENT
    assert views[0].priority_reason == "Fever after chemo"


async def test_call_next_serves_urgent_first(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    await _enqueue_visit(session, clinic, token_no=1)
    await _enqueue_visit(session, clinic, token_no=7, red_flags=[URGENT_FLAG])
    queue = await q.get_or_create_queue(session, department_id=clinic["department"].id, on=TODAY)

    called = await q.call_next(session, queue_id=queue.id)
    assert called is not None
    assert called.token_no == 7
    assert called.state is QueueEntryState.CALLED
    assert called.called_at is not None


async def test_call_next_on_empty_queue_returns_none(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    queue = await q.get_or_create_queue(session, department_id=clinic["department"].id, on=TODAY)
    assert await q.call_next(session, queue_id=queue.id) is None


# -- service: state machine ---------------------------------------------------


async def test_state_transitions_and_visit_status(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    entry = await _enqueue_visit(session, clinic, token_no=4)

    entry = await q.set_state(session, entry_id=entry.id, state=QueueEntryState.CALLED)
    entry = await q.set_state(session, entry_id=entry.id, state=QueueEntryState.IN_CONSULT)
    assert entry.started_at is not None
    visit = await session.get(f.Visit, entry.visit_id)
    assert visit.status is VisitStatus.IN_CONSULT

    entry = await q.set_state(session, entry_id=entry.id, state=QueueEntryState.DONE)
    assert entry.ended_at is not None
    visit = await session.get(f.Visit, entry.visit_id)
    assert visit.status is VisitStatus.DONE


async def test_illegal_transition_raises(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    entry = await _enqueue_visit(session, clinic, token_no=6)
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.CALLED)
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.IN_CONSULT)
    await q.set_state(session, entry_id=entry.id, state=QueueEntryState.DONE)
    with pytest.raises(q.QueueError):
        await q.set_state(session, entry_id=entry.id, state=QueueEntryState.WAITING)


async def test_lab_requeue_rejoins_at_the_back(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    e1 = await _enqueue_visit(session, clinic, token_no=1)
    await _enqueue_visit(session, clinic, token_no=2)
    await _enqueue_visit(session, clinic, token_no=3)

    # e1 goes to lab then comes back — it must not return to the front.
    await q.set_state(session, entry_id=e1.id, state=QueueEntryState.CALLED)
    await q.set_state(session, entry_id=e1.id, state=QueueEntryState.LAB_REQUEUE)
    e1 = await q.set_state(session, entry_id=e1.id, state=QueueEntryState.WAITING)

    views = await q.department_queue(session, department_id=clinic["department"].id, on=TODAY)
    assert views[-1].token_no == 1  # back of the line now


# -- service: reorder (drag) --------------------------------------------------


async def test_reorder_rewrites_position(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    e1 = await _enqueue_visit(session, clinic, token_no=1)
    e2 = await _enqueue_visit(session, clinic, token_no=2)
    e3 = await _enqueue_visit(session, clinic, token_no=3)
    queue = await q.get_or_create_queue(session, department_id=clinic["department"].id, on=TODAY)

    await q.reorder(session, queue_id=queue.id, ordered_ids=[e3.id, e1.id, e2.id])
    views = await q.department_queue(session, department_id=clinic["department"].id, on=TODAY)
    assert [v.token_no for v in views] == [3, 1, 2]


async def test_reorder_cannot_demote_urgent_below_routine(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    routine = await _enqueue_visit(session, clinic, token_no=1)
    urgent = await _enqueue_visit(session, clinic, token_no=2, red_flags=[URGENT_FLAG])
    queue = await q.get_or_create_queue(session, department_id=clinic["department"].id, on=TODAY)

    # Coordinator drags routine above urgent — priority still wins the sort.
    await q.reorder(session, queue_id=queue.id, ordered_ids=[routine.id, urgent.id])
    views = await q.department_queue(session, department_id=clinic["department"].id, on=TODAY)
    assert views[0].token_no == 2  # the urgent one


# -- service: estimator + board ----------------------------------------------


def test_estimate_wait_scales_with_people_ahead() -> None:
    low, high = q.estimate_wait(ahead=4, mean_minutes=6)
    assert low <= 24 <= high
    zero_low, zero_high = q.estimate_wait(ahead=0, mean_minutes=6)
    assert zero_low == 0


async def test_board_shows_now_serving_next_and_wait(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    e1 = await _enqueue_visit(session, clinic, token_no=1)
    await _enqueue_visit(session, clinic, token_no=2)
    await _enqueue_visit(session, clinic, token_no=3)
    await q.set_state(session, entry_id=e1.id, state=QueueEntryState.CALLED)

    boards = await q.board(session, on=TODAY)
    assert len(boards) == 1
    board = boards[0]
    assert board.now_serving == 1
    assert [e.token_no for e in board.next_tokens] == [2, 3]
    assert board.waiting_count == 2
    assert board.est_wait_high >= board.est_wait_low


async def test_board_drops_a_finished_department(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    e1 = await _enqueue_visit(session, clinic, token_no=1)
    await q.set_state(session, entry_id=e1.id, state=QueueEntryState.CALLED)
    await q.set_state(session, entry_id=e1.id, state=QueueEntryState.IN_CONSULT)
    await q.set_state(session, entry_id=e1.id, state=QueueEntryState.DONE)

    boards = await q.board(session, on=TODAY)
    assert boards == []


# -- service: paper entry (downtime) ------------------------------------------


async def test_paper_entry_creates_visit_intake_and_enqueues(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    result = await q.paper_entry(
        session,
        department=clinic["department"],
        token_no=501,
        lang=f.Lang.HI,
        chief_complaint="बुखार",
        urgent=True,
        urgent_reason="nurse flagged fever",
    )
    assert result.token_no == 501
    assert result.priority is Priority.URGENT

    views = await q.department_queue(session, department_id=clinic["department"].id, on=TODAY)
    assert views[0].token_no == 501
    assert views[0].priority_reason == "nurse flagged fever"


async def test_paper_entry_duplicate_token_refuses(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    await q.paper_entry(
        session, department=clinic["department"], token_no=502, lang=f.Lang.HI,
        chief_complaint=None,
    )
    with pytest.raises(q.QueueError):
        await q.paper_entry(
            session, department=clinic["department"], token_no=502, lang=f.Lang.HI,
            chief_complaint=None,
        )


async def test_mean_consult_uses_observed_durations(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    entry = await _enqueue_visit(session, clinic, token_no=1)
    entry.started_at = datetime.now(UTC) - timedelta(minutes=10)
    entry.ended_at = datetime.now(UTC)
    entry.state = QueueEntryState.DONE
    await session.flush()
    queue = await q.get_or_create_queue(session, department_id=clinic["department"].id, on=TODAY)
    mean = await q._mean_consult_minutes(session, queue_id=queue.id)
    assert 9 < mean < 11


# -- routes -------------------------------------------------------------------


def _staff_headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id, role=user.role, name=user.name, settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def _coordinator(session: AsyncSession, clinic: dict):
    user = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(user)
    await session.flush()
    return user


async def test_board_route_is_public_and_lists_departments(
    client: AsyncClient, session: AsyncSession
) -> None:
    clinic = await _clinic(session)
    await _enqueue_visit(session, clinic, token_no=1, red_flags=[URGENT_FLAG])

    resp = await client.get("/queue/board")
    assert resp.status_code == 200
    body = resp.json()
    assert body["downtime"] is False
    dept = next(d for d in body["departments"] if d["department_key"] == clinic["department"].code)
    assert dept["next"][0]["priority"] == "urgent"
    assert dept["next"][0]["red_flag"] is True
    assert dept["next"][0]["priority_reason"] == "Fever after chemo"


async def test_console_requires_staff(client: AsyncClient, session: AsyncSession) -> None:
    resp = await client.get("/queue/console")
    assert resp.status_code == 401


async def test_console_shows_chief_complaint(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await _clinic(session)
    user = await _coordinator(session, clinic)
    await _enqueue_visit(session, clinic, token_no=1)

    resp = await client.get("/queue/console", headers=_staff_headers(settings, user))
    assert resp.status_code == 200
    dept = next(
        d for d in resp.json()["departments"] if d["department_key"] == clinic["department"].code
    )
    assert dept["entries"][0]["chief_complaint"] == "पेट में दर्द"


async def test_downtime_toggle_and_read(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await _clinic(session)
    user = await _coordinator(session, clinic)
    headers = _staff_headers(settings, user)

    assert (await client.get("/queue/downtime")).json()["active"] is False
    resp = await client.post("/queue/downtime", json={"active": True}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["active"] is True
    assert (await client.get("/queue/downtime")).json()["active"] is True
    # And it shows on the public board.
    assert (await client.get("/queue/board")).json()["downtime"] is True


async def test_downtime_toggle_requires_staff(client: AsyncClient) -> None:
    resp = await client.post("/queue/downtime", json={"active": True})
    assert resp.status_code == 401


async def test_paper_entry_route(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await _clinic(session)
    user = await _coordinator(session, clinic)
    resp = await client.post(
        "/queue/downtime/paper-entry",
        json={
            "department_key": clinic["department"].code,
            "token_no": 555,
            "lang": "hi",
            "chief_complaint": "बुखार",
            "urgent": True,
            "urgent_reason": "fever",
        },
        headers=_staff_headers(settings, user),
    )
    assert resp.status_code == 200
    assert resp.json()["token_no"] == 555
    assert resp.json()["priority"] == "urgent"


async def test_reconciliation_lists_paper_entries(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await _clinic(session)
    user = await _coordinator(session, clinic)
    await q.paper_entry(
        session, department=clinic["department"], token_no=560, lang=f.Lang.HI,
        chief_complaint="बुखार",
    )
    resp = await client.get("/queue/reconciliation", headers=_staff_headers(settings, user))
    assert resp.status_code == 200
    body = resp.json()
    tokens = [e["token_no"] for e in body["entries"]]
    assert 560 in tokens
    entry = next(e for e in body["entries"] if e["token_no"] == 560)
    assert entry["channel"] == "paper"


# -- hub fan-out --------------------------------------------------------------


class _FakeWS:
    """The slice of starlette's WebSocket the hub touches."""

    def __init__(self) -> None:
        from starlette.websockets import WebSocketState

        self.application_state = WebSocketState.CONNECTED
        self.sent: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


async def test_hub_broadcasts_queue_update_to_all_clients() -> None:
    from app.queue_hub import QueueHub

    hub = QueueHub()
    a, b = _FakeWS(), _FakeWS()
    await hub.connect(a)
    await hub.connect(b)
    assert a.accepted and b.accepted
    assert hub.client_count == 2

    await hub.notify_queue_changed()
    assert a.sent[-1]["type"] == "queue_update"
    assert b.sent[-1]["type"] == "queue_update"


async def test_hub_downtime_is_broadcast_and_sent_on_connect() -> None:
    from app.queue_hub import QueueHub

    hub = QueueHub()
    early = _FakeWS()
    await hub.connect(early)
    assert early.sent[0] == {"type": "downtime", "active": False, "since": None}

    await hub.set_downtime(True)
    assert early.sent[-1]["active"] is True
    assert hub.downtime is True

    # A screen that joins mid-outage is told immediately.
    late = _FakeWS()
    await hub.connect(late)
    assert late.sent[0]["active"] is True


async def test_hub_drops_a_disconnected_client() -> None:
    from starlette.websockets import WebSocketState

    from app.queue_hub import QueueHub

    hub = QueueHub()
    ws = _FakeWS()
    await hub.connect(ws)
    ws.application_state = WebSocketState.DISCONNECTED
    await hub.notify_queue_changed()
    assert hub.client_count == 0


async def test_call_next_and_state_via_routes(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic = await _clinic(session)
    user = await _coordinator(session, clinic)
    headers = _staff_headers(settings, user)
    await _enqueue_visit(session, clinic, token_no=1)

    resp = await client.post(
        "/queue/call-next", json={"department_key": clinic["department"].code}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["token_no"] == 1
    assert resp.json()["state"] == "called"
