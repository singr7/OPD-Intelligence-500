"""Next-cycle reminders — D-2 and D-0 (doc 03 §9).

> "Also handles **next-cycle reminders** (D-2 and D-0 morning) with
>  confirm/reschedule buttons." — doc 03 §9

The other half of continuity. A check-in asks how she is after the last
treatment; this tells her the next one is coming, twice: two days before, so a
family in a village can arrange the travel and the money, and on the morning
itself.

## It reuses the appointment machinery rather than growing its own

If the next cycle is a **booked slot**, the reminder is `app.notify` — the same
function S15's bookings go through, which already sends WhatsApp *and* SMS,
already carries the one-tap `appt:confirm` / `appt:cancel` buttons in window, and
already falls back to a registered template out of it. Building a second
reminder with its own buttons would mean two implementations of "the patient
tapped cancel" and two things to keep in step with Meta.

If the next cycle is only a **date the doctor dictated** with no slot behind it,
there is nothing to confirm or cancel, so the reminder is the `next_cycle_due`
template plus an SMS: it tells her the date and asks her to reply. Turning that
into a booking is the receptionist's job (S15) and the reply lands there.

## Sent once per rung, ever

`CheckinPlan.cycle_reminders` records `{rung, at, channel, status}`. The job runs
hourly and a patient hears from us twice, not twenty-four times — the same guard
`OutboundCall.fallback_sent_at` gives the campaign.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checkins.window import is_quiet
from app.config import Settings, get_settings
from app.models.content import CheckinPlan
from app.models.enums import AppointmentStatus, CheckinPlanStatus, Lang, UsagePurpose
from app.models.org import Doctor, Hospital
from app.models.patient import Patient
from app.models.scheduling import Appointment
from app.notify import format_when, notify_appointment
from app.providers.base import ProviderError, with_fallback
from app.providers.registry import get_messaging_provider, get_sms_provider
from app.providers.sms import SmsMessage
from app.whatsapp.conversation import ConversationStore
from app.whatsapp.templates import TemplateError, template_message

logger = logging.getLogger(__name__)

#: The two rungs doc 03 §9 names, as days before the cycle.
RUNGS: tuple[int, ...] = (2, 0)

#: SMS bodies for the no-appointment case. The WhatsApp side is a template.
_SMS_CYCLE: dict[Lang, str] = {
    Lang.EN: "{hospital}: your next treatment is due on {when}. Please reply on WhatsApp "
    "or call {phone} to fix a time.",
    Lang.HI: "{hospital}: आपका अगला इलाज {when} को है। समय तय करने के लिए WhatsApp पर उत्तर दें "
    "या {phone} पर कॉल करें।",
    Lang.MR: "{hospital}: तुमचा पुढचा उपचार {when} रोजी आहे. वेळ ठरवण्यासाठी WhatsApp वर उत्तर "
    "द्या किंवा {phone} वर कॉल करा.",
    Lang.TE: "{hospital}: మీ తదుపరి చికిత్స {when} నాడు ఉంది. సమయం నిర్ణయించడానికి WhatsAppలో "
    "ప్రత్యుత్తరం ఇవ్వండి లేదా {phone}కు కాల్ చేయండి.",
}


def rung_due(plan: CheckinPlan, *, now: datetime) -> int | None:
    """Which rung, if any, is due for this plan right now.

    D-2 fires once the cycle is two days out, D-0 on the day itself. Both are
    "on or after", not "exactly on", so a job that misses a tick (a restart, a
    quiet-hours deferral) still sends rather than silently skipping the rung.
    """
    if plan.next_cycle_at is None:
        return None
    sent = {entry.get("rung") for entry in plan.cycle_reminders or []}
    days_away = (plan.next_cycle_at.date() - now.astimezone(UTC).date()).days
    for rung in RUNGS:
        if rung in sent:
            continue
        if days_away <= rung:
            # Never remind about a cycle that is already in the past.
            return rung if days_away >= 0 else None
    return None


def _record(plan: CheckinPlan, *, rung: int, channel: str, status: str) -> None:
    plan.cycle_reminders = [
        *(plan.cycle_reminders or []),
        {
            "at": datetime.now(UTC).isoformat(),
            "rung": rung,
            "channel": channel,
            "status": status,
        },
    ]


async def _appointment_near(session: AsyncSession, *, plan: CheckinPlan) -> Appointment | None:
    """A booked slot within a day of the dictated cycle date, if there is one."""
    if plan.next_cycle_at is None:  # pragma: no cover - guarded by the caller
        return None
    window = timedelta(days=1)
    return await session.scalar(
        select(Appointment)
        .where(
            Appointment.patient_id == plan.patient_id,
            Appointment.deleted_at.is_(None),
            Appointment.status.in_([AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED]),
            Appointment.slot_at >= plan.next_cycle_at - window,
            Appointment.slot_at <= plan.next_cycle_at + window,
        )
        .order_by(Appointment.slot_at)
        .limit(1)
    )


async def remind(
    session: AsyncSession,
    *,
    plan: CheckinPlan,
    rung: int,
    conversations: ConversationStore | None = None,
    settings: Settings | None = None,
) -> bool:
    """Send one rung of one plan's cycle reminder. Never raises."""
    settings = settings or get_settings()
    patient = await session.get(Patient, plan.patient_id)
    if patient is None or not patient.phone:  # pragma: no cover - FK-guarded
        _record(plan, rung=rung, channel="none", status="no phone")
        return False
    hospital = await session.get(Hospital, patient.hospital_id)
    hospital_name = hospital.name if hospital is not None else "the hospital"

    appointment = await _appointment_near(session, plan=plan)
    if appointment is not None:
        # A booked slot: S15's confirmation is exactly this message, buttons and
        # all. One implementation of "the patient tapped cancel".
        in_window = False
        if conversations is not None:
            conversation = await conversations.get(patient.phone)
            in_window = conversation is not None and conversation.within_window()
        doctor = await session.get(Doctor, appointment.doctor_id)
        delivery = await notify_appointment(
            session,
            appointment=appointment,
            patient=patient,
            hospital_name=hospital_name,
            doctor_name=doctor.name if doctor is not None else "your doctor",
            hospital_phone=settings.coordinator_phone,
            kind="booked",
            in_window=in_window,
        )
        _record(
            plan,
            rung=rung,
            channel="appointment",
            status="sent" if delivery.any_delivered else "failed",
        )
        return delivery.any_delivered

    lang = patient.lang or plan.lang
    when = format_when(plan.next_cycle_at, lang)  # type: ignore[arg-type]
    sent = False
    try:
        await with_fallback(
            [get_messaging_provider(settings)],
            lambda p: p.send(
                template_message(
                    to=patient.phone,
                    name="next_cycle_due",
                    lang=lang,
                    variables=(patient.name, hospital_name, when),
                ),
                purpose=UsagePurpose.CHECKIN,
            ),
        )
        sent = True
        _record(plan, rung=rung, channel="whatsapp", status="sent")
    except (ProviderError, TemplateError) as exc:
        _record(plan, rung=rung, channel="whatsapp", status="failed")
        logger.warning("cycle reminder (whatsapp) for plan %s failed: %s", plan.id, exc)

    body = _SMS_CYCLE.get(lang, _SMS_CYCLE[Lang.EN]).format(
        hospital=hospital_name, when=when, phone=settings.coordinator_phone or "the OPD"
    )
    try:
        await get_sms_provider(settings).send(
            SmsMessage(to=patient.phone, body=body, template_key="next_cycle_due"),
            purpose=UsagePurpose.CHECKIN,
        )
        sent = True
        _record(plan, rung=rung, channel="sms", status="sent")
    except ProviderError as exc:
        _record(plan, rung=rung, channel="sms", status="failed")
        logger.warning("cycle reminder (sms) for plan %s failed: %s", plan.id, exc)

    await session.flush()
    return sent


async def send_due_reminders(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 200,
    conversations: ConversationStore | None = None,
    settings: Settings | None = None,
) -> list[CheckinPlan]:
    """One beat tick's worth of cycle reminders."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    if not settings.checkins_enabled:
        return []
    if is_quiet(now, settings=settings):
        # Deferred, not skipped: `rung_due` is "on or after", so the same rung is
        # still due at 08:00.
        return []

    found = await session.scalars(
        select(CheckinPlan)
        .where(
            CheckinPlan.deleted_at.is_(None),
            CheckinPlan.status == CheckinPlanStatus.ACTIVE,
            CheckinPlan.next_cycle_at.is_not(None),
        )
        .order_by(CheckinPlan.next_cycle_at)
        .limit(limit)
    )
    reminded: list[CheckinPlan] = []
    for plan in found:
        rung = rung_due(plan, now=now)
        if rung is None:
            continue
        await remind(session, plan=plan, rung=rung, conversations=conversations, settings=settings)
        reminded.append(plan)

    await session.flush()
    return reminded
