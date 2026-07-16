"""Kiosk channel tests — the first HTTP surface over the intake engine (S6).

Drives the four-tool contract through the REST routes exactly as the PWA does:
`/start` (routing Q1 + first node) → `/answer` per tap → `/finish` (read-back) →
`/confirm` (token + cost). The fake LLM answers routing with its default "ok",
which is unreadable JSON, so the classifier honours `needs_human` — that is the
`needs_department` path here, and the staff-picked `dept_key` path is the happy
walk.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.org import Hospital
from tests import factories as f

pytestmark = pytest.mark.asyncio


async def _seed_departments(session: AsyncSession) -> Hospital:
    """A hospital with the department codes the tree bank routes to."""
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    for code, name in [("MEDONC", "Medical Oncology"), ("DERM", "Dermatology")]:
        session.add(f.make_department(hospital, code=code, name=name))
    await session.flush()
    return hospital


async def _walk_to_completion(client: AsyncClient, session_id: str, node: dict[str, Any]) -> None:
    """Answer every question with a valid value until the tree completes."""
    seen = 0
    while node is not None:
        seen += 1
        assert seen < 100, "walk did not terminate"
        value: Any = None
        raw_text: str | None = None
        ntype = node["type"]
        if ntype == "single":
            value = node["options"][0]["id"]
        elif ntype in ("multi", "body_map"):
            value = [node["options"][0]["id"]]
        elif ntype in ("scale", "number"):
            value = node["min"] if node["min"] is not None else 1
        elif ntype == "free_voice":
            raw_text = "mujhe pet mein dard hai"
            value = raw_text  # a free_voice answer *is* the spoken text (V3 turn)
        resp = await client.post(
            f"/kiosk/{session_id}/answer",
            json={"node_id": node["id"], "value": value, "raw_text": raw_text},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"], body
        if body["complete"]:
            assert body["node"] is None
            return
        node = body["node"]
    return


async def test_uncertain_routing_shows_the_department_chooser(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The fake classifier cannot read its own reply → needs_human → chooser."""
    await _seed_departments(session)
    resp = await client.post(
        "/kiosk/start",
        json={"lang": "hi", "chief_complaint": "mujhe kuch theek nahi lag raha"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "needs_department"
    assert body["session_id"] is None
    codes = {d["key"] for d in body["departments"]}
    assert {"MEDONC", "DERM"} <= codes


async def test_full_kiosk_intake_start_to_token(client: AsyncClient, session: AsyncSession) -> None:
    """A staff-picked department drives a complete V3 walk to a token."""
    await _seed_departments(session)

    start = await client.post(
        "/kiosk/start",
        json={
            "lang": "hi",
            "chief_complaint": "seene mein dard",
            "dept_key": "MEDONC",
            "caregiver": True,
        },
    )
    assert start.status_code == 200, start.text
    started = start.json()
    assert started["status"] == "routed"
    assert started["department"]["key"] == "MEDONC"
    assert started["tier"] == "prerecorded"  # kiosk is a V3 client
    assert started["tree_key"].startswith("med_onc")
    session_id = started["session_id"]
    first = started["node"]
    assert first is not None
    # The node is already rendered in the patient's language, in the wire shape
    # the PWA renders directly.
    assert first["text"]

    await _walk_to_completion(client, session_id, first)

    finish = await client.post(f"/kiosk/{session_id}/finish")
    assert finish.status_code == 200, finish.text
    fin = finish.json()
    assert fin["complete"]
    assert fin["readback"]  # the patient-language read-back script

    confirm = await client.post(f"/kiosk/{session_id}/confirm")
    assert confirm.status_code == 200, confirm.text
    conf = confirm.json()
    assert isinstance(conf["token_no"], int)
    assert conf["token_no"] >= 1
    assert conf["department"]["key"] == "MEDONC"


async def test_answer_rejects_a_value_that_does_not_fit(
    client: AsyncClient, session: AsyncSession
) -> None:
    """A tap the node cannot accept comes back ok=False so the kiosk re-asks —
    it does not 500 and does not advance."""
    await _seed_departments(session)
    start = await client.post(
        "/kiosk/start",
        json={"lang": "hi", "chief_complaint": "x", "dept_key": "MEDONC"},
    )
    started = start.json()
    node = started["node"]
    session_id = started["session_id"]

    # A bogus option id for a choice node (or nonsense for any node).
    if node["type"] in ("single", "multi", "body_map"):
        bad_value: Any = "not-a-real-option"
    else:
        bad_value = "not-a-number"
    resp = await client.post(
        f"/kiosk/{session_id}/answer",
        json={"node_id": node["id"], "value": bad_value},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]
    # Still on the same question.
    assert body["node"]["id"] == node["id"]


async def test_next_resumes_the_current_question(
    client: AsyncClient, session: AsyncSession
) -> None:
    """`/next` re-renders the current node — the idle-reset / reload path."""
    await _seed_departments(session)
    start = await client.post(
        "/kiosk/start",
        json={"lang": "hi", "chief_complaint": "x", "dept_key": "MEDONC"},
    )
    started = start.json()
    session_id = started["session_id"]

    resp = await client.get(f"/kiosk/{session_id}/next")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == started["node"]["id"]


async def test_unknown_session_is_404(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_departments(session)
    resp = await client.get("/kiosk/nope-not-a-session/next")
    assert resp.status_code == 404


async def test_unknown_department_is_422(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_departments(session)
    resp = await client.post(
        "/kiosk/start",
        json={"lang": "hi", "chief_complaint": "x", "dept_key": "NOSUCH"},
    )
    assert resp.status_code == 422
