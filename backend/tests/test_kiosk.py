"""Kiosk channel tests — the first HTTP surface over the intake engine (S6).

Drives the four-tool contract through the REST routes exactly as the PWA does:
`/start` (routing Q1 + first node) → `/answer` per tap → `/finish` (read-back) →
`/confirm` (token + cost). The fake LLM answers routing with its default "ok",
which is unreadable JSON, so the classifier honours `needs_human` — that is the
`needs_department` path here, and the staff-picked `dept_key` path is the happy
walk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
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


async def test_stt_transcribes_the_uploaded_clip(client: AsyncClient) -> None:
    """The server-STT path: a posted clip comes back as text via the STT chain.

    With the default `fake` provider the transcript is deterministic ("haan");
    on a V-OSS box the same route runs the clip through local Whisper so the
    audio never leaves the premises.
    """
    resp = await client.post(
        "/kiosk/stt",
        files={"file": ("clip.webm", b"\x00\x01\x02\x03fake-audio", "audio/webm")},
        data={"lang": "hi", "duration_seconds": "2.5"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "haan"
    assert body["provider"] == "fake-stt"
    assert body["uncertain"] is False


async def test_stt_rejects_an_empty_upload(client: AsyncClient) -> None:
    resp = await client.post(
        "/kiosk/stt",
        files={"file": ("clip.webm", b"", "audio/webm")},
        data={"lang": "hi"},
    )
    assert resp.status_code == 422


async def test_tts_synthesizes_the_read_aloud(client: AsyncClient) -> None:
    """The server-TTS path: text comes back as playable audio via the TTS chain.

    With the default `fake` provider the clip is deterministic silence; on a V-OSS
    box the same route runs Voicebox's cloned Dhara voice on the premises, so the
    read-aloud never leaves the box (doc 10 §6).
    """
    resp = await client.post("/kiosk/tts", json={"text": "aap kaise hain", "lang": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["audio"]  # base64 audio present
    assert body["provider"] == "fake-tts"
    assert body["sample_rate"] == 24000


async def test_tts_rejects_empty_text(client: AsyncClient) -> None:
    resp = await client.post("/kiosk/tts", json={"text": "   ", "lang": "hi"})
    assert resp.status_code == 422


# -- adaptive intake: answer by voice + one clarify (S-ADAPT.1, doc 11) --------


class _ScriptedInterpreter:
    """A route-test double: returns queued `Interpretation`s, records the nodes it
    saw. Lets the kiosk route be tested without a live LLM or the tree's specifics."""

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.calls: list[tuple[str, str]] = []

    def push(self, interpretation: Any) -> None:
        self.queue.append(interpretation)

    async def interpret(self, node: Any, utterance: str, lang: Any) -> Any:
        self.calls.append((node.id, utterance))
        return self.queue.pop(0)


@pytest_asyncio.fixture
async def adaptive(
    session: AsyncSession, settings, sms
) -> AsyncIterator[tuple[AsyncClient, _ScriptedInterpreter]]:
    """A kiosk client whose engine has the adaptive interpreter wired (doc 11 §2).

    Mirrors the conftest `client` fixture but injects a `_ScriptedInterpreter`, so a
    test drives the clarify / value / fall-back-to-taps branches deterministically.
    """
    from httpx import ASGITransport

    from app.config import get_settings
    from app.db import get_session
    from app.intake import InMemorySessionStore, IntakeEngine
    from app.main import create_app
    from app.queue_hub import QueueHub

    interp = _ScriptedInterpreter()
    app = create_app(settings)
    app.state.intake_engine = IntakeEngine(InMemorySessionStore(), interpreter=interp)
    app.state.queue_hub = QueueHub()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, interp


async def _start_med_onc(client: AsyncClient, session: AsyncSession) -> dict[str, Any]:
    await _seed_departments(session)
    start = await client.post(
        "/kiosk/start",
        json={"lang": "hi", "chief_complaint": "seene mein dard", "dept_key": "MEDONC"},
    )
    assert start.status_code == 200, start.text
    return start.json()


def _valid_value(node: dict[str, Any]) -> Any:
    """A value the given node accepts — mirrors `_walk_to_completion`."""
    ntype = node["type"]
    if ntype == "single":
        return node["options"][0]["id"]
    if ntype in ("multi", "body_map"):
        return [node["options"][0]["id"]]
    if ntype in ("scale", "number"):
        return node["min"] if node["min"] is not None else 1
    return None


async def test_voice_answer_maps_to_a_value_and_advances(
    adaptive: tuple[AsyncClient, _ScriptedInterpreter], session: AsyncSession
) -> None:
    """A spoken answer the interpreter maps is saved through the normal validator
    and the walk advances — same as a tap would (doc 11 §2, §5)."""
    from app.intake.interpret import Interpretation

    client, interp = adaptive
    started = await _start_med_onc(client, session)
    node = started["node"]
    sid = started["session_id"]
    interp.push(Interpretation(value=_valid_value(node), confidence=0.95))

    resp = await client.post(
        f"/kiosk/{sid}/answer",
        json={"node_id": node["id"], "value": None, "raw_text": "haan bilkul", "attempt": 0},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert interp.calls and interp.calls[0][0] == node["id"]
    # Advanced off the node (or completed) — not stuck re-asking.
    assert body["node"] is None or body["node"]["id"] != node["id"] or body["complete"]


async def test_vague_voice_answer_earns_one_clarify(
    adaptive: tuple[AsyncClient, _ScriptedInterpreter], session: AsyncSession
) -> None:
    """First vague answer → one spoken clarify on the same node (doc 11 §2)."""
    from app.intake.interpret import Interpretation

    client, interp = adaptive
    started = await _start_med_onc(client, session)
    node = started["node"]
    sid = started["session_id"]
    interp.push(Interpretation(clarify="क्या आपको बुखार है?", confidence=0.2))

    resp = await client.post(
        f"/kiosk/{sid}/answer",
        json={"node_id": node["id"], "value": None, "raw_text": "pata nahi", "attempt": 0},
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["clarify"] == "क्या आपको बुखार है?"
    assert body["adaptive_exhausted"] is False
    # Still on the same question — the mic re-opens, the walk did not advance.
    assert body["node"]["id"] == node["id"]


async def test_second_vague_answer_falls_back_to_taps(
    adaptive: tuple[AsyncClient, _ScriptedInterpreter], session: AsyncSession
) -> None:
    """A second vague answer stops clarifying and hands the node's taps back —
    no infinite clarify loop (doc 11 §5)."""
    from app.intake.interpret import Interpretation

    client, interp = adaptive
    started = await _start_med_onc(client, session)
    node = started["node"]
    sid = started["session_id"]
    interp.push(Interpretation(clarify="फिर से बताइए?", confidence=0.1))

    resp = await client.post(
        f"/kiosk/{sid}/answer",
        json={"node_id": node["id"], "value": None, "raw_text": "hmm", "attempt": 1},
    )
    body = resp.json()
    assert body["ok"] is False
    assert body["clarify"] is None
    assert body["adaptive_exhausted"] is True
    assert body["node"]["id"] == node["id"]


async def test_rejected_candidate_does_not_advance(
    adaptive: tuple[AsyncClient, _ScriptedInterpreter], session: AsyncSession
) -> None:
    """A value the node rejects never advances the walk and never 500s — it falls
    back to taps (doc 11 §5: the interpreter cannot write an invalid value)."""
    from app.intake.interpret import Interpretation

    client, interp = adaptive
    started = await _start_med_onc(client, session)
    node = started["node"]
    sid = started["session_id"]
    interp.push(Interpretation(value="definitely-not-an-option", confidence=0.9))

    resp = await client.post(
        f"/kiosk/{sid}/answer",
        json={"node_id": node["id"], "value": None, "raw_text": "kuch bhi", "attempt": 0},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["node"]["id"] == node["id"]  # not advanced


async def test_voice_answer_is_ignored_when_adaptive_is_off(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The default engine has no interpreter — a value=null answer is not
    interpreted, so the kiosk stays on today's tap flow (doc 04 law 8, doc 11 §5)."""
    started = await _start_med_onc(client, session)
    node = started["node"]
    sid = started["session_id"]

    resp = await client.post(
        f"/kiosk/{sid}/answer",
        json={"node_id": node["id"], "value": None, "raw_text": "haan", "attempt": 0},
    )
    body = resp.json()
    # No interpretation happened: a null value is simply an invalid answer.
    assert body["ok"] is False
    assert body.get("clarify") is None
    assert body.get("adaptive_exhausted") is False
