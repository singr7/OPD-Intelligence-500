"""Appointment confirmations — WhatsApp + SMS (doc 03 §2, doc 01 §4.3/4.4).

> "Confirmations: WhatsApp interactive message + SMS fallback (MSG91). One-tap
>  confirm/cancel in WhatsApp" — doc 03 §2
> "**AC:** … every booking generates WhatsApp+SMS" — doc 03 §2

Both channels go out on every booking, not WhatsApp-then-SMS-if-it-fails. The AC
says both, and the reason holds in Alwar: WhatsApp "delivered" means the app has
it, which tells you nothing about a patient on a feature phone whose son owns the
WhatsApp number. SMS is the one that reaches the handset that answered the call.

## Why a template, usually

A booking made on a phone call is by definition **outside** the WhatsApp 24-hour
window — the patient never messaged us. Meta rejects free text there, so the
confirmation is a registered template (`app.whatsapp.templates`). The interactive
one-tap confirm/cancel buttons doc 03 §2 asks for only exist inside an open
window, so `in_window=True` (the bot knows) switches to the button variant. The
button ids are namespaced `appt:confirm:<id>` / `appt:cancel:<id>`, which
`app.whatsapp.bot` routes back into `app.scheduling`.

## Nothing here can fail a booking

Every send is caught. A confirmation that did not go out is recorded on
`Appointment.reminders` with `"status": "failed"` and the appointment stands: a
patient with a seat and no SMS is a phone call from a coordinator, while a
rolled-back booking because Meta had a bad minute is a patient who travelled
200km for nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Lang, UsagePurpose
from app.models.patient import Patient
from app.models.scheduling import Appointment
from app.providers.base import ProviderError, with_fallback
from app.providers.messaging import Button, OutboundMessage
from app.providers.registry import get_messaging_provider, get_sms_provider
from app.providers.sms import SmsMessage
from app.scheduling import hospital_tz
from app.whatsapp.templates import TemplateError, template_message

logger = logging.getLogger(__name__)

#: WhatsApp reply-id namespace for the one-tap actions (doc 03 §2). The bot's
#: other namespaces are `lang:` / `dept:` / `confirm:`; a tree option id never
#: contains ":", so these can never be mistaken for an answer.
CONFIRM_PREFIX = "appt:confirm:"
CANCEL_PREFIX = "appt:cancel:"

#: Weekday names, not month names: a date read as "Tuesday 4/8, 10:00" survives
#: translation review far better than a month table in four scripts, and the
#: weekday is the part a patient actually plans around. 24-hour clock throughout,
#: so no am/pm word has to be invented for mr/te (all mr/te copy is owed a native
#: review — S21).
WEEKDAYS: dict[Lang, tuple[str, ...]] = {
    Lang.EN: ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    Lang.HI: ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
    Lang.MR: ("सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
    Lang.TE: ("సోమవారం", "మంగళవారం", "బుధవారం", "గురువారం", "శుక్రవారం", "శనివారం", "ఆదివారం"),
}

BUTTON_CONFIRM: dict[Lang, str] = {
    Lang.EN: "Confirm",
    Lang.HI: "पक्का करें",
    Lang.MR: "निश्चित करा",
    Lang.TE: "నిర్ధారించండి",
}

BUTTON_CANCEL: dict[Lang, str] = {
    Lang.EN: "Cancel",
    Lang.HI: "रद्द करें",
    Lang.MR: "रद्द करा",
    Lang.TE: "రద్దు చేయండి",
}

#: In-window WhatsApp bodies (free text is allowed, so the interactive variant can
#: say more than the template). `{when}`, `{doctor}`, `{hospital}` are filled.
IN_WINDOW_BOOKED: dict[Lang, str] = {
    Lang.EN: "Your appointment with Dr. {doctor} at {hospital} is {when}.",
    Lang.HI: "डॉ. {doctor} के साथ {hospital} में आपका अपॉइंटमेंट {when} है।",
    Lang.MR: "डॉ. {doctor} यांच्यासोबत {hospital} मधील तुमची अपॉइंटमेंट {when} आहे.",
    Lang.TE: "డా. {doctor} తో {hospital}లో మీ అపాయింట్‌మెంట్ {when}.",
}

#: SMS bodies. Short by design — one segment where the script allows it, and the
#: same facts as the WhatsApp message so the two never disagree.
SMS_BOOKED: dict[Lang, str] = {
    Lang.EN: "{hospital}: appointment with Dr. {doctor} on {when}. Call {phone} to change.",
    Lang.HI: "{hospital}: डॉ. {doctor} के साथ अपॉइंटमेंट {when}। बदलने के लिए {phone} पर कॉल करें।",
    Lang.MR: "{hospital}: डॉ. {doctor} यांच्यासोबत अपॉइंटमेंट {when}. बदलण्यासाठी {phone} वर कॉल करा.",
    Lang.TE: "{hospital}: డా. {doctor} తో అపాయింట్‌మెంట్ {when}. మార్చడానికి {phone}కు కాల్ చేయండి.",
}

SMS_CANCELLED: dict[Lang, str] = {
    Lang.EN: "{hospital}: your appointment on {when} is cancelled. Call {phone} to rebook.",
    Lang.HI: "{hospital}: {when} का आपका अपॉइंटमेंट रद्द हो गया। दोबारा लेने के लिए {phone} पर कॉल करें।",
    Lang.MR: "{hospital}: {when} रोजीची तुमची अपॉइंटमेंट रद्द झाली. पुन्हा घेण्यासाठी {phone} वर कॉल करा.",
    Lang.TE: "{hospital}: {when} నాటి మీ అపాయింట్‌మెంట్ రద్దైంది. మళ్లీ బుక్ చేయడానికి {phone}కు కాల్ చేయండి.",
}


def format_when(at: datetime, lang: Lang) -> str:
    """ "मंगलवार 4/8, 10:00" — the appointment time as a patient hears it.

    Rendered in the hospital's timezone, because that is the clock on the wall the
    patient will walk in under.
    """
    local = at.astimezone(hospital_tz())
    weekday = WEEKDAYS.get(lang, WEEKDAYS[Lang.EN])[local.weekday()]
    return f"{weekday} {local.day}/{local.month}, {local:%H:%M}"


@dataclass(slots=True)
class Delivery:
    """What actually went out. Recorded on the appointment and asserted in tests."""

    whatsapp: bool = False
    sms: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def any_delivered(self) -> bool:
        return self.whatsapp or self.sms


def _record(appointment: Appointment, *, kind: str, channel: str, status: str) -> None:
    appointment.reminders = [
        *(appointment.reminders or []),
        {
            "at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "channel": channel,
            "status": status,
        },
    ]


async def _send_whatsapp(message: OutboundMessage) -> None:
    provider = get_messaging_provider()
    await with_fallback([provider], lambda p: p.send(message, purpose=UsagePurpose.OTHER))


async def _send_sms(to: str, body: str, *, template_key: str) -> None:
    provider = get_sms_provider()
    await provider.send(
        SmsMessage(to=to, body=body, template_key=template_key), purpose=UsagePurpose.OTHER
    )


async def notify_appointment(
    session: AsyncSession,
    *,
    appointment: Appointment,
    patient: Patient,
    hospital_name: str,
    doctor_name: str,
    hospital_phone: str = "",
    kind: str = "booked",
    in_window: bool = False,
) -> Delivery:
    """Send one appointment confirmation on both channels.

    `kind` is "booked" (also used for a reschedule — the patient cares about the
    new time, not the bookkeeping) or "cancelled". Returns what went out; never
    raises, and never leaves the appointment without a record of the attempt.
    """
    lang = patient.lang or Lang.HI
    when = format_when(appointment.slot_at, lang)
    delivery = Delivery()

    template = "appointment_cancelled" if kind == "cancelled" else "appointment_confirmed"
    variables = (
        (patient.name, hospital_name, when)
        if kind == "cancelled"
        else (patient.name, doctor_name, hospital_name, when)
    )

    try:
        if in_window and kind != "cancelled":
            body = IN_WINDOW_BOOKED.get(lang, IN_WINDOW_BOOKED[Lang.EN]).format(
                doctor=doctor_name, hospital=hospital_name, when=when
            )
            message = OutboundMessage(
                to=patient.phone,
                text=body,
                buttons=[
                    Button(
                        id=f"{CONFIRM_PREFIX}{appointment.id}",
                        title=BUTTON_CONFIRM.get(lang, BUTTON_CONFIRM[Lang.EN]),
                    ),
                    Button(
                        id=f"{CANCEL_PREFIX}{appointment.id}",
                        title=BUTTON_CANCEL.get(lang, BUTTON_CANCEL[Lang.EN]),
                    ),
                ],
            )
        else:
            message = template_message(
                to=patient.phone, name=template, lang=lang, variables=variables
            )
        await _send_whatsapp(message)
        delivery.whatsapp = True
        _record(appointment, kind=kind, channel="whatsapp", status="sent")
    except (ProviderError, TemplateError) as exc:
        delivery.errors.append(f"whatsapp: {exc}")
        _record(appointment, kind=kind, channel="whatsapp", status="failed")
        logger.warning("appointment %s whatsapp confirmation failed: %s", appointment.id, exc)

    body_map = SMS_CANCELLED if kind == "cancelled" else SMS_BOOKED
    sms_body = body_map.get(lang, body_map[Lang.EN]).format(
        hospital=hospital_name, doctor=doctor_name, when=when, phone=hospital_phone or "the OPD"
    )
    try:
        await _send_sms(patient.phone, sms_body, template_key=template)
        delivery.sms = True
        _record(appointment, kind=kind, channel="sms", status="sent")
    except ProviderError as exc:
        delivery.errors.append(f"sms: {exc}")
        _record(appointment, kind=kind, channel="sms", status="failed")
        logger.warning("appointment %s sms confirmation failed: %s", appointment.id, exc)

    await session.flush()
    return delivery


async def send_intake_call_fallback(
    session: AsyncSession,
    *,
    appointment: Appointment,
    patient: Patient,
    hospital_name: str,
) -> Delivery:
    """The D-1 campaign's last rung: two calls went unanswered, so invite the same
    intake over WhatsApp instead (doc 03 §1b)."""
    lang = patient.lang or Lang.HI
    when = format_when(appointment.slot_at, lang)
    delivery = Delivery()
    try:
        await _send_whatsapp(
            template_message(
                to=patient.phone,
                name="intake_call_missed",
                lang=lang,
                variables=(patient.name, hospital_name, when),
            )
        )
        delivery.whatsapp = True
        _record(appointment, kind="intake_call_missed", channel="whatsapp", status="sent")
    except (ProviderError, TemplateError) as exc:
        delivery.errors.append(f"whatsapp: {exc}")
        _record(appointment, kind="intake_call_missed", channel="whatsapp", status="failed")
        logger.warning("campaign fallback for %s failed: %s", appointment.id, exc)
    await session.flush()
    return delivery
