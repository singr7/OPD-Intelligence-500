"""The clinic roster — slot templates, and importing them from a spreadsheet
(doc 03 §2/§10, S-GL.2).

> "Slot inventory per doctor/dept (admin-configured)" — doc 03 §2
> "Departments, doctors, rooms, **slot templates**" — doc 03 §10

`SlotTemplate` has existed since S15 and was edited by `seeds/slot_templates.json`
plus `make seed`. This module is the half doc 03 §10 asked for and S18-late left as
an honest placeholder: authoring the weekly grid from the console, and loading a
whole hospital's roster from the file the hospital already keeps it in.

## A template edit is not a slot edit

The trap, and the reason this file is longer than a CRUD wrapper: templates
generate slots, and `app.scheduling.generate_slots` is **additive**. Move a
Tuesday clinic from 10:00 to 11:00 and the 10:00 slots it already generated are
still there, still bookable, still pointing at a clinic that no longer exists.
Nothing in S15 was ever asked to run against a *changed* template.

So every write here computes its effect on future inventory first
(`change_impact`), and applying it does two different things to two different
kinds of slot:

- a future slot from this template with **nobody in it** is `blocked` — invisible
  to every booker, and reversible,
- a future slot with **somebody in it** is left exactly as it is, and returned to
  the caller by patient name.

That asymmetry is the whole design. A booked slot is a promise made to a person,
and no roster edit gets to quietly break one; an empty slot is inventory, and
stale inventory is how a caller gets offered a clinic that will not happen.
Nothing is ever deleted, matching `app.scheduling`'s own rule.

## Import is all-or-nothing, and dry-run first

A roster is one document. Half of it applied — because row 14 named a doctor who
has not been created yet — is worse than none of it: the admin cannot tell what
landed without reading the database, and re-uploading the fixed file would then
double-apply the good rows. So parsing collects **every** row's errors, reports
them against the row number the administrator sees in their spreadsheet, and
`apply` refuses a plan that has any. The dry run is the same code path with the
write withheld, so what it previews is what happens.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_admin_action
from app.models.enums import AuditAction, SlotType
from app.models.org import Department, Doctor
from app.models.patient import Patient
from app.models.scheduling import Appointment, AppointmentSlot, SlotTemplate
from app.people import BookedAppointment
from app.scheduling import LIVE_STATUSES, generate_slots, hospital_tz, instants_on

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: Accepted spellings of a weekday in an uploaded roster. Numbers are accepted
#: too, and mean Monday = 0 — the same convention as `date.weekday()` and the
#: seed file, so a hospital that exports from either does not silently shift its
#: whole week by one.
_WEEKDAYS: dict[str, int] = {
    **{name.lower(): i for i, name in enumerate(WEEKDAY_NAMES)},
    **{name.lower()[:3]: i for i, name in enumerate(WEEKDAY_NAMES)},
    "tues": 1,
    "thur": 3,
    "thurs": 3,
}

#: Column name → the field it fills. Several spellings per field, because the
#: file comes from whatever the hospital already keeps its roster in and
#: refusing "Start Time" because the code says `start` helps nobody.
_COLUMNS: dict[str, str] = {
    "doctor": "doctor",
    "doctor_name": "doctor",
    "name": "doctor",
    "reg_no": "doctor",
    "doctor_reg_no": "doctor",
    "registration": "doctor",
    "weekday": "weekday",
    "day": "weekday",
    "start": "start",
    "start_time": "start",
    "from": "start",
    "end": "end",
    "end_time": "end",
    "to": "end",
    "slot_type": "slot_type",
    "type": "slot_type",
    "capacity": "capacity",
    "seats": "capacity",
    "slot_minutes": "slot_minutes",
    "minutes": "slot_minutes",
    "duration": "slot_minutes",
}

REQUIRED = ("doctor", "weekday", "start", "end")

#: Default slot length when the roster does not say. 15 minutes is the pilot's own
#: grid (`seeds/slot_templates.json`) and doc 03 §2's follow-up shape.
DEFAULT_SLOT_MINUTES = 15


class RosterError(Exception):
    """A refusal about the file as a whole — a missing column, an unreadable
    upload. Per-row problems are not exceptions; they are rows in the plan."""


# -- reading the grid ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Clinic:
    """One template as the console's weekly grid renders it."""

    template_id: uuid.UUID
    doctor_id: uuid.UUID
    doctor_name: str
    reg_no: str
    department_code: str
    weekday: int
    weekday_name: str
    start_time: time
    end_time: time
    slot_minutes: int
    capacity: int
    slot_type: SlotType
    active: bool
    #: Materialised inventory ahead of now — the difference between "authored" and
    #: "bookable", which is exactly what the Generate button changes.
    future_slots: int
    future_booked: int

    @property
    def slots_per_week(self) -> int:
        start = datetime.combine(date(2000, 1, 1), self.start_time)
        end = datetime.combine(date(2000, 1, 1), self.end_time)
        if end <= start or self.slot_minutes <= 0:
            return 0
        return int((end - start).total_seconds() // 60) // self.slot_minutes


async def list_clinics(session: AsyncSession, *, include_retired: bool = False) -> list[Clinic]:
    stmt = (
        select(SlotTemplate, Doctor, Department)
        .join(Doctor, Doctor.id == SlotTemplate.doctor_id)
        .join(Department, Department.id == SlotTemplate.department_id)
        .where(SlotTemplate.deleted_at.is_(None))
        .order_by(Doctor.name, SlotTemplate.weekday, SlotTemplate.start_time)
    )
    if not include_retired:
        stmt = stmt.where(SlotTemplate.active.is_(True))
    rows = (await session.execute(stmt)).all()

    now = datetime.now(UTC)
    counts = {
        template_id: (int(total), int(booked))
        for template_id, total, booked in (
            await session.execute(
                select(
                    AppointmentSlot.template_id,
                    func.count(),
                    func.coalesce(func.sum(AppointmentSlot.booked), 0),
                )
                .where(
                    AppointmentSlot.template_id.in_([t.id for t, _, _ in rows]),
                    AppointmentSlot.deleted_at.is_(None),
                    AppointmentSlot.starts_at >= now,
                )
                .group_by(AppointmentSlot.template_id)
            )
        ).all()
    }

    return [
        Clinic(
            template_id=template.id,
            doctor_id=doctor.id,
            doctor_name=doctor.name,
            reg_no=doctor.reg_no,
            department_code=department.code,
            weekday=template.weekday,
            weekday_name=WEEKDAY_NAMES[template.weekday],
            start_time=template.start_time,
            end_time=template.end_time,
            slot_minutes=template.slot_minutes,
            capacity=template.capacity,
            slot_type=template.slot_type,
            active=template.active,
            future_slots=counts.get(template.id, (0, 0))[0],
            future_booked=counts.get(template.id, (0, 0))[1],
        )
        for template, doctor, department in rows
    ]


# -- what a change would do to inventory ---------------------------------------


@dataclass(frozen=True, slots=True)
class ChangeImpact:
    """The future inventory a template edit or retirement would touch."""

    template_id: uuid.UUID
    label: str
    #: Empty future slots this clinic has already materialised. **Retiring** the
    #: clinic blocks all of them; **editing** it blocks only the ones the new
    #: shape no longer produces and adjusts the rest in place (`_reconcile`).
    empty_future_slots: int
    #: Future slots with patients in them. Untouched by either, and listed so
    #: somebody rings them.
    booked: list[BookedAppointment] = field(default_factory=list)

    @property
    def needs_a_decision(self) -> bool:
        return bool(self.booked)


async def change_impact(session: AsyncSession, *, template_id: uuid.UUID) -> ChangeImpact:
    template = await _template(session, template_id)
    now = datetime.now(UTC)

    empty = await session.scalar(
        select(func.count())
        .select_from(AppointmentSlot)
        .where(
            AppointmentSlot.template_id == template.id,
            AppointmentSlot.deleted_at.is_(None),
            AppointmentSlot.blocked.is_(False),
            AppointmentSlot.booked == 0,
            AppointmentSlot.starts_at >= now,
        )
    )

    rows = (
        await session.execute(
            select(Appointment, Patient)
            .join(Patient, Patient.id == Appointment.patient_id)
            .join(AppointmentSlot, AppointmentSlot.id == Appointment.slot_id)
            .where(
                AppointmentSlot.template_id == template.id,
                Appointment.deleted_at.is_(None),
                Appointment.status.in_(LIVE_STATUSES),
                Appointment.slot_at >= now,
            )
            .order_by(Appointment.slot_at)
        )
    ).all()

    return ChangeImpact(
        template_id=template.id,
        label=_label(template),
        empty_future_slots=int(empty or 0),
        booked=[
            BookedAppointment(
                appointment_id=appointment.id,
                patient_name=patient.name,
                patient_phone=patient.phone,
                at=appointment.slot_at,
                slot_type=str(appointment.slot_type) if appointment.slot_type else None,
            )
            for appointment, patient in rows
        ],
    )


async def _future_slots(session: AsyncSession, template: SlotTemplate) -> list[AppointmentSlot]:
    return list(
        (
            await session.execute(
                select(AppointmentSlot)
                .where(
                    AppointmentSlot.template_id == template.id,
                    AppointmentSlot.deleted_at.is_(None),
                    AppointmentSlot.starts_at >= datetime.now(UTC),
                )
                .order_by(AppointmentSlot.starts_at)
            )
        )
        .scalars()
        .all()
    )


async def _block_stale_slots(session: AsyncSession, template: SlotTemplate) -> int:
    """Block every empty future slot this template made — retirement's half.

    Booked slots are never touched: the clinic is over, but the promise made to
    the patient in it is not this function's to break.
    """
    blocked = 0
    for slot in await _future_slots(session, template):
        if slot.booked == 0 and not slot.blocked:
            slot.blocked = True
            blocked += 1
    return blocked


@dataclass(frozen=True, slots=True)
class Reconciliation:
    blocked: int
    adjusted: int


async def _reconcile(session: AsyncSession, template: SlotTemplate) -> Reconciliation:
    """Bring an edited template's existing future inventory into line with it.

    This is the function the handoff's design warning was about, and blocking
    everything would have been the wrong answer: `generate_slots` dedupes on
    `(doctor, instant)` **regardless of `blocked`**, so a blocked slot at an
    instant the clinic still runs would never be re-created and the clinic would
    quietly empty out. So:

    - an instant the new shape **no longer runs** → blocked if empty, left alone
      (and reported) if somebody is in it,
    - an instant it **still runs** → updated in place (length, type, capacity) and
      unblocked if a previous edit had blocked it,
    - an instant it runs that has **no row yet** → left to `generate_slots`, which
      is the one thing that creates inventory.

    Capacity is only ever *raised* below the number already booked — shrinking a
    slot under its own bookings would violate `booked <= capacity`, and the
    database would refuse it anyway. Those slots keep their old capacity and the
    admin sees the clinic and the seats disagree, which is true.
    """
    slots = await _future_slots(session, template)
    if not slots:
        return Reconciliation(blocked=0, adjusted=0)

    tz = hospital_tz()
    today = datetime.now(tz).date()
    last = max(slot.starts_at for slot in slots).astimezone(tz).date()
    wanted: dict[datetime, datetime] = {}
    for offset in range((last - today).days + 1):
        for starts_at, ends_at in instants_on(template, today + timedelta(days=offset), tz):
            wanted[starts_at] = ends_at

    blocked = adjusted = 0
    for slot in slots:
        ends_at = wanted.get(slot.starts_at)
        if ends_at is None:
            if slot.booked == 0 and not slot.blocked:
                slot.blocked = True
                blocked += 1
            continue
        slot.ends_at = ends_at
        slot.slot_type = template.slot_type
        if template.capacity >= slot.booked:
            slot.capacity = template.capacity
        if slot.blocked and slot.booked == 0:
            slot.blocked = False
        adjusted += 1
    return Reconciliation(blocked=blocked, adjusted=adjusted)


def _label(template: SlotTemplate) -> str:
    return (
        f"{WEEKDAY_NAMES[template.weekday]} "
        f"{template.start_time.strftime('%H:%M')}–{template.end_time.strftime('%H:%M')} "
        f"({template.slot_type})"
    )


async def _template(session: AsyncSession, template_id: uuid.UUID) -> SlotTemplate:
    template = await session.get(SlotTemplate, template_id)
    if template is None or template.deleted_at is not None:
        raise RosterError("no such clinic")
    return template


# -- writing one clinic --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClinicWrite:
    """The authored shape of one clinic — what a form or a roster row produces."""

    doctor_id: uuid.UUID
    weekday: int
    start_time: time
    end_time: time
    slot_type: SlotType = SlotType.FOLLOW_UP
    capacity: int = 1
    slot_minutes: int = DEFAULT_SLOT_MINUTES

    def validate(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise RosterError(f"weekday {self.weekday} is not a day of the week")
        if self.end_time <= self.start_time:
            raise RosterError("the clinic ends before it starts")
        if self.slot_minutes <= 0:
            raise RosterError("slot length must be positive")
        if self.capacity <= 0:
            raise RosterError("capacity must be at least one seat")
        span = (
            datetime.combine(date(2000, 1, 1), self.end_time)
            - datetime.combine(date(2000, 1, 1), self.start_time)
        ).total_seconds() // 60
        if span < self.slot_minutes:
            raise RosterError(
                f"a {int(span)}-minute clinic cannot hold a {self.slot_minutes}-minute slot"
            )


async def save_clinic(
    session: AsyncSession,
    *,
    write: ClinicWrite,
    template_id: uuid.UUID | None = None,
    acknowledge: bool = False,
) -> tuple[SlotTemplate, ChangeImpact | None]:
    """Create a clinic, or edit one — retiring the inventory the old shape made.

    Editing refuses while patients are booked into the *existing* slots unless the
    caller acknowledges them, for the same reason deactivating a doctor does: the
    admin should see the five people affected before, not discover them after.
    """
    write.validate()
    doctor = await session.get(Doctor, write.doctor_id)
    if doctor is None or doctor.deleted_at is not None:
        raise RosterError("no such doctor")

    impact: ChangeImpact | None = None
    if template_id is not None:
        impact = await change_impact(session, template_id=template_id)
        if impact.needs_a_decision and not acknowledge:
            raise RosterError(
                f"{len(impact.booked)} patient(s) are booked into {impact.label}. "
                "Changing the clinic does not move them — review and confirm."
            )
        template = await _template(session, template_id)
    else:
        clash = (
            await session.execute(
                select(SlotTemplate).where(
                    SlotTemplate.doctor_id == doctor.id,
                    SlotTemplate.weekday == write.weekday,
                    SlotTemplate.start_time == write.start_time,
                    SlotTemplate.slot_type == write.slot_type,
                    SlotTemplate.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            # The unique constraint would refuse this anyway; saying which clinic
            # it collided with is the difference between a fixable message and a
            # 500 from a constraint name.
            raise RosterError(
                f"{doctor.name} already has a {_label(clash)} clinic — edit that one instead"
            )
        template = SlotTemplate(doctor_id=doctor.id, department_id=doctor.department_id)
        session.add(template)

    template.department_id = doctor.department_id
    template.weekday = write.weekday
    template.start_time = write.start_time
    template.end_time = write.end_time
    template.slot_minutes = write.slot_minutes
    template.capacity = write.capacity
    template.slot_type = write.slot_type
    template.active = True
    await session.flush()

    # Reconcile *after* the template carries its new shape, so the instants come
    # from the same function generation uses (`instants_on`) rather than from a
    # second copy of the rule.
    reconciled = await _reconcile(session, template) if template_id else Reconciliation(0, 0)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.UPDATE if template_id else AuditAction.CREATE,
        entity=SlotTemplate.__tablename__,
        entity_id=template.id,
        meta={
            "doctor": doctor.reg_no,
            "clinic": _label(template),
            "slots_blocked": reconciled.blocked,
            "slots_adjusted": reconciled.adjusted,
        },
    )
    return template, impact


async def retire_clinic(
    session: AsyncSession, *, template_id: uuid.UUID, acknowledge: bool = False
) -> ChangeImpact:
    """Stop a clinic. Soft, like everything else here: the template deactivates,
    its empty future slots block, and its booked ones stand."""
    impact = await change_impact(session, template_id=template_id)
    if impact.needs_a_decision and not acknowledge:
        raise RosterError(
            f"{len(impact.booked)} patient(s) are booked into {impact.label}. "
            "Stopping the clinic does not cancel them — review and confirm."
        )
    template = await _template(session, template_id)
    blocked = await _block_stale_slots(session, template)
    template.active = False
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=SlotTemplate.__tablename__,
        entity_id=template.id,
        meta={"active": False, "slots_blocked": blocked, "clinic": impact.label},
    )
    return impact


# -- the import ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RosterRow:
    """One line of the uploaded file, before it means anything.

    `line` is the number the administrator sees in their spreadsheet — header
    included, 1-based — because "row 14 names a doctor we do not have" is only
    actionable if row 14 is the row they can go and look at.
    """

    line: int
    raw: dict[str, str]


@dataclass(frozen=True, slots=True)
class PlannedClinic:
    line: int
    doctor_label: str
    doctor_name: str | None
    department_code: str | None
    weekday_name: str
    start: str
    end: str
    slot_type: str
    capacity: int
    slot_minutes: int
    slots_per_week: int
    #: "create" | "update" | "unchanged" | "error"
    action: str
    error: str | None = None
    template_id: uuid.UUID | None = None
    write: ClinicWrite | None = None


@dataclass(frozen=True, slots=True)
class RosterPlan:
    """What an import would do, row by row. The dry run *is* this object."""

    rows: list[PlannedClinic]

    @property
    def errors(self) -> list[PlannedClinic]:
        return [r for r in self.rows if r.action == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)

    def counts(self) -> dict[str, int]:
        out = {"create": 0, "update": 0, "unchanged": 0, "error": 0}
        for row in self.rows:
            out[row.action] = out.get(row.action, 0) + 1
        return out


def read_rows(content: bytes, filename: str = "") -> list[RosterRow]:
    """CSV or XLSX → rows keyed by normalised column name.

    XLSX is read without a spreadsheet library: an .xlsx is a zip of XML, and the
    only sheet shape this needs is "the first worksheet's cells as strings".
    Pulling in a full workbook parser to read six columns would add a dependency
    with a much larger surface than the thing it parses.
    """
    if filename.lower().endswith((".xlsx", ".xlsm")):
        table = _read_xlsx(content)
    else:
        table = _read_csv(content)
    if not table:
        raise RosterError("the file is empty")

    header = [_COLUMNS.get(_key(cell), "") for cell in table[0]]
    missing = [column for column in REQUIRED if column not in header]
    if missing:
        raise RosterError(
            "the file needs a column for " + ", ".join(missing) + " — "
            "expected columns: doctor, weekday, start, end, slot_type, capacity"
        )

    rows: list[RosterRow] = []
    for offset, cells in enumerate(table[1:], start=2):
        record = {
            column: (cells[index].strip() if index < len(cells) else "")
            for index, column in enumerate(header)
            if column
        }
        if not any(record.values()):
            continue  # a blank line in a spreadsheet is not a row
        rows.append(RosterRow(line=offset, raw=record))
    if not rows:
        raise RosterError("the file has a header but no clinics")
    return rows


def _key(cell: str) -> str:
    return cell.strip().lower().replace(" ", "_").replace("-", "_")


def _read_csv(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RosterError("the file is not UTF-8 text — save it as CSV UTF-8") from exc
    return [row for row in csv.reader(io.StringIO(text))]


def _read_xlsx(content: bytes) -> list[list[str]]:
    """The first worksheet, as strings. Enough of the format and no more.

    Handles the two ways a cell holds text (inline, or an index into the shared
    string table) and leaves numbers and times as the stored value — which is why
    a time in a spreadsheet is safest typed as text, and why `_parse_time` also
    accepts Excel's fractional-day serial.
    """
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise RosterError("that .xlsx could not be read — try exporting it as CSV") from exc

    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        strings = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{ns}t")) for si in strings]

    table: list[list[str]] = []
    for row in sheet.iter(f"{ns}row"):
        cells: list[str] = []
        for cell in row.iter(f"{ns}c"):
            # Column letters, so a gap in the middle of a row stays a gap rather
            # than shifting every later column left onto the wrong header.
            ref = "".join(ch for ch in (cell.get("r") or "") if ch.isalpha())
            index = _column_index(ref)
            while len(cells) < index:
                cells.append("")
            if cell.get("t") == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{ns}t"))
            else:
                raw = cell.find(f"{ns}v")
                value = raw.text or "" if raw is not None else ""
                if cell.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
            cells.append(value)
        table.append(cells)
    return table


def _column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - 64)
    return max(0, index - 1)


def _parse_time(raw: str) -> time:
    value = raw.strip()
    if not value:
        raise ValueError("missing")
    # Excel stores a bare time as a fraction of a day; a roster exported without
    # text formatting arrives as "0.4375" rather than "10:30".
    if value.replace(".", "", 1).isdigit() and "." in value:
        minutes = round(float(value) * 24 * 60)
        return time(hour=(minutes // 60) % 24, minute=minutes % 60)
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%H.%M"):
        try:
            return datetime.strptime(value.upper().replace(" AM", " AM"), fmt).time()
        except ValueError:
            continue
    raise ValueError(f"{raw!r} is not a time — use 24-hour HH:MM")


def _parse_weekday(raw: str) -> int:
    value = raw.strip().lower()
    if value.isdigit():
        day = int(value)
        if not 0 <= day <= 6:
            raise ValueError(f"{raw!r} is not a weekday — 0 is Monday, 6 is Sunday")
        return day
    if value in _WEEKDAYS:
        return _WEEKDAYS[value]
    raise ValueError(f"{raw!r} is not a weekday")


def _parse_slot_type(raw: str) -> SlotType:
    value = _key(raw) or "follow_up"
    aliases = {
        "new": SlotType.NEW_CONSULT,
        "new_consult": SlotType.NEW_CONSULT,
        "new_patient": SlotType.NEW_CONSULT,
        "consult": SlotType.NEW_CONSULT,
        "follow_up": SlotType.FOLLOW_UP,
        "followup": SlotType.FOLLOW_UP,
        "review": SlotType.FOLLOW_UP,
        "chemo": SlotType.CHEMO_REVIEW,
        "chemo_review": SlotType.CHEMO_REVIEW,
    }
    if value not in aliases:
        raise ValueError(f"{raw!r} is not a slot type — use new_consult, follow_up or chemo_review")
    return aliases[value]


def _parse_int(raw: str, *, default: int, label: str) -> int:
    value = raw.strip()
    if not value:
        return default
    try:
        number = int(float(value))
    except ValueError as exc:
        raise ValueError(f"{label} {raw!r} is not a number") from exc
    if number <= 0:
        raise ValueError(f"{label} must be at least 1")
    return number


async def plan_roster(session: AsyncSession, rows: list[RosterRow]) -> RosterPlan:
    """Resolve every row against the database. Writes nothing.

    Every row is evaluated even after one fails, because an administrator fixing a
    file wants all of its problems at once rather than one upload per typo.
    """
    doctors = (
        await session.execute(
            select(Doctor, Department)
            .join(Department, Department.id == Doctor.department_id)
            .where(Doctor.deleted_at.is_(None), Doctor.active.is_(True))
        )
    ).all()
    by_reg = {doctor.reg_no.strip().lower(): (doctor, dept) for doctor, dept in doctors}
    by_name: dict[str, list[tuple[Doctor, Department]]] = {}
    for doctor, dept in doctors:
        by_name.setdefault(_name_key(doctor.name), []).append((doctor, dept))

    existing = {
        (t.doctor_id, t.weekday, t.start_time, t.slot_type): t
        for t in (
            await session.execute(select(SlotTemplate).where(SlotTemplate.deleted_at.is_(None)))
        )
        .scalars()
        .all()
    }

    planned: list[PlannedClinic] = []
    for row in rows:
        planned.append(_plan_row(row, by_reg, by_name, existing))
    return RosterPlan(rows=planned)


def _name_key(name: str) -> str:
    """Match "Dr. Anil Gupta" to "Dr Anil Gupta" and "anil gupta"."""
    cleaned = name.strip().lower().replace(".", " ")
    words = [w for w in cleaned.split() if w not in {"dr", "doctor", "prof"}]
    return " ".join(words)


def _plan_row(
    row: RosterRow,
    by_reg: dict[str, tuple[Doctor, Department]],
    by_name: dict[str, list[tuple[Doctor, Department]]],
    existing: dict[tuple[uuid.UUID, int, time, SlotType], SlotTemplate],
) -> PlannedClinic:
    raw = row.raw
    label = raw.get("doctor", "")

    def failed(message: str) -> PlannedClinic:
        return PlannedClinic(
            line=row.line,
            doctor_label=label,
            doctor_name=None,
            department_code=None,
            weekday_name=raw.get("weekday", ""),
            start=raw.get("start", ""),
            end=raw.get("end", ""),
            slot_type=raw.get("slot_type", ""),
            capacity=0,
            slot_minutes=0,
            slots_per_week=0,
            action="error",
            error=message,
        )

    key = label.strip().lower()
    found = by_reg.get(key)
    if found is None:
        candidates = by_name.get(_name_key(label), [])
        if len(candidates) > 1:
            return failed(
                f"row {row.line}: more than one doctor is called {label!r} — "
                "use the registration number instead"
            )
        found = candidates[0] if candidates else None
    if found is None:
        return failed(
            f"row {row.line}: no doctor called {label!r} — create them first, "
            "or check the registration number"
        )
    doctor, department = found

    try:
        weekday = _parse_weekday(raw.get("weekday", ""))
        start = _parse_time(raw.get("start", ""))
        end = _parse_time(raw.get("end", ""))
        slot_type = _parse_slot_type(raw.get("slot_type", ""))
        capacity = _parse_int(raw.get("capacity", ""), default=1, label="capacity")
        minutes = _parse_int(
            raw.get("slot_minutes", ""), default=DEFAULT_SLOT_MINUTES, label="slot length"
        )
    except ValueError as exc:
        return failed(f"row {row.line}: {exc}")

    write = ClinicWrite(
        doctor_id=doctor.id,
        weekday=weekday,
        start_time=start,
        end_time=end,
        slot_type=slot_type,
        capacity=capacity,
        slot_minutes=minutes,
    )
    try:
        write.validate()
    except RosterError as exc:
        return failed(f"row {row.line}: {exc}")

    current = existing.get((doctor.id, weekday, start, slot_type))
    if current is None:
        action = "create"
    elif (
        current.end_time == end
        and current.capacity == capacity
        and current.slot_minutes == minutes
        and current.active
    ):
        action = "unchanged"
    else:
        action = "update"

    span = (
        datetime.combine(date(2000, 1, 1), end) - datetime.combine(date(2000, 1, 1), start)
    ).total_seconds() // 60

    return PlannedClinic(
        line=row.line,
        doctor_label=label,
        doctor_name=doctor.name,
        department_code=department.code,
        weekday_name=WEEKDAY_NAMES[weekday],
        start=start.strftime("%H:%M"),
        end=end.strftime("%H:%M"),
        slot_type=str(slot_type),
        capacity=capacity,
        slot_minutes=minutes,
        slots_per_week=int(span) // minutes,
        action=action,
        template_id=current.id if current is not None else None,
        write=write,
    )


@dataclass(frozen=True, slots=True)
class ImportResult:
    created: int
    updated: int
    unchanged: int
    slots_generated: int
    #: Patients sitting in inventory an updated clinic just retired. Empty in the
    #: ordinary case; not empty is the case an admin must be shown.
    disturbed: list[BookedAppointment] = field(default_factory=list)


async def apply_roster(
    session: AsyncSession,
    plan: RosterPlan,
    *,
    generate: bool = True,
    horizon_days: int = 60,
    acknowledge: bool = False,
) -> ImportResult:
    """Write a plan that has no errors, and optionally materialise the inventory.

    Refuses a plan containing any error row — see the module docstring on why a
    half-applied roster is worse than none.
    """
    if plan.errors:
        first = plan.errors[0]
        raise RosterError(
            f"{len(plan.errors)} row(s) cannot be imported and nothing was written. "
            f"First problem — {first.error}"
        )
    if not plan.rows:
        raise RosterError("nothing to import")

    disturbed: list[BookedAppointment] = []
    created = updated = unchanged = 0
    for row in plan.rows:
        if row.action == "unchanged":
            unchanged += 1
            continue
        assert row.write is not None
        _, impact = await save_clinic(
            session,
            write=row.write,
            template_id=row.template_id,
            acknowledge=acknowledge,
        )
        if impact is not None:
            disturbed.extend(impact.booked)
        if row.action == "create":
            created += 1
        else:
            updated += 1

    generated = 0
    if generate:
        today = datetime.now(hospital_tz()).date()
        generated = len(await generate_slots(session, start=today, days=horizon_days))

    logger.info(
        "roster import: %d created, %d updated, %d unchanged, %d slots generated",
        created,
        updated,
        unchanged,
        generated,
    )
    return ImportResult(
        created=created,
        updated=updated,
        unchanged=unchanged,
        slots_generated=generated,
        disturbed=disturbed,
    )


# -- generating inventory ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GenerationResult:
    created: int
    start: date
    days: int
    doctor_id: uuid.UUID | None


async def generate(
    session: AsyncSession,
    *,
    doctor_id: uuid.UUID | None = None,
    start: date | None = None,
    days: int = 60,
) -> GenerationResult:
    """The console's Generate button, over S15's own idempotent generator.

    Safe to press twice: an instant that already has a slot is skipped, so this
    never duplicates inventory or resets a booking count.
    """
    if days <= 0 or days > 366:
        raise RosterError("generate between 1 and 366 days")
    begins = start or datetime.now(hospital_tz()).date()
    created = await generate_slots(session, start=begins, days=days, doctor_id=doctor_id)
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=AppointmentSlot.__tablename__,
        meta={"generated": len(created), "days": days, "from": begins.isoformat()},
    )
    return GenerationResult(created=len(created), start=begins, days=days, doctor_id=doctor_id)


def sample_csv() -> str:
    """The file the console offers as a download, so an admin starts from a
    working example rather than from a column list in a paragraph."""
    lines = ["doctor,weekday,start,end,slot_type,capacity,slot_minutes"]
    lines.append("RMC-ONC-1001,Tuesday,10:00,13:00,follow_up,2,15")
    lines.append("RMC-ONC-1001,Thursday,09:30,11:30,new_consult,1,30")
    return "\n".join(lines) + "\n"


def upcoming_dates(weekday: int, *, count: int = 3, after: date | None = None) -> list[date]:
    """The next few dates a weekly clinic would actually run — what the console
    shows next to "Tuesdays 10:00", because a weekday name is not a date and an
    admin checking their work wants the date."""
    start = after or datetime.now(hospital_tz()).date()
    first = start + timedelta(days=(weekday - start.weekday()) % 7)
    return [first + timedelta(days=7 * i) for i in range(count)]
