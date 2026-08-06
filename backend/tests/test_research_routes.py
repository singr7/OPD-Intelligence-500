"""The `/research` HTTP surface (M5, plan §4).

Two routes, and most of this file is what they refuse: a coordinator, another
department's patient, a question that arrives with context text attached, a
doctor whose day's turns are spent, and a vendor that is down.

The GET is the one worth reading first. It exists so the panel can show the
doctor exactly what would be sent *before* anything is sent, which is the
control plan §4.1 asks for and the reason the POST can afford to take ids only.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import queue as q
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.clinical import ClinicalNote, Prescription, ResearchThread, ResearchTurn
from app.models.enums import Channel, DictationStatus, Role
from app.providers.llm import FakeLLMProvider, FakeLLMScript

TODAY = q.today()

pytestmark = pytest.mark.asyncio

ANSWER = (
    "Restrictive transfusion thresholds are standard in most guidance. "
    "Discuss against your local protocol."
)


def _headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def _clinic_with_visit(session: AsyncSession):
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    return clinic, visit


def _use_model(monkeypatch, text: str = ANSWER) -> FakeLLMProvider:
    fake = FakeLLMProvider(script=[FakeLLMScript(text=text)] * 20)
    monkeypatch.setattr("app.routes.research.llm_chain", lambda settings=None: [fake])
    return fake


def _use_broken_model(monkeypatch) -> FakeLLMProvider:
    from app.providers import ProviderUnavailable

    fake = FakeLLMProvider()
    fake.fail_with = ProviderUnavailable("gemini http 503")
    monkeypatch.setattr("app.routes.research.llm_chain", lambda settings=None: [fake])
    return fake


async def _sign_a_diagnosis(session: AsyncSession, visit, doctor, text: str) -> None:
    from datetime import UTC, datetime

    session.add(
        f.make_dictation(
            visit,
            doctor,
            structured={"diagnosis": text},
            status=DictationStatus.SIGNED,
            signed_at=datetime.now(UTC),
            signed_by=doctor.id,
        )
    )
    await session.flush()


# =============================================================================
# The flow
# =============================================================================


async def test_the_panel_shows_what_would_be_sent_before_anything_is_sent(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """The control plan §4.1 asks for: "the doctor can see and trim exactly what
    leaves the box"."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    await _sign_a_diagnosis(session, visit, clinic["doctor"], "Carcinoma breast, T2N1M0")

    resp = await client.get(f"/research/visits/{visit.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    ids = [item["id"] for item in body["context"]]
    assert ids == ["demographics", "diagnosis"]
    assert "50-59" in body["context"][0]["text"]
    assert clinic["patient"].name not in resp.text
    # Sources that produced nothing say why, rather than simply not appearing.
    assert ["Flagged lab values", "nothing out of range on file from a scanned report"] in body[
        "absent"
    ]
    # Nothing has been asked, so no thread, no turns, a full budget.
    assert body["turns"] == []
    assert body["include"] is None
    assert body["budget"] == {
        "used": 0,
        "limit": settings.research_daily_turns,
        "remaining": settings.research_daily_turns,
    }
    assert body["enabled"] is True


async def test_asking_stores_the_exchange_and_spends_one_turn(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)
    await _sign_a_diagnosis(session, visit, clinic["doctor"], "Carcinoma breast")

    resp = await client.post(
        f"/research/visits/{visit.id}",
        json={"question": "How is anaemia managed during AC-T?"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["turn"]["answer"] == ANSWER
    assert body["turn"]["question"] == "How is anaemia managed during AC-T?"
    assert body["budget"] == {
        "used": 1,
        "limit": settings.research_daily_turns,
        "remaining": settings.research_daily_turns - 1,
    }
    # The context that actually left the box is frozen onto the turn.
    assert any("Carcinoma breast" in line for line in body["turn"]["context_sent"])
    # And it is what the model was given.
    assert "Carcinoma breast" in fake.calls[-1].prompt
    assert fake.calls[-1].json_output is False

    resp = await client.get(f"/research/visits/{visit.id}", headers=headers)
    assert len(resp.json()["turns"]) == 1


async def test_a_second_question_carries_the_conversation(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """Multi-turn via `LLMRequest.history` — but the *context* is the current
    one, never the older turn's."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)

    await client.post(
        f"/research/visits/{visit.id}", json={"question": "first question"}, headers=headers
    )
    await client.post(
        f"/research/visits/{visit.id}",
        json={"question": "and in an older patient?"},
        headers=headers,
    )

    history = fake.calls[-1].history
    assert ("user", "first question") in history
    assert ("assistant", ANSWER) in history
    assert "and in an older patient?" in fake.calls[-1].prompt


async def test_the_doctor_can_trim_the_context_and_the_trim_is_remembered(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)
    await _sign_a_diagnosis(session, visit, clinic["doctor"], "Carcinoma breast")

    resp = await client.post(
        f"/research/visits/{visit.id}",
        json={"question": "general question", "include": ["demographics"]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert "Carcinoma breast" not in fake.calls[-1].prompt
    assert "50-59" in fake.calls[-1].prompt
    assert resp.json()["turn"]["context_sent"] == ["Patient: 50-59, female."]

    # Re-opening the tab does not silently restore what they turned off.
    resp = await client.get(f"/research/visits/{visit.id}", headers=headers)
    assert resp.json()["include"] == ["demographics"]


async def test_a_doctor_can_send_no_context_at_all(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """Unticking every line is a legitimate way to ask a general question, and
    it must not be read as "everything"."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)
    await _sign_a_diagnosis(session, visit, clinic["doctor"], "Carcinoma breast")

    resp = await client.post(
        f"/research/visits/{visit.id}",
        json={"question": "What is Lynch syndrome?", "include": []},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["turn"]["context_sent"] == []
    assert "Carcinoma breast" not in fake.calls[-1].prompt
    assert "50-59" not in fake.calls[-1].prompt
    assert "the doctor sent no patient context" in fake.calls[-1].prompt


# =============================================================================
# What the surface refuses
# =============================================================================


async def test_context_text_cannot_be_smuggled_through_include(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The acceptance criterion. `include` is ids; there is no field on any
    request model that carries context text, and an unknown id is refused rather
    than ignored."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)

    resp = await client.post(
        f"/research/visits/{visit.id}",
        json={
            "question": "q",
            "include": ["demographics", "the patient is Sunita Devi, phone 9876543210"],
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "not context this system builds" in resp.json()["detail"]
    assert fake.calls == [], "a rejected request must not reach a vendor"


async def test_extra_request_fields_are_not_a_way_in(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """A client inventing `context` on the request body gets it ignored by the
    schema — the words never reach the prompt."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)

    resp = await client.post(
        f"/research/visits/{visit.id}",
        json={
            "question": "q",
            "context": "Patient Sunita Devi, MRN 12345, lives in Ramgarh",
            "context_sent": ["Sunita Devi"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert "Sunita" not in fake.calls[-1].prompt
    assert "12345" not in fake.calls[-1].prompt
    assert resp.json()["turn"]["context_sent"] == ["Patient: 50-59, female."]


async def test_a_coordinator_cannot_open_the_panel(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """Decision 7: the assistant advises the doctor and only the doctor."""
    clinic, visit = await _clinic_with_visit(session)
    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()

    resp = await client.get(f"/research/visits/{visit.id}", headers=_headers(settings, coordinator))
    assert resp.status_code == 403


async def test_another_departments_patient_is_refused(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    other_department = f.make_department(clinic["hospital"])
    session.add(other_department)
    await session.flush()
    outsider_user = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add(outsider_user)
    await session.flush()
    session.add(f.make_doctor(outsider_user, other_department))
    await session.flush()

    resp = await client.get(
        f"/research/visits/{visit.id}", headers=_headers(settings, outsider_user)
    )
    assert resp.status_code == 403
    assert "another department" in resp.json()["detail"]


async def test_a_provider_outage_is_a_503_that_stores_nothing(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The panel says the assistant is unavailable and closes. Nothing queues —
    not even the thread."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    _use_broken_model(monkeypatch)

    resp = await client.post(
        f"/research/visits/{visit.id}", json={"question": "anything"}, headers=headers
    )
    assert resp.status_code == 503

    assert await session.scalar(select(func.count(ResearchTurn.id))) == 0
    assert await session.scalar(select(func.count(ResearchThread.id))) == 0

    resp = await client.get(f"/research/visits/{visit.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["turns"] == []
    assert resp.json()["budget"]["used"] == 0, "a failed question must not spend a turn"


async def test_the_days_budget_runs_out_and_says_so(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)
    monkeypatch.setattr(settings, "research_daily_turns", 2)

    for n in range(2):
        resp = await client.post(
            f"/research/visits/{visit.id}", json={"question": f"q{n}"}, headers=headers
        )
        assert resp.status_code == 200

    calls_before = len(fake.calls)
    resp = await client.post(
        f"/research/visits/{visit.id}", json={"question": "one more"}, headers=headers
    )
    assert resp.status_code == 429
    assert "2 research questions today" in resp.json()["detail"]
    assert len(fake.calls) == calls_before, "an exhausted budget must not bill a vendor"


async def test_an_empty_or_enormous_question_is_refused_before_a_vendor_is_called(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)

    resp = await client.post(
        f"/research/visits/{visit.id}", json={"question": "   "}, headers=headers
    )
    assert resp.status_code == 400

    resp = await client.post(
        f"/research/visits/{visit.id}",
        json={"question": "x" * (settings.research_max_question + 1)},
        headers=headers,
    )
    assert resp.status_code == 400
    assert fake.calls == []


async def test_the_switch_closes_the_surface_without_a_404(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """`RESEARCH_ENABLED=false` is an operator's decision the tab states, not a
    route that vanishes — a 404 would look like a broken build."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    _use_model(monkeypatch)
    monkeypatch.setattr(settings, "research_enabled", False)

    resp = await client.get(f"/research/visits/{visit.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = await client.post(
        f"/research/visits/{visit.id}", json={"question": "q"}, headers=headers
    )
    assert resp.status_code == 503


async def test_no_route_writes_a_clinical_record(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The whole conversation happens and the clinical tables stay empty.

    There is also no route to try: no PATCH, no DELETE, and nothing that marks a
    turn as accepted. This asserts the router's own shape as well as the effect.
    """
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    _use_model(monkeypatch, "Start T. Tamoxifen 20 mg OD and admit her today.")

    for question in ("what should I do?", "and after that?"):
        resp = await client.post(
            f"/research/visits/{visit.id}", json={"question": question}, headers=headers
        )
        assert resp.status_code == 200

    assert await session.scalar(select(func.count(Prescription.id))) == 0
    assert await session.scalar(select(func.count(ClinicalNote.id))) == 0

    from app.routes.research import router

    methods = {method for route in router.routes for method in getattr(route, "methods", set())}
    assert methods == {"GET", "POST"}, f"the research surface has grown a verb: {methods}"


async def test_an_unknown_visit_is_a_404(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    import uuid

    clinic = await f.build_clinic(session)
    resp = await client.get(
        f"/research/visits/{uuid.uuid4()}", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 404
