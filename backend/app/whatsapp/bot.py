"""The WhatsApp bot service (S12, doc 03 §1d) — inbound message → intake turn.

This is the WhatsApp adapter over the shared `IntakeEngine`, the sibling of the
kiosk service (`app.kiosk`). The engine, the four-tool contract, the tree walker
and the red-flag rules are all unchanged; what this file owns is the part that is
WhatsApp's alone — turning one webhook message into one step of a conversation
whose state lives on the server (`app.whatsapp.conversation`), and turning the
next intake question back into WhatsApp's native interactive shapes
(`app.whatsapp.render`).

The flow mirrors the kiosk's, one message at a time instead of one screen:

    language  →  chief complaint (typed OR a voice note → STT)  →  department
    chooser (only if the classifier is unsure)  →  the tree, as buttons/lists  →
    read-back  →  confirm  →  token.

Two patient-initiated commands short-circuit the flow at any idle point: "token
status" and "resend my prescription". Both reply as free text — the patient just
messaged us, so the 24-hour window is open by definition (doc 03 §1d); the
out-of-window *template* path is for proactive sends (the S11 delivery hook), not
for a reply to a message we just received.

Nothing here sends: `handle` returns the `OutboundMessage`s and the webhook does
the sending (and the one commit), so the bot stays a pure function of (state,
inbound) that the tests drive without a live Meta.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import kiosk as kiosk_svc
from app import prescription as rx_svc
from app import queue as queue_svc
from app import rx_sheets, scheduling
from app.checkins import delivery as checkin_delivery
from app.checkins import grading as checkin_grading
from app.config import Settings
from app.intake import IntakeEngine
from app.models.clinical import Intake, Visit
from app.models.content import Checkin, CheckinPlan
from app.models.enums import (
    AppointmentStatus,
    Channel,
    CheckinState,
    IntakeTier,
    Lang,
    UsagePurpose,
    VisitStatus,
)
from app.models.org import Department, Hospital
from app.models.patient import Patient
from app.models.scheduling import Appointment
from app.notify import format_when, notify_appointment
from app.providers.audio import AudioClip
from app.providers.base import ProviderError, with_fallback
from app.providers.messaging import Button, ListRow, OutboundMessage
from app.providers.metering import get_meter, usage_scope
from app.providers.registry import get_messaging_provider, stt_chain, tts_chain
from app.trees.schema import Tree
from app.whatsapp import render
from app.whatsapp.conversation import (
    Conversation,
    ConversationStep,
    ConversationStore,
)

logger = logging.getLogger(__name__)

#: WhatsApp runs the deterministic V3 walk, like the kiosk: the questions are taps
#: (buttons), and the one model call is the chief-complaint classifier. A voice
#: note is transcribed to a complaint, not a spoken dialogue turn (that is V1/V2,
#: telephony's S14).
WHATSAPP_TIER = IntakeTier.PRERECORDED

#: Control-reply id namespaces. A tree option id never contains ":", so a namespaced
#: id is unambiguously one of ours (a language pick, a department, a confirmation)
#: rather than an answer to the current question.
_LANG_PREFIX = "lang:"
_DEPT_PREFIX = "dept:"
_CONFIRM_YES = "confirm:yes"
_CONFIRM_NO = "confirm:no"
#: The one-tap appointment buttons (doc 03 §2). `app.notify` writes these ids onto
#: the confirmation message; this is the other end of them.
_APPT_PREFIX = "appt:"
_CHECKIN_PREFIX = "ck:"

#: A tap on a check-in that has since been answered, expired or cancelled.
_CHECKIN_CLOSED: dict[Lang, str] = {
    Lang.EN: "Thank you. That question is already closed — if something is wrong, "
    "please call the hospital.",
    Lang.HI: "धन्यवाद। वह सवाल अब बंद हो चुका है — अगर कुछ तकलीफ़ है तो कृपया अस्पताल में फ़ोन करें।",
    Lang.MR: "धन्यवाद. तो प्रश्न आता बंद झाला आहे — काही त्रास असेल तर कृपया रुग्णालयात फोन करा.",
    Lang.TE: "ధన్యవాదాలు. ఆ ప్రశ్న ఇప్పుడు ముగిసింది — ఏదైనా ఇబ్బంది ఉంటే దయచేసి ఆసుపత్రికి ఫోన్ చేయండి.",
}

#: An answer the question cannot accept. Re-asked, never guessed at.
_CHECKIN_AGAIN: dict[Lang, str] = {
    Lang.EN: "Sorry, I did not understand that. Please answer again:",
    Lang.HI: "माफ़ कीजिए, मैं समझ नहीं पाया। कृपया दोबारा उत्तर दें:",
    Lang.MR: "क्षमस्व, मला समजलं नाही. कृपया पुन्हा उत्तर द्या:",
    Lang.TE: "క్షమించండి, నాకు అర్థం కాలేదు. దయచేసి మళ్లీ సమాధానం ఇవ్వండి:",
}

#: The same close, whatever the grade — a bot does not tell a patient her answer
#: was alarming. `app.checkins.grading.submit` has already raised the escalation.
_CHECKIN_THANKS: dict[Lang, str] = {
    Lang.EN: "Thank you. We have your answers, and the hospital will contact you if "
    "anything needs attention.",
    Lang.HI: "धन्यवाद। आपके उत्तर हमें मिल गए हैं। अगर किसी बात पर ध्यान देने की ज़रूरत हुई तो "
    "अस्पताल आपसे संपर्क करेगा।",
    Lang.MR: "धन्यवाद. तुमची उत्तरं आम्हाला मिळाली आहेत. काही लक्ष देण्यासारखं असेल तर रुग्णालय "
    "तुमच्याशी संपर्क करेल.",
    Lang.TE: "ధన్యవాదాలు. మీ సమాధానాలు మాకు అందాయి. దేనికైనా శ్రద్ధ అవసరమైతే ఆసుపత్రి మిమ్మల్ని సంప్రదిస్తుంది.",
}

#: What a tap gets back. Short — the patient already has the details in the
#: message they tapped.
_APPT_CONFIRMED: dict[Lang, str] = {
    Lang.EN: "Thank you — your appointment on {when} is confirmed. Please come 15 minutes early.",
    Lang.HI: "धन्यवाद — {when} का आपका अपॉइंटमेंट पक्का है। कृपया 15 मिनट पहले आइए।",
    Lang.MR: "dhanyavaad — {when} chi tumchi appointment nishchit aahe. kripaya 15 minite "
    "aadhi ya.",
    Lang.TE: "dhanyavaadaalu — {when} naati mee appointment kharaaru ayindi. dayachesi 15 "
    "nimushaalu mundhuga randi.",
}

_APPT_CANCELLED: dict[Lang, str] = {
    Lang.EN: "Your appointment on {when} is cancelled. Message us any time to book again.",
    Lang.HI: "{when} का आपका अपॉइंटमेंट रद्द कर दिया गया है। दोबारा लेने के लिए कभी भी संदेश भेजिए।",
    Lang.MR: "{when} chi tumchi appointment radd keli aahe. punha ghenyasathi kadhihi sandesh "
    "pathva.",
    Lang.TE: "{when} naati mee appointment raddu chesaamu. malli book cheyadaaniki eppudaina "
    "sandesham pampandi.",
}

#: A tap we cannot place: an old button, a forwarded message, someone else's
#: appointment. Deliberately says nothing about whose it was.
_APPT_UNKNOWN: dict[Lang, str] = {
    Lang.EN: "I could not find that appointment. Please call the OPD if you need to change it.",
    Lang.HI: "वह अपॉइंटमेंट नहीं मिला। बदलने के लिए कृपया OPD पर कॉल कीजिए।",
    Lang.MR: "ti appointment sapadli nahi. badalnyasathi kripaya OPD la call kara.",
    Lang.TE: "aa appointment dorakaledu. maarchadaaniki dayachesi OPD ki call cheyandi.",
}

#: Patient-initiated command keywords (matched case-insensitively, en + hi).
_STATUS_WORDS = {"status", "token", "queue", "number", "टोकन", "स्थिति", "नंबर"}
_RX_WORDS = {"prescription", "rx", "medicine", "medicines", "dawai", "दवा", "दवाई", "पर्ची"}


@dataclass(slots=True)
class Inbound:
    """One normalised inbound message. The webhook builds this from Meta's payload
    so the bot never sees Meta's wire shape."""

    wa_id: str
    kind: str  # "text" | "reply" | "audio"
    text: str = ""
    reply_id: str | None = None
    media_id: str | None = None
    profile_name: str | None = None
    #: Meta's message id (`wamid...`), used to drop a redelivered duplicate.
    message_id: str | None = None


@dataclass(slots=True)
class _VoicePrompt:
    """A message text queued for a voice-note reply, synthesized after `handle`."""

    to: str
    text: str
    lang: Lang


@dataclass(slots=True)
class BotReply:
    """What the webhook does next: the messages to send, and whether a token just
    landed on the queue (so it can commit and nudge the board/console).

    `voice_prompts` are the texts to read aloud (doc 03 §1d); the webhook calls
    `synthesize_pending` to turn them into audio messages once, off the hot path.
    """

    messages: list[OutboundMessage] = field(default_factory=list)
    queue_changed: bool = False
    voice_prompts: list[_VoicePrompt] = field(default_factory=list)


class WhatsAppBot:
    def __init__(
        self,
        *,
        engine: IntakeEngine,
        conversations: ConversationStore,
        settings: Settings,
    ) -> None:
        self._engine = engine
        self._conversations = conversations
        self._settings = settings

    # -- entry point ----------------------------------------------------------

    async def handle(self, session: AsyncSession, inbound: Inbound) -> BotReply:
        """Advance one thread by one message. Persists the conversation state at the
        end so the next webhook resumes exactly here; the DB writes it makes (visit,
        token) are committed by the caller."""
        conv = await self._conversations.get(inbound.wa_id) or Conversation(wa_id=inbound.wa_id)
        if inbound.message_id is not None and inbound.message_id == conv.last_message_id:
            # An exact redelivery (Meta retries until it gets a 200). Acknowledge it
            # without re-running the step — a re-tap must not answer twice.
            return BotReply()
        conv.mark_inbound()
        if inbound.message_id is not None:
            conv.last_message_id = inbound.message_id
        try:
            reply = await self._route(session, conv, inbound)
        finally:
            await self._conversations.save(conv)
        return reply

    async def _route(self, session: AsyncSession, conv: Conversation, inbound: Inbound) -> BotReply:
        # A tap on an appointment confirmation is unambiguous whatever the thread
        # is doing (the id is namespaced), and it is time-sensitive — a patient
        # cancelling tomorrow's slot must not have to finish an intake first.
        if inbound.reply_id and inbound.reply_id.startswith(_APPT_PREFIX):
            return await self._appointment_tap(session, conv, inbound)

        # Same argument for a check-in tap (S17): the id is namespaced and
        # carries the check-in it answers, so it is unambiguous whatever else
        # the thread is doing, and a woman with a fever should not have to
        # finish an intake before she can say so.
        if inbound.reply_id and inbound.reply_id.startswith(_CHECKIN_PREFIX):
            return await self._checkin_tap(session, conv, inbound)
        # A typed answer only counts as a check-in answer while one is open and
        # the outstanding question is one you type into (a number or her own
        # words). Everything else falls through to the intake FSM below.
        if conv.checkin_id is not None and conv.step in {
            ConversationStep.IDLE,
            ConversationStep.DONE,
        }:
            return await self._checkin_typed(session, conv, inbound)

        # A command wins whenever we are not mid-answer — a patient checking their
        # token should not have to finish an intake first.
        if conv.step in {ConversationStep.IDLE, ConversationStep.DONE}:
            command = self._command_of(inbound)
            if command == "status":
                return await self._token_status(session, conv)
            if command == "rx":
                return await self._resend_rx(session, conv)
            return await self._ask_language_or_complaint(session, conv, inbound)

        if conv.step is ConversationStep.LANGUAGE:
            return self._capture_language(conv, inbound)
        if conv.step is ConversationStep.COMPLAINT:
            return await self._capture_complaint(session, conv, inbound)
        if conv.step is ConversationStep.DEPARTMENT:
            return await self._choose_department(session, conv, inbound)
        if conv.step is ConversationStep.INTAKE:
            return await self._answer_intake(session, conv, inbound)
        if conv.step is ConversationStep.READBACK:
            return await self._confirm(session, conv, inbound)
        # Unreachable, but never strand a patient: restart cleanly.
        conv.reset_flow()
        return await self._ask_language_or_complaint(session, conv, inbound)

    # -- language -------------------------------------------------------------

    async def _ask_language_or_complaint(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        """First contact (or a fresh start): a returning patient whose language we
        know skips straight to the chief complaint; a new one picks a language."""
        if conv.lang is not None:
            conv.step = ConversationStep.COMPLAINT
            return self._say(conv, self._complaint_prompt(conv.lang))
        conv.step = ConversationStep.LANGUAGE
        return BotReply(
            messages=[
                OutboundMessage(
                    to=conv.wa_id,
                    text="Namaste! / नमस्ते!\nPlease choose your language. / अपनी भाषा चुनें।",
                    buttons=[
                        Button(id=f"{_LANG_PREFIX}en", title="English"),
                        Button(id=f"{_LANG_PREFIX}hi", title="हिंदी"),
                    ],
                )
            ]
        )

    def _capture_language(self, conv: Conversation, inbound: Inbound) -> BotReply:
        picked = self._picked_language(inbound)
        if picked is None:
            # They typed instead of tapping, or tapped something stale — re-offer.
            return BotReply(
                messages=[
                    OutboundMessage(
                        to=conv.wa_id,
                        text="Please tap a language. / कृपया एक भाषा चुनें।",
                        buttons=[
                            Button(id=f"{_LANG_PREFIX}en", title="English"),
                            Button(id=f"{_LANG_PREFIX}hi", title="हिंदी"),
                        ],
                    )
                ]
            )
        conv.lang = picked
        conv.step = ConversationStep.COMPLAINT
        return self._say(conv, self._complaint_prompt(picked))

    # -- chief complaint ------------------------------------------------------

    async def _capture_complaint(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        lang = conv.lang or Lang.HI
        complaint = await self._complaint_text(inbound, lang)
        if not complaint:
            return self._say(
                conv,
                "Please describe your problem in a message or a voice note."
                if lang is Lang.EN
                else "कृपया अपनी समस्या एक संदेश या वॉइस नोट में बताएं।",
            )

        conv.chief_complaint = complaint
        routed = await kiosk_svc.route_complaint(session, complaint=complaint, lang=lang)
        if routed.needs_department:
            departments = await kiosk_svc._departments(session)
            conv.step = ConversationStep.DEPARTMENT
            conv.department_options = [[d.code, d.name] for d in departments]
            return self._say(
                conv,
                None,
                extra=OutboundMessage(
                    to=conv.wa_id,
                    text=(
                        routed.guess.reason or "Let's confirm the right doctor for you."
                        if lang is Lang.EN
                        else routed.guess.reason or "आइए आपके लिए सही डॉक्टर चुनें।"
                    ),
                    list_rows=[
                        ListRow(id=f"{_DEPT_PREFIX}{d.code}", title=d.name[:24])
                        for d in departments
                    ],
                    list_button="Choose" if lang is Lang.EN else "चुनें",
                ),
            )

        assert routed.department is not None and routed.tree is not None
        return await self._start_intake(session, conv, routed.department, routed.tree, complaint)

    async def _choose_department(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        key = self._chosen_department_key(conv, inbound)
        if key is None:
            return self._say(
                conv,
                "Please choose a department from the list."
                if conv.lang is Lang.EN
                else "कृपया सूची से एक विभाग चुनें।",
            )
        complaint = conv.chief_complaint or ""
        routed = await kiosk_svc.route_complaint(
            session, complaint=complaint, lang=conv.lang or Lang.HI, dept_key=key
        )
        if routed.department is None or routed.tree is None:
            return self._say(conv, "That department is unavailable. Please try again.")
        return await self._start_intake(session, conv, routed.department, routed.tree, complaint)

    async def _start_intake(
        self,
        session: AsyncSession,
        conv: Conversation,
        department: Department,
        tree: Tree,
        complaint: str,
    ) -> BotReply:
        lang = conv.lang or Lang.HI
        patient = await self._resolve_or_create_patient(session, conv, department)
        visit, intake = await _create_visit_and_intake(
            session, patient=patient, department=department, lang=lang
        )
        state = await self._engine.start_session(
            tree=tree,
            channel=Channel.WHATSAPP,
            lang=lang,
            configured_tier=WHATSAPP_TIER,
            intake_id=intake.id,
            visit_id=visit.id,
            chief_complaint=complaint,
        )
        conv.session_id = state.session_id
        conv.patient_id = patient.id
        conv.visit_id = visit.id
        conv.step = ConversationStep.INTAKE

        dispatcher = self._engine.dispatcher(state, tree)
        node = await dispatcher.get_next_node()
        return await self._present_node(conv, node)

    # -- walking the tree -----------------------------------------------------

    async def _answer_intake(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        state = await self._engine.store.get(conv.session_id or "")
        if state is None:
            # The session TTL lapsed (a patient who wandered off for hours). Start
            # over rather than answer a question we no longer hold.
            conv.reset_flow()
            return await self._ask_language_or_complaint(session, conv, inbound)

        dispatcher = self._engine.dispatcher(state)
        current = await dispatcher.get_next_node()
        if current.get("complete") or current.get("node") is None:
            return await self._finish(conv, dispatcher)

        node = current["node"]
        parsed = self._parse_answer(node, inbound, conv.lang or Lang.HI)
        if parsed is None:
            # An unmatched tap/typed answer, or a voice note on a tap question:
            # re-ask the same question rather than guess (doc 04 law 8 spirit).
            return await self._present_node(conv, current, retry=True)

        value, raw_text = parsed
        saved = await dispatcher.save_answer(node["id"], value, raw_text=raw_text)
        if not saved["ok"]:
            return await self._present_node(conv, current, retry=True)
        if saved["complete"]:
            return await self._finish(conv, dispatcher)
        return await self._present_node(conv, await dispatcher.get_next_node())

    async def _finish(self, conv: Conversation, dispatcher) -> BotReply:
        result = await dispatcher.finish_and_summarize("complete")
        conv.step = ConversationStep.READBACK
        lang = conv.lang or Lang.HI
        confirm = OutboundMessage(
            to=conv.wa_id,
            text=result["readback"],
            buttons=[
                Button(id=_CONFIRM_YES, title="Confirm" if lang is Lang.EN else "पुष्टि करें"),
                Button(id=_CONFIRM_NO, title="Change" if lang is Lang.EN else "बदलें"),
            ],
        )
        reply = BotReply(messages=[confirm])
        self._attach_voice(reply, result["readback"], lang)
        return reply

    async def _confirm(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        if inbound.reply_id == _CONFIRM_NO:
            # "Change something" restarts the intake — the kiosk has no per-node
            # rewind either (STATE.md), and re-walking is lossless.
            conv.reset_flow()
            conv.step = ConversationStep.COMPLAINT
            return self._say(conv, self._complaint_prompt(conv.lang or Lang.HI))
        if inbound.reply_id != _CONFIRM_YES:
            lang = conv.lang or Lang.HI
            return self._say(
                conv,
                "Please tap Confirm to finish, or Change to start over."
                if lang is Lang.EN
                else "समाप्त करने के लिए 'पुष्टि करें' या फिर से शुरू करने के लिए 'बदलें' दबाएं।",
            )
        return await self._issue_token(session, conv)

    async def _issue_token(self, session: AsyncSession, conv: Conversation) -> BotReply:
        state = await self._engine.store.get(conv.session_id or "")
        if state is None or state.visit_id is None:
            conv.reset_flow()
            return self._say(conv, "Your session expired. Please send a message to start again.")

        state.confirmed = True
        await self._engine.store.save(state)

        visit = await session.get(Visit, state.visit_id)
        # Drain the batched meter so finalize_cost sums a complete set of usage
        # events (the classifier's routing call is metered async) — same ordering
        # the kiosk confirm relies on.
        meter = get_meter()
        if meter is not None:
            await meter.flush()
        await self._engine.finalize_cost(state, session)

        lang = conv.lang or Lang.HI
        token_no: int | None = None
        queue_changed = False
        if visit is not None:
            token_no = await kiosk_svc.allocate_token(session, visit)
            intake = await session.get(Intake, state.intake_id)
            if intake is not None:
                await queue_svc.enqueue_from_intake(session, visit=visit, intake=intake)
                queue_changed = True

        conv.reset_flow()
        conv.step = ConversationStep.DONE
        text = (
            f"आपका टोकन नंबर {token_no} है। कृपया प्रतीक्षा क्षेत्र में बैठें।"
            if lang is Lang.HI
            else f"Your token number is {token_no}. Please take a seat in the waiting area."
        )
        reply = BotReply(
            messages=[OutboundMessage(to=conv.wa_id, text=text)], queue_changed=queue_changed
        )
        self._attach_voice(reply, text, lang)
        return reply

    # -- commands -------------------------------------------------------------

    # -- check-ins (S17, doc 03 §9) -------------------------------------------

    async def _checkin_tap(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        """A tapped option on a check-in question.

        The button id carries the **check-in** it answers, so a reply that
        arrives after the next check-in has gone out is applied to the one it was
        actually an answer to — or, if that one is closed, to none at all.
        """
        raw_checkin, _, rest = inbound.reply_id[len(_CHECKIN_PREFIX) :].partition(":")
        question_id, _, value = rest.partition(":")
        checkin = await self._open_checkin(session, conv, raw_checkin)
        if checkin is None:
            return self._say(conv, _CHECKIN_CLOSED[conv.lang or Lang.HI])
        return await self._checkin_answer(session, conv, checkin, question_id, value)

    async def _checkin_typed(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        """A typed answer to the outstanding question — a count, or her own words."""
        checkin = await self._open_checkin(session, conv, str(conv.checkin_id))
        if checkin is None or not conv.checkin_question:
            conv.checkin_id = None
            conv.checkin_question = None
            return await self._ask_language_or_complaint(session, conv, inbound)
        return await self._checkin_answer(
            session, conv, checkin, conv.checkin_question, (inbound.text or "").strip()
        )

    async def _open_checkin(
        self, session: AsyncSession, conv: Conversation, raw_id: str
    ) -> Checkin | None:
        """The check-in this reply is for, if it is still open and still hers."""
        try:
            checkin_id = uuid.UUID(raw_id)
        except (ValueError, AttributeError):
            return None
        checkin = await session.get(Checkin, checkin_id)
        if checkin is None or checkin.deleted_at is not None:
            return None
        if checkin.state not in {CheckinState.PENDING, CheckinState.SENT}:
            return None
        patient = await self._resolve_patient(session, conv.wa_id)
        plan = await session.get(CheckinPlan, checkin.plan_id)
        if patient is None or plan is None or plan.patient_id != patient.id:
            # Authorised by the number it came from, exactly like an appointment
            # tap: the button id is in the message, and a forwarded message must
            # not let anyone answer a stranger's check-in.
            return None
        return checkin

    async def _checkin_answer(
        self,
        session: AsyncSession,
        conv: Conversation,
        checkin: Checkin,
        question_id: str,
        value: str,
    ) -> BotReply:
        lang = checkin.lang
        try:
            _, finished = await checkin_grading.answer_one(
                session,
                checkin=checkin,
                question_id=question_id,
                raw=value,
                settings=self._settings,
            )
        except checkin_grading.AnswerError:
            # Re-ask rather than guess. A "hundred and two" into a Celsius
            # question is a real patient reading a real thermometer in the other
            # unit, and coercing it invents a red flag.
            question = next((q for q in checkin.asked or [] if q.get("id") == question_id), None)
            if question is None:
                return self._say(conv, _CHECKIN_CLOSED[lang])
            conv.checkin_question = question_id
            return BotReply(
                messages=[
                    OutboundMessage(to=conv.wa_id, text=_CHECKIN_AGAIN[lang]),
                    checkin_delivery.question_message(checkin, question, to=conv.wa_id),
                ]
            )

        if not finished:
            question = checkin_grading.unanswered(checkin)[0]
            conv.checkin_id = checkin.id
            conv.checkin_question = str(question["id"])
            return BotReply(
                messages=[checkin_delivery.question_message(checkin, question, to=conv.wa_id)]
            )

        conv.checkin_id = None
        conv.checkin_question = None
        # Deliberately the same thank-you whatever the grade. A patient is not
        # told "this is serious" by a bot — that is a nurse's call and a nurse's
        # phone call, and the escalation has already been raised by `submit`.
        return self._say(conv, _CHECKIN_THANKS[lang])

    async def _appointment_tap(
        self, session: AsyncSession, conv: Conversation, inbound: Inbound
    ) -> BotReply:
        """One-tap confirm/cancel on an appointment confirmation (doc 03 §2).

        The tap is authorised by the number it came from: the appointment must
        belong to the patient this WhatsApp id resolves to. Without that check a
        forwarded message would let anyone cancel a stranger's slot — the button
        id is not a secret, it is in the message.
        """
        lang = conv.lang or Lang.HI
        action, _, raw_id = inbound.reply_id[len(_APPT_PREFIX) :].partition(":")
        try:
            appointment_id = uuid.UUID(raw_id)
        except ValueError:
            return self._say(conv, _APPT_UNKNOWN[lang])

        patient = await self._resolve_patient(session, conv.wa_id)
        appointment = await session.get(Appointment, appointment_id)
        if (
            patient is None
            or appointment is None
            or appointment.deleted_at is not None
            or appointment.patient_id != patient.id
        ):
            return self._say(conv, _APPT_UNKNOWN[lang])

        when = format_when(appointment.slot_at, lang)
        if action == "cancel":
            await scheduling.cancel(
                session, appointment=appointment, reason="cancelled on WhatsApp"
            )
            hospital = await session.get(Hospital, patient.hospital_id)
            await notify_appointment(
                session,
                appointment=appointment,
                patient=patient,
                hospital_name=hospital.name if hospital is not None else "the hospital",
                doctor_name="",
                kind="cancelled",
            )
            return self._say(conv, _APPT_CANCELLED[lang].format(when=when))

        appointment.status = AppointmentStatus.CONFIRMED
        await session.flush()
        return self._say(conv, _APPT_CONFIRMED[lang].format(when=when))

    async def _token_status(self, session: AsyncSession, conv: Conversation) -> BotReply:
        lang = conv.lang or Lang.HI
        patient = await self._resolve_patient(session, conv.wa_id)
        if patient is None:
            return self._say(conv, self._no_visit_text(lang))
        visit = await _latest_tokened_visit(session, patient_id=patient.id)
        if visit is None or visit.token_no is None:
            return self._say(conv, self._no_visit_text(lang))
        ahead = await _people_ahead(session, visit)
        if lang is Lang.HI:
            ahead_txt = "आपका नंबर अगला है" if ahead == 0 else f"आपसे पहले {ahead} लोग हैं"
            text = f"आपका आज का टोकन {visit.token_no} है। {ahead_txt}।"
        else:
            ahead_txt = "you are next" if ahead == 0 else f"{ahead} ahead of you"
            text = f"Your token today is {visit.token_no}. {ahead_txt}."
        return self._say(conv, text)

    async def _resend_rx(self, session: AsyncSession, conv: Conversation) -> BotReply:
        lang = conv.lang or Lang.HI
        patient = await self._resolve_patient(session, conv.wa_id)
        if patient is None:
            return self._say(conv, self._no_rx_text(lang))
        rows = await rx_svc.history(session, patient_id=patient.id)
        if not rows:
            return self._say(conv, self._no_rx_text(lang))
        prescription, visit = rows[0]  # history is newest-first
        hospital = await _hospital_of_visit(session, visit)
        lines = rx_svc.lines_of(prescription)
        text = rx_sheets.sms_body(
            lines=lines, hospital=hospital.name if hospital else "", lang=patient.lang
        )
        rx_svc.record_delivery(prescription, channel="whatsapp", status="sent", detail="resend")
        return self._say(conv, text)

    # -- presentation helpers -------------------------------------------------

    async def _present_node(
        self, conv: Conversation, node_result: dict, *, retry: bool = False
    ) -> BotReply:
        node = node_result.get("node")
        if node is None:
            # Nothing left to ask — the walk is done; the caller handles finishing.
            return BotReply()
        lang = conv.lang or Lang.HI
        message = render.render_question(conv.wa_id, node, lang)
        if retry:
            prefix = "Sorry, I didn't get that. " if lang is Lang.EN else "क्षमा करें, समझ नहीं आया। "
            message = _with_prefix(message, prefix)
        reply = BotReply(messages=[message])
        self._attach_voice(reply, node.get("text") or "", lang)
        return reply

    def _say(
        self, conv: Conversation, text: str | None, *, extra: OutboundMessage | None = None
    ) -> BotReply:
        messages: list[OutboundMessage] = []
        if text:
            messages.append(OutboundMessage(to=conv.wa_id, text=text))
        if extra is not None:
            messages.append(extra)
        reply = BotReply(messages=messages)
        if text:
            self._attach_voice(reply, text, conv.lang or Lang.HI)
        return reply

    def _attach_voice(self, reply: BotReply, text: str, lang: Lang) -> None:
        """Queue a voice-note reply (doc 03 §1d): a patient who cannot read still
        hears the message. The synthesis itself is deferred to `synthesize_pending`
        (it is async and best-effort); here we only record what to speak.

        Off by default (`WHATSAPP_VOICE_NOTES`) and never fatal — a TTS outage drops
        the voice note and keeps the text, because the words are the delivery that
        matters and the audio is the accessibility bonus.
        """
        if not self._settings.whatsapp_voice_notes or not text.strip() or not reply.messages:
            return
        reply.voice_prompts.append(_VoicePrompt(to=reply.messages[0].to, text=text, lang=lang))

    # -- identity + parsing ---------------------------------------------------

    def _command_of(self, inbound: Inbound) -> str | None:
        if inbound.kind != "text" or not inbound.text:
            return None
        words = {w.strip(".,!?।").lower() for w in inbound.text.split()}
        if words & _STATUS_WORDS:
            return "status"
        if words & _RX_WORDS:
            return "rx"
        return None

    def _picked_language(self, inbound: Inbound) -> Lang | None:
        if inbound.reply_id == f"{_LANG_PREFIX}en":
            return Lang.EN
        if inbound.reply_id == f"{_LANG_PREFIX}hi":
            return Lang.HI
        text = inbound.text.strip().lower()
        if text in {"english", "en", "1"}:
            return Lang.EN
        if text in {"hindi", "हिंदी", "hi", "2"}:
            return Lang.HI
        return None

    def _chosen_department_key(self, conv: Conversation, inbound: Inbound) -> str | None:
        valid = {code for code, _name in conv.department_options}
        if inbound.reply_id and inbound.reply_id.startswith(_DEPT_PREFIX):
            key = inbound.reply_id[len(_DEPT_PREFIX) :]
            return key if key in valid else None
        return None

    def _parse_answer(
        self, node: dict, inbound: Inbound, lang: Lang
    ) -> tuple[object, str | None] | None:
        """Map an inbound onto the current node's own allowed answers, or None to
        re-ask. Never invents an option — a tap's echoed id or a typed value that a
        node accepts, nothing else (the walker re-validates regardless)."""
        options = node.get("options") or []
        # A multi-select node's walker expects a *list*; a single WhatsApp tap picks
        # one, so it is wrapped. Picking several options over WhatsApp (a list reply
        # is single-select too) is a backlog limitation, noted in STATE.md.
        multi = node.get("type") in {"multi", "body_map"}

        def _as_answer(option_id: str) -> tuple[object, str | None]:
            return ([option_id], None) if multi else (option_id, None)

        if inbound.kind == "reply" and inbound.reply_id:
            if options and not any(o["id"] == inbound.reply_id for o in options):
                return None
            return _as_answer(inbound.reply_id)
        if inbound.kind == "audio":
            # A voice note on a tap question needs the adaptive interpreter, which is
            # flag-gated and off by default (doc 11) — fall back to the buttons.
            return None
        text = (inbound.text or "").strip()
        if not text:
            return None
        if options:
            for opt in options:
                if text.lower() == opt["id"].lower() or text.lower() == (opt["text"] or "").lower():
                    return _as_answer(opt["id"])
            return None
        if node.get("type") in {"number", "scale"}:
            number = _parse_number(text)
            return (number, text) if number is not None else None
        # free_voice / free-text
        return (text, text)

    # -- patient + visit rows -------------------------------------------------

    async def _resolve_patient(self, session: AsyncSession, wa_id: str) -> Patient | None:
        digits = _last10(wa_id)
        if not digits:
            return None
        result = await session.execute(
            select(Patient).where(Patient.phone.like(f"%{digits}"), Patient.deleted_at.is_(None))
        )
        return result.scalars().first()

    async def _resolve_or_create_patient(
        self, session: AsyncSession, conv: Conversation, department: Department
    ) -> Patient:
        existing = await self._resolve_patient(session, conv.wa_id)
        if existing is not None:
            return existing
        patient = Patient(
            hospital_id=department.hospital_id,
            mrn=f"WA-{uuid.uuid4().hex[:10].upper()}",
            # Anonymous like a kiosk walk-in — the registration desk attaches the
            # real identity (doc 03 §1a); the phone is the one thing we do know.
            name="WhatsApp patient",
            # Stored with a leading '+' and full digits so a later token-status or
            # Rx lookup finds them by trailing-10-digit suffix (`_resolve_patient`).
            phone=_normalize_phone(conv.wa_id),
            lang=conv.lang or Lang.HI,
        )
        session.add(patient)
        await session.flush()
        return patient

    # -- small text helpers ---------------------------------------------------

    def _complaint_prompt(self, lang: Lang) -> str:
        return (
            "What brings you in today? You can type it, or send a voice note."
            if lang is Lang.EN
            else "आज आप किस समस्या के लिए आए हैं? आप टाइप कर सकते हैं, या वॉइस नोट भेज सकते हैं।"
        )

    def _no_visit_text(self, lang: Lang) -> str:
        return (
            "I couldn't find a token for you today. Send a message to check in."
            if lang is Lang.EN
            else "मुझे आज आपका कोई टोकन नहीं मिला। चेक-इन करने के लिए संदेश भेजें।"
        )

    def _no_rx_text(self, lang: Lang) -> str:
        return (
            "I couldn't find a prescription on your file yet."
            if lang is Lang.EN
            else "मुझे अभी आपकी फ़ाइल पर कोई प्रिस्क्रिप्शन नहीं मिला।"
        )

    async def _complaint_text(self, inbound: Inbound, lang: Lang) -> str:
        if inbound.kind == "audio" and inbound.media_id:
            return await self._transcribe(inbound.media_id, lang)
        return (inbound.text or "").strip()

    async def _transcribe(self, media_id: str, lang: Lang) -> str:
        """Download the voice note and run it through the STT chain (on a V-OSS box,
        local Whisper — the audio never leaves the premises, doc 08)."""
        provider = get_messaging_provider(self._settings)
        try:
            clip = await provider.download_media(media_id)
        except ProviderError as exc:
            logger.warning("whatsapp voice-note download failed: %s", exc)
            return ""
        try:
            with usage_scope(channel=Channel.WHATSAPP):
                transcript = await with_fallback(
                    stt_chain(self._settings),
                    lambda p: p.transcribe(clip, str(lang), purpose=UsagePurpose.INTAKE_TURN),
                )
        except ProviderError as exc:
            logger.warning("whatsapp voice-note STT failed: %s", exc)
            return ""
        return transcript.text.strip()

    async def synthesize_pending(self, reply: BotReply) -> None:
        """Turn each queued voice prompt into an audio message, appended in place.
        Called by the webhook after `handle`, so the TTS calls happen once, off the
        hot path, and a failure simply drops the voice note (the text still goes)."""
        for prompt in reply.voice_prompts:
            clip = await self._synthesize(prompt.text, prompt.lang)
            if clip is not None:
                reply.messages.append(OutboundMessage(to=prompt.to, audio=clip))
        reply.voice_prompts = []

    async def _synthesize(self, text: str, lang: Lang) -> AudioClip | None:
        try:
            with usage_scope(channel=Channel.WHATSAPP):
                speech = await with_fallback(
                    tts_chain(self._settings),
                    lambda p: p.synthesize(text, str(lang), purpose=UsagePurpose.INTAKE_TURN),
                )
        except ProviderError as exc:
            logger.info("whatsapp voice-note synthesis skipped: %s", exc)
            return None
        return speech.audio


# -- module-level DB helpers --------------------------------------------------


async def _create_visit_and_intake(
    session: AsyncSession, *, patient: Patient, department: Department, lang: Lang
) -> tuple[Visit, Intake]:
    visit = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=datetime.now(UTC).date(),
        status=VisitStatus.REGISTERED,
        channel=Channel.WHATSAPP,
    )
    session.add(visit)
    await session.flush()
    intake = Intake(visit_id=visit.id, tier=WHATSAPP_TIER, lang=lang)
    session.add(intake)
    await session.flush()
    return visit, intake


async def _latest_tokened_visit(session: AsyncSession, *, patient_id: uuid.UUID) -> Visit | None:
    result = await session.execute(
        select(Visit)
        .where(
            Visit.patient_id == patient_id,
            Visit.date == queue_svc.today(),
            Visit.token_no.is_not(None),
            Visit.deleted_at.is_(None),
        )
        .order_by(Visit.created_at.desc())
    )
    return result.scalars().first()


async def _people_ahead(session: AsyncSession, visit: Visit) -> int:
    """How many waiting entries sit before this visit in its department queue."""
    views = await queue_svc.department_queue(session, department_id=visit.department_id)
    ahead = 0
    for view in views:
        if view.visit_id == visit.id:
            return ahead
        if view.state == queue_svc.QueueEntryState.WAITING:
            ahead += 1
    return ahead


async def _hospital_of_visit(session: AsyncSession, visit: Visit) -> Hospital | None:
    department = await session.get(Department, visit.department_id)
    if department is None:
        return None
    return await session.get(Hospital, department.hospital_id)


# -- pure helpers -------------------------------------------------------------


def _with_prefix(message: OutboundMessage, prefix: str) -> OutboundMessage:
    from dataclasses import replace

    return replace(message, text=f"{prefix}{message.text}")


def _parse_number(text: str) -> float | None:
    import re

    match = re.search(r"-?\d+(\.\d+)?", text.replace(",", ""))
    if match is None:
        return None
    value = float(match.group())
    return int(value) if value.is_integer() else value


def _last10(wa_id: str) -> str:
    digits = "".join(ch for ch in wa_id if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def _normalize_phone(wa_id: str) -> str:
    digits = "".join(ch for ch in wa_id if ch.isdigit())
    return f"+{digits}" if digits else wa_id
