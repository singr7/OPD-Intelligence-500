"""Staff onboarding (S-GL.2) — `app.people` and its console routes.

The headline is `test_a_doctor_onboarded_from_the_console_is_bookable_and_visible`:
create a doctor, give her a clinic, generate her slots, and she appears in the
*receptionist's own inventory* (`app.scheduling.find_slots`) and the *doctor
console's own read* (`app.doctor.day_list`'s department query) — with no seed run.
That is half the session AC, and it is asserted against the code those two
surfaces actually call rather than against a route that merely echoes what was
written.

The rest pin the two refusals that make this safe to hand an administrator: a
deactivation that would strand booked patients, and a phone number that would
create an account nobody could log into.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app import people, roster, scheduling
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.audit import AuditLog
from app.models.enums import Channel, Lang, Role, SlotType
from app.models.org import Doctor, User
from app.models.scheduling import AppointmentSlot, SlotTemplate
from tests.factories import (
    generation_start,
    make_department,
    make_doctor,
    make_hospital,
    make_patient,
    make_slot_template,
    make_user,
)


async def _clinic(session):
    """A hospital with a department and a patient, but no doctors — the shape a
    box is in before anybody is onboarded."""
    hospital = make_hospital()
    session.add(hospital)
    await session.flush()
    department = make_department(hospital, code="MEDONC", name="Medical Oncology")
    session.add(department)
    await session.flush()
    patient = make_patient(hospital)
    session.add(patient)
    await session.flush()
    return hospital, department, patient


async def _admin_headers(session, settings: Settings, hospital) -> dict[str, str]:
    user = make_user(hospital, role=Role.ADMIN)
    session.add(user)
    await session.flush()
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


def _next(weekday: int, *, weeks: int = 1) -> datetime:
    """A datetime on the next `weekday`, safely in the future."""
    tz = scheduling.hospital_tz()
    today = datetime.now(tz).date()
    day = today + timedelta(days=(weekday - today.weekday()) % 7 or 7, weeks=weeks - 1)
    return datetime.combine(day, time(10, 0), tzinfo=tz).astimezone(UTC)


# -- phone numbers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("9876543210", "+919876543210"),
        ("98765 43210", "+919876543210"),
        ("098765-43210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("+44 7700 900123", "+447700900123"),
    ],
)
def test_a_number_is_stored_in_the_one_shape_the_login_looks_up(typed: str, stored: str):
    # The OTP flow matches `users.phone` exactly, so any other shape is an
    # account that silently cannot sign in.
    assert people.normalise_phone(typed) == stored


@pytest.mark.parametrize("typed", ["", "12345", "not a phone", "+1"])
def test_a_number_that_could_not_receive_an_otp_is_refused(typed: str):
    with pytest.raises(people.PeopleError):
        people.normalise_phone(typed)


# -- creating ------------------------------------------------------------------


async def test_creating_a_doctor_writes_the_login_and_the_profile_together(session):
    hospital, department, _ = await _clinic(session)

    doctor = await people.create_doctor(
        session,
        name="Dr. Meera Joshi",
        phone="9812345678",
        department_code="MEDONC",
        reg_no="RMC-ONC-2001",
        qualification="MD, DM (Medical Oncology)",
        lang=Lang.HI,
    )

    user = await session.get(User, doctor.user_id)
    assert user is not None
    assert (user.role, user.active, user.phone) == (Role.DOCTOR, True, "+919812345678")
    assert doctor.department_id == department.id
    # A doctor-role user with no profile breaks every screen that joins on it,
    # so the two rows are written in one transaction or not at all.
    assert user.can_login


async def test_a_taken_number_names_the_person_who_has_it(session):
    hospital, _, _ = await _clinic(session)
    session.add(make_user(hospital, name="Rekha Meena", phone="+919812345678"))
    await session.flush()

    with pytest.raises(people.PeopleError, match="Rekha Meena"):
        await people.create_user(session, name="Someone Else", phone="9812345678", role=Role.NURSE)


async def test_a_patient_identity_cannot_be_minted_from_the_console(session):
    hospital, _, _ = await _clinic(session)
    # Patients come from registration and caregivers from a consented grant
    # (S16). A console that could create either would be a second, unaudited
    # path into the patient identity model.
    with pytest.raises(people.PeopleError):
        await people.create_user(session, name="Kamla Devi", phone="9812345670", role=Role.PATIENT)


async def test_a_duplicate_registration_number_is_refused_by_name(session):
    hospital, department, _ = await _clinic(session)
    user = make_user(hospital, role=Role.DOCTOR, phone="+919800000001")
    session.add(user)
    await session.flush()
    session.add(make_doctor(user, department, name="Dr. Anil Gupta", reg_no="RMC-ONC-1001"))
    await session.flush()

    with pytest.raises(people.PeopleError, match="Dr. Anil Gupta"):
        await people.create_doctor(
            session,
            name="Dr. Someone",
            phone="9800000002",
            department_code="MEDONC",
            reg_no="RMC-ONC-1001",
        )


async def test_onboarding_is_audited(session):
    hospital, _, _ = await _clinic(session)
    await people.create_doctor(
        session,
        name="Dr. Meera Joshi",
        phone="9812345678",
        department_code="MEDONC",
        reg_no="RMC-ONC-2001",
    )
    await session.flush()

    entries = (
        (await session.execute(select(AuditLog).where(AuditLog.entity.in_(("users", "doctors")))))
        .scalars()
        .all()
    )
    assert {e.entity for e in entries} == {"users", "doctors"}


# -- the invite ----------------------------------------------------------------


async def test_an_invite_says_the_number_signs_in_and_mints_nothing(session, sms, settings):
    hospital, _, _ = await _clinic(session)
    doctor = await people.create_doctor(
        session,
        name="Dr. Meera Joshi",
        phone="9812345678",
        department_code="MEDONC",
        reg_no="RMC-ONC-2001",
        lang=Lang.EN,
    )

    result = await people.send_invite(session, user_id=doctor.user_id, settings=settings)

    assert result.sent and result.to == "+919812345678"
    assert len(sms.sent) == 1
    body = sms.sent[0].body
    assert "Dr. Meera Joshi" in body and "one-time code" in body
    # No token, no link, nothing to leak or expire: the OTP login *is* the
    # credential, and this message only tells somebody it works.
    assert "http" not in body


async def test_a_deactivated_account_cannot_be_invited(session, sms, settings):
    hospital, _, _ = await _clinic(session)
    user = await people.create_user(
        session, name="Rekha Meena", phone="9812345671", role=Role.COORDINATOR
    )
    await people.deactivate(session, user_id=user.id)

    with pytest.raises(people.PeopleError, match="deactivated"):
        await people.send_invite(session, user_id=user.id, settings=settings)
    assert sms.sent == []


# -- deactivation --------------------------------------------------------------


async def _booked_doctor(session):
    """A doctor with a Tuesday clinic, one generated slot booked and one empty."""
    hospital, department, patient = await _clinic(session)
    user = make_user(hospital, role=Role.DOCTOR, phone="+919800000001")
    session.add(user)
    await session.flush()
    doctor = make_doctor(user, department, name="Dr. Anil Gupta", reg_no="RMC-ONC-1001")
    session.add(doctor)
    await session.flush()
    template = make_slot_template(doctor, weekday=1, start_time=time(10, 0), end_time=time(11, 0))
    session.add(template)
    await session.flush()

    await scheduling.generate_slots(session, start=generation_start(), days=21)
    slots = (
        (
            await session.execute(
                select(AppointmentSlot)
                .where(AppointmentSlot.doctor_id == doctor.id)
                .order_by(AppointmentSlot.starts_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(slots) >= 2
    await scheduling.book(session, patient=patient, slot_id=slots[0].id, source=Channel.KIOSK)
    return hospital, department, doctor, user, patient, slots


async def test_deactivating_refuses_while_patients_are_booked(session):
    _, _, doctor, user, patient, _ = await _booked_doctor(session)

    impact = await people.deactivation_impact(session, user_id=user.id)
    assert impact.needs_a_decision
    assert [b.patient_name for b in impact.booked] == [patient.name]

    with pytest.raises(people.PeopleError, match="booked"):
        await people.deactivate(session, user_id=user.id)

    # Nothing moved: refusing must not half-apply.
    assert (await session.get(User, user.id)).active is True


async def test_acknowledged_deactivation_shuts_the_empty_slots_and_keeps_the_booked_one(session):
    _, _, doctor, user, _, slots = await _booked_doctor(session)
    booked_slot = slots[0]

    result = await people.deactivate(session, user_id=user.id, acknowledge=True)

    assert result.clinics_retired == 1
    assert result.slots_blocked >= 1
    assert len(result.appointments_left) == 1

    await session.refresh(booked_slot)
    # The promise made to the patient in it stands, and stays findable.
    assert booked_slot.blocked is False and booked_slot.booked == 1

    empty = (
        (
            await session.execute(
                select(AppointmentSlot).where(
                    AppointmentSlot.doctor_id == doctor.id,
                    AppointmentSlot.booked == 0,
                    AppointmentSlot.starts_at >= datetime.now(UTC),
                )
            )
        )
        .scalars()
        .all()
    )
    assert empty and all(slot.blocked for slot in empty)

    # …and the receptionist cannot offer her to anybody new.
    offers = await scheduling.find_slots(session, doctor_id=doctor.id, limit=10)
    assert offers == []

    assert (await session.get(User, user.id)).can_login is False
    assert (await session.get(Doctor, doctor.id)).active is False


async def test_reactivating_restores_the_login_but_not_the_clinic(session):
    _, _, doctor, user, _, _ = await _booked_doctor(session)
    await people.deactivate(session, user_id=user.id, acknowledge=True)

    person = await people.activate(session, user_id=user.id)

    assert person.active is True
    assert (await session.get(Doctor, doctor.id)).active is True
    # "She is back" and "she is back on Tuesdays at ten" are different facts.
    live = await session.scalar(
        select(func.count())
        .select_from(SlotTemplate)
        .where(SlotTemplate.doctor_id == doctor.id, SlotTemplate.active.is_(True))
    )
    assert live == 0
    assert await scheduling.find_slots(session, doctor_id=doctor.id, limit=5) == []


async def test_a_coordinator_deactivates_with_no_clinic_to_worry_about(session):
    hospital, _, _ = await _clinic(session)
    user = await people.create_user(
        session, name="Rekha Meena", phone="9812345671", role=Role.COORDINATOR
    )

    impact = await people.deactivation_impact(session, user_id=user.id)
    assert impact.is_doctor is False and impact.needs_a_decision is False

    result = await people.deactivate(session, user_id=user.id)
    assert (result.clinics_retired, result.slots_blocked) == (0, 0)
    assert (await session.get(User, user.id)).can_login is False


# -- the session AC ------------------------------------------------------------


async def test_a_doctor_onboarded_from_the_console_is_bookable_and_visible(session):
    """Half the S-GL.2 AC, against the surfaces that actually read her.

    Onboard → CSV roster → generate slots, then check she is in the
    receptionist's inventory (`find_slots`, which is the query the AI
    receptionist reads options from) and in the doctor console's own department
    read. No seed run, no deploy.
    """
    hospital, department, patient = await _clinic(session)

    doctor = await people.create_doctor(
        session,
        name="Dr. Meera Joshi",
        phone="9812345678",
        department_code="MEDONC",
        reg_no="RMC-ONC-2001",
    )

    csv = (
        "doctor,weekday,start,end,slot_type,capacity\n"
        "RMC-ONC-2001,Tuesday,10:00,13:00,follow_up,2\n"
    )
    plan = await roster.plan_roster(session, roster.read_rows(csv.encode(), "roster.csv"))
    assert plan.ok and plan.counts()["create"] == 1
    result = await roster.apply_roster(session, plan, horizon_days=21)
    assert result.created == 1 and result.slots_generated > 0

    # The receptionist's own inventory query.
    offers = await scheduling.find_slots(
        session, department_code="MEDONC", slot_type=SlotType.FOLLOW_UP, limit=5
    )
    assert offers, "the new doctor has no bookable inventory"
    assert offers[0].doctor_name == "Dr. Meera Joshi"
    assert offers[0].starts_at.astimezone(scheduling.hospital_tz()).weekday() == 1
    assert offers[0].seats_left == 2

    # …and she can actually be booked into it.
    appointment = await scheduling.book(
        session, patient=patient, slot_id=offers[0].slot_id, source=Channel.KIOSK
    )
    assert appointment.doctor_id == doctor.id

    # The doctor console lists her department's day; she is a real doctor row in
    # it, which is what the console's day_list joins against.
    listed = await people.list_people(session)
    hers = next(p for p in listed if p.doctor_id == doctor.id)
    assert (hers.department_code, hers.clinics, hers.upcoming_appointments) == ("MEDONC", 1, 1)


async def test_the_whole_thing_over_http(client: AsyncClient, session, settings):
    """The same journey through the routes an admin's browser calls."""
    hospital, department, _ = await _clinic(session)
    headers = await _admin_headers(session, settings, hospital)

    departments = (await client.get("/admin/departments", headers=headers)).json()
    assert "MEDONC" in {d["code"] for d in departments}

    created = await client.post(
        "/admin/people/doctors",
        headers=headers,
        json={
            "name": "Dr. Meera Joshi",
            "phone": "9812345678",
            "department_code": "MEDONC",
            "reg_no": "RMC-ONC-2001",
            "qualification": "MD, DM",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["phone"] == "+919812345678" and body["active"] is True

    listed = (await client.get("/admin/people", headers=headers)).json()
    assert "RMC-ONC-2001" in {p["reg_no"] for p in listed}

    invited = await client.post(f"/admin/people/{body['user_id']}/invite", headers=headers)
    assert invited.status_code == 200 and invited.json()["sent"] is True

    impact = (
        await client.get(f"/admin/people/{body['user_id']}/deactivation-impact", headers=headers)
    ).json()
    assert impact["needs_a_decision"] is False

    off = await client.post(f"/admin/people/{body['user_id']}/deactivate", headers=headers, json={})
    assert off.status_code == 200
    assert (await client.get("/admin/people", headers=headers)).json()

    on = await client.post(f"/admin/people/{body['user_id']}/activate", headers=headers)
    assert on.status_code == 200 and on.json()["active"] is True


async def test_the_people_routes_are_admin_only(client: AsyncClient, session, settings):
    hospital, _, _ = await _clinic(session)
    nurse = make_user(hospital, role=Role.NURSE)
    session.add(nurse)
    await session.flush()
    token = create_access_token(
        user_id=nurse.id,
        role=nurse.role,
        name=nurse.name,
        settings=settings,
        hospital_id=nurse.hospital_id,
    ).token
    headers = {"Authorization": f"Bearer {token}"}

    assert (await client.get("/admin/people", headers=headers)).status_code == 403
    assert (await client.post("/admin/people", headers=headers, json={})).status_code == 403
