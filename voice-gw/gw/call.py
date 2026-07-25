"""The phone-call session driver (S14) — the Exotel channel over the intake engine.

One `handle_call` per websocket. It bridges the Exotel Voicebot protocol
(`gw.exotel`) to the shared `IntakeEngine`:

    start frame ─▶ consent line ─▶ start_session ─▶ engine.run(turn_source, on_audio)
                                                        ▲                     │
                                caller media/dtmf ──────┘                     ▼
                                                              assistant audio ─▶ Exotel media

The engine owns the tool loop, tier downgrade and the summary; this module owns the
things that are the *channel's* job and the engine explicitly defers to S14:

* **Consent** at call start, recorded (doc 03 §1b).
* **Barge-in** — stop playback the instant the caller speaks (`clear`), which the
  engine's V1 loop notes is "the channel's job (S14)". Implemented in the playback
  pump: inbound speech during playback flushes the queue and sends `clear`.
* **DTMF fallback** — after two utterances we could not make sense of, offer the
  keypad ("press 1 for yes") and take the digit as the answer (doc 03 §1b).
* **Utterance detection** — a phone call has no turn markers; a short window of
  near-silence ends an utterance (a stand-in for real VAD, tunable later).
* **Max 8 min**, **partial save on hangup**, and **cost recorded per intake** on the
  `Intake` row via the engine's `finalize_cost`.

Everything money is a `Decimal`/string (STATE.md): we never do rupee arithmetic here.
"""

from __future__ import annotations

import array
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.intake import IntakeEngine, PatientTurn, SessionState, SessionStatus
from app.models.enums import Channel, IntakeTier, Lang, VisitStatus
from app.providers.audio import PCM16, AudioClip
from app.providers.base import ProviderError
from app.providers.metering import get_meter, usage_scope
from app.providers.tts import TTSProvider
from app.trees import bank
from app.trees.schema import Tree

from gw.exotel import (
    Dtmf,
    ExotelTransport,
    Media,
    Start,
    Stop,
    encode_clear,
    encode_media,
    parse_inbound,
)

logger = logging.getLogger(__name__)

PHONE = Channel.PHONE
V1 = IntakeTier.CONVERSATIONAL
V2 = IntakeTier.RULE_BASED
V3 = IntakeTier.PRERECORDED

#: Ladder labels (config/tiers.yaml) → the engine tier the call opens on. `v_oss`
#: is the V2 pipeline backed by local providers (doc 08 §5), so it maps to V2 — the
#: registry decides local-vs-cloud, not the tier value.
LABEL_TO_TIER: dict[str, IntakeTier] = {
    "v1": V1,
    "v_oss": V2,
    "v2": V2,
    "v3": V3,
}

MAX_CALL_SECONDS = 8 * 60  # doc 03 §1b

#: Audio is 8 kHz 16-bit mono. One Exotel media frame ≈ 20 ms (320 bytes) so
#: playback can be interrupted between frames — that is what makes barge-in feel
#: instant rather than "after the sentence".
FRAME_BYTES = 320

#: Peak |sample| at/below this is silence — an utterance boundary (stand-in for VAD).
SILENCE_PEAK = 8
#: Non-silent but below this is a mumble we could not resolve — the DTMF trigger.
#: A proxy for STT confidence <0.5 (doc 03 §1b): the engine's STT confidence is not
#: visible to the channel, so we read the channel's own audio energy instead. Tunable
#: against the S13 language QA harness on real Alwar-accented telephony audio.
UNCLEAR_PEAK = 1500
#: Two unclear attempts, then the keypad (doc 03 §1b).
DTMF_TRIGGER = 2
#: Keypad → answer. 1/2 are the yes/no fallback; the engine's V2 safety net coerces
#: the word onto a single-choice node even if the model forgets to save it.
DTMF_ANSWERS: dict[str, str] = {"1": "yes", "2": "no"}

# Romanized placeholders (mr/te native-script + clinical review is owed — S21, same
# carry as the rest of the mr/te copy). en/hi are the ones exercised in the pilot.
CONSENT_TEXT: dict[str, str] = {
    "en": "This call is with an automated health assistant and is recorded to help "
    "your doctor. Please speak after the tone.",
    "hi": "yah call ek svachaalit svaasthya sahaayak ke saath hai aur aapke doctor "
    "kee madad ke lie record kee jaatee hai. kripya tone ke baad boliye.",
    "mr": "ha call ek svayamchalit aarogya sahayyakashi aahe aani tumchya doctorsathi "
    "record kela jato. kripya tone nantar bola.",
    "te": "ee call oka swayamchaalaka aarogya sahaayakutho jarugutundi mariyu mee "
    "doctor koraku record cheyabadutundi. dayachesi tone taruvaata maatlaadandi.",
}

DTMF_PROMPT: dict[str, str] = {
    "en": "Sorry, I did not catch that. Press 1 for yes, 2 for no.",
    "hi": "maaf keejiye, samajh nahin aaya. haan ke lie 1 dabaaiye, nahin ke lie 2.",
    "mr": "maaf kara, samajle nahi. hoyisathi 1 dabava, nahisathi 2.",
    "te": "kshaminchandi, ardham kaaledu. avunuku 1 nokkandi, kaaduku 2.",
}


# -- call-state record (persisted; the S15 status callback reconciles against it) --


@dataclass
class PhoneCallRecord:
    """What we know about a call, independent of the intake. Persisted so a mid-call
    crash and the S15 Exotel status callback have state to reconcile against — the
    intake row carries the clinical result, this carries the telephony facts."""

    call_sid: str
    stream_sid: str
    cli: str | None = None
    tier: str = ""
    lang: str = "hi"
    consent_at: str | None = None
    state: str = "in-progress"  # mirrors app.providers.telephony.CallState values
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ended_at: str | None = None
    session_id: str | None = None
    intake_id: str | None = None
    cost_inr: str | None = None
    end_reason: str | None = None
    keypad_prompts: int = 0  # DTMF fallbacks used on this call

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


class PhoneCallStore:
    """In-memory call-record store (dev/tests). Mirrors `build_session_store`: the
    Redis-backed variant is used off-box; the interface is the same so the S15
    callback can look a call up by sid."""

    def __init__(self) -> None:
        self._records: dict[str, PhoneCallRecord] = {}

    async def save(self, record: PhoneCallRecord) -> None:
        self._records[record.call_sid] = record

    async def get(self, call_sid: str) -> PhoneCallRecord | None:
        return self._records.get(call_sid)


def build_phonecall_store(settings: Settings) -> PhoneCallStore:
    # A dedicated Redis store lands with the S15 status-callback webhook; the pilot
    # runs a single voice-gw process, so in-memory is correct until then.
    return PhoneCallStore()


# -- audio helpers ------------------------------------------------------------


def peak(clip: AudioClip) -> int:
    """Peak |sample| of a 16-bit PCM clip — the channel's crude energy read for VAD
    and the DTMF trigger. Zero for empty/odd data rather than raising."""
    data = clip.data
    if len(data) < 2:
        return 0
    samples = array.array("h")
    samples.frombytes(data[: len(data) - (len(data) % 2)])
    return max((abs(s) for s in samples), default=0)


def _iter_frames(data: bytes):
    for i in range(0, len(data), FRAME_BYTES):
        yield data[i : i + FRAME_BYTES]


# -- playback pump: assistant audio out, with barge-in ------------------------


class PlaybackPump:
    """Streams assistant audio to Exotel one small frame at a time, so a barge-in can
    stop it mid-clip. `on_audio` (the engine's passthrough sink) calls `play`."""

    def __init__(self, transport: ExotelTransport, stream_sid: str) -> None:
        self._transport = transport
        self._stream_sid = stream_sid
        self._interrupted = False
        self._playing = False
        self.clears = 0  # barge-in count, for tests/telemetry

    @property
    def is_playing(self) -> bool:
        return self._playing

    async def play(self, clip: AudioClip) -> None:
        if not clip.data:
            return
        self._playing = True
        self._interrupted = False
        try:
            for frame in _iter_frames(clip.data):
                if self._interrupted:
                    break
                await self._transport.send(
                    encode_media(self._stream_sid, AudioClip(data=frame, mime=PCM16))
                )
                # Yield so a concurrent reader can barge in between frames. No real
                # sleep — tests stay fast; a live gateway paces to wall-clock audio.
                await asyncio.sleep(0)
        finally:
            self._playing = False

    async def barge_in(self) -> None:
        """The caller spoke while we were talking: drop what is queued and tell Exotel
        to flush its buffer (doc 03 §1b)."""
        if not self._playing:
            return
        self._interrupted = True
        self.clears += 1
        await self._transport.send(encode_clear(self._stream_sid))


# -- turn source: caller audio in, as PatientTurns ----------------------------


class ExotelTurnSource:
    """Turns the inbound Exotel frame stream into `PatientTurn`s the engine pulls one
    at a time (`IntakeEngine`'s streaming `turn_source`, S14).

    A `reader` task feeds frames in via `feed`; `next_turn` groups media into an
    utterance (ended by a near-silent frame), and returns None on hangup. It also
    owns the DTMF fallback: two unclear utterances → speak the keypad prompt and take
    the next digit as the answer.
    """

    def __init__(
        self,
        *,
        lang: str,
        pump: PlaybackPump,
        say,  # Callable[[str], Awaitable[AudioClip]]
        scope: dict[str, Any],
    ) -> None:
        self._q: asyncio.Queue[Media | Dtmf | Stop] = asyncio.Queue()
        self._lang = lang
        self._pump = pump
        self._say = say
        self._scope = scope
        self._unclear_streak = 0
        self._closed = False
        self.keypad_prompts = 0  # how many times we fell back to the keypad (tests/telemetry)

    async def feed(self, frame: Media | Dtmf | Stop) -> None:
        await self._q.put(frame)

    async def next_turn(self) -> PatientTurn | None:
        if self._closed:
            return None
        buf = bytearray()
        while True:
            frame = await self._q.get()
            if isinstance(frame, Stop):
                self._closed = True
                if buf:
                    return PatientTurn(audio=self._clip(buf))
                return None
            if isinstance(frame, Dtmf):
                # A keypad press ends any utterance immediately and is the answer.
                answer = DTMF_ANSWERS.get(frame.digit, frame.digit)
                self._unclear_streak = 0
                return PatientTurn(text=answer, audio=None)
            # Media
            if peak(frame.audio) <= SILENCE_PEAK:
                if not buf:
                    continue  # leading silence — keep waiting for speech
                clip = self._clip(buf)
                if peak(clip) > UNCLEAR_PEAK:  # clear speech — a real answer
                    self._unclear_streak = 0
                    return PatientTurn(audio=clip)
                # Could not make it out. Do NOT answer on a guess — count it, and
                # after two tries fall to the keypad (doc 03 §1b). Reset the buffer
                # and keep listening rather than returning a turn.
                self._unclear_streak += 1
                if self._unclear_streak >= DTMF_TRIGGER:
                    return await self._offer_keypad()
                buf = bytearray()
                continue
            buf.extend(frame.audio.data)

    async def _offer_keypad(self) -> PatientTurn | None:
        """Two unclear tries: play the keypad prompt, then block for a digit."""
        self._unclear_streak = 0
        self.keypad_prompts += 1
        with usage_scope(**self._scope):
            await self._pump.play(await self._say(DTMF_PROMPT.get(self._lang, DTMF_PROMPT["en"])))
        while True:
            frame = await self._q.get()
            if isinstance(frame, Stop):
                self._closed = True
                return None  # hangup during the prompt → graceful partial
            if isinstance(frame, Dtmf):
                return PatientTurn(text=DTMF_ANSWERS.get(frame.digit, frame.digit), audio=None)
            # ignore further audio while we wait for the keypad

    @staticmethod
    def _clip(buf: bytearray) -> AudioClip:
        return AudioClip(data=bytes(buf), mime=PCM16, sample_rate=8000, channels=1)


# -- phone intake rows --------------------------------------------------------


@dataclass
class PhoneIntake:
    patient_id: uuid.UUID
    visit_id: uuid.UUID
    intake_id: uuid.UUID


async def create_phone_intake(
    session: AsyncSession,
    *,
    cli: str | None,
    lang: Lang,
    tree: Tree,
    tier: IntakeTier,
    consent_marker: str | None,
) -> PhoneIntake | None:
    """Create (or attach to) the patient, visit and intake a phone call needs before
    the engine can persist answers and cost.

    The patient is looked up by CLI (doc 03 §1b: caller identified by their number)
    and created anonymously if unknown. Returns None if the tree's department is not
    seeded — the caller can still be served (the engine runs DB-less), we just have
    nowhere durable to write, which a live box never hits after `make seed`.
    """
    from app.models.clinical import Intake, Visit
    from app.models.org import Department
    from app.models.patient import Patient

    dept = await session.scalar(select(Department).where(Department.code == tree.department))
    if dept is None:
        logger.warning("no department %s seeded; phone intake runs DB-less", tree.department)
        return None

    patient: Patient | None = None
    if cli:
        patient = await session.scalar(
            select(Patient).where(Patient.hospital_id == dept.hospital_id, Patient.phone == cli)
        )
    if patient is None:
        patient = Patient(
            hospital_id=dept.hospital_id,
            mrn=f"PHONE-{uuid.uuid4().hex[:10].upper()}",
            name="Phone caller",
            phone=cli or "",
            lang=lang,
        )
        session.add(patient)
        await session.flush()

    # Consent is recorded on the patient (doc 03 §1b): the date it was given and a
    # reference to the recording. Real object storage of the clip is S15/S19; the
    # marker keeps the audit truthful until then.
    if consent_marker:
        patient.consent_given_at = date.today()
        patient.consent_audio_url = consent_marker

    visit = Visit(
        patient_id=patient.id,
        department_id=dept.id,
        date=datetime.now(UTC).date(),
        status=VisitStatus.REGISTERED,
        channel=PHONE,
    )
    session.add(visit)
    await session.flush()

    intake = Intake(visit_id=visit.id, tier=tier, lang=lang)
    session.add(intake)
    await session.flush()

    return PhoneIntake(patient_id=patient.id, visit_id=visit.id, intake_id=intake.id)


# -- tier / tree / lang resolution --------------------------------------------


def resolve_tier(custom: dict[str, str], settings: Settings) -> IntakeTier:
    """The tier the call opens on: an explicit `tier` custom parameter (the S15 applet
    / a test pins it), else the top of the phone ladder from config/tiers.yaml."""
    label = (custom.get("tier") or "").lower()
    if label in LABEL_TO_TIER:
        return LABEL_TO_TIER[label]
    from app.tiers import get_tier_config

    ladder = get_tier_config().ladder_for(PHONE)
    for lbl in ladder:
        if lbl in LABEL_TO_TIER:
            return LABEL_TO_TIER[lbl]
    return V2


def resolve_lang(custom: dict[str, str]) -> Lang:
    raw = (custom.get("lang") or "hi").lower()
    try:
        return Lang(raw)
    except ValueError:
        return Lang.HI


def resolve_tree(custom: dict[str, str]) -> Tree:
    """The tree to run. The applet/campaign passes a `tree` key; routing a caller's
    spoken chief complaint to a department is the inbound receptionist's job (S15).
    Falls back to the first pilot routing tree so a bare call still gets served."""
    key = custom.get("tree")
    if key:
        return bank.get(key)
    return bank.get("general_medicine_routing")


# -- the driver ---------------------------------------------------------------


async def handle_call(
    transport: ExotelTransport,
    *,
    engine: IntakeEngine,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    tts: TTSProvider | None = None,
    phonecall_store: PhoneCallStore | None = None,
    tree: Tree | None = None,
) -> PhoneCallRecord:
    """Run one phone intake end to end over `transport`. Returns the call record."""
    settings = settings or get_settings()
    phonecall_store = phonecall_store or build_phonecall_store(settings)
    if tts is None:
        from app.providers.registry import tts_chain

        tts = tts_chain()[0]

    start = await _await_start(transport)
    lang = resolve_lang(start.custom)
    tier = resolve_tier(start.custom, settings)
    tree = tree or resolve_tree(start.custom)

    record = PhoneCallRecord(
        call_sid=start.call_sid or uuid.uuid4().hex,
        stream_sid=start.stream_sid,
        cli=start.cli,
        tier=tier.value,
        lang=str(lang),
    )
    await phonecall_store.save(record)

    async def say(text: str) -> AudioClip:
        try:
            speech = await tts.synthesize(text, str(lang))
            return speech.audio
        except ProviderError:
            return AudioClip(data=b"")

    pump = PlaybackPump(transport, record.stream_sid)

    # Consent first, and recorded (doc 03 §1b). Metered under the phone scope.
    consent_scope = {"channel": PHONE, "tier": tier}
    with usage_scope(**consent_scope):
        await pump.play(await say(CONSENT_TEXT.get(str(lang), CONSENT_TEXT["en"])))
    consent_marker = f"consent:phone:{record.call_sid}"
    record.consent_at = datetime.now(UTC).isoformat()
    await phonecall_store.save(record)

    # Durable rows (best-effort; the engine runs DB-less if unseeded/no DB).
    phone_intake: PhoneIntake | None = None
    if sessionmaker is not None:
        async with sessionmaker() as db:
            phone_intake = await create_phone_intake(
                db, cli=start.cli, lang=lang, tree=tree, tier=tier, consent_marker=consent_marker
            )
            await db.commit()

    state = await engine.start_session(
        tree=tree,
        channel=PHONE,
        lang=lang,
        configured_tier=tier,
        intake_id=phone_intake.intake_id if phone_intake else None,
        visit_id=phone_intake.visit_id if phone_intake else None,
    )
    record.session_id = state.session_id
    record.intake_id = str(state.intake_id) if state.intake_id else None
    record.tier = state.active_tier.value  # the cost guard may have opened us lower
    await phonecall_store.save(record)

    scope = {"session_id": state.session_id, "channel": PHONE, "tier": state.active_tier}
    source = ExotelTurnSource(lang=str(lang), pump=pump, say=say, scope=scope)
    reader = asyncio.create_task(_read_frames(transport, source, pump))

    try:
        await asyncio.wait_for(
            engine.run(state, on_audio=pump.play, turn_source=source),
            timeout=MAX_CALL_SECONDS,
        )
        record.end_reason = _end_reason(state)
    except TimeoutError:
        # Max call length: the answers so far are already persisted per turn, so this
        # is a graceful partial (doc 03 §1b). Reload the latest state to finalise.
        state = await engine.store.get(state.session_id) or state
        record.end_reason = "max_duration"
        logger.info("phone call %s hit the %ds cap", record.call_sid, MAX_CALL_SECONDS)
    finally:
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass

    # Cost per intake: drain the meter so finalize sums a complete set of rows, then
    # write the total onto the intake (doc 02 §8). Same sequence as the kiosk.
    meter = get_meter()
    if meter is not None:
        await meter.flush()
    if sessionmaker is not None and state.intake_id is not None:
        async with sessionmaker() as db:
            # Eager-load intake.visit so finalize_cost → _persist_intake reads it
            # without a lazy load (async lazy loads raise MissingGreenlet). The kiosk
            # gets this for free from its request-scoped session; a fresh session here
            # must force it. `refresh` loads the relationship inside the async context,
            # and the internal get() in _persist_intake reuses this identity-mapped row.
            from app.models.clinical import Intake

            intake = await db.get(Intake, state.intake_id)
            if intake is not None and state.visit_id is not None:
                await db.refresh(intake, ["visit"])
            cost = await engine.finalize_cost(state, db)
            await db.commit()
            record.cost_inr = str(cost)
    else:
        record.cost_inr = str(state.cost_inr or Decimal("0"))

    record.keypad_prompts = source.keypad_prompts
    record.state = "completed"
    record.ended_at = datetime.now(UTC).isoformat()
    await phonecall_store.save(record)
    return record


async def _await_start(transport: ExotelTransport) -> Start:
    """Read frames until the `start` — Exotel sends a `connected` first."""
    while True:
        raw = await transport.receive()
        if raw is None:
            raise ConnectionError("socket closed before start frame")
        frame = parse_inbound(raw)
        if isinstance(frame, Start):
            return frame


async def _read_frames(
    transport: ExotelTransport, source: ExotelTurnSource, pump: PlaybackPump
) -> None:
    """Pump inbound frames to the turn source, and barge in when the caller speaks
    over us. Runs for the life of the call; cancelled when the intake ends."""
    while True:
        raw = await transport.receive()
        if raw is None:
            await source.feed(Stop())
            return
        frame = parse_inbound(raw)
        if isinstance(frame, Media):
            if pump.is_playing and peak(frame.audio) > SILENCE_PEAK:
                await pump.barge_in()
            await source.feed(frame)
        elif isinstance(frame, (Dtmf, Stop)):
            await source.feed(frame)
            if isinstance(frame, Stop):
                return
        # connected / mark: nothing to route


def _end_reason(state: SessionState) -> str:
    if state.status is SessionStatus.COMPLETE:
        return "complete"
    if state.status in (SessionStatus.ENDED, SessionStatus.HANDOFF):
        return "patient_ended"
    return state.status.value
