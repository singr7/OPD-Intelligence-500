"""The inbound receptionist call driver (S15) — the appointment line.

S14's `gw.call` bridges Exotel audio to the **intake** engine. This is its sibling
for **appointments**: the same transport, the same playback pump with barge-in,
the same silence-VAD and keypad handling, driving `app.receptionist` instead.

    start ─▶ greeting ─▶ [caller speaks] ─▶ STT ─▶ receptionist.handle ─▶ TTS ─▶ …
                                                          │
                                                          └─▶ handoff ─▶ transfer to
                                                                        a coordinator

Why a second driver rather than a flag on the first: the two calls have different
shapes. An intake is a tree walk the engine owns end to end, with a summary and a
cost per intake; a receptionist call is a short, mostly-keypad transaction against
slot inventory that writes appointments. Folding them together would mean an
`if receptionist:` in every branch of a 600-line driver, and the intake path is
the one that must not be disturbed.

What is shared is real and deliberate: `PlaybackPump`, `ExotelTurnSource`, the
frame codec, and the energy thresholds all come from `gw.call`, so barge-in and
the keypad behave identically on both numbers.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels import channel_state, resolve_config
from app.channels.state import closed_message
from app.config import Settings, get_settings
from app.models.enums import Channel, Lang
from app.providers.audio import AudioClip
from app.providers.base import ProviderError
from app.providers.metering import usage_scope
from app.providers.registry import get_telephony_provider, stt_chain, tts_chain
from app.providers.stt import STTProvider
from app.providers.telephony import TransferRequest
from app.providers.tts import TTSProvider
from app.receptionist import Receptionist
from app.receptionist import say as say_line

from gw.call import ExotelTurnSource, PlaybackPump, await_start, read_frames
from gw.exotel import ExotelTransport

logger = logging.getLogger(__name__)

PHONE = Channel.PHONE

#: doc 03 §2's AC: "end-to-end call books a real slot in <3 min". The cap is
#: generous against that target and well under S14's 8-minute intake cap — a
#: receptionist call that has run five minutes is one a human should have.
MAX_CALL_SECONDS = 5 * 60


@dataclass
class ReceptionistCallRecord:
    """The telephony facts about one appointment call.

    Deliberately thin next to `PhoneCallRecord`: there is no intake, no tier
    ladder and no per-intake cost here. What matters afterwards is whether the
    call booked something and whether a human ended up on it.
    """

    call_sid: str
    stream_sid: str
    cli: str | None = None
    lang: str = "hi"
    turns: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ended_at: str | None = None
    booked_appointment_id: str | None = None
    handed_off: bool = False
    whisper: str = ""
    end_reason: str = ""

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def handle_receptionist_call(
    transport: ExotelTransport,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings | None = None,
    tts: TTSProvider | None = None,
    stt: STTProvider | None = None,
    receptionist: Receptionist | None = None,
) -> ReceptionistCallRecord:
    """Run one inbound appointment call end to end. Returns the call record."""
    settings = settings or get_settings()
    tts = tts or tts_chain()[0]
    stt = stt or stt_chain()[0]
    receptionist = receptionist or Receptionist()

    start = await await_start(transport)
    lang = _lang_of(start.custom)
    record = ReceptionistCallRecord(
        call_sid=start.call_sid or uuid.uuid4().hex,
        stream_sid=start.stream_sid,
        cli=start.cli,
        lang=str(lang),
    )
    scope = {"channel": PHONE, "session_id": record.call_sid}

    async def say(text: str) -> AudioClip:
        if not text:
            return AudioClip(data=b"")
        try:
            with usage_scope(**scope):
                speech = await tts.synthesize(text, record.lang)
            return speech.audio
        except ProviderError:
            # A dead TTS is not a dead call: the caller hears nothing for a beat,
            # and the keypad still works. Better than dropping them.
            logger.warning("receptionist TTS unavailable on call %s", record.call_sid)
            return AudioClip(data=b"")

    pump = PlaybackPump(transport, record.stream_sid)

    # S-GL.1: the same phone switch the intake applet honours. The appointment line
    # is the other half of the phone channel, and a hospital that has not opened
    # phone has not opened this either — an AI receptionist booking real slots on a
    # number nobody has announced is precisely the half-configured channel doc 12 §4
    # is about.
    async with sessionmaker() as db:
        phone_state = channel_state(await resolve_config(db), Channel.PHONE, settings)
    if not phone_state.is_open:
        await pump.play(await say(closed_message(Channel.PHONE, lang)))
        record.end_reason = "channel_closed"
        record.ended_at = datetime.now(UTC).isoformat()
        logger.info(
            "receptionist call %s refused: channel closed (%s)",
            record.call_sid,
            phone_state.reason or "not open",
        )
        return record

    source = ExotelTurnSource(
        lang=record.lang,
        pump=pump,
        say=say,
        scope=scope,
        # On this line a digit is "the time you want", not yes/no — the intake's
        # 1=yes/2=no map would turn a slot choice into the word "yes".
        dtmf_answers={},
        keypad_prompt={lang.value: say_line("keypad", lang) for lang in Lang},
    )
    reader = asyncio.create_task(read_frames(transport, source, pump))

    try:
        await asyncio.wait_for(
            _converse(
                record,
                sessionmaker=sessionmaker,
                receptionist=receptionist,
                source=source,
                pump=pump,
                say=say,
                stt=stt,
                lang=lang,
                cli=start.cli or "",
                scope=scope,
                settings=settings,
            ),
            timeout=MAX_CALL_SECONDS,
        )
    except TimeoutError:
        # Anything booked before the cap is already committed, per turn.
        record.end_reason = "max_duration"
        logger.info("receptionist call %s hit the %ds cap", record.call_sid, MAX_CALL_SECONDS)
    finally:
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass

    record.ended_at = datetime.now(UTC).isoformat()
    return record


async def _converse(
    record: ReceptionistCallRecord,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    receptionist: Receptionist,
    source: ExotelTurnSource,
    pump: PlaybackPump,
    say,
    stt: STTProvider,
    lang: Lang,
    cli: str,
    scope: dict,
    settings: Settings,
) -> None:
    """Greeting, then one turn at a time until the receptionist says it is done."""
    async with sessionmaker() as session:
        state, reply = await receptionist.open(session, cli=cli, lang=lang)
        await session.commit()
        await pump.play(await say(reply.text))

        while not reply.done and record.turns < MAX_TURNS:
            turn = await source.next_turn()
            if turn is None:  # hangup
                record.end_reason = "caller_hung_up"
                break
            record.turns += 1
            utterance = await _utterance_text(turn, stt=stt, lang=record.lang, scope=scope)
            reply = await receptionist.handle(session, state, utterance)
            # Commit per turn: a call that drops after the booking must leave the
            # appointment behind, exactly as the intake driver saves answers per turn.
            await session.commit()
            await pump.play(await say(reply.text))

        record.booked_appointment_id = (
            str(state.booked_appointment_id) if state.booked_appointment_id else None
        )
        if reply.done and not record.end_reason:
            record.end_reason = "handoff" if reply.handoff else "complete"
        if reply.handoff:
            record.handed_off = True
            record.whisper = reply.whisper
            await _transfer(record, settings=settings)


#: A hard stop on turns as well as seconds: a caller stuck in a loop with a
#: misbehaving STT should reach a human, not talk to us until the 5-minute cap.
MAX_TURNS = 12


def _lang_of(custom: dict[str, str]) -> Lang:
    """The applet's `lang` parameter. Detecting the caller's language from their
    greeting (doc 01 §4.4) needs STT before the first prompt, which costs the one
    thing an anxious caller has none of; the number is provisioned per language
    instead, and the applet says which."""
    raw = (custom.get("lang") or "hi").lower()
    try:
        return Lang(raw)
    except ValueError:
        return Lang.HI


async def _utterance_text(turn, *, stt: STTProvider, lang: str, scope: dict) -> str:
    """A `PatientTurn` from the shared turn source → words.

    A keypad press already arrives as text (`gw.call.DTMF_ANSWERS`), and that path
    must never touch STT — the keypad exists precisely for callers whose speech
    the recogniser cannot hold.
    """
    if turn.text:
        return turn.text
    if turn.audio is None:
        return ""
    try:
        with usage_scope(**scope):
            result = await stt.transcribe(turn.audio, lang)
        return result.text
    except ProviderError:
        logger.warning("receptionist STT unavailable; treating the turn as unclear")
        return ""


async def _transfer(record: ReceptionistCallRecord, *, settings: Settings) -> None:
    """Bridge the caller to the coordinator, whisper first (doc 03 §2).

    A failed transfer is logged, not raised: the caller has already been told a
    person is coming, and crashing the websocket would drop them instead.
    """
    if not settings.coordinator_phone:
        logger.warning(
            "call %s wants a coordinator but COORDINATOR_PHONE is unset; whisper: %s",
            record.call_sid,
            record.whisper,
        )
        return
    try:
        await get_telephony_provider(settings).transfer_call(
            TransferRequest(
                call_sid=record.call_sid,
                to=settings.coordinator_phone,
                whisper=record.whisper,
                caller_id=settings.exotel_caller_id or None,
            )
        )
    except ProviderError as exc:
        logger.error("handoff transfer failed for call %s: %s", record.call_sid, exc)
