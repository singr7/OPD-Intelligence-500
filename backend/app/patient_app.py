"""The patient's own view of the system (doc 03 §1c) — the Android app's service layer.

Everything a patient sees on her phone is *derived from rows other sessions
already write*. Nothing here is a second source of truth:

* the care file is S11's signed prescriptions and S5's stored summaries,
* the queue position is S8's `QueueEntry` in S8's own order,
* appointments are S15's `app.scheduling`, rules and all,
* the home intake is S5's `IntakeEngine` over S4's trees.

Two things are genuinely new, because no other channel needed them:

**Identity.** Every other channel is anonymous (a kiosk walk-in) or staff-held (a
console). The app is the first surface where a *patient* authenticates, so
`profiles_for_phone` resolves a phone number to the files it may open — her own,
plus any patient who has granted that number caregiver access.

**Adherence.** The phone owns its alarms; the server owns the consequence. The app
reports what happened to a dose (`record_dose`), and a missed one pings the
caregiver over the same provider layer everything else uses.

The scoping rule this module exists to enforce: **a patient id is never taken from
a request body**. It comes from the token (`PatientPrincipal.patient_id`), and
every read below takes it as a keyword argument, so a route that forgets to scope
does not compile into something that returns another patient's file.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import prescription as rx_svc
from app import queue as queue_svc
from app import scheduling
from app.models.clinical import DoseEvent, Intake, Prescription, Visit
from app.models.enums import (
    CaregiverLinkStatus,
    Channel,
    DoseStatus,
    Lang,
    QueueEntryState,
    SlotType,
    VisitStatus,
)
from app.models.org import Department, Doctor
from app.models.patient import CaregiverLink, Patient
from app.models.scheduling import Appointment, Queue, QueueEntry
from app.providers.base import ProviderError
from app.providers.registry import get_sms_provider
from app.providers.sms import SmsMessage

logger = logging.getLogger(__name__)


class PatientAppError(Exception):
    """A patient-facing failure that is safe to show the patient."""


# -- who may open which file ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Profile:
    """One file a phone number may open, and on what footing."""

    patient: Patient
    via: Literal["self", "caregiver"]
    relation: str | None = None


async def profiles_for_phone(session: AsyncSession, phone: str) -> list[Profile]:
    """Every care file `phone` is entitled to open, the patient's own first.

    A number can legitimately reach several files — a rural household shares one
    handset, and a son is caregiver to both parents. The app makes the holder
    pick; it never merges two patients into one screen.

    Only `patients.phone` / `alt_phone` count as "self". `caregiver_phone` does
    **not**: it is a contact number captured at a registration desk, and treating
    a contact as a login would hand a cancer file to whoever answered that day.
    Caregiver access is exactly the `caregiver_links` this patient approved.
    """
    if not phone:
        return []

    profiles: list[Profile] = []
    seen: set[uuid.UUID] = set()

    own = await session.execute(
        select(Patient)
        .where(
            Patient.deleted_at.is_(None),
            (Patient.phone == phone) | (Patient.alt_phone == phone),
        )
        .order_by(Patient.created_at)
    )
    for patient in own.scalars().all():
        # Kiosk walk-ins carry a generated MRN and an empty phone; they cannot
        # match here, and must not — they have no identity to log in as.
        profiles.append(Profile(patient=patient, via="self"))
        seen.add(patient.id)

    linked = await session.execute(
        select(CaregiverLink, Patient)
        .join(Patient, CaregiverLink.patient_id == Patient.id)
        .where(
            CaregiverLink.phone == phone,
            CaregiverLink.status == CaregiverLinkStatus.ACTIVE,
            CaregiverLink.deleted_at.is_(None),
            Patient.deleted_at.is_(None),
        )
        .order_by(CaregiverLink.consented_at)
    )
    for link, patient in linked.all():
        if patient.id in seen:
            continue
        profiles.append(Profile(patient=patient, via="caregiver", relation=link.relation))
        seen.add(patient.id)

    return profiles


async def profile_for(
    session: AsyncSession, *, phone: str, patient_id: uuid.UUID
) -> Profile | None:
    """The one profile `phone` may open for `patient_id`, or None.

    The check behind profile switching: re-resolved from the database rather than
    from anything the client sent, so switching cannot be an id-guessing game.
    """
    for profile in await profiles_for_phone(session, phone):
        if profile.patient.id == patient_id:
            return profile
    return None


# -- My Cancer Care File -------------------------------------------------------


@dataclass(slots=True)
class FileEntry:
    """One thing in the file: a prescription, or a visit's summary."""

    kind: Literal["prescription", "summary"]
    id: uuid.UUID
    visit_id: uuid.UUID
    at: datetime
    department: str
    doctor: str | None
    #: Prescription rows only: the frozen `RxLine` dicts S11 wrote at signing.
    meds: list[dict[str, Any]] = field(default_factory=list)
    #: Summary rows only: the doc 03 §4 summary in the patient's own language.
    summary_md: str | None = None
    chief_complaint: str | None = None
    red_flags: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CareFile:
    patient: Patient
    entries: list[FileEntry]
    #: The newest `updated_at` across everything in the file — the app's sync
    #: cursor and the route's ETag. A file that has not changed costs one 304 on
    #: a 2G connection instead of a payload.
    revision: datetime | None


async def care_file(session: AsyncSession, *, patient_id: uuid.UUID, limit: int = 50) -> CareFile:
    """Every prescription and every intake summary this patient has, newest first.

    This is the feature the patient installs the app for (doc 03 §1c.1: the
    plastic bag of papers). It is deliberately *one* payload with no pagination
    cursor to lose: a pilot patient has tens of rows, not thousands, and the
    whole point is that the phone holds all of it offline.
    """
    patient = await session.get(Patient, patient_id)
    if patient is None or patient.is_deleted:
        raise PatientAppError("no such patient")

    entries: list[FileEntry] = []
    revision: datetime | None = None

    def bump(at: datetime | None) -> None:
        nonlocal revision
        if at is not None and (revision is None or at > revision):
            revision = at

    visits = await session.execute(
        select(Visit, Department, Doctor)
        .join(Department, Visit.department_id == Department.id)
        .outerjoin(Doctor, Visit.doctor_id == Doctor.id)
        .where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
        .order_by(Visit.date.desc())
        .limit(limit)
    )
    context: dict[uuid.UUID, tuple[Visit, Department, Doctor | None]] = {}
    for visit, department, doctor in visits.all():
        context[visit.id] = (visit, department, doctor)

    if context:
        intakes = await session.execute(
            select(Intake)
            .where(
                Intake.visit_id.in_(list(context)),
                Intake.deleted_at.is_(None),
                Intake.completed_at.is_not(None),
            )
            .order_by(Intake.completed_at.desc())
        )
        for intake in intakes.scalars().all():
            visit, department, doctor = context[intake.visit_id]
            # The patient's own language wins: `summary_lang_versions` is where
            # S5 stores the translated read-back, and a file the patient cannot
            # read is not a file (doc 04 law 1).
            versions = intake.summary_lang_versions or {}
            summary = versions.get(str(patient.lang)) or intake.summary_md
            entries.append(
                FileEntry(
                    kind="summary",
                    id=intake.id,
                    visit_id=visit.id,
                    at=intake.completed_at or datetime.combine(visit.date, _MIDNIGHT),
                    department=department.name,
                    doctor=doctor.name if doctor else None,
                    summary_md=summary,
                    chief_complaint=intake.chief_complaint,
                    red_flags=list(intake.red_flags or []),
                )
            )
            bump(intake.updated_at)

    for prescription, visit in await rx_svc.history(session, patient_id=patient_id, limit=limit):
        _, department, doctor = context.get(visit.id, (visit, None, None))
        entries.append(
            FileEntry(
                kind="prescription",
                id=prescription.id,
                visit_id=visit.id,
                at=prescription.created_at,
                department=department.name if department else "",
                doctor=doctor.name if doctor else None,
                meds=[line.to_dict() for line in rx_svc.lines_of(prescription)],
            )
        )
        bump(prescription.updated_at)

    entries.sort(key=lambda e: e.at, reverse=True)
    return CareFile(patient=patient, entries=entries, revision=revision)


_MIDNIGHT = datetime.min.time().replace(tzinfo=UTC)


# -- live queue position -------------------------------------------------------


@dataclass(slots=True)
class QueuePosition:
    """Where the patient stands right now (doc 03 §1c.3)."""

    visit_id: uuid.UUID
    token_no: int
    department: str
    state: QueueEntryState
    #: How many people are genuinely in front — computed in the queue's own
    #: order, so an urgent token that jumped the line is counted where it is,
    #: not where its number suggests.
    ahead: int
    est_wait_low: int
    est_wait_high: int
    #: When to walk out of the door, given `travel_minutes`. None once the
    #: patient has been called — there is nothing left to leave for.
    leave_by: datetime | None
    now_serving: int | None


async def queue_position(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    travel_minutes: int = 0,
    on: date_type | None = None,
    now: datetime | None = None,
) -> QueuePosition | None:
    """This patient's place in today's queue, or None if she is not in one.

    The ordering is not re-derived here. `app.queue.department_queue` already
    ranks the room the way the board and the coordinator's console rank it, and
    a patient screen that counted differently would be a second opinion about
    whose turn it is — the one thing a queue cannot have.
    """
    on = on or queue_svc.today()
    now = now or datetime.now(UTC)

    entry_row = await session.execute(
        select(QueueEntry, Visit, Department)
        .join(Visit, QueueEntry.visit_id == Visit.id)
        .join(Queue, QueueEntry.queue_id == Queue.id)
        .join(Department, Visit.department_id == Department.id)
        .where(
            Visit.patient_id == patient_id,
            Queue.date == on,
            QueueEntry.state.notin_((QueueEntryState.DONE, QueueEntryState.NO_SHOW)),
        )
        .order_by(QueueEntry.created_at.desc())
        .limit(1)
    )
    found = entry_row.first()
    if found is None:
        return None
    entry, visit, department = found

    ordered = await queue_svc.department_queue(session, department_id=visit.department_id, on=on)
    waiting = [view for view in ordered if view.state == QueueEntryState.WAITING]
    ahead = next((i for i, view in enumerate(waiting) if view.id == entry.id), len(waiting))

    mean = await queue_svc._mean_consult_minutes(session, queue_id=entry.queue_id)
    low, high = queue_svc.estimate_wait(ahead=ahead, mean_minutes=mean)

    serving = next(
        (view.token_no for view in ordered if view.state == QueueEntryState.IN_CONSULT),
        next((view.token_no for view in ordered if view.state == QueueEntryState.CALLED), None),
    )

    leave_by: datetime | None = None
    if entry.state == QueueEntryState.WAITING:
        # Leave for the *low* end of the range, minus travel. Being early in a
        # waiting room is an inconvenience; being late is a lost token and a
        # wasted 200km, so the estimate is deliberately pessimistic about the
        # patient's own time and never about the hospital's.
        leave_by = now + timedelta(minutes=max(0, low - travel_minutes))

    return QueuePosition(
        visit_id=visit.id,
        token_no=entry.token_no,
        department=department.name,
        state=entry.state,
        ahead=ahead,
        est_wait_low=low,
        est_wait_high=high,
        leave_by=leave_by,
        now_serving=serving,
    )


# -- home intake + arrival -----------------------------------------------------


async def open_visit_for(
    session: AsyncSession,
    *,
    patient: Patient,
    department: Department,
    lang: Lang,
    on: date_type | None = None,
) -> Visit:
    """The visit a home intake attaches to (doc 03 §1c.2).

    Unlike a kiosk walk-in this does **not** invent a patient — she is logged in.
    An intake done at home tonight is for tomorrow's visit, so the visit is
    created `registered` with **no token**: the token is issued on arrival
    (`arrive`), which is the whole promise ("skip the kiosk queue, get a token
    faster on arrival"), and issuing one tonight would put an absent patient on
    the board and have her called while she is still in her village.
    """
    on = on or datetime.now(UTC).date()
    existing = await session.scalar(
        select(Visit)
        .where(
            Visit.patient_id == patient.id,
            Visit.department_id == department.id,
            Visit.date == on,
            Visit.deleted_at.is_(None),
            Visit.status.in_((VisitStatus.REGISTERED, VisitStatus.INTAKE_DONE)),
        )
        .order_by(Visit.created_at.desc())
    )
    if existing is not None:
        return existing

    visit = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=on,
        status=VisitStatus.REGISTERED,
        channel=Channel.APP,
    )
    session.add(visit)
    await session.flush()
    return visit


@dataclass(slots=True)
class Arrival:
    token_no: int
    department: str
    position: QueuePosition | None
    already_queued: bool


async def arrive(
    session: AsyncSession, *, patient_id: uuid.UUID, visit_id: uuid.UUID | None = None
) -> Arrival:
    """ "I am at the hospital" — turn a completed intake into a real token.

    This is where a home intake finally costs the patient nothing at the desk: the
    answers are already in, so arrival is a token and a place in the queue, taken
    with the *same* red-flag priority a kiosk intake would have earned
    (`enqueue_from_intake` — the rules decide urgency, arrival does not).

    Idempotent: a patient who taps twice, or who arrives with the app and then
    again at the desk, keeps one token.
    """
    stmt = (
        select(Visit)
        .where(
            Visit.patient_id == patient_id,
            Visit.deleted_at.is_(None),
            Visit.date == datetime.now(UTC).date(),
            Visit.status.in_(
                (VisitStatus.REGISTERED, VisitStatus.INTAKE_DONE, VisitStatus.IN_QUEUE)
            ),
        )
        .order_by(Visit.created_at.desc())
    )
    if visit_id is not None:
        stmt = stmt.where(Visit.id == visit_id)
    visit = await session.scalar(stmt)
    if visit is None:
        raise PatientAppError("no visit to check in for today")

    intake = await session.scalar(
        select(Intake)
        .where(Intake.visit_id == visit.id, Intake.deleted_at.is_(None))
        .order_by(Intake.created_at.desc())
    )
    if intake is None or intake.completed_at is None:
        raise PatientAppError("finish your questions before checking in")

    already = visit.token_no is not None
    from app import kiosk as kiosk_svc

    token_no = await kiosk_svc.allocate_token(session, visit)
    await queue_svc.enqueue_from_intake(session, visit=visit, intake=intake)

    department = await session.get(Department, visit.department_id)
    position = await queue_position(session, patient_id=patient_id)
    return Arrival(
        token_no=token_no,
        department=department.name if department else "",
        position=position,
        already_queued=already,
    )


# -- medicine reminders --------------------------------------------------------


@dataclass(slots=True)
class DoseTime:
    """One clock time the app should ring for, and what to say."""

    med_index: int
    #: The drug exactly as the doctor said it (`MedLine.name`) — never a
    #: formulary generic swapped in behind the patient's back.
    drug: str
    dose: str | None
    route: str | None
    duration: str | None
    #: "morning" | "afternoon" | "night" | "unscheduled"
    slot: str
    #: 24h local time as "HH:MM", or None when the doctor's words gave no time
    #: and the app must ask the patient to choose one.
    at: str | None


@dataclass(slots=True)
class ReminderPlan:
    prescription_id: uuid.UUID | None
    prescribed_on: date_type | None
    doses: list[DoseTime]
    #: Drugs whose frequency the doctor's words did not pin to a time. The app
    #: shows them as "your doctor said: <words>" and never invents a schedule —
    #: the same rule the printed prescription obeys (S11).
    unscheduled: list[str]


#: Default clock times per slot. Config, not clinical: they are when a household
#: in Alwar eats, and the app lets the patient move each one. `parse_schedule`
#: decides *which* slots exist; this only decides where the alarm lands.
SLOT_TIMES = {"morning": "08:00", "afternoon": "14:00", "night": "20:00"}


async def reminder_plan(session: AsyncSession, *, patient_id: uuid.UUID) -> ReminderPlan:
    """The alarms the phone should set, from the newest prescription.

    Reads the *frozen* schedule S11 stored at signing (`lines_of` does not
    re-parse), so a phone that rings at 8pm is ringing for what the doctor said,
    not for what today's parser would make of the same words. A drug whose
    frequency was unreadable ("SOS", "alternate days") produces no alarm at all
    and is handed back as `unscheduled` — an invented dose time is exactly the
    failure mode the prescription session was built to prevent.
    """
    history = await rx_svc.history(session, patient_id=patient_id, limit=1)
    if not history:
        return ReminderPlan(prescription_id=None, prescribed_on=None, doses=[], unscheduled=[])

    prescription, visit = history[0]
    doses: list[DoseTime] = []
    unscheduled: list[str] = []

    for index, line in enumerate(rx_svc.lines_of(prescription)):
        med = line.med
        schedule = line.schedule
        if schedule is None:
            unscheduled.append(med.name)
            continue

        slots = [
            name
            for name, on in (
                ("morning", schedule.morning),
                ("afternoon", schedule.afternoon),
                ("night", schedule.night),
            )
            if on
        ]
        if slots and schedule.slots_known:
            for slot in slots:
                doses.append(
                    DoseTime(
                        med_index=index,
                        drug=med.name,
                        dose=med.dose,
                        route=med.route,
                        duration=med.duration,
                        slot=slot,
                        at=SLOT_TIMES[slot],
                    )
                )
            continue

        # A bare count ("BD", "twice a day") — the doctor said how many, not
        # when. The phone asks the patient to place them; it does not guess.
        per_day = schedule.per_day or len(slots) or 1
        for n in range(per_day):
            doses.append(
                DoseTime(
                    med_index=index,
                    drug=med.name,
                    dose=med.dose,
                    route=med.route,
                    duration=med.duration,
                    slot="unscheduled",
                    at=None,
                )
            )

    return ReminderPlan(
        prescription_id=prescription.id,
        prescribed_on=visit.date,
        doses=doses,
        unscheduled=unscheduled,
    )


#: The missed-dose ping, in the patient's language (mr/te owed a native review —
#: S21, HANDOFF). Addressed to the caregiver, about the patient, and deliberately
#: free of the drug name: an SMS is read by whoever picks the handset up.
MISSED_DOSE_SMS: dict[Lang, str] = {
    Lang.EN: (
        "{hospital}: {patient} has not taken a scheduled medicine today. Please check on them."
    ),
    Lang.HI: "{hospital}: {patient} ने आज एक दवा नहीं ली है। कृपया उनका हाल पूछ लें।",
    Lang.MR: "{hospital}: {patient} यांनी आज एक औषध घेतलेले नाही. कृपया त्यांची विचारपूस करा.",
    Lang.TE: "{hospital}: {patient} ఈరోజు ఒక మందు వేసుకోలేదు. దయచేసి వారిని ఒకసారి కనుక్కోండి.",
}


async def record_dose(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    prescription_id: uuid.UUID,
    med_index: int,
    scheduled_for: datetime,
    status: DoseStatus,
    now: datetime | None = None,
) -> tuple[DoseEvent, bool]:
    """Record what happened to one dose; ping the caregiver on a first miss.

    Returns `(event, pinged)`. Upserts on the natural key, so the phone may
    re-report the same dose as often as a flaky connection makes it — and the
    caregiver is pinged **once**, because `caregiver_notified_at` is on the row
    that the second report finds already there.
    """
    now = now or datetime.now(UTC)

    prescription = await session.get(Prescription, prescription_id)
    if prescription is None or prescription.deleted_at is not None:
        raise PatientAppError("no such prescription")
    visit = await session.get(Visit, prescription.visit_id)
    if visit is None or visit.patient_id != patient_id:
        # The prescription belongs to somebody else. Same message as "no such":
        # a patient app is not a place to confirm other people's record ids.
        raise PatientAppError("no such prescription")

    event = await session.scalar(
        select(DoseEvent).where(
            DoseEvent.prescription_id == prescription_id,
            DoseEvent.med_index == med_index,
            DoseEvent.scheduled_for == scheduled_for,
        )
    )
    if event is None:
        event = DoseEvent(
            patient_id=patient_id,
            prescription_id=prescription_id,
            med_index=med_index,
            scheduled_for=scheduled_for,
            status=status,
            reported_at=now,
        )
        session.add(event)
    else:
        event.status = status
        event.reported_at = now
    await session.flush()

    if status is not DoseStatus.MISSED or event.caregiver_notified_at is not None:
        return event, False

    pinged = await _ping_caregivers(session, patient_id=patient_id)
    if pinged:
        event.caregiver_notified_at = now
        await session.flush()
    return event, pinged


async def _ping_caregivers(session: AsyncSession, *, patient_id: uuid.UUID) -> bool:
    """SMS every active caregiver about a missed dose. Never raises.

    A provider outage must not fail the patient's "I missed it" tap — she did the
    honest thing and the phone should say so. The failure is logged and the row
    keeps `caregiver_notified_at` null, so a later miss tries again.
    """
    patient = await session.get(Patient, patient_id)
    if patient is None:
        return False

    links = await session.execute(
        select(CaregiverLink).where(
            CaregiverLink.patient_id == patient_id,
            CaregiverLink.status == CaregiverLinkStatus.ACTIVE,
            CaregiverLink.deleted_at.is_(None),
        )
    )
    numbers = [link.phone for link in links.scalars().all() if link.phone]
    if not numbers:
        return False

    from app.models.org import Hospital

    hospital = await session.get(Hospital, patient.hospital_id)
    body = MISSED_DOSE_SMS.get(patient.lang, MISSED_DOSE_SMS[Lang.EN]).format(
        hospital=hospital.name_in(patient.lang) if hospital else "OPD",
        patient=patient.name,
    )

    sent = False
    provider = get_sms_provider()
    for number in numbers:
        try:
            await provider.send(
                SmsMessage(to=number, body=body, template_key="missed_dose_caregiver")
            )
            sent = True
        except ProviderError:
            logger.warning("missed-dose ping failed for patient %s", patient_id, exc_info=True)
    return sent


# -- chemo calendar ------------------------------------------------------------

#: Plain-language "what to expect" per regimen family, in all four pilot
#: languages. Content, not code — the same reason the tree bank is JSON on disk.
REGIMEN_NOTES_PATH = Path(__file__).resolve().parents[2] / "seeds" / "regimen_notes.json"

_notes_cache: dict[str, Any] | None = None


def regimen_notes() -> dict[str, Any]:
    global _notes_cache
    if _notes_cache is None:
        _notes_cache = json.loads(REGIMEN_NOTES_PATH.read_text(encoding="utf-8"))
    return _notes_cache


@dataclass(slots=True)
class CycleEntry:
    """One chemo-cycle date and what the days after it usually feel like."""

    appointment_id: uuid.UUID | None
    at: datetime
    doctor: str | None
    department: str
    status: str
    #: Cycle number within this patient's chemo-review series, 1-based. Counted
    #: from her own appointments — the regimen protocol that would state a real
    #: cycle count arrives with the check-in engine (S17).
    cycle_no: int
    title: str
    #: Two or three sentences the app reads aloud with the device's own TTS
    #: (doc 03 §1c.5). Text, not an audio file: it is a tenth of the size, it
    #: works offline, and it is the same string the language QA harness checks.
    expect: list[str]


async def chemo_calendar(
    session: AsyncSession, *, patient_id: uuid.UUID, lang: Lang | None = None
) -> list[CycleEntry]:
    """The patient's chemo-review dates, past and future, with what to expect.

    Derived from `chemo_review` appointments rather than from a regimen record,
    because the regimen lives in the doctor's signed note and the structured
    protocol is S17's. That is an honest limitation, not a placeholder: the dates
    are real, and the advice is generic-but-true rather than personalised.
    """
    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise PatientAppError("no such patient")
    lang = lang or patient.lang

    rows = await session.execute(
        select(Appointment, Doctor, Department)
        .outerjoin(Doctor, Appointment.doctor_id == Doctor.id)
        .join(Department, Appointment.department_id == Department.id)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.deleted_at.is_(None),
            Appointment.slot_type == SlotType.CHEMO_REVIEW,
        )
        .order_by(Appointment.slot_at)
    )

    notes = regimen_notes()
    generic = notes.get("generic", {})
    entries: list[CycleEntry] = []
    for index, (appointment, doctor, department) in enumerate(rows.all(), start=1):
        entries.append(
            CycleEntry(
                appointment_id=appointment.id,
                at=appointment.slot_at,
                doctor=doctor.name if doctor else None,
                department=department.name,
                status=appointment.status.value,
                cycle_no=index,
                title=_localise(generic.get("title", {}), lang),
                expect=[_localise(line, lang) for line in generic.get("expect", [])],
            )
        )
    return entries


def _localise(bundle: dict[str, Any], lang: Lang) -> str:
    if not isinstance(bundle, dict):
        return str(bundle)
    return str(bundle.get(str(lang)) or bundle.get("en") or "")


# -- caregiver links -----------------------------------------------------------


async def caregivers_of(session: AsyncSession, *, patient_id: uuid.UUID) -> list[CaregiverLink]:
    result = await session.execute(
        select(CaregiverLink)
        .where(CaregiverLink.patient_id == patient_id, CaregiverLink.deleted_at.is_(None))
        .order_by(CaregiverLink.created_at)
    )
    return list(result.scalars().all())


async def invite_caregiver(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    phone: str,
    name: str | None,
    relation: str | None,
    now: datetime | None = None,
) -> CaregiverLink:
    """The patient adds a family member. Created **active**, because the patient
    is the one asking — consent is hers to give and she has just given it.

    (The `invited` state exists for the other direction — a caregiver asking to be
    let in — which the app does not offer in S16: an unsolicited invitation is a
    social-engineering surface, and a patient can always add her daughter herself.)
    """
    now = now or datetime.now(UTC)
    existing = await session.scalar(
        select(CaregiverLink).where(
            CaregiverLink.patient_id == patient_id, CaregiverLink.phone == phone
        )
    )
    if existing is not None:
        existing.status = CaregiverLinkStatus.ACTIVE
        existing.consented_at = existing.consented_at or now
        existing.revoked_at = None
        existing.deleted_at = None
        if name:
            existing.name = name
        if relation:
            existing.relation = relation
        await session.flush()
        return existing

    link = CaregiverLink(
        patient_id=patient_id,
        phone=phone,
        name=name,
        relation=relation,
        status=CaregiverLinkStatus.ACTIVE,
        consented_at=now,
    )
    session.add(link)
    await session.flush()
    return link


async def revoke_caregiver(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    link_id: uuid.UUID,
    now: datetime | None = None,
) -> CaregiverLink:
    """Take a caregiver's access away, now.

    A state change rather than a delete: who could see this file and when is part
    of the record. `current_patient` re-reads this state on every request, so the
    caregiver's next screen refresh is her last.
    """
    link = await session.get(CaregiverLink, link_id)
    if link is None or link.patient_id != patient_id or link.deleted_at is not None:
        raise PatientAppError("no such caregiver")
    link.status = CaregiverLinkStatus.REVOKED
    link.revoked_at = now or datetime.now(UTC)
    await session.flush()
    return link


# -- appointments (thin, on purpose) -------------------------------------------


async def upcoming(session: AsyncSession, *, patient_id: uuid.UUID) -> list[Appointment]:
    return await scheduling.upcoming_for_patient(session, patient_id=patient_id)


async def owns_appointment(
    session: AsyncSession, *, patient_id: uuid.UUID, appointment_id: uuid.UUID
) -> Appointment:
    """The appointment, if it is this patient's. Raises otherwise.

    Booking, rescheduling and cancelling all go through `app.scheduling`
    unchanged — the app gets no rules of its own, and a seat released from the
    phone is released exactly the way the receptionist releases one. All this
    adds is the ownership check the staff router does not need."""
    appointment = await session.get(Appointment, appointment_id)
    if (
        appointment is None
        or appointment.deleted_at is not None
        or appointment.patient_id != patient_id
    ):
        raise PatientAppError("no such appointment")
    return appointment


__all__ = [
    "Arrival",
    "CareFile",
    "CycleEntry",
    "DoseTime",
    "FileEntry",
    "PatientAppError",
    "Profile",
    "QueuePosition",
    "ReminderPlan",
    "arrive",
    "care_file",
    "caregivers_of",
    "chemo_calendar",
    "invite_caregiver",
    "open_visit_for",
    "owns_appointment",
    "profile_for",
    "profiles_for_phone",
    "queue_position",
    "record_dose",
    "reminder_plan",
    "revoke_caregiver",
    "upcoming",
]
