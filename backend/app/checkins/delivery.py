"""The delivery ladder, and the clock it runs on (doc 03 §9).

> "Delivery ladder per patient preference/reachability: WhatsApp → AI voice call
>  → SMS. Celery beat scheduler; quiet hours 21:00–08:00." — doc 03 §9

`send_due` is one beat job's worth of work: take the check-ins whose moment has
come, put each on the next rung it has not tried, and write down what happened.

## A rung is "asked", not "delivered"

The ladder does not advance on a failed *send* alone — it advances on **silence**.
A WhatsApp message Meta accepted and a patient never opened looks identical to us
either way, so a rung that goes out successfully still sets `next_attempt_at` to
`now + ANSWER_WINDOW`, and if she has not answered by then the next rung is
tried. A rung the vendor *refuses* advances immediately, because waiting six
hours for a message that was never sent helps nobody.

## Quiet hours defer, they never consume an attempt

A check-in that comes due at 22:00 is moved to 08:00 with `attempts` untouched
(`app.checkins.window`). A patient must not burn her WhatsApp rung on a message
nobody was awake to read.

## What each rung actually is

- **WhatsApp** — the real thing: the covering message plus the first question as
  reply buttons, and `app.whatsapp.bot` walks the rest. Out of the 24h window it
  sends the registered `checkin_due` template instead, which invites the reply
  that opens a window; the bot then asks the questions.
- **Voice** — an outbound call through the telephony provider, pointed at the
  check-in Voicebot applet. **The voice-gw handler for that applet does not exist
  yet** (S14/S15 built the intake and receptionist ones); with no
  `EXOTEL_CHECKIN_APPLET_URL` configured the rung records "not configured" and
  the ladder moves on rather than dialling a patient into silence.
- **SMS** — a nudge, deliberately not a questionnaire. Structured answers over a
  DLT-templated Indian SMS gateway is not a thing that works, so this rung says
  "we have been trying to reach you, please reply on WhatsApp or call us" and the
  check-in expires after it. It is the rung that reaches a feature phone, and
  what it buys is a human knowing to ring her.

## Expiry is a clinical fact, not a failure

A check-in nobody could reach ends `EXPIRED` with **no grade**. "We could not
reach her" and "she said she is fine" are different things, and a system that
recorded the first as the second would be worse than one that never asked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checkins.plan import LADDER
from app.checkins.window import is_quiet, next_sendable
from app.config import Settings, get_settings
from app.models.content import Checkin, CheckinPlan
from app.models.enums import Channel, CheckinPlanStatus, CheckinState, Lang, UsagePurpose
from app.models.patient import Patient
from app.providers.base import ProviderError, with_fallback
from app.providers.messaging import Button, OutboundMessage
from app.providers.registry import (
    get_messaging_provider,
    get_sms_provider,
    get_telephony_provider,
)
from app.providers.sms import SmsMessage
from app.providers.telephony import CallRequest
from app.whatsapp.conversation import ConversationStore
from app.whatsapp.templates import TemplateError, template_message

logger = logging.getLogger(__name__)

#: How long a rung waits for an answer before the next one is tried. Long enough
#: that a patient asleep after chemotherapy is not chased at lunchtime, short
#: enough that a D+2 question is still about D+2.
ANSWER_WINDOW = timedelta(hours=6)

#: Reply-id namespace for a check-in answer, routed by `app.whatsapp.bot`. The
#: bot's other namespaces are `lang:` / `dept:` / `appt:`; a question or option id
#: never contains ":" so these can never collide with a tree answer.
REPLY_PREFIX = "ck:"

#: The SMS nudge, per language. Says what it is and what to do; it does not ask a
#: clinical question, because it has nowhere to put the answer.
_SMS_NUDGE: dict[Lang, str] = {
    Lang.EN: "{hospital}: we are trying to reach you about how you are after your "
    "treatment. Please reply on WhatsApp, or call {phone}.",
    Lang.HI: "{hospital}: इलाज के बाद आप कैसे हैं, यह जानने के लिए हम आपसे संपर्क कर रहे हैं। "
    "कृपया WhatsApp पर उत्तर दें, या {phone} पर कॉल करें।",
    Lang.MR: "{hospital}: उपचारानंतर तुम्ही कसे आहात हे जाणून घेण्यासाठी आम्ही संपर्क करत आहोत. "
    "कृपया WhatsApp वर उत्तर द्या, किंवा {phone} वर कॉल करा.",
    Lang.TE: "{hospital}: చికిత్స తర్వాత మీరు ఎలా ఉన్నారో తెలుసుకోవడానికి మేము ప్రయత్నిస్తున్నాము. "
    "దయచేసి WhatsAppలో ప్రత్యుత్తరం ఇవ్వండి, లేదా {phone}కు కాల్ చేయండి.",
}


def next_rung(channel: Channel) -> Channel | None:
    """The rung after this one, or None at the bottom of the ladder."""
    try:
        index = LADDER.index(channel)
    except ValueError:
        return None
    return LADDER[index + 1] if index + 1 < len(LADDER) else None


def _record(checkin: Checkin, *, channel: Channel, status: str, detail: str = "") -> None:
    checkin.delivery = [
        *(checkin.delivery or []),
        {
            "at": datetime.now(UTC).isoformat(),
            "channel": str(channel),
            "status": status,
            "detail": detail,
        },
    ]


def first_question(checkin: Checkin) -> dict | None:
    questions = checkin.asked or []
    return questions[0] if questions else None


def question_message(
    checkin: Checkin, question: dict, *, to: str, prefix: str = ""
) -> OutboundMessage:
    """One question as an interactive WhatsApp message.

    Option ids are namespaced with the check-in's id, so a reply that arrives
    late — after the next check-in has gone out — is applied to the check-in it
    was actually an answer to, or to none at all.
    """
    lang = checkin.lang
    text = question["prompt"].get(str(lang)) or question["prompt"]["en"]
    buttons = [
        Button(
            id=f"{REPLY_PREFIX}{checkin.id}:{question['id']}:{option['id']}",
            title=(option["label"].get(str(lang)) or option["label"]["en"])[:20],
        )
        for option in question.get("options", ())
    ]
    return OutboundMessage(to=to, text=f"{prefix}{text}".strip(), buttons=buttons)


# -- the rungs -----------------------------------------------------------------


async def _send_whatsapp(
    checkin: Checkin,
    *,
    patient: Patient,
    hospital_name: str,
    conversations: ConversationStore | None,
    settings: Settings,
    now: datetime | None = None,
) -> str:
    """Returns a detail line. Raises `ProviderError`/`TemplateError` on failure.

    `now` is the beat tick's clock, not the wall clock. The 24h window has to be
    judged against the same instant the rest of the tick is judged against —
    reading `datetime.now()` here would let a replay, a backfill, or a skewed box
    decide the window differently from the caller that chose to send.
    """
    in_window = False
    if conversations is not None:
        conversation = await conversations.get(patient.phone)
        in_window = conversation is not None and conversation.within_window(now=now)

    if not in_window:
        # Meta refuses free text out of window, so the personalised message
        # cannot go. The template invites the reply that opens a window; the bot
        # asks the questions on that reply.
        message = template_message(
            to=patient.phone,
            name="checkin_due",
            lang=checkin.lang,
            variables=(patient.name, hospital_name),
        )
        await with_fallback(
            [get_messaging_provider(settings)],
            lambda p: p.send(message, purpose=UsagePurpose.CHECKIN),
        )
        return "template (out of window)"

    question = first_question(checkin)
    if question is None:  # pragma: no cover - a set with no questions cannot load
        raise ProviderError("check-in has no questions")
    message = question_message(checkin, question, to=patient.phone, prefix=f"{checkin.message}\n\n")
    await with_fallback(
        [get_messaging_provider(settings)],
        lambda p: p.send(message, purpose=UsagePurpose.CHECKIN),
    )
    if conversations is not None:
        conversation = await conversations.get(patient.phone)
        if conversation is not None:
            conversation.checkin_id = checkin.id
            conversation.checkin_question = str(question["id"])
            await conversations.save(conversation)
    return "message + first question"


async def _place_call(checkin: Checkin, *, patient: Patient, settings: Settings) -> str:
    applet = settings.exotel_checkin_applet_url
    if not applet:
        # Dialling a patient into an applet that does not exist is worse than not
        # dialling: she answers a hospital call and hears nothing.
        raise ProviderError("no check-in voice applet configured")
    await get_telephony_provider(settings).place_call(
        CallRequest(
            to=patient.phone,
            applet_url=applet,
            caller_id=settings.exotel_caller_id or None,
            status_callback=settings.exotel_status_callback_url or None,
            reference=str(checkin.id),
        )
    )
    return "voice call placed"


async def _send_sms(
    checkin: Checkin, *, patient: Patient, hospital_name: str, settings: Settings
) -> str:
    body = _SMS_NUDGE.get(checkin.lang, _SMS_NUDGE[Lang.EN]).format(
        hospital=hospital_name, phone=settings.coordinator_phone or "the OPD"
    )
    await get_sms_provider(settings).send(
        SmsMessage(to=patient.phone, body=body, template_key="checkin_nudge"),
        purpose=UsagePurpose.CHECKIN,
    )
    return "nudge"


# -- one check-in --------------------------------------------------------------


async def deliver(
    session: AsyncSession,
    *,
    checkin: Checkin,
    patient: Patient,
    hospital_name: str = "the hospital",
    conversations: ConversationStore | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> bool:
    """Try this check-in's current rung. Returns whether anything went out.

    Never raises: a vendor having a bad minute must not stop the beat job from
    working through the rest of the queue.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    channel = checkin.channel
    checkin.attempts += 1

    try:
        if channel is Channel.WHATSAPP:
            detail = await _send_whatsapp(
                checkin,
                patient=patient,
                hospital_name=hospital_name,
                conversations=conversations,
                settings=settings,
                now=now,
            )
        elif channel is Channel.PHONE:
            detail = await _place_call(checkin, patient=patient, settings=settings)
        else:
            detail = await _send_sms(
                checkin, patient=patient, hospital_name=hospital_name, settings=settings
            )
    except (ProviderError, TemplateError) as exc:
        _record(checkin, channel=channel, status="failed", detail=str(exc)[:200])
        _advance(checkin, now=now, wait=False, settings=settings)
        logger.warning("check-in %s rung %s failed: %s", checkin.id, channel, exc)
        return False

    _record(checkin, channel=channel, status="sent", detail=detail)
    if checkin.sent_at is None:
        checkin.sent_at = now
    checkin.state = CheckinState.SENT
    _advance(checkin, now=now, wait=True, settings=settings)
    return True


def _advance(
    checkin: Checkin, *, now: datetime, wait: bool, settings: Settings | None = None
) -> None:
    """Point the check-in at its next rung, or end it.

    `wait=True` after a successful send — the ladder moves on only if she stays
    silent. `wait=False` after a refused one — there is nothing to wait for.
    """
    following = next_rung(checkin.channel)
    if following is None:
        if wait:
            # The last rung went out; give her the window to answer before the
            # check-in is written off.
            checkin.next_attempt_at = now + ANSWER_WINDOW
        else:
            checkin.state = CheckinState.EXPIRED
            checkin.next_attempt_at = None
        return
    checkin.channel = following
    checkin.next_attempt_at = next_sendable(now + ANSWER_WINDOW if wait else now, settings=settings)


def expire(checkin: Checkin) -> None:
    """The bottom of the ladder, with the window gone and no answer."""
    checkin.state = CheckinState.EXPIRED
    checkin.next_attempt_at = None
    _record(checkin, channel=checkin.channel, status="expired", detail="no answer on any rung")


# -- the beat job's unit of work -----------------------------------------------


async def due_checkins(
    session: AsyncSession, *, now: datetime | None = None, limit: int = 200
) -> list[Checkin]:
    """Everything whose moment has come, oldest first.

    Only check-ins on an **active** plan: cancelling a plan stops its unsent
    rungs, and a plan still in draft has never been approved by anyone.
    """
    now = now or datetime.now(UTC)
    found = await session.scalars(
        select(Checkin)
        .join(CheckinPlan, CheckinPlan.id == Checkin.plan_id)
        .where(
            Checkin.deleted_at.is_(None),
            Checkin.state.in_([CheckinState.PENDING, CheckinState.SENT]),
            Checkin.next_attempt_at.is_not(None),
            Checkin.next_attempt_at <= now,
            CheckinPlan.status == CheckinPlanStatus.ACTIVE,
            CheckinPlan.deleted_at.is_(None),
        )
        .order_by(Checkin.next_attempt_at)
        .limit(limit)
    )
    return list(found)


async def send_due(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 200,
    conversations: ConversationStore | None = None,
    settings: Settings | None = None,
) -> list[Checkin]:
    """One beat tick. Returns the check-ins something actually happened to."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    if not settings.checkins_enabled:
        logger.info("check-in delivery disabled — sending nothing")
        return []

    touched: list[Checkin] = []
    for checkin in await due_checkins(session, now=now, limit=limit):
        if is_quiet(now, settings=settings):
            # Deferred, not attempted: `attempts` is untouched, so a patient does
            # not burn a rung on a message nobody was awake to read.
            checkin.next_attempt_at = next_sendable(now, settings=settings)
            continue

        # At the bottom of the ladder with the answer window gone.
        if checkin.state is CheckinState.SENT and next_rung(checkin.channel) is None:
            expire(checkin)
            touched.append(checkin)
            continue

        plan = await session.get(CheckinPlan, checkin.plan_id)
        patient = await session.get(Patient, plan.patient_id) if plan is not None else None
        if patient is None or not patient.phone:  # pragma: no cover - FK-guarded
            expire(checkin)
            continue

        hospital_name = await _hospital_name(session, patient=patient)
        await deliver(
            session,
            checkin=checkin,
            patient=patient,
            hospital_name=hospital_name,
            conversations=conversations,
            now=now,
            settings=settings,
        )
        touched.append(checkin)

    await session.flush()
    return touched


async def _hospital_name(session: AsyncSession, *, patient: Patient) -> str:
    from app.models.org import Hospital

    hospital = await session.get(Hospital, patient.hospital_id)
    return hospital.name_in(patient.lang) if hospital is not None else "the hospital"
