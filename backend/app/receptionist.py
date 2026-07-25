"""The inbound AI receptionist (doc 03 §2, doc 01 §4.4).

> "Patient calls hospital line → AI answers in caller's language → Intent: book /
>  reschedule / cancel / 'when is my appointment?' / talk to human → Books against
>  real slot inventory → SMS + WhatsApp confirmation → human handoff on 2 failed
>  turns" — doc 01 §4.4

This is the appointment half of the phone channel. S14 built the *intake* half:
`voice-gw` bridges Exotel audio to `IntakeEngine`. The receptionist is not an
intake — there is no tree, no red flag, no clinical content — so it does not run
on that engine. It is a small, explicit state machine over `app.scheduling`, and
`voice-gw` drives it with the same transport, VAD and DTMF machinery.

## One model call, and it decides nothing

Exactly like the kiosk's Q1 classifier (`app.routing`), the only model call here
turns the caller's first sentence into an **intent**. It never picks a slot,
never resolves "next Tuesday" to a date, never writes. Everything after the
intent is deterministic code over real inventory, so what a caller is offered and
what the database does are the same thing.

Every one of the model's answers is distrusted the same way `app.routing`
distrusts its own: an invented intent, a low confidence, or a provider outage all
land on the same square — a human coordinator — because a caller on a hospital
line who is not understood must reach a person, not a retry loop.

## Two failed turns and a person picks up

doc 01 §4.4's rule, taken literally. `HANDOFF_AFTER_FAILURES` counts turns this
module could not act on; on the second, the call transfers with a **whisper
summary** ("Kamla Devi, wants to move Thursday 10:00 review") so the coordinator
hears the situation before the caller starts again. An emergency skips the
counter entirely — the classifier is told to return `human` immediately.

## Slots are offered by keypad number

The caller picks with a digit, not a sentence: "for Tuesday at 10, press 1". The
keypad works on a bad line, in any language, for a caller whose speech the STT
mangles — the same reason doc 03 §1b puts DTMF under the intake. Spoken digits
are accepted too (`ek`/`एक`/`1`), because plenty of callers just say the number.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AppointmentStatus, Channel, Lang, UsagePurpose
from app.models.org import Doctor, Hospital
from app.models.patient import Patient
from app.models.scheduling import Appointment
from app.notify import format_when, notify_appointment
from app.prompts import load
from app.providers import LLMProvider, LLMRequest, ProviderError, llm_chain, with_fallback
from app.scheduling import (
    BookingError,
    SlotOffer,
    SlotUnavailable,
    book,
    find_slots,
    reschedule,
    upcoming_for_patient,
)
from app.scheduling import cancel as cancel_appointment

logger = logging.getLogger(__name__)

#: doc 01 §4.4: "human handoff on 2 failed turns".
HANDOFF_AFTER_FAILURES = 2

#: Same floor as the routing classifier, for the same reason (`app.routing`).
CONFIDENCE_FLOOR = 0.6

#: How many slots to read out. Three is what a caller can hold in their head, and
#: it fits the three WhatsApp buttons the same offer becomes on that channel.
OFFER_COUNT = 3


class Intent(StrEnum):
    BOOK = "book"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    STATUS = "status"
    HUMAN = "human"
    UNCLEAR = "unclear"


class Step(StrEnum):
    """Where the call is. The state is the position; nothing is inferred."""

    INTENT = "intent"  # waiting for the caller to say what they want
    CHOOSING = "choosing"  # slots read out, waiting for a keypad digit
    CONFIRMING = "confirming"  # waiting for yes/no on a cancellation
    DONE = "done"


@dataclass(frozen=True, slots=True)
class IntentGuess:
    intent: Intent
    confidence: float
    when_hint: str = ""
    reason: str = ""
    #: False when this is a fall-back rather than the model's own answer, so S18
    #: never counts an outage as the classifier's opinion.
    from_model: bool = True

    @property
    def needs_human(self) -> bool:
        return self.intent is Intent.HUMAN or self.confidence < CONFIDENCE_FLOOR


@dataclass(slots=True)
class ReceptionistState:
    """One call's position. Lives for the duration of the call in the process
    driving it (`voice-gw`), like a `Walk` — derived, not authoritative."""

    lang: Lang = Lang.HI
    cli: str = ""
    patient_id: uuid.UUID | None = None
    patient_name: str = ""
    step: Step = Step.INTENT
    intent: Intent | None = None
    failures: int = 0
    offers: list[SlotOffer] = field(default_factory=list)
    #: The appointment a reschedule/cancel is operating on.
    appointment_id: uuid.UUID | None = None
    #: Set when this call actually booked something — the AC's proof.
    booked_appointment_id: uuid.UUID | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class Reply:
    """What the channel should say next, and what it should do about the call."""

    text: str
    done: bool = False
    handoff: bool = False
    #: Read to the coordinator before the caller is connected (doc 03 §2).
    whisper: str = ""
    #: True when this turn expects a keypad digit — the channel can prompt for it.
    expects_digit: bool = False


# -- phrasebook ----------------------------------------------------------------
#
# Everything a caller hears. mr/te are romanized placeholders pending the S21
# native + clinical review, exactly like the S14 consent line.

SAY: dict[str, dict[Lang, str]] = {
    "greeting_known": {
        Lang.EN: "Namaste {name}. This is the cancer hospital appointment line. "
        "How can I help you?",
        Lang.HI: "नमस्ते {name}। यह कैंसर अस्पताल की अपॉइंटमेंट लाइन है। मैं आपकी क्या मदद कर सकती हूँ?",
        Lang.MR: "namaskar {name}. ha cancer rugnalayacha appointment number aahe. "
        "mi tumchi kay madat karu shakate?",
        Lang.TE: "namaste {name}. idi cancer aasupatri appointment line. "
        "nenu meeku ela sahaayam cheyagalanu?",
    },
    "greeting_unknown": {
        Lang.EN: "Namaste. This is the cancer hospital appointment line. How can I help you?",
        Lang.HI: "नमस्ते। यह कैंसर अस्पताल की अपॉइंटमेंट लाइन है। मैं आपकी क्या मदद कर सकती हूँ?",
        Lang.MR: "namaskar. ha cancer rugnalayacha appointment number aahe. "
        "mi tumchi kay madat karu shakate?",
        Lang.TE: "namaste. idi cancer aasupatri appointment line. "
        "nenu meeku ela sahaayam cheyagalanu?",
    },
    "offer_intro": {
        Lang.EN: "These times are free.",
        Lang.HI: "ये समय खाली हैं।",
        Lang.MR: "hya velaa moklya aahet.",
        Lang.TE: "ee samayaalu khaalee ga unnaayi.",
    },
    "offer_line": {
        Lang.EN: "For {when} with Dr. {doctor}, press {digit}.",
        Lang.HI: "{when}, डॉ. {doctor} के लिए {digit} दबाइए।",
        Lang.MR: "{when}, dr. {doctor} sathi {digit} dabava.",
        Lang.TE: "{when}, dr. {doctor} koraku {digit} nokkandi.",
    },
    "no_slots": {
        Lang.EN: "I have no free times right now. I will connect you to a coordinator.",
        Lang.HI: "अभी कोई समय खाली नहीं है। मैं आपको कोऑर्डिनेटर से जोड़ती हूँ।",
        Lang.MR: "sadhya kahich vel mokli nahi. mi tumhala coordinator kade jodate.",
        Lang.TE: "ippudu khaalee samayam ledu. nenu mimmalni coordinator ki kaluputaanu.",
    },
    "booked": {
        Lang.EN: "Done. Your appointment is {when} with Dr. {doctor}. "
        "You will get a message on your phone.",
        Lang.HI: "हो गया। आपका अपॉइंटमेंट {when}, डॉ. {doctor} के साथ है। आपके फोन पर संदेश आ जाएगा।",
        Lang.MR: "jhala. tumchi appointment {when}, dr. {doctor} yanchyasobat aahe. "
        "tumchya phone var sandesh yeil.",
        Lang.TE: "ayindi. mee appointment {when}, dr. {doctor} tho. "
        "mee phone ki sandesham vastundi.",
    },
    "moved": {
        Lang.EN: "Done. Your appointment is moved to {when}. You will get a message on your phone.",
        Lang.HI: "हो गया। आपका अपॉइंटमेंट {when} पर बदल दिया गया है। आपके फोन पर संदेश आ जाएगा।",
        Lang.MR: "jhala. tumchi appointment {when} var badalali aahe. "
        "tumchya phone var sandesh yeil.",
        Lang.TE: "ayindi. mee appointment {when} ki maarchabadindi. "
        "mee phone ki sandesham vastundi.",
    },
    "cancelled": {
        Lang.EN: "Your appointment on {when} is cancelled. Call us any time to book again.",
        Lang.HI: "{when} का आपका अपॉइंटमेंट रद्द कर दिया गया है। दोबारा लेने के लिए कभी भी कॉल कीजिए।",
        Lang.MR: "{when} chi tumchi appointment radd keli aahe. punha ghenyasathi "
        "kadhihi call kara.",
        Lang.TE: "{when} naati mee appointment raddu chesaamu. malli book cheyadaaniki eppudaina "
        "call cheyandi.",
    },
    "confirm_cancel": {
        Lang.EN: "Your appointment is {when}. To cancel it, press 1. To keep it, press 2.",
        Lang.HI: "आपका अपॉइंटमेंट {when} है। रद्द करने के लिए 1 दबाइए, रखने के लिए 2।",
        Lang.MR: "tumchi appointment {when} aahe. radd karnyasathi 1 dabava, thevnyasathi 2.",
        Lang.TE: "mee appointment {when}. raddu cheyadaaniki 1 nokkandi, unchadaaniki 2.",
    },
    "kept": {
        Lang.EN: "Your appointment stays as it is. See you then.",
        Lang.HI: "आपका अपॉइंटमेंट वैसा ही रहेगा। तब मिलते हैं।",
        Lang.MR: "tumchi appointment tashich rahil. tevha bhetu.",
        Lang.TE: "mee appointment alaage untundi. appudu kalusukundaam.",
    },
    "status": {
        Lang.EN: "Your appointment is {when} with Dr. {doctor}.",
        Lang.HI: "आपका अपॉइंटमेंट {when}, डॉ. {doctor} के साथ है।",
        Lang.MR: "tumchi appointment {when}, dr. {doctor} yanchyasobat aahe.",
        Lang.TE: "mee appointment {when}, dr. {doctor} tho.",
    },
    "no_appointment": {
        Lang.EN: "I cannot find an appointment for this number. "
        "I will connect you to a coordinator.",
        Lang.HI: "इस नंबर पर कोई अपॉइंटमेंट नहीं मिला। मैं आपको कोऑर्डिनेटर से जोड़ती हूँ।",
        Lang.MR: "hya number var appointment sapadli nahi. mi tumhala coordinator kade jodate.",
        Lang.TE: "ee number ki appointment kanabadaledu. nenu mimmalni coordinator ki kalupataanu.",
    },
    "unknown_caller": {
        Lang.EN: "I could not find your record for this number. "
        "I will connect you to a coordinator.",
        Lang.HI: "इस नंबर से आपका रिकॉर्ड नहीं मिला। मैं आपको कोऑर्डिनेटर से जोड़ती हूँ।",
        Lang.MR: "hya number varun tumcha record sapadla nahi. mi tumhala coordinator kade jodate.",
        Lang.TE: "ee number tho mee record dorakaledu. nenu mimmalni coordinator ki kalupataanu.",
    },
    "handoff": {
        Lang.EN: "Let me connect you to a person who can help. Please stay on the line.",
        Lang.HI: "मैं आपको एक व्यक्ति से जोड़ती हूँ जो मदद कर सकेंगे। कृपया लाइन पर बने रहें।",
        Lang.MR: "mi tumhala madat karnarya vyaktikade jodate. kripaya line var thamba.",
        Lang.TE: "sahaayam cheyagala vyakti ki kalupataanu. dayachesi line lo undandi.",
    },
    "retry": {
        Lang.EN: "Sorry, I did not understand. Do you want a new appointment, "
        "to change one, or to cancel one?",
        Lang.HI: "माफ़ कीजिए, समझ नहीं आया। क्या आपको नया अपॉइंटमेंट चाहिए, बदलना है, या रद्द करना है?",
        Lang.MR: "maaf kara, samajle nahi. tumhala navi appointment havi, badalaychi aahe, "
        "ki radd karaychi aahe?",
        Lang.TE: "kshaminchandi, ardham kaledu. meeku kotta appointment kaavaala, maarchaalaa, "
        "leda raddu cheyaalaa?",
    },
    "taken": {
        Lang.EN: "Sorry, that time was just taken. Here are the times that are free now.",
        Lang.HI: "माफ़ कीजिए, वह समय अभी किसी और ने ले लिया। ये समय अब खाली हैं।",
        Lang.MR: "maaf kara, ti vel aataach dusaryane ghetli. hya vela aata moklya aahet.",
        Lang.TE: "kshaminchandi, aa samayam ippude vere vaaru teesukunnaaru. ee samayaalu "
        "ippudu khaalee ga unnaayi.",
    },
}


def say(key: str, lang: Lang, **values: object) -> str:
    line = SAY[key].get(lang) or SAY[key][Lang.EN]
    return line.format(**values) if values else line


#: Spoken digits a caller might say instead of pressing. STT gives us words as
#: often as numerals, and refusing "ek" would send a cooperative caller to a
#: coordinator for no reason.
_SPOKEN_DIGITS: dict[str, int] = {
    "1": 1, "one": 1, "ek": 1, "एक": 1, "pehla": 1, "pehle": 1, "first": 1,
    "2": 2, "two": 2, "do": 2, "दो": 2, "dusra": 2, "doosra": 2, "dusre": 2,
    "doosre": 2, "second": 2,
    "3": 3, "three": 3, "teen": 3, "तीन": 3, "tisra": 3, "teesra": 3, "tisre": 3,
    "teesre": 3, "third": 3,
}  # fmt: skip

_YES = {"1", "yes", "haan", "हाँ", "हां", "ha", "avunu", "ho"}
_NO = {"2", "no", "nahi", "नहीं", "nako", "kaadu"}


def parse_digit(text: str) -> int | None:
    """A keypad digit or a spoken number, or None. Punctuation and politeness are
    stripped — "haan, do wala" is a 2."""
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    for token in cleaned.split():
        if token in _SPOKEN_DIGITS:
            return _SPOKEN_DIGITS[token]
    return None


def parse_yes_no(text: str) -> bool | None:
    cleaned = re.sub(r"[^\w\s]", " ", (text or "").lower())
    tokens = set(cleaned.split())
    if tokens & _YES:
        return True
    if tokens & _NO:
        return False
    return None


# -- the one model call --------------------------------------------------------


async def classify_intent(
    utterance: str,
    *,
    lang: Lang | str = Lang.HI,
    providers: list[LLMProvider] | None = None,
) -> IntentGuess:
    """Caller's words → one intent. Never raises: every failure is a handoff."""
    prompt = load("receptionist")
    request = LLMRequest(
        prompt=prompt.render(utterance=utterance, lang=str(lang)),
        system=prompt.system,
        prompt_ref=prompt.ref,
        json_output=True,
        # A switchboard is a lookup: the same sentence must reach the same desk
        # on Tuesday as it did on Monday.
        temperature=0.0,
        max_tokens=200,
    )
    chain = list(providers) if providers is not None else llm_chain()

    try:
        result = await with_fallback(
            chain, lambda provider: provider.complete(request, purpose=UsagePurpose.ROUTING)
        )
    except ProviderError as exc:
        logger.warning("receptionist classifier unavailable, handing to a human: %s", exc)
        return _to_human("classifier unavailable")

    try:
        payload = result.json()
    except Exception as exc:  # noqa: BLE001 - unreadable output is not an outage
        logger.warning("receptionist classifier returned unreadable output: %s", exc)
        return _to_human("classifier returned no usable answer")

    return _interpret(payload)


def _interpret(payload: object) -> IntentGuess:
    if not isinstance(payload, dict):
        return _to_human("classifier returned no usable answer")

    raw = payload.get("intent")
    try:
        intent = Intent(str(raw))
    except ValueError:
        logger.warning("receptionist classifier invented intent %r", raw)
        return _to_human(f"classifier chose {raw!r}, which is not an intent")

    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0  # no confidence is not high confidence
    confidence = min(1.0, max(0.0, float(confidence)))

    reason = payload.get("reason")
    hint = payload.get("when_hint")
    return IntentGuess(
        intent=intent,
        confidence=confidence,
        when_hint=hint.strip() if isinstance(hint, str) else "",
        reason=reason.strip() if isinstance(reason, str) else "",
    )


def _to_human(reason: str) -> IntentGuess:
    return IntentGuess(intent=Intent.HUMAN, confidence=0.0, reason=reason, from_model=False)


# -- the whisper summary -------------------------------------------------------


def whisper_summary(
    *, patient_name: str, intent: Intent | None, appointment: Appointment | None, lang: Lang
) -> str:
    """One line read to the coordinator before the caller is connected.

    doc 03 §2's own example — "Kamla Devi, wants to move Thursday review" — is the
    target shape: who, what they want, and the appointment in question. English
    only: it is read to staff, not to the patient.
    """
    who = patient_name or "Unknown caller"
    when = format_when(appointment.slot_at, Lang.EN) if appointment is not None else ""
    wants = {
        Intent.BOOK: "wants a new appointment",
        Intent.RESCHEDULE: f"wants to move {when}".strip(),
        Intent.CANCEL: f"wants to cancel {when}".strip(),
        Intent.STATUS: "asking about their appointment",
        Intent.HUMAN: "asked for a person",
        Intent.UNCLEAR: "could not be understood",
    }.get(intent or Intent.UNCLEAR, "could not be understood")
    return f"{who}, {wants}. Speaking {lang.value}."


# -- the state machine ---------------------------------------------------------


class Receptionist:
    """Drives one inbound appointment call, turn by turn.

    Stateless itself — the call's position is the `ReceptionistState` the channel
    holds — so one instance serves every concurrent call, like `IntakeEngine`.
    """

    def __init__(self, *, providers: list[LLMProvider] | None = None) -> None:
        self._providers = providers

    async def open(
        self, session: AsyncSession, *, cli: str, lang: Lang = Lang.HI
    ) -> tuple[ReceptionistState, Reply]:
        """Answer the phone. Looks the caller up by CLI so a known patient is
        greeted by name (doc 03 §1b's longitudinal context, applied to the
        appointment line)."""
        patient = await _patient_by_phone(session, cli)
        state = ReceptionistState(
            lang=patient.lang if patient is not None else lang,
            cli=cli,
            patient_id=patient.id if patient is not None else None,
            patient_name=patient.name if patient is not None else "",
        )
        if patient is None:
            return state, self._handoff(state, Intent.UNCLEAR, None, key="unknown_caller")
        return state, Reply(text=say("greeting_known", state.lang, name=patient.name))

    async def handle(
        self, session: AsyncSession, state: ReceptionistState, utterance: str
    ) -> Reply:
        """One caller turn in, one thing to say out."""
        if state.step is Step.DONE:
            return Reply(text=say("kept", state.lang), done=True)
        if state.step is Step.CHOOSING:
            return await self._choose(session, state, utterance)
        if state.step is Step.CONFIRMING:
            return await self._confirm_cancel(session, state, utterance)
        return await self._route_intent(session, state, utterance)

    # -- intent ---------------------------------------------------------------

    async def _route_intent(
        self, session: AsyncSession, state: ReceptionistState, utterance: str
    ) -> Reply:
        guess = await classify_intent(utterance, lang=state.lang, providers=self._providers)
        state.intent = guess.intent

        if guess.needs_human:
            appointment = await _next_appointment(session, state)
            return self._handoff(state, guess.intent, appointment)

        if guess.intent is Intent.BOOK:
            return await self._offer_slots(session, state)
        if guess.intent is Intent.RESCHEDULE:
            appointment = await _next_appointment(session, state)
            if appointment is None:
                return self._handoff(state, guess.intent, None, key="no_appointment")
            state.appointment_id = appointment.id
            return await self._offer_slots(session, state)
        if guess.intent is Intent.CANCEL:
            appointment = await _next_appointment(session, state)
            if appointment is None:
                return self._handoff(state, guess.intent, None, key="no_appointment")
            state.appointment_id = appointment.id
            state.step = Step.CONFIRMING
            return Reply(
                text=say(
                    "confirm_cancel",
                    state.lang,
                    when=format_when(appointment.slot_at, state.lang),
                ),
                expects_digit=True,
            )
        if guess.intent is Intent.STATUS:
            appointment = await _next_appointment(session, state)
            if appointment is None:
                return self._handoff(state, guess.intent, None, key="no_appointment")
            doctor = await _doctor_name(session, appointment.doctor_id)
            state.step = Step.DONE
            return Reply(
                text=say(
                    "status",
                    state.lang,
                    when=format_when(appointment.slot_at, state.lang),
                    doctor=doctor,
                ),
                done=True,
            )
        return self._failed_turn(session, state)

    # -- offering + booking ---------------------------------------------------

    async def _offer_slots(self, session: AsyncSession, state: ReceptionistState) -> Reply:
        department_id = await _preferred_department(session, state)
        offers = await find_slots(session, department_id=department_id, limit=OFFER_COUNT)
        if not offers and department_id is not None:
            # The caller's usual department is full for the horizon; the hospital
            # is not. Widening beats hanging up on them.
            offers = await find_slots(session, limit=OFFER_COUNT)
        if not offers:
            appointment = await _next_appointment(session, state)
            return self._handoff(state, state.intent, appointment, key="no_slots")

        state.offers = offers
        state.step = Step.CHOOSING
        lines = [say("offer_intro", state.lang)]
        for index, offer in enumerate(offers, start=1):
            lines.append(
                say(
                    "offer_line",
                    state.lang,
                    when=format_when(offer.starts_at, state.lang),
                    doctor=offer.doctor_name,
                    digit=index,
                )
            )
        return Reply(text=" ".join(lines), expects_digit=True)

    async def _choose(
        self, session: AsyncSession, state: ReceptionistState, utterance: str
    ) -> Reply:
        digit = parse_digit(utterance)
        if digit is None or not (1 <= digit <= len(state.offers)):
            return self._failed_turn(session, state)

        offer = state.offers[digit - 1]
        patient = await session.get(Patient, state.patient_id)
        if patient is None:  # pragma: no cover - open() refuses an unknown caller
            return self._handoff(state, state.intent, None, key="unknown_caller")

        try:
            if state.appointment_id is not None:
                appointment = await session.get(Appointment, state.appointment_id)
                await reschedule(session, appointment=appointment, slot_id=offer.slot_id)
                text_key = "moved"
            else:
                appointment = await book(
                    session,
                    patient=patient,
                    slot_id=offer.slot_id,
                    source=Channel.PHONE,
                    status=AppointmentStatus.CONFIRMED,
                )
                text_key = "booked"
        except SlotUnavailable:
            # Somebody took it while we were reading it out. Re-offer rather than
            # apologise and hang up — this is the common race, not an error.
            reply = await self._offer_slots(session, state)
            return Reply(
                text=f"{say('taken', state.lang)} {reply.text}",
                expects_digit=reply.expects_digit,
                handoff=reply.handoff,
                whisper=reply.whisper,
                done=reply.done,
            )
        except BookingError as exc:
            logger.info("receptionist booking refused: %s", exc)
            return self._failed_turn(session, state)

        state.booked_appointment_id = appointment.id
        state.step = Step.DONE
        await _confirm_by_message(session, appointment=appointment, patient=patient)
        return Reply(
            text=say(
                text_key,
                state.lang,
                when=format_when(appointment.slot_at, state.lang),
                doctor=offer.doctor_name,
            ),
            done=True,
        )

    # -- cancelling -----------------------------------------------------------

    async def _confirm_cancel(
        self, session: AsyncSession, state: ReceptionistState, utterance: str
    ) -> Reply:
        answer = parse_yes_no(utterance)
        if answer is None:
            return self._failed_turn(session, state)

        appointment = await session.get(Appointment, state.appointment_id)
        if appointment is None:  # pragma: no cover - set one turn earlier
            return self._handoff(state, state.intent, None, key="no_appointment")

        state.step = Step.DONE
        if not answer:
            return Reply(text=say("kept", state.lang), done=True)

        when = format_when(appointment.slot_at, state.lang)
        await cancel_appointment(session, appointment=appointment, reason="cancelled by caller")
        patient = await session.get(Patient, state.patient_id)
        if patient is not None:
            await _confirm_by_message(
                session, appointment=appointment, patient=patient, kind="cancelled"
            )
        return Reply(text=say("cancelled", state.lang, when=when), done=True)

    # -- failure + handoff ----------------------------------------------------

    def _failed_turn(self, session: AsyncSession, state: ReceptionistState) -> Reply:
        state.failures += 1
        if state.failures >= HANDOFF_AFTER_FAILURES:
            return self._handoff(state, state.intent, None)
        return Reply(text=say("retry", state.lang), expects_digit=state.step is Step.CHOOSING)

    def _handoff(
        self,
        state: ReceptionistState,
        intent: Intent | None,
        appointment: Appointment | None,
        *,
        key: str = "handoff",
    ) -> Reply:
        state.step = Step.DONE
        return Reply(
            text=say(key, state.lang),
            done=True,
            handoff=True,
            whisper=whisper_summary(
                patient_name=state.patient_name,
                intent=intent,
                appointment=appointment,
                lang=state.lang,
            ),
        )


# -- lookups -------------------------------------------------------------------


async def _patient_by_phone(session: AsyncSession, phone: str) -> Patient | None:
    """Match a caller by CLI. Compares the last 10 digits, because carriers hand
    us `+919876543210`, `919876543210` and `09876543210` for one handset."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[-10:]
    if len(digits) < 10:
        return None
    found = await session.execute(
        select(Patient).where(Patient.deleted_at.is_(None), Patient.phone.like(f"%{digits}"))
    )
    return found.scalars().first()


async def _next_appointment(session: AsyncSession, state: ReceptionistState) -> Appointment | None:
    if state.patient_id is None:
        return None
    upcoming = await upcoming_for_patient(session, patient_id=state.patient_id, limit=1)
    return upcoming[0] if upcoming else None


async def _preferred_department(
    session: AsyncSession, state: ReceptionistState
) -> uuid.UUID | None:
    """Where this caller is already being treated. A returning oncology patient
    asking for "an appointment" means their own clinic, not the first one with a
    free chair."""
    if state.patient_id is None:
        return None
    found = await session.execute(
        select(Appointment.department_id)
        .where(Appointment.patient_id == state.patient_id, Appointment.deleted_at.is_(None))
        .order_by(Appointment.slot_at.desc())
        .limit(1)
    )
    return found.scalar_one_or_none()


async def _doctor_name(session: AsyncSession, doctor_id: uuid.UUID | None) -> str:
    if doctor_id is None:
        return ""
    doctor = await session.get(Doctor, doctor_id)
    return doctor.name if doctor is not None else ""


async def _confirm_by_message(
    session: AsyncSession, *, appointment: Appointment, patient: Patient, kind: str = "booked"
) -> None:
    """WhatsApp + SMS, per doc 03 §2's AC. Failures are recorded, never raised —
    see `app.notify`."""
    hospital = await session.get(Hospital, patient.hospital_id)
    await notify_appointment(
        session,
        appointment=appointment,
        patient=patient,
        hospital_name=hospital.name if hospital is not None else "the hospital",
        doctor_name=await _doctor_name(session, appointment.doctor_id),
        kind=kind,
    )
