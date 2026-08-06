"""The `/notes` HTTP surface (M4, plan §3).

The console drives these verbs in order, so most of this file is that order over
HTTP. The rest is what the surface refuses: a coordinator, another department's
patient, an edit after confirmation, and — the one that matters — any route at
all that produces a prescription.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import queue as q
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.clinical import Prescription
from app.models.enums import Channel, Role
from app.providers.llm import FakeLLMProvider, FakeLLMScript

TODAY = q.today()

MAPPED: dict[str, Any] = {
    "subjective": "Feels better than after the last cycle. Mouth sore for three days.",
    "objective": "Grade 1 oral mucositis. No pallor.",
    "assessment": "Tolerating AC-T through cycle 3.",
    "plan_narrative": "Salt-water rinses. Repeat CBC before the next cycle.",
    "tags": {
        "problems": ["carcinoma breast"],
        "symptoms": [{"name": "mucositis", "grade_mentioned": "1"}],
        "followups": ["CBC before next cycle"],
    },
}

pytestmark = pytest.mark.asyncio


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


def _use_model(monkeypatch, payload: dict[str, Any] | None = None) -> FakeLLMProvider:
    fake = (
        FakeLLMProvider(script=[FakeLLMScript(text=json.dumps(payload))])
        if payload is not None
        else FakeLLMProvider()
    )
    monkeypatch.setattr("app.routes.notes.llm_chain", lambda settings=None: [fake])
    return fake


# =============================================================================
# The flow
# =============================================================================


async def test_the_full_flow_over_http(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """start → map → correct → confirm, as the console drives it."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    _use_model(monkeypatch, MAPPED)

    resp = await client.get(f"/notes/visits/{visit.id}", headers=headers)
    assert resp.status_code == 200 and resp.json() == []

    resp = await client.post(
        f"/notes/visits/{visit.id}",
        json={"transcript": "post-chemo cycle 3, tolerating well, grade 1 mucositis"},
        headers=headers,
    )
    assert resp.status_code == 200
    note_id = resp.json()["id"]
    assert resp.json()["status"] == "draft"
    assert resp.json()["fields"] is None

    resp = await client.post(f"/notes/{note_id}/map", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["fields"]["assessment"] == "Tolerating AC-T through cycle 3."
    assert body["fields"]["tags"]["symptoms"][0] == {"name": "mucositis", "grade_mentioned": "1"}
    assert body["mapped"] == body["fields"]
    assert body["prompt_ref"] == "note_map@v1"

    resp = await client.patch(
        f"/notes/{note_id}",
        json={"assessment": "Tolerating AC-T; mucositis settling."},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["fields"]["assessment"] == "Tolerating AC-T; mucositis settling."
    # The model's version is untouched, so the review can show the difference.
    assert resp.json()["mapped"]["assessment"] == "Tolerating AC-T through cycle 3."
    assert [e["field"] for e in resp.json()["edits"]] == ["assessment"]

    resp = await client.post(f"/notes/{note_id}/confirm", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
    assert resp.json()["confirmed_at"]

    # And it is locked.
    resp = await client.patch(
        f"/notes/{note_id}", json={"assessment": "second thoughts"}, headers=headers
    )
    assert resp.status_code == 409


async def test_confirming_over_http_produces_no_prescription(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The AC, at the surface a client can actually reach.

    `app.notes` cannot import the prescription path; this checks that no route
    on top of it found another way there.
    """
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    _use_model(
        monkeypatch,
        {**MAPPED, "plan_narrative": "Continue T. Tamoxifen 20 OD until review."},
    )

    resp = await client.post(
        f"/notes/visits/{visit.id}", json={"transcript": "continue tamoxifen"}, headers=headers
    )
    note_id = resp.json()["id"]
    await client.post(f"/notes/{note_id}/map", headers=headers)
    resp = await client.post(f"/notes/{note_id}/confirm", headers=headers)
    assert resp.status_code == 200

    assert (await session.scalars(select(Prescription))).all() == []
    # And there is no verb on this router that would have made one.
    from app.routes import notes as notes_routes

    paths = {route.path for route in notes_routes.router.routes}
    assert not any("sign" in p or "prescription" in p or "print" in p for p in paths)


async def test_the_note_payload_can_never_carry_a_medication_list(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """A model that volunteers `meds` gets it dropped before the wire, and the
    PATCH that tries to add one is refused."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    _use_model(
        monkeypatch,
        {**MAPPED, "meds": [{"name": "Inj Monocef 1 gm", "dose": "1 g", "freq": "BD"}]},
    )

    resp = await client.post(
        f"/notes/visits/{visit.id}", json={"transcript": "start monocef"}, headers=headers
    )
    note_id = resp.json()["id"]
    resp = await client.post(f"/notes/{note_id}/map", headers=headers)
    assert resp.status_code == 200
    assert "Monocef" not in json.dumps(resp.json())

    # And a client cannot put one there either. `PatchIn` has no `meds` field, so
    # `exclude_unset` never sees it — the note is unchanged rather than errored,
    # which is the FastAPI default and is fine: nothing was written.
    resp = await client.patch(
        f"/notes/{note_id}", json={"meds": [{"name": "Tab Dolo 650"}]}, headers=headers
    )
    assert resp.status_code == 200
    assert "Dolo" not in json.dumps(resp.json())


async def test_two_captures_in_one_consult_come_back_in_order(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    _use_model(monkeypatch, MAPPED)

    await client.post(
        f"/notes/visits/{visit.id}", json={"transcript": "mucositis grade 1"}, headers=headers
    )
    await client.post(
        f"/notes/visits/{visit.id}", json={"transcript": "counts recovered"}, headers=headers
    )

    resp = await client.get(f"/notes/visits/{visit.id}", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["transcript"] for r in rows] == ["mucositis grade 1", "counts recovered"]


async def test_a_typed_note_needs_no_model_at_all(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """No transcript, no `/map`, and it still confirms."""
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])

    resp = await client.post(f"/notes/visits/{visit.id}", json={}, headers=headers)
    note_id = resp.json()["id"]

    resp = await client.post(f"/notes/{note_id}/compose", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["fields"]["subjective"] == ""
    assert resp.json()["mapped"] is None

    resp = await client.patch(
        f"/notes/{note_id}", json={"objective": "Grade 1 mucositis."}, headers=headers
    )
    assert resp.status_code == 200

    resp = await client.post(f"/notes/{note_id}/confirm", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


async def test_an_empty_note_is_refused_at_confirm(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])

    resp = await client.post(f"/notes/visits/{visit.id}", json={}, headers=headers)
    note_id = resp.json()["id"]
    await client.post(f"/notes/{note_id}/compose", headers=headers)

    resp = await client.post(f"/notes/{note_id}/confirm", headers=headers)
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]


async def test_a_model_outage_is_a_503_that_keeps_the_words(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """503, fields open, transcript intact — the state the console renders as
    "the model is down, type what you meant"."""
    from app.providers import ProviderUnavailable

    clinic, visit = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])
    fake = _use_model(monkeypatch)
    fake.fail_with = ProviderUnavailable("gemini http 503")

    spoken = "post-chemo cycle 3, tolerating well"
    resp = await client.post(
        f"/notes/visits/{visit.id}", json={"transcript": spoken}, headers=headers
    )
    note_id = resp.json()["id"]

    resp = await client.post(f"/notes/{note_id}/map", headers=headers)
    assert resp.status_code == 503

    resp = await client.get(f"/notes/visits/{visit.id}", headers=headers)
    note = resp.json()[0]
    assert note["transcript"] == spoken
    assert "503" in note["mapping_error"]
    assert note["mapped"] is None
    assert note["fields"]["subjective"] == ""  # open, and empty


# =============================================================================
# What the surface refuses
# =============================================================================


async def test_routes_require_a_doctor(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """A note is the doctor's working memory. A coordinator is not `require_doctor`."""
    clinic, visit = await _clinic_with_visit(session)
    assert (await client.get(f"/notes/visits/{visit.id}")).status_code == 401

    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()
    resp = await client.get(f"/notes/visits/{visit.id}", headers=_headers(settings, coordinator))
    assert resp.status_code == 403


async def test_another_departments_patient_is_refused(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    other = f.make_department(clinic["hospital"])
    session.add(other)
    await session.flush()
    visit.department_id = other.id
    await session.flush()

    resp = await client.post(
        f"/notes/visits/{visit.id}",
        json={"transcript": "…"},
        headers=_headers(settings, clinic["user"]),
    )
    assert resp.status_code == 400
    assert "another department" in resp.json()["detail"]


async def test_a_note_id_that_is_not_ours_is_not_readable(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """Scoped by the visit's department, so a doctor elsewhere gets a 403 rather
    than the note."""
    clinic, visit = await _clinic_with_visit(session)
    _use_model(monkeypatch, MAPPED)
    resp = await client.post(
        f"/notes/visits/{visit.id}",
        json={"transcript": "…"},
        headers=_headers(settings, clinic["user"]),
    )
    note_id = resp.json()["id"]

    stranger_dept = f.make_department(clinic["hospital"])
    session.add(stranger_dept)
    await session.flush()
    stranger_user = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add(stranger_user)
    await session.flush()
    session.add(f.make_doctor(stranger_user, stranger_dept))
    await session.flush()

    resp = await client.post(f"/notes/{note_id}/confirm", headers=_headers(settings, stranger_user))
    assert resp.status_code == 403


async def test_a_missing_note_is_a_404(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, _ = await _clinic_with_visit(session)
    resp = await client.post(
        f"/notes/{f.new_uuid()}/confirm", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 404


async def test_the_note_stt_route_is_metered_as_a_note_not_a_dictation(
    client: AsyncClient, session: AsyncSession, settings: Settings, meter, seeded_prices
) -> None:
    """The reason `/notes/stt` exists at all rather than reusing `/dictation/stt`.

    `analytics._per_dictation` divides DICTATION spend by the count of signed
    dictations; an observation metered there inflates cost-per-prescription by
    something that produced no prescription. The two routes share every line of
    their implementation (`app.routes._stt`) and differ only here.
    """
    from app.models.enums import UsagePurpose
    from app.models.metering import UsageEvent

    clinic, _ = await _clinic_with_visit(session)
    headers = _headers(settings, clinic["user"])

    resp = await client.post(
        "/notes/stt",
        files={"file": ("note.webm", b"not really audio, the fake does not care", "audio/webm")},
        data={"lang": "en", "duration_seconds": "12"},
        headers=headers,
    )
    assert resp.status_code == 200
    await meter.flush()

    purposes = {e.purpose for e in (await session.scalars(select(UsageEvent))).all()}
    assert UsagePurpose.NOTE in purposes
    assert UsagePurpose.DICTATION not in purposes


async def test_an_empty_upload_is_refused(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    clinic, _ = await _clinic_with_visit(session)
    resp = await client.post(
        "/notes/stt",
        files={"file": ("note.webm", b"", "audio/webm")},
        headers=_headers(settings, clinic["user"]),
    )
    assert resp.status_code == 422
