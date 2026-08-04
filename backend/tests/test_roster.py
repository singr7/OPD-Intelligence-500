"""The clinic roster (S-GL.2) — `app.roster`: the grid, the import, and the one
thing that could quietly break a booking.

Two tests carry the session's weight:

- `test_the_dry_run_names_the_row_that_names_a_doctor_we_do_not_have` — the AC's
  second half, word for word.
- `test_moving_a_clinic_retires_the_old_inventory_and_keeps_the_new` — the
  handoff's design warning. `generate_slots` dedupes on `(doctor, instant)`
  regardless of `blocked`, so the obvious implementation (block everything, then
  regenerate) empties the clinic. This asserts the reconciliation instead.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, time

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app import roster, scheduling
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.enums import Channel, Role
from app.models.org import Department, Hospital
from app.models.scheduling import AppointmentSlot, SlotTemplate
from tests.factories import (
    a_weekday_ahead,
    generation_start,
    make_department,
    make_doctor,
    make_hospital,
    make_patient,
    make_slot_template,
    make_user,
)


async def _hospital_with_two_doctors(session):
    hospital = make_hospital()
    session.add(hospital)
    await session.flush()
    department = make_department(hospital, code="MEDONC", name="Medical Oncology")
    session.add(department)
    await session.flush()

    doctors = []
    for index, (name, reg_no) in enumerate(
        [("Dr. Anil Gupta", "RMC-ONC-1001"), ("Dr. Meera Joshi", "RMC-ONC-1002")]
    ):
        user = make_user(hospital, role=Role.DOCTOR, phone=f"+91980000000{index}")
        session.add(user)
        await session.flush()
        doctor = make_doctor(user, department, name=name, reg_no=reg_no)
        session.add(doctor)
        doctors.append(doctor)
    patient = make_patient(hospital)
    session.add(patient)
    await session.flush()
    return hospital, department, doctors, patient


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


def _rows(csv: str, filename: str = "roster.csv"):
    return roster.read_rows(csv.encode(), filename)


HEADER = "doctor,weekday,start,end,slot_type,capacity\n"


# -- parsing -------------------------------------------------------------------


def test_the_columns_a_hospital_actually_exports_are_accepted():
    rows = _rows("Doctor Name,Day,Start Time,End Time,Type,Seats\nX,Tue,10:00,13:00,follow_up,2\n")
    assert rows[0].raw == {
        "doctor": "X",
        "weekday": "Tue",
        "start": "10:00",
        "end": "13:00",
        "slot_type": "follow_up",
        "capacity": "2",
    }
    # The line number is the one in their spreadsheet, header included.
    assert rows[0].line == 2


def test_a_file_missing_a_column_says_which_one():
    with pytest.raises(roster.RosterError, match="end"):
        _rows("doctor,weekday,start\nX,Tue,10:00\n")


def test_blank_spreadsheet_lines_are_not_rows():
    rows = _rows(HEADER + "X,Tue,10:00,13:00,follow_up,2\n,,,,,\n")
    assert len(rows) == 1


@pytest.mark.parametrize(
    ("typed", "weekday"),
    [("Tuesday", 1), ("tue", 1), ("TUES", 1), ("1", 1), ("Thursday", 3), ("thurs", 3), ("0", 0)],
)
def test_a_weekday_is_read_the_way_it_was_written(typed: str, weekday: int):
    assert roster._parse_weekday(typed) == weekday


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("10:00", time(10, 0)), ("09:30:00", time(9, 30)), ("0.4375", time(10, 30))],
)
def test_a_time_survives_the_shapes_a_spreadsheet_stores_it_in(typed: str, expected: time):
    # 0.4375 is Excel's fractional day — what a roster exported without text
    # formatting actually contains.
    assert roster._parse_time(typed) == expected


def test_an_xlsx_is_read_without_a_workbook_library():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1">'
            + "".join(
                f'<c r="{col}1" t="inlineStr"><is><t>{value}</t></is></c>'
                for col, value in zip(
                    "ABCDEF", ["doctor", "weekday", "start", "end", "slot_type", "capacity"]
                )
            )
            + "</row>"
            '<row r="2">'
            + "".join(
                f'<c r="{col}2" t="inlineStr"><is><t>{value}</t></is></c>'
                for col, value in zip(
                    "ABCDEF", ["RMC-ONC-1001", "Tuesday", "10:00", "13:00", "follow_up", "2"]
                )
            )
            + "</row>"
            "</sheetData></worksheet>",
        )
    rows = roster.read_rows(buffer.getvalue(), "roster.xlsx")
    assert rows[0].raw["doctor"] == "RMC-ONC-1001"
    assert rows[0].raw["capacity"] == "2"


def test_something_that_is_not_a_spreadsheet_says_so_rather_than_crashing():
    with pytest.raises(roster.RosterError, match="could not be read"):
        roster.read_rows(b"this is not a zip", "roster.xlsx")


# -- the dry run ---------------------------------------------------------------


async def test_the_dry_run_names_the_row_that_names_a_doctor_we_do_not_have(session):
    """The session AC, exactly: 'the import dry-run refuses a row naming an
    unknown doctor and says which row'."""
    _, _, doctors, _ = await _hospital_with_two_doctors(session)

    csv = (
        HEADER
        + "RMC-ONC-1001,Tuesday,10:00,13:00,follow_up,2\n"
        + "RMC-ONC-9999,Wednesday,10:00,13:00,follow_up,2\n"
        + "Dr. Meera Joshi,Friday,09:30,11:30,new_consult,1\n"
    )
    plan = await roster.plan_roster(session, _rows(csv))

    assert plan.ok is False
    assert plan.counts() == {"create": 2, "update": 0, "unchanged": 0, "error": 1}
    (failed,) = plan.errors
    assert failed.line == 3
    assert "row 3" in failed.error and "RMC-ONC-9999" in failed.error

    # …and nothing is written, not even the two good rows.
    with pytest.raises(roster.RosterError, match="row 3"):
        await roster.apply_roster(session, plan)
    assert (await session.execute(select(SlotTemplate))).scalars().all() == []


async def test_a_doctor_can_be_named_by_name_and_an_ambiguous_name_is_refused(session):
    hospital, department, doctors, _ = await _hospital_with_two_doctors(session)
    plan = await roster.plan_roster(
        session, _rows(HEADER + "Dr Anil Gupta,Tuesday,10:00,13:00,follow_up,2\n")
    )
    assert plan.ok and plan.rows[0].doctor_name == "Dr. Anil Gupta"

    twin_user = make_user(hospital, role=Role.DOCTOR, phone="+919800000099")
    session.add(twin_user)
    await session.flush()
    session.add(make_doctor(twin_user, department, name="Dr. Anil Gupta", reg_no="RMC-ONC-1003"))
    await session.flush()

    plan = await roster.plan_roster(
        session, _rows(HEADER + "Anil Gupta,Tuesday,10:00,13:00,follow_up,2\n")
    )
    assert not plan.ok
    assert "registration number" in plan.errors[0].error


async def test_every_bad_row_is_reported_at_once(session):
    await _hospital_with_two_doctors(session)
    csv = (
        HEADER
        + "RMC-ONC-1001,Blursday,10:00,13:00,follow_up,2\n"
        + "RMC-ONC-1001,Tuesday,13:00,10:00,follow_up,2\n"
        + "RMC-ONC-1001,Tuesday,10:00,13:00,teatime,2\n"
    )
    plan = await roster.plan_roster(session, _rows(csv))
    # An administrator fixing a file wants all of its problems, not one upload
    # per typo.
    assert [row.line for row in plan.errors] == [2, 3, 4]


async def test_a_row_that_matches_what_is_already_there_is_unchanged(session):
    _, _, doctors, _ = await _hospital_with_two_doctors(session)
    session.add(
        make_slot_template(
            doctors[0],
            weekday=1,
            start_time=time(10, 0),
            end_time=time(13, 0),
            capacity=2,
            slot_minutes=15,
        )
    )
    await session.flush()

    plan = await roster.plan_roster(
        session, _rows(HEADER + "RMC-ONC-1001,Tuesday,10:00,13:00,follow_up,2\n")
    )
    assert plan.rows[0].action == "unchanged"
    result = await roster.apply_roster(session, plan, generate=False)
    assert (result.created, result.updated, result.unchanged) == (0, 0, 1)


async def test_applying_creates_the_clinic_and_its_inventory(session):
    _, _, doctors, _ = await _hospital_with_two_doctors(session)
    # Not "Tuesday": `apply_roster` generates from today inclusive, so a clinic on
    # *today's* weekday contributes hours that have already elapsed, and the
    # closing `future_slots == slots_generated` assertion would hold only on the
    # days of the week this suite is not run.
    weekday = a_weekday_ahead()
    plan = await roster.plan_roster(
        session, _rows(HEADER + f"RMC-ONC-1001,{weekday},10:00,13:00,follow_up,2\n")
    )
    assert plan.rows[0].slots_per_week == 12

    result = await roster.apply_roster(session, plan, horizon_days=14)
    assert result.created == 1
    # Two occurrences at most in a fortnight, twelve 15-minute slots each.
    assert result.slots_generated in (12, 24)

    clinics = await roster.list_clinics(session)
    assert len(clinics) == 1
    assert clinics[0].slots_per_week == 12
    assert clinics[0].future_slots == result.slots_generated


# -- the trap the handoff warned about -----------------------------------------


async def _clinic_with_a_booking(session):
    """A Tuesday 10:00–12:00 clinic, generated, with the first slot booked."""
    hospital, department, doctors, patient = await _hospital_with_two_doctors(session)
    doctor = doctors[0]
    template = make_slot_template(
        doctor, weekday=1, start_time=time(10, 0), end_time=time(12, 0), capacity=1
    )
    session.add(template)
    await session.flush()
    await scheduling.generate_slots(session, start=generation_start(), days=21)
    slots = (
        (
            await session.execute(
                select(AppointmentSlot)
                .where(AppointmentSlot.template_id == template.id)
                .order_by(AppointmentSlot.starts_at)
            )
        )
        .scalars()
        .all()
    )
    await scheduling.book(session, patient=patient, slot_id=slots[0].id, source=Channel.KIOSK)
    return doctor, template, slots, patient


async def test_moving_a_clinic_retires_the_old_inventory_and_keeps_the_new(session):
    """The design warning, made a test.

    Blocking every future slot on an edit would be silently wrong:
    `generate_slots` skips an instant that already has a row *whatever its
    blocked flag*, so those slots would never come back and the clinic would
    empty out. Reconciliation blocks only the instants the new shape no longer
    runs.
    """
    doctor, template, slots, _ = await _clinic_with_a_booking(session)
    booked_slot = slots[0]

    # 10:00–12:00 becomes 11:00–12:00: the 10:00 and 10:15… instants go away,
    # the 11:00 ones stay.
    _, impact = await roster.save_clinic(
        session,
        write=roster.ClinicWrite(
            doctor_id=doctor.id,
            weekday=1,
            start_time=time(11, 0),
            end_time=time(12, 0),
            capacity=1,
            slot_minutes=15,
        ),
        template_id=template.id,
        acknowledge=True,
    )
    assert impact is not None and len(impact.booked) == 1

    await scheduling.generate_slots(session, start=generation_start(), days=21)
    tz = scheduling.hospital_tz()
    live = [
        slot
        for slot in (
            (
                await session.execute(
                    select(AppointmentSlot).where(
                        AppointmentSlot.template_id == template.id,
                        AppointmentSlot.blocked.is_(False),
                        AppointmentSlot.starts_at >= datetime.now(UTC),
                    )
                )
            )
            .scalars()
            .all()
        )
    ]
    assert live, "the clinic emptied out — reconciliation regressed to block-everything"
    hours = {slot.starts_at.astimezone(tz).hour for slot in live if slot.booked == 0}
    assert hours == {11}, f"stale 10:00 inventory is still bookable: {sorted(hours)}"

    # The patient booked at 10:00 keeps her appointment.
    await session.refresh(booked_slot)
    assert booked_slot.booked == 1 and booked_slot.blocked is False


async def test_editing_refuses_while_somebody_is_booked_unless_acknowledged(session):
    doctor, template, _, patient = await _clinic_with_a_booking(session)
    write = roster.ClinicWrite(
        doctor_id=doctor.id,
        weekday=1,
        start_time=time(11, 0),
        end_time=time(12, 0),
        capacity=1,
        slot_minutes=15,
    )
    with pytest.raises(roster.RosterError, match="booked into"):
        await roster.save_clinic(session, write=write, template_id=template.id)

    impact = await roster.change_impact(session, template_id=template.id)
    assert [b.patient_name for b in impact.booked] == [patient.name]
    assert impact.empty_future_slots > 0


async def test_raising_capacity_reaches_the_slots_that_already_exist(session):
    doctor, template, slots, _ = await _clinic_with_a_booking(session)
    await roster.save_clinic(
        session,
        write=roster.ClinicWrite(
            doctor_id=doctor.id,
            weekday=1,
            start_time=time(10, 0),
            end_time=time(12, 0),
            capacity=3,
            slot_minutes=15,
        ),
        template_id=template.id,
        acknowledge=True,
    )
    await session.refresh(slots[0])
    await session.refresh(slots[1])
    # A capacity change with no time change must reach existing inventory:
    # generation would skip these instants entirely.
    assert slots[0].capacity == 3 and slots[1].capacity == 3


async def test_capacity_is_never_shrunk_below_what_is_already_booked(session):
    hospital, department, doctors, patient = await _hospital_with_two_doctors(session)
    doctor = doctors[0]
    template = make_slot_template(
        doctor, weekday=1, start_time=time(10, 0), end_time=time(12, 0), capacity=2
    )
    session.add(template)
    await session.flush()
    await scheduling.generate_slots(session, start=generation_start(), days=21)
    slot = (
        (
            await session.execute(
                select(AppointmentSlot)
                .where(AppointmentSlot.template_id == template.id)
                .order_by(AppointmentSlot.starts_at)
            )
        )
        .scalars()
        .first()
    )
    await scheduling.book(session, patient=patient, slot_id=slot.id, source=Channel.KIOSK)

    await roster.save_clinic(
        session,
        write=roster.ClinicWrite(
            doctor_id=doctor.id,
            weekday=1,
            start_time=time(10, 0),
            end_time=time(12, 0),
            capacity=1,
            slot_minutes=15,
        ),
        template_id=template.id,
        acknowledge=True,
    )
    await session.refresh(slot)
    # `booked <= capacity` is a database CHECK; shrinking under it would be a
    # 500 rather than a policy. The slot keeps the capacity it needs and the
    # admin can see the clinic and the seat count disagree, which is true.
    assert slot.capacity >= slot.booked


async def test_retiring_a_clinic_blocks_its_empty_future_slots_only(session):
    doctor, template, slots, _ = await _clinic_with_a_booking(session)

    impact = await roster.retire_clinic(session, template_id=template.id, acknowledge=True)
    assert len(impact.booked) == 1

    await session.refresh(template)
    assert template.active is False
    await session.refresh(slots[0])
    assert slots[0].blocked is False  # booked
    await session.refresh(slots[1])
    assert slots[1].blocked is True
    assert await scheduling.find_slots(session, doctor_id=doctor.id, limit=5) == []


async def test_a_clinic_that_collides_with_an_existing_one_says_which(session):
    _, _, doctors, _ = await _hospital_with_two_doctors(session)
    session.add(make_slot_template(doctors[0], weekday=1, start_time=time(10, 0)))
    await session.flush()

    with pytest.raises(roster.RosterError, match="Tuesday 10:00"):
        await roster.save_clinic(
            session,
            write=roster.ClinicWrite(
                doctor_id=doctors[0].id,
                weekday=1,
                start_time=time(10, 0),
                end_time=time(12, 0),
            ),
        )


@pytest.mark.parametrize(
    "write_kwargs",
    [
        {"start_time": time(13, 0), "end_time": time(10, 0)},
        {"start_time": time(10, 0), "end_time": time(10, 10), "slot_minutes": 30},
        {"start_time": time(10, 0), "end_time": time(12, 0), "capacity": 0},
    ],
)
async def test_a_clinic_that_could_not_run_is_refused(session, write_kwargs):
    _, _, doctors, _ = await _hospital_with_two_doctors(session)
    with pytest.raises(roster.RosterError):
        await roster.save_clinic(
            session,
            write=roster.ClinicWrite(
                doctor_id=doctors[0].id,
                weekday=1,
                **{"start_time": time(10, 0), "end_time": time(12, 0), **write_kwargs},
            ),
        )


# -- generation ----------------------------------------------------------------


async def test_generating_twice_creates_nothing_the_second_time(session):
    _, _, doctors, _ = await _hospital_with_two_doctors(session)
    session.add(make_slot_template(doctors[0], weekday=1, end_time=time(12, 0)))
    await session.flush()

    first = await roster.generate(session, days=21)
    second = await roster.generate(session, days=21)
    assert first.created > 0 and second.created == 0


# -- HTTP ----------------------------------------------------------------------


async def test_the_console_walks_the_whole_roster_flow(client: AsyncClient, session, settings):
    hospital, department, doctors, _ = await _hospital_with_two_doctors(session)
    headers = await _admin_headers(session, settings, hospital)

    # The panel that was a deferral marker until this session.
    empty = await client.get("/admin/slot-templates", headers=headers)
    assert empty.status_code == 200 and empty.json() == []

    sample = await client.get("/admin/roster/sample.csv", headers=headers)
    assert sample.status_code == 200 and "doctor,weekday,start" in sample.text

    csv = HEADER + "RMC-ONC-1001,Tuesday,10:00,13:00,follow_up,2\nnobody,Friday,10:00,11:00,,\n"
    files = {"file": ("roster.csv", csv.encode(), "text/csv")}

    dry = await client.post("/admin/roster/import", headers=headers, files=files)
    assert dry.status_code == 200
    assert dry.json()["plan"]["ok"] is False
    assert dry.json()["applied"] is None
    bad = next(r for r in dry.json()["plan"]["rows"] if r["action"] == "error")
    assert bad["line"] == 3 and "row 3" in bad["error"]

    # A file with the bad row removed applies.
    good = {"file": ("roster.csv", (HEADER + csv.splitlines()[1] + "\n").encode(), "text/csv")}
    applied = await client.post("/admin/roster/import?dry_run=false", headers=headers, files=good)
    assert applied.status_code == 200
    assert applied.json()["applied"]["created"] == 1
    assert applied.json()["applied"]["slots_generated"] > 0

    grid = (await client.get("/admin/slot-templates", headers=headers)).json()
    assert len(grid) == 1
    assert grid[0]["weekday_name"] == "Tuesday" and grid[0]["slots_per_week"] == 12
    assert len(grid[0]["next_dates"]) == 3

    again = await client.post("/admin/slots/generate", headers=headers, json={"days": 14})
    assert again.status_code == 200 and again.json()["created"] == 0

    impact = (
        await client.get(f"/admin/slot-templates/{grid[0]['template_id']}/impact", headers=headers)
    ).json()
    assert impact["needs_a_decision"] is False and impact["empty_future_slots"] > 0

    gone = await client.delete(f"/admin/slot-templates/{grid[0]['template_id']}", headers=headers)
    assert gone.status_code == 200
    assert (await client.get("/admin/slot-templates", headers=headers)).json() == []


async def test_an_edit_with_patients_booked_is_a_409(client: AsyncClient, session, settings):
    doctor, template, _, _ = await _clinic_with_a_booking(session)
    department = await session.get(Department, doctor.department_id)
    hospital = await session.get(Hospital, department.hospital_id)
    headers = await _admin_headers(session, settings, hospital)

    resp = await client.put(
        f"/admin/slot-templates/{template.id}",
        headers=headers,
        json={
            "doctor_id": str(doctor.id),
            "weekday": 1,
            "start": "11:00",
            "end": "12:00",
            "capacity": 1,
            "slot_minutes": 15,
        },
    )
    # 409, not 422: the request is fine and the state is the problem. The console
    # shows the patients and re-sends with acknowledge.
    assert resp.status_code == 409
    assert "booked into" in resp.json()["detail"]

    confirmed = await client.put(
        f"/admin/slot-templates/{template.id}",
        headers=headers,
        json={
            "doctor_id": str(doctor.id),
            "weekday": 1,
            "start": "11:00",
            "end": "12:00",
            "capacity": 1,
            "slot_minutes": 15,
            "acknowledge": True,
        },
    )
    assert confirmed.status_code == 200 and confirmed.json()["start"] == "11:00"
