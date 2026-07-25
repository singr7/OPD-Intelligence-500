"""The patient app's backend (doc 03 §1c, S16).

The service rules for what the app *shows* are proven elsewhere — scheduling in
`test_scheduling.py`, the queue's order in `test_queue.py`, the prescription's
frozen schedule in `test_prescription.py`. What can only go wrong here is
**scope**: whose file a token opens, what a caregiver may do with it, and what
stops one patient's phone from reading another's cancer record. Most of this file
is therefore about the boundary, not the payloads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import patient_app
from app.auth.tokens import create_access_token, create_patient_access_token
from app.config import Settings
from app.models.clinical import DoseEvent, Prescription, Visit
from app.models.enums import (
    CaregiverLinkStatus,
    Channel,
    Lang,
    Priority,
    Role,
    SlotType,
)
from app.models.patient import CaregiverLink

pytestmark = pytest.mark.asyncio


# -- helpers -------------------------------------------------------------------


def _headers(settings: Settings, patient, *, via: str = "self", phone: str | None = None):
    token = create_patient_access_token(
        patient_id=patient.id,
        name=patient.name,
        hospital_id=patient.hospital_id,
        via=via,  # type: ignore[arg-type]
        actor_phone=phone or patient.phone,
        settings=settings,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def _clinic_with_tree(session: AsyncSession):
    """A clinic whose department code the tree bank actually routes to.

    The intake trees are keyed by department code (S4), so a generated code has
    no tree and `/intake/start` correctly falls back to the chooser. Medical
    oncology is the pilot's own first tree.
    """
    clinic = await f.build_clinic(session)
    clinic["department"].code = "MEDONC"
    clinic["department"].name = "Medical Oncology"
    await session.flush()
    return clinic


async def _walk_to_completion(client: AsyncClient, session_id: str, node, headers) -> None:
    """Answer every question with a valid value until the tree completes.

    The app walks the kiosk's four-tool contract through its own authenticated
    routes — same walker, same node shape, a login in front of it.
    """
    seen = 0
    while node is not None:
        seen += 1
        assert seen < 100, "walk did not terminate"
        value = None
        raw_text = None
        ntype = node["type"]
        if ntype == "single":
            value = node["options"][0]["id"]
        elif ntype in ("multi", "body_map"):
            value = [node["options"][0]["id"]]
        elif ntype in ("scale", "number"):
            value = node["min"] if node["min"] is not None else 1
        elif ntype == "free_voice":
            raw_text = "mujhe pet mein dard hai"
            value = raw_text
        resp = await client.post(
            f"/patient/intake/{session_id}/answer",
            json={"node_id": node["id"], "value": value, "raw_text": raw_text},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"], body
        if body["complete"]:
            return
        node = body["node"]


async def _link(session: AsyncSession, patient, phone: str, **overrides) -> CaregiverLink:
    link = CaregiverLink(
        patient_id=patient.id,
        phone=phone,
        name="Daughter",
        relation="daughter",
        status=overrides.pop("status", CaregiverLinkStatus.ACTIVE),
        consented_at=overrides.pop("consented_at", datetime.now(UTC)),
        **overrides,
    )
    session.add(link)
    await session.flush()
    return link


async def _signed_prescription(session: AsyncSession, clinic, *, meds=None) -> Prescription:
    """A prescription with the frozen shape S11 writes at signing."""
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()
    prescription = Prescription(
        visit_id=visit.id,
        meds=meds
        or [
            {
                "name": "Tab Ondansetron",
                "dose": "4mg",
                "freq": "1-0-1",
                "known": True,
                "schedule": {
                    "morning": True,
                    "afternoon": False,
                    "night": True,
                    "per_day": 2,
                    "slots_known": True,
                    "source": "1-0-1",
                },
            },
            {
                "name": "Tab Paracetamol",
                "dose": "500mg",
                "freq": "SOS",
                "known": True,
                "schedule": None,
            },
        ],
    )
    session.add(prescription)
    await session.flush()
    return prescription


# -- who may open which file ---------------------------------------------------


async def test_phone_resolves_to_her_own_file_and_the_ones_she_cares_for(
    session: AsyncSession,
):
    clinic = await f.build_clinic(session)
    mother = clinic["patient"]
    daughter = f.make_patient(clinic["hospital"], phone="+915551230001")
    session.add(daughter)
    await session.flush()
    await _link(session, mother, daughter.phone)

    profiles = await patient_app.profiles_for_phone(session, daughter.phone)

    assert [(p.patient.id, p.via) for p in profiles] == [
        (daughter.id, "self"),
        (mother.id, "caregiver"),
    ]


async def test_a_registration_desk_contact_number_is_not_a_login(session: AsyncSession):
    """`Patient.caregiver_phone` is who to call, not who may read.

    The distinction is the whole reason `caregiver_links` exists: a number
    written on a form has consented to nothing.
    """
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    patient.caregiver_phone = "+915559998887"
    await session.flush()

    assert await patient_app.profiles_for_phone(session, "+915559998887") == []


async def test_revoking_ends_the_caregivers_session_on_her_next_request(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    link = await _link(session, patient, "+915551230002")
    await session.commit()

    headers = _headers(settings, patient, via="caregiver", phone=link.phone)
    assert (await client.get("/patient/me", headers=headers)).status_code == 200

    link.status = CaregiverLinkStatus.REVOKED
    link.revoked_at = datetime.now(UTC)
    await session.commit()

    # Same token, one second later: consent is re-read per request, not per token.
    assert (await client.get("/patient/me", headers=headers)).status_code == 401


async def test_a_staff_token_cannot_open_a_patient_file_and_vice_versa(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    staff = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(staff)
    await session.flush()
    await session.commit()

    staff_token = create_access_token(
        user_id=staff.id,
        role=staff.role,
        name=staff.name,
        settings=settings,
        hospital_id=staff.hospital_id,
    ).token
    patient_headers = _headers(settings, clinic["patient"])

    # A staff token on the patient surface.
    assert (
        await client.get("/patient/file", headers={"Authorization": f"Bearer {staff_token}"})
    ).status_code == 401
    # A patient token on a staff surface.
    assert (await client.get("/queue/console", headers=patient_headers)).status_code == 401


async def test_login_never_reveals_whether_a_number_is_registered(
    client: AsyncClient, session: AsyncSession, sms
):
    stranger = await client.post("/auth/patient/otp/request", json={"phone": "+915550000000"})
    assert stranger.status_code == 200
    assert stranger.json()["sent"] is True
    # Same answer, but nothing was sent and no code exists.
    assert stranger.json()["debug_code"] is None
    assert sms.sent == []


async def test_otp_login_issues_a_session_for_the_right_file(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    await session.commit()

    requested = await client.post("/auth/patient/otp/request", json={"phone": patient.phone})
    code = requested.json()["debug_code"]
    assert code is not None

    verified = await client.post(
        "/auth/patient/otp/verify", json={"phone": patient.phone, "code": code}
    )
    assert verified.status_code == 200
    body = verified.json()
    assert body["patient_id"] == str(patient.id)
    assert body["via"] == "self"

    me = await client.get(
        "/patient/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.json()["name"] == patient.name


async def test_switching_to_a_file_this_phone_may_not_open_is_refused(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    stranger = f.make_patient(clinic["hospital"], phone="+915557770001")
    session.add(stranger)
    await session.flush()
    await session.commit()

    refused = await client.post(
        "/auth/patient/switch",
        json={"patient_id": str(stranger.id)},
        headers=_headers(settings, clinic["patient"]),
    )
    assert refused.status_code == 403


async def test_refresh_re_resolves_a_revoked_caregiver(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    """A rotation is not a renewal of yesterday's permission."""
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    link = await _link(session, patient, "+915551230003")
    await session.commit()

    requested = await client.post("/auth/patient/otp/request", json={"phone": link.phone})
    verified = await client.post(
        "/auth/patient/otp/verify",
        json={"phone": link.phone, "code": requested.json()["debug_code"]},
    )
    refresh_token = verified.json()["refresh_token"]

    link.status = CaregiverLinkStatus.REVOKED
    await session.commit()

    rotated = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert rotated.status_code == 401


# -- My Cancer Care File -------------------------------------------------------


async def test_the_file_holds_her_prescriptions_and_summaries_and_nobody_elses(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    await _signed_prescription(session, clinic)

    visit = f.make_visit(patient, clinic["department"])
    session.add(visit)
    await session.flush()
    intake = f.make_intake(
        visit,
        summary_md="Summary in English",
        summary_lang_versions={"hi": "हिंदी में सारांश"},
        completed_at=datetime.now(UTC),
    )
    session.add(intake)

    other = f.make_patient(clinic["hospital"], phone="+915556660001")
    session.add(other)
    await session.flush()
    other_visit = f.make_visit(other, clinic["department"])
    session.add(other_visit)
    await session.flush()
    session.add(
        Prescription(visit_id=other_visit.id, meds=[{"name": "Tab Someone Else", "known": True}])
    )
    await session.commit()

    body = (await client.get("/patient/file", headers=_headers(settings, patient))).json()
    kinds = {entry["kind"] for entry in body["entries"]}
    names = {med["name"] for entry in body["entries"] for med in entry["meds"]}

    assert kinds == {"prescription", "summary"}
    assert "Tab Ondansetron" in names
    assert "Tab Someone Else" not in names
    # Her own language, not the English original (doc 04 law 1).
    summary = next(e for e in body["entries"] if e["kind"] == "summary")
    assert summary["summary_md"] == "हिंदी में सारांश"


async def test_an_unchanged_file_costs_a_304(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    await _signed_prescription(session, clinic)
    await session.commit()

    headers = _headers(settings, clinic["patient"])
    first = await client.get("/patient/file", headers=headers)
    etag = first.headers["etag"]

    again = await client.get("/patient/file", headers={**headers, "If-None-Match": etag})
    assert again.status_code == 304


# -- queue position ------------------------------------------------------------


async def test_queue_position_counts_the_people_actually_ahead(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    """An urgent token that jumped the line is counted where it now stands."""
    from app import queue as queue_svc

    clinic = await f.build_clinic(session)
    patient = clinic["patient"]

    mine = f.make_visit(patient, clinic["department"], token_no=11, date=queue_svc.today())
    ahead = f.make_patient(clinic["hospital"], phone="+915554440001")
    session.add_all([mine, ahead])
    await session.flush()
    theirs = f.make_visit(ahead, clinic["department"], token_no=12, date=queue_svc.today())
    session.add(theirs)
    await session.flush()

    await queue_svc.enqueue(session, visit=mine)
    await queue_svc.enqueue(
        session, visit=theirs, priority=Priority.URGENT, priority_reason="bleeding"
    )
    await session.commit()

    body = (
        await client.get("/patient/queue?travel_minutes=30", headers=_headers(settings, patient))
    ).json()

    assert body["in_queue"] is True
    assert body["token_no"] == 11
    # Token 12 is urgent, so it is in front of token 11 despite the number.
    assert body["ahead"] == 1
    assert body["est_wait_high"] >= body["est_wait_low"]
    assert body["leave_by"] is not None


async def test_a_patient_not_in_a_queue_is_told_so_plainly(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    await session.commit()
    body = (
        await client.get("/patient/queue", headers=_headers(settings, clinic["patient"]))
    ).json()
    assert body == {
        "in_queue": False,
        "visit_id": None,
        "token_no": None,
        "department": None,
        "state": None,
        "ahead": None,
        "est_wait_low": None,
        "est_wait_high": None,
        "leave_by": None,
        "now_serving": None,
    }


# -- home intake + arrival -----------------------------------------------------


async def test_home_intake_confirms_without_a_token_and_arrival_issues_one(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    """The session's headline AC: the whole flow from her sofa to her token.

    Confirming at home must *not* put her on the board — she is 200km away.
    """
    clinic = await _clinic_with_tree(session)
    patient = clinic["patient"]
    await session.commit()
    headers = _headers(settings, patient)

    started = await client.post(
        "/patient/intake/start",
        json={
            "lang": "hi",
            "chief_complaint": "पेट में दर्द",
            "dept_key": "MEDONC",
        },
        headers=headers,
    )
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    assert session_id

    await _walk_to_completion(client, session_id, started.json()["node"], headers)

    finished = await client.post(f"/patient/intake/{session_id}/finish", headers=headers)
    assert finished.status_code == 200

    confirmed = await client.post(f"/patient/intake/{session_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["token_no"] is None

    visit = await session.get(Visit, uuid.UUID(confirmed.json()["visit_id"]))
    await session.refresh(visit)
    assert visit.channel is Channel.APP
    assert visit.token_no is None
    assert visit.patient_id == patient.id

    arrived = await client.post("/patient/arrive", json={}, headers=headers)
    assert arrived.status_code == 200, arrived.text
    assert arrived.json()["token_no"] > 0
    assert arrived.json()["position"]["in_queue"] is True

    # Twice is once: one patient, one token.
    again = await client.post("/patient/arrive", json={}, headers=headers)
    assert again.json()["token_no"] == arrived.json()["token_no"]
    assert again.json()["already_queued"] is True


async def test_arriving_before_finishing_the_questions_is_refused(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    visit = f.make_visit(
        patient, clinic["department"], token_no=None, date=datetime.now(UTC).date()
    )
    session.add(visit)
    await session.flush()
    session.add(f.make_intake(visit, completed_at=None))
    await session.commit()

    refused = await client.post("/patient/arrive", json={}, headers=_headers(settings, patient))
    assert refused.status_code == 409


async def test_a_caregiver_may_not_answer_the_patients_intake(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    link = await _link(session, patient, "+915551230004")
    await session.commit()

    refused = await client.post(
        "/patient/intake/start",
        json={"lang": "hi", "chief_complaint": "पेट में दर्द"},
        headers=_headers(settings, patient, via="caregiver", phone=link.phone),
    )
    assert refused.status_code == 403


async def test_one_patients_session_id_cannot_confirm_anothers_intake(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await _clinic_with_tree(session)
    intruder = f.make_patient(clinic["hospital"], phone="+915552220001")
    session.add(intruder)
    await session.flush()
    await session.commit()

    started = await client.post(
        "/patient/intake/start",
        json={"lang": "hi", "chief_complaint": "पेट में दर्द", "dept_key": "MEDONC"},
        headers=_headers(settings, clinic["patient"]),
    )
    session_id = started.json()["session_id"]

    refused = await client.post(
        f"/patient/intake/{session_id}/confirm", headers=_headers(settings, intruder)
    )
    assert refused.status_code == 403


# -- medicines -----------------------------------------------------------------


async def test_the_reminder_plan_never_invents_a_dose_time(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    await _signed_prescription(session, clinic)
    await session.commit()

    body = (
        await client.get("/patient/reminders", headers=_headers(settings, clinic["patient"]))
    ).json()

    times = sorted(d["at"] for d in body["doses"])
    assert times == ["08:00", "20:00"]
    # "SOS" is unreadable as a schedule, so it rings for nothing and says why.
    assert body["unscheduled"] == ["Tab Paracetamol"]


async def test_a_bare_count_produces_doses_with_no_time(
    session: AsyncSession,
):
    """ "BD" says how many, not when — the app asks the patient to place them."""
    clinic = await f.build_clinic(session)
    await _signed_prescription(
        session,
        clinic,
        meds=[
            {
                "name": "Tab Amoxicillin",
                "freq": "BD",
                "known": True,
                "schedule": {
                    "morning": False,
                    "afternoon": False,
                    "night": False,
                    "per_day": 2,
                    "slots_known": False,
                    "source": "BD",
                },
            }
        ],
    )
    plan = await patient_app.reminder_plan(session, patient_id=clinic["patient"].id)

    assert len(plan.doses) == 2
    assert {d.at for d in plan.doses} == {None}
    assert {d.slot for d in plan.doses} == {"unscheduled"}


async def test_a_missed_dose_pings_the_caregiver_exactly_once(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    prescription = await _signed_prescription(session, clinic)
    await _link(session, patient, "+915551230005")
    await session.commit()

    payload = {
        "prescription_id": str(prescription.id),
        "med_index": 0,
        "scheduled_for": datetime.now(UTC).isoformat(),
        "status": "missed",
    }
    headers = _headers(settings, patient)

    first = await client.post("/patient/reminders/events", json=payload, headers=headers)
    assert first.json() == {"recorded": True, "caregiver_notified": True}
    assert len(sms.sent) == 1
    assert sms.sent[0].to == "+915551230005"
    # The ping carries no drug name — an SMS is read by whoever picks the phone up.
    assert "Ondansetron" not in sms.sent[0].body

    # A flaky connection re-reports the same dose; the daughter is not pinged twice.
    again = await client.post("/patient/reminders/events", json=payload, headers=headers)
    assert again.json()["caregiver_notified"] is False
    assert len(sms.sent) == 1

    rows = (
        (
            await session.execute(
                select(DoseEvent).where(DoseEvent.prescription_id == prescription.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_a_dose_on_somebody_elses_prescription_is_not_recordable(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    prescription = await _signed_prescription(session, clinic)
    intruder = f.make_patient(clinic["hospital"], phone="+915553330001")
    session.add(intruder)
    await session.flush()
    await session.commit()

    refused = await client.post(
        "/patient/reminders/events",
        json={
            "prescription_id": str(prescription.id),
            "med_index": 0,
            "scheduled_for": datetime.now(UTC).isoformat(),
            "status": "taken",
        },
        headers=_headers(settings, intruder),
    )
    assert refused.status_code == 404


# -- chemo calendar ------------------------------------------------------------


async def test_the_chemo_calendar_speaks_the_patients_language(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    from app.models.enums import AppointmentStatus
    from app.models.scheduling import Appointment

    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    patient.lang = Lang.TE
    session.add(
        Appointment(
            patient_id=patient.id,
            department_id=clinic["department"].id,
            doctor_id=clinic["doctor"].id,
            slot_at=datetime.now(UTC) + timedelta(days=7),
            status=AppointmentStatus.BOOKED,
            source=Channel.APP,
            slot_type=SlotType.CHEMO_REVIEW,
        )
    )
    await session.commit()

    body = (await client.get("/patient/chemo-calendar", headers=_headers(settings, patient))).json()

    assert len(body) == 1
    assert body[0]["cycle_no"] == 1
    assert body[0]["expect"]
    # Telugu script, not an English placeholder (the S13 leak check's rule).
    from app.languages import looks_like_script

    assert looks_like_script(body[0]["expect"][0], Lang.TE)


# -- family access -------------------------------------------------------------


async def test_the_patient_adds_and_revokes_her_own_caregiver(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    await session.commit()
    headers = _headers(settings, patient)

    added = await client.post(
        "/patient/caregivers",
        json={"phone": "+915551230006", "name": "Meena", "relation": "daughter"},
        headers=headers,
    )
    assert added.status_code == 201
    assert added.json()["status"] == "active"
    assert added.json()["consented_at"] is not None

    listed = await client.get("/patient/caregivers", headers=headers)
    assert [c["phone"] for c in listed.json()] == ["+915551230006"]

    removed = await client.delete(f"/patient/caregivers/{added.json()['id']}", headers=headers)
    assert removed.json()["status"] == "revoked"


async def test_a_caregiver_cannot_grant_access_to_a_second_caregiver(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    """Consent has to have a root, and the root is the patient."""
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    link = await _link(session, patient, "+915551230007")
    await session.commit()

    headers = _headers(settings, patient, via="caregiver", phone=link.phone)
    refused = await client.post(
        "/patient/caregivers", json={"phone": "+915551230008"}, headers=headers
    )
    assert refused.status_code == 403
    # She can still read — that is what she was linked for.
    assert (await client.get("/patient/caregivers", headers=headers)).status_code == 200


# -- appointments --------------------------------------------------------------


async def test_booking_from_the_app_uses_the_same_inventory_as_the_phone_line(
    client: AsyncClient, session: AsyncSession, settings: Settings, sms
):
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]
    slot = f.make_slot(
        clinic["doctor"],
        (datetime.now(UTC) + timedelta(days=2)).replace(hour=6, minute=0, second=0, microsecond=0),
    )
    session.add(slot)
    await session.flush()
    await session.commit()
    headers = _headers(settings, patient)

    listed = await client.get("/patient/appointments/slots", headers=headers)
    assert any(s["slot_id"] == str(slot.id) for s in listed.json())

    booked = await client.post(
        "/patient/appointments", json={"slot_id": str(slot.id)}, headers=headers
    )
    assert booked.status_code == 201, booked.text

    # The seat is gone from the inventory the receptionist reads.
    again = await client.get("/patient/appointments/slots", headers=headers)
    assert not any(s["slot_id"] == str(slot.id) for s in again.json())

    mine = await client.get("/patient/appointments", headers=headers)
    assert [a["id"] for a in mine.json()] == [booked.json()["id"]]

    cancelled = await client.post(
        f"/patient/appointments/{booked.json()['id']}/cancel", headers=headers
    )
    assert cancelled.json()["status"] == "cancelled"


async def test_cancelling_somebody_elses_appointment_reads_as_not_found(
    client: AsyncClient, session: AsyncSession, settings: Settings
):
    from app import scheduling

    clinic = await f.build_clinic(session)
    slot = f.make_slot(
        clinic["doctor"],
        (datetime.now(UTC) + timedelta(days=3)).replace(hour=6, minute=0, second=0, microsecond=0),
    )
    session.add(slot)
    await session.flush()
    appointment = await scheduling.book(
        session, patient=clinic["patient"], slot_id=slot.id, source=Channel.PHONE
    )
    intruder = f.make_patient(clinic["hospital"], phone="+915551110001")
    session.add(intruder)
    await session.flush()
    await session.commit()

    refused = await client.post(
        f"/patient/appointments/{appointment.id}/cancel",
        headers=_headers(settings, intruder),
    )
    assert refused.status_code == 404
