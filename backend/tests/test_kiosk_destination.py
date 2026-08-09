"""The destination, applied — the request half of doc 24 §4/§5.

`test_tree_destination.py` proves `Walk.destination()` decides correctly from a
set of answers. This file proves the two things only a live request can:

- **A confirmed intake lands in the department the answers named**, with its
  token drawn from that department's series. The token is per department per
  day, so applying the destination after issuing a number would hand the patient
  a number in a queue she is not in — the ordering is the test.
- **A closed department's offer never leaves the server.** Not on `/start`, not
  on the next question, and not in the offline bundle. The kiosk is not asked to
  filter anything, so an outage cannot surface a question the server would not
  have asked.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import Visit
from app.models.org import Department, Hospital
from tests import factories as f

pytestmark = pytest.mark.asyncio


async def _hospital(session: AsyncSession, *, ayurveda_open: bool) -> Hospital:
    """GENMED and PULM open; AYUR open or closed, which is the whole variable."""
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    session.add(f.make_department(hospital, code="GENMED", name="General Medicine"))
    session.add(f.make_department(hospital, code="PULM", name="Pulmonology"))
    session.add(f.make_department(hospital, code="AYUR", name="Ayurveda", active=ayurveda_open))
    await session.flush()
    return hospital


async def _start(client: AsyncClient, dept: str = "GENMED") -> dict[str, Any]:
    resp = await client.post(
        "/kiosk/start",
        json={"chief_complaint": "kamzori lag rahi hai", "lang": "hi", "dept_key": dept},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "routed", body
    return body


async def _answer(client: AsyncClient, sid: str, node_id: str, value: Any) -> dict[str, Any]:
    resp = await client.post(
        f"/kiosk/{sid}/answer",
        json={"node_id": node_id, "value": value, "raw_text": None},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"], body
    return body


async def _walk_genmed(client: AsyncClient, sid: str, *, ayurveda: bool, problem: str = "weakness"):
    """The GENMED walk-in, answering the ayurveda offer either way."""
    await _answer(client, sid, "gm.problem", problem)
    if problem == "fever":
        await _answer(client, sid, "gm.fever_temp", 39)
    await _answer(client, sid, "gm.duration", 5)
    await _answer(client, sid, "gm.severity", 4)
    await _answer(client, sid, "gm.ayur", "ayurveda" if ayurveda else "regular")
    await _answer(client, sid, "gm.words", "bas thakan rehti hai")


async def _confirm(client: AsyncClient, sid: str) -> dict[str, Any]:
    assert (await client.post(f"/kiosk/{sid}/finish")).status_code == 200
    resp = await client.post(f"/kiosk/{sid}/confirm")
    assert resp.status_code == 200, resp.text
    return resp.json()


# -- 1. the destination is applied --------------------------------------------


async def test_asking_for_ayurveda_puts_the_visit_and_the_token_in_ayurveda(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _hospital(session, ayurveda_open=True)
    started = await _start(client)
    await _walk_genmed(client, started["session_id"], ayurveda=True)

    body = await _confirm(client, started["session_id"])

    assert body["department"]["key"] == "AYUR"
    assert body["department"]["care_system"] == "allopathy"  # the factory's default
    assert body["token_no"] == 1

    visit = (await session.execute(select(Visit))).scalars().one()
    ayur = await session.scalar(select(Department).where(Department.code == "AYUR"))
    assert visit.department_id == ayur.id
    # The number belongs to the department it was issued in. Token 1 in AYUR and
    # token 1 in GENMED are different patients on different boards; a visit moved
    # after allocation would be holding GENMED's number on AYUR's queue.
    assert visit.token_no == 1


async def test_declining_the_offer_leaves_the_visit_in_general_medicine(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _hospital(session, ayurveda_open=True)
    started = await _start(client)
    await _walk_genmed(client, started["session_id"], ayurveda=False)

    body = await _confirm(client, started["session_id"])

    assert body["department"]["key"] == "GENMED"
    visit = (await session.execute(select(Visit))).scalars().one()
    genmed = await session.scalar(select(Department).where(Department.code == "GENMED"))
    assert visit.department_id == genmed.id


async def test_a_red_flag_keeps_the_patient_in_the_staffed_department(
    client: AsyncClient, session: AsyncSession
) -> None:
    """doc 24 §4, end to end. She reports trouble breathing at the first question
    and asks for ayurveda at the last; she stays in General Medicine, urgent."""
    await _hospital(session, ayurveda_open=True)
    started = await _start(client)
    await _walk_genmed(client, started["session_id"], ayurveda=True, problem="breathing")

    body = await _confirm(client, started["session_id"])

    assert body["red_flags"], "the breathlessness flag should have fired"
    assert body["department"]["key"] == "GENMED"


async def test_a_destination_that_is_closed_is_ignored_rather_than_fatal(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A department can be closed between `/start` and `/confirm` — an
    administrator taking Ayurveda off the kiosk mid-morning. The patient in front
    of the screen gets a token in the department she is already in, not a failed
    confirm.
    """
    await _hospital(session, ayurveda_open=True)
    started = await _start(client)
    await _walk_genmed(client, started["session_id"], ayurveda=True)

    ayur = await session.scalar(select(Department).where(Department.code == "AYUR"))
    ayur.active = False
    await session.flush()

    body = await _confirm(client, started["session_id"])

    assert body["department"]["key"] == "GENMED"
    assert body["token_no"] == 1


# -- 2. a closed department is never offered ----------------------------------


async def _questions_asked(client: AsyncClient, started: dict[str, Any]) -> list[str]:
    """Every node id this session is actually asked, walking to the end."""
    sid = started["session_id"]
    asked: list[str] = []
    node = started["node"]
    while node is not None:
        asked.append(node["id"])
        value: Any
        if node["type"] == "single":
            value = node["options"][0]["id"]
        elif node["type"] in ("multi", "body_map"):
            value = [node["options"][0]["id"]]
        elif node["type"] in ("scale", "number"):
            value = node["min"] or 1
        else:
            value = "kuch nahin"
        body = await _answer(client, sid, node["id"], value)
        node = body["node"]
    return asked


async def test_the_offer_is_not_asked_while_ayurveda_is_closed(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _hospital(session, ayurveda_open=False)
    started = await _start(client)

    asked = await _questions_asked(client, started)

    assert "gm.ayur" not in asked
    # ...and the tree is otherwise untouched: the question before the offer leads
    # straight to the one after it, exactly as it did before doc 24.
    assert asked == ["gm.problem", "gm.fever_temp", "gm.duration", "gm.severity", "gm.words"]


async def test_the_offer_is_asked_once_ayurveda_is_open(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _hospital(session, ayurveda_open=True)
    started = await _start(client)

    asked = await _questions_asked(client, started)

    assert asked == [
        "gm.problem",
        "gm.fever_temp",
        "gm.duration",
        "gm.severity",
        "gm.ayur",
        "gm.words",
    ]


async def test_closing_ayurveda_mid_intake_does_not_change_the_questions(
    client: AsyncClient, session: AsyncSession
) -> None:
    """`SessionState.open_departments` is pinned at `/start` for the same reason
    `contract_version` is: a patient three questions in is not the person to
    discover an administrator's edit."""
    await _hospital(session, ayurveda_open=True)
    started = await _start(client)
    await _answer(client, started["session_id"], "gm.problem", "weakness")

    ayur = await session.scalar(select(Department).where(Department.code == "AYUR"))
    ayur.active = False
    await session.flush()

    await _answer(client, started["session_id"], "gm.duration", 5)
    body = await _answer(client, started["session_id"], "gm.severity", 4)
    assert body["node"]["id"] == "gm.ayur"


async def test_the_offline_bundle_omits_the_offer_while_ayurveda_is_closed(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The kiosk does no filtering, so the pack it caches must already be right —
    otherwise the question survives the next outage."""
    await _hospital(session, ayurveda_open=False)

    resp = await client.get("/kiosk/bundle")

    assert resp.status_code == 200, resp.text
    trees = {t["tree"]["key"]: t["tree"] for t in resp.json()["trees"]}
    assert "gm.ayur" not in {n["id"] for n in trees["general_medicine_routing"]["nodes"]}
    assert "pu.ayur" not in {n["id"] for n in trees["pulmonology_routing"]["nodes"]}
    # The ayurveda trees themselves still ship: a kiosk that caches the pack
    # while the department is closed and syncs after it opens is a real
    # sequence, and the trees are content either way.
    assert "ayurveda_routing" in trees


async def test_opening_ayurveda_changes_the_bundle_etag(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A cached pack has to be invalidated by this, exactly the way a rename is
    (AYUR-1) — otherwise a kiosk keeps asking yesterday's questions."""
    await _hospital(session, ayurveda_open=False)
    closed = (await client.get("/kiosk/bundle")).json()["etag"]

    ayur = await session.scalar(select(Department).where(Department.code == "AYUR"))
    ayur.active = True
    await session.flush()

    assert (await client.get("/kiosk/bundle")).json()["etag"] != closed
