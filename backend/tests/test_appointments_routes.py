"""Appointment HTTP surface + the Exotel status callback (doc 03 §2).

The service rules are proven in `test_scheduling.py`; this file is about the
things only the route layer can get wrong — who is allowed in, what a lost race
looks like on the wire (409, not 500), and whether the vendor's callback can be
posted by anyone who finds the URL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import campaign as campaign_svc
from app import scheduling
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.enums import AppointmentStatus, Channel, OutboundCallState, Role

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


async def _clinic_with_slots(session: AsyncSession, count: int = 2):
    clinic = await f.build_clinic(session)
    slots = [
        f.make_slot(
            clinic["doctor"],
            (datetime.now(UTC) + timedelta(days=d + 1)).replace(
                hour=6, minute=0, second=0, microsecond=0
            ),
        )
        for d in range(count)
    ]
    session.add_all(slots)
    user = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(user)
    await session.flush()
    await session.commit()
    return clinic, slots, user


async def test_slots_booking_reschedule_and_cancel_over_http(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    clinic, slots, user = await _clinic_with_slots(session)
    headers = _headers(settings, user)

    listed = await client.get(
        "/appointments/slots", params={"doctor_id": str(clinic["doctor"].id)}, headers=headers
    )
    assert listed.status_code == 200
    assert [s["slot_id"] for s in listed.json()] == [str(slots[0].id), str(slots[1].id)]

    booked = await client.post(
        "/appointments",
        json={"patient_id": str(clinic["patient"].id), "slot_id": str(slots[0].id)},
        headers=headers,
    )
    assert booked.status_code == 201
    body = booked.json()
    assert body["seat_no"] == 1
    assert body["status"] == AppointmentStatus.BOOKED.value

    moved = await client.post(
        f"/appointments/{body['id']}/reschedule",
        json={"slot_id": str(slots[1].id)},
        headers=headers,
    )
    assert moved.status_code == 200
    assert moved.json()["slot_id"] == str(slots[1].id)

    cancelled = await client.post(
        f"/appointments/{body['id']}/cancel", json={"reason": "patient called"}, headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == AppointmentStatus.CANCELLED.value
    assert cancelled.json()["seat_no"] is None

    remaining = await client.get(
        "/appointments", params={"patient_id": str(clinic["patient"].id)}, headers=headers
    )
    assert remaining.json() == []


async def test_booking_a_full_slot_is_a_409_not_a_500(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    clinic, slots, user = await _clinic_with_slots(session)
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.commit()
    headers = _headers(settings, user)

    first = await client.post(
        "/appointments",
        json={"patient_id": str(clinic["patient"].id), "slot_id": str(slots[0].id)},
        headers=headers,
    )
    assert first.status_code == 201

    second = await client.post(
        "/appointments",
        json={"patient_id": str(other.id), "slot_id": str(slots[0].id)},
        headers=headers,
    )
    assert second.status_code == 409


async def test_the_appointment_routes_need_staff_auth(client: AsyncClient):
    assert (await client.get("/appointments/slots")).status_code in (401, 403)
    assert (
        await client.post("/appointments", json={"patient_id": "x", "slot_id": "y"})
    ).status_code in (401, 403, 422)


async def test_the_campaign_plan_endpoint_is_read_only(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    clinic, slots, user = await _clinic_with_slots(session)
    headers = _headers(settings, user)
    booked = await client.post(
        "/appointments",
        json={"patient_id": str(clinic["patient"].id), "slot_id": str(slots[0].id)},
        headers=headers,
    )
    for_date = slots[0].starts_at.astimezone(scheduling.hospital_tz()).date()

    plan = await client.get(
        "/appointments/campaign/plan", params={"for_date": str(for_date)}, headers=headers
    )

    assert plan.status_code == 200
    assert [t["appointment_id"] for t in plan.json()["targets"]] == [booked.json()["id"]]
    assert await campaign_svc.due_calls(session) == []  # a plan dials nobody


# -- the Exotel status callback ------------------------------------------------


async def test_the_status_callback_settles_a_campaign_call(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    clinic, slots, user = await _clinic_with_slots(session)
    await scheduling.book(
        session, patient=clinic["patient"], slot_id=slots[0].id, source=Channel.KIOSK
    )
    for_date = slots[0].starts_at.astimezone(scheduling.hospital_tz()).date()
    await campaign_svc.launch_campaign(session, for_date=for_date, dry_run=False)
    [call] = await campaign_svc.due_calls(session)
    call.last_call_sid = "exotel-sid-1"
    call.state = OutboundCallState.DIALING
    call.attempts = 1
    await session.commit()

    posted = await client.post(
        "/appointments/telephony/status",
        data={
            "CallSid": "exotel-sid-1",
            "Status": "completed",
            "Duration": "184",
            "CustomField": str(call.id),
        },
    )

    assert posted.status_code == 200
    assert posted.json() == {"ok": True, "matched": True, "state": "completed"}
    await session.refresh(call)
    assert call.state is OutboundCallState.COMPLETED


async def test_an_unknown_callback_is_accepted_and_ignored(client: AsyncClient):
    """A 500 here means Exotel retries for hours over a call we never placed."""
    posted = await client.post(
        "/appointments/telephony/status",
        data={"CallSid": "not-ours", "Status": "no-answer", "CustomField": ""},
    )
    assert posted.status_code == 200
    assert posted.json()["matched"] is False


async def test_a_wrong_webhook_token_gets_nothing(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    settings.exotel_webhook_token = "s3cret"
    try:
        posted = await client.post(
            "/appointments/telephony/status",
            params={"token": "guess"},
            data={"CallSid": "x", "Status": "completed"},
        )
        assert posted.status_code == 404

        allowed = await client.post(
            "/appointments/telephony/status",
            params={"token": "s3cret"},
            data={"CallSid": "x", "Status": "completed"},
        )
        assert allowed.status_code == 200
    finally:
        settings.exotel_webhook_token = ""
