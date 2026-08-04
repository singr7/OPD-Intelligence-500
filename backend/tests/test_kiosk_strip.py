"""The coordinator's staff strip on the kiosk's last screen (AR2).

Two boundaries are load-bearing and get most of the attention here:

1. Nothing patient-identifying leaves the kiosk's *unauthenticated* routes. The
   arrival screen may recognise a returning patient; the terminal must not say so.
2. The PIN's narrow token opens the strip and nothing else, and an ordinary staff
   token does not open the strip.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app.auth import kiosk_pin as kp
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.clinical import Visit
from app.models.enums import PatientLinkState, Role
from app.models.org import Hospital

pytestmark = pytest.mark.asyncio


async def _hospital_with_departments(session: AsyncSession) -> Hospital:
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    for code, name in [("MEDONC", "Medical Oncology"), ("DERM", "Dermatology")]:
        session.add(f.make_department(hospital, code=code, name=name))
    await session.flush()
    return hospital


async def _coordinator_with_pin(session: AsyncSession, hospital: Hospital, pin: str = "4718"):
    user = f.make_user(hospital, role=Role.COORDINATOR, name="Sunita Rao")
    session.add(user)
    await session.flush()
    await kp.set_pin(session, user=user, pin=pin)
    return user


async def _start_intake(client: AsyncClient, **extra: Any) -> dict[str, Any]:
    resp = await client.post(
        "/kiosk/start",
        json={
            "lang": "hi",
            "chief_complaint": "seene mein dard",
            "dept_key": "MEDONC",
            "patient_name": "सीमा देवी",
            **extra,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# -- the unauthenticated half discloses nothing -------------------------------


async def test_a_recognised_patient_is_not_announced_to_the_terminal(
    client: AsyncClient, session: AsyncSession
):
    """The whole reason the match is deferred to a staffed step."""
    hospital = await _hospital_with_departments(session)
    prior = f.make_patient(hospital, name="Lakshmi Nair", phone="+919876543210")
    session.add(prior)
    await session.flush()

    body = await _start_intake(client, patient_phone="+919876543210")

    serialised = str(body)
    assert "Lakshmi" not in serialised
    assert prior.mrn not in serialised
    assert str(prior.id) not in serialised


async def test_the_match_is_recorded_on_the_visit_for_the_coordinator(
    client: AsyncClient, session: AsyncSession
):
    hospital = await _hospital_with_departments(session)
    prior = f.make_patient(hospital, name="Lakshmi Nair", phone="+919876543210")
    session.add(prior)
    await session.flush()

    await _start_intake(client, patient_phone="+919876543210")

    visit = await session.scalar(select(Visit).where(Visit.candidate_patient_id == prior.id))
    assert visit is not None
    assert visit.patient_link_state is PatientLinkState.CANDIDATE
    # Still the walk-in's own row until a human agrees.
    assert visit.patient_id != prior.id


async def test_an_intake_with_no_phone_or_uhc_id_still_completes(
    client: AsyncClient, session: AsyncSession
):
    """Both identifiers are optional in the strictest sense."""
    await _hospital_with_departments(session)
    body = await _start_intake(client)
    assert body["status"] == "routed"
    assert body["session_id"]


async def test_a_uhc_id_alone_finds_the_file(client: AsyncClient, session: AsyncSession):
    hospital = await _hospital_with_departments(session)
    prior = f.make_patient(hospital, name="Vikram Deshmukh", external_id="UHC-4242")
    session.add(prior)
    await session.flush()

    await _start_intake(client, patient_external_id="UHC-4242")

    visit = await session.scalar(select(Visit).where(Visit.candidate_patient_id == prior.id))
    assert visit is not None


# -- the lock -----------------------------------------------------------------


async def test_the_strip_is_shut_without_a_token(client: AsyncClient, session: AsyncSession):
    await _hospital_with_departments(session)
    body = await _start_intake(client)

    resp = await client.get(f"/kiosk/{body['session_id']}/strip")
    assert resp.status_code == 401


async def test_an_ordinary_staff_token_does_not_open_the_strip(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    """A PIN-shaped hole must not be reachable from a normal login, and the
    reverse is asserted in test_kiosk_pin — the two types never substitute."""
    hospital = await _hospital_with_departments(session)
    coordinator = await _coordinator_with_pin(session, hospital)
    body = await _start_intake(client)

    staff = create_access_token(
        user_id=coordinator.id,
        role=coordinator.role,
        name=coordinator.name,
        settings=settings,
        hospital_id=hospital.id,
    )
    resp = await client.get(
        f"/kiosk/{body['session_id']}/strip",
        headers={"Authorization": f"Bearer {staff.token}"},
    )
    assert resp.status_code == 401


async def test_holders_expose_a_name_and_nothing_contactable(
    client: AsyncClient, session: AsyncSession
):
    hospital = await _hospital_with_departments(session)
    coordinator = await _coordinator_with_pin(session, hospital)

    resp = await client.get("/kiosk/staff/holders")
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["name"] for r in rows] == ["Sunita Rao"]
    assert set(rows[0]) == {"id", "name"}
    assert coordinator.phone not in str(rows)


async def test_a_doctor_is_not_offered_as_a_kiosk_holder(
    client: AsyncClient, session: AsyncSession
):
    hospital = await _hospital_with_departments(session)
    await _coordinator_with_pin(session, hospital)
    doctor_user = f.make_user(hospital, role=Role.DOCTOR)
    session.add(doctor_user)
    await session.flush()

    rows = (await client.get("/kiosk/staff/holders")).json()
    assert all(r["id"] != str(doctor_user.id) for r in rows)


async def test_a_wrong_pin_and_an_unknown_user_look_identical(
    client: AsyncClient, session: AsyncSession
):
    """Otherwise the strip becomes a way to probe which staff ids exist."""
    import uuid

    hospital = await _hospital_with_departments(session)
    coordinator = await _coordinator_with_pin(session, hospital)

    wrong = await client.post(
        "/kiosk/staff/unlock", json={"user_id": str(coordinator.id), "pin": "9999"}
    )
    unknown = await client.post(
        "/kiosk/staff/unlock", json={"user_id": str(uuid.uuid4()), "pin": "4718"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


# -- the unlocked strip -------------------------------------------------------


async def _unlock(client: AsyncClient, user_id, pin: str = "4718") -> dict[str, str]:
    resp = await client.post("/kiosk/staff/unlock", json={"user_id": str(user_id), "pin": pin})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


async def test_the_unlocked_strip_shows_the_candidate_and_the_roster(
    client: AsyncClient, session: AsyncSession
):
    hospital = await _hospital_with_departments(session)
    coordinator = await _coordinator_with_pin(session, hospital)
    prior = f.make_patient(hospital, name="Lakshmi Nair", phone="+919876543210")
    session.add(prior)
    await session.flush()
    body = await _start_intake(client, patient_phone="+919876543210")

    headers = await _unlock(client, coordinator.id)
    resp = await client.get(f"/kiosk/{body['session_id']}/strip", headers=headers)

    assert resp.status_code == 200, resp.text
    strip = resp.json()
    assert strip["link_state"] == "candidate"
    assert strip["candidate"]["name"] == "Lakshmi Nair"
    assert strip["candidate"]["mrn"] == prior.mrn
    assert strip["department_key"] == "MEDONC"
    assert {d["key"] for d in strip["departments"]} >= {"MEDONC", "DERM"}


async def test_confirming_the_link_and_assigning_is_one_action(
    client: AsyncClient, session: AsyncSession
):
    hospital = await _hospital_with_departments(session)
    coordinator = await _coordinator_with_pin(session, hospital)
    prior = f.make_patient(hospital, name="Lakshmi Nair", phone="+919876543210")
    session.add(prior)
    await session.flush()

    dept = await session.scalar(select(f.Department).where(f.Department.code == "MEDONC"))
    user = f.make_user(hospital, role=Role.DOCTOR, name="Dr Ananya Rao")
    session.add(user)
    await session.flush()
    doctor = f.make_doctor(user, dept, name="Dr Ananya Rao")
    session.add(doctor)
    await session.flush()

    body = await _start_intake(client, patient_phone="+919876543210")
    headers = await _unlock(client, coordinator.id)

    resp = await client.post(
        f"/kiosk/{body['session_id']}/assign",
        json={"link_candidate": True, "doctor_id": str(doctor.id)},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["link_state"] == "confirmed"
    assert out["patient_name"] == "Lakshmi Nair"
    assert out["assigned_doctor_name"] == "Dr Ananya Rao"
    assert out["token_reissued"] is False


async def test_rejecting_the_link_keeps_the_walk_in(client: AsyncClient, session: AsyncSession):
    hospital = await _hospital_with_departments(session)
    coordinator = await _coordinator_with_pin(session, hospital)
    prior = f.make_patient(hospital, name="Lakshmi Nair", phone="+919876543210")
    session.add(prior)
    await session.flush()
    body = await _start_intake(client, patient_phone="+919876543210")
    headers = await _unlock(client, coordinator.id)

    resp = await client.post(
        f"/kiosk/{body['session_id']}/assign",
        json={"link_candidate": False},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["link_state"] == "rejected"
    assert out["patient_name"] != "Lakshmi Nair"


async def test_skipping_leaves_the_visit_in_the_department_pool(
    client: AsyncClient, session: AsyncSession
):
    """`Skip` is a legal outcome — and the one an offline kiosk takes."""
    hospital = await _hospital_with_departments(session)
    coordinator = await _coordinator_with_pin(session, hospital)
    body = await _start_intake(client)
    headers = await _unlock(client, coordinator.id)

    resp = await client.post(f"/kiosk/{body['session_id']}/assign", json={}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_doctor_id"] is None
