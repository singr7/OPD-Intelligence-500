"""IntakeEngine (doc 02 §5) — one intake, three tiers, one set of answers.

> "One Intake Engine service class consumed by all channels." — doc 02 §5

The engine owns an intake's life: it starts a session, drives the conversation on
whichever tier is live, downgrades that tier without losing a word when a provider
dies or the budget trips, produces the doctor summary and the patient read-back,
and finalises the cost. Channels (kiosk WS in S6, Exotel WS in S14, WhatsApp in
S12) are thin adapters that feed it patient turns and play back its audio; the
clinical logic is not theirs and not the model's — it is the tree and the rules.

## Why the tiers share everything that matters

All three tiers call the *same* `ToolDispatcher` over the *same* `Walk`
(`app.intake.dispatch`), and position is derived from the stored answers
(`app.intake.state`). So the three tiers differ only in **how the tools get
called**, never in what they mean:

- **V1 (Gemini Live)** — a full-duplex session; the model calls the tools and the
  engine bridges each call to the dispatcher, streaming the model's audio through
  to the channel (`on_audio`, the voice-gw passthrough hook, S14).
- **V2 (STT → LLM → TTS)** — a pipeline; the engine hears the patient (STT), asks
  the dialogue model to map the answer via the tool contract, and speaks the
  reply (TTS).
- **V3 (deterministic)** — no model at all; the engine walks the tree and plays
  pre-recorded audio (`app.intake.voicepack`), the offline/zero-AI floor.

A downgrade (V1→V2→V3) rebuilds the dispatcher on the lower tier from the same
stored answers and keeps going. Because the answers are the only state, the
patient never re-answers anything — the property doc 03 §1's AC demands and the
whole reason the walker refuses to store a cursor.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intake import voicepack as voicepack_mod
from app.intake.dispatch import ToolDispatcher
from app.intake.interpret import Interpreter, LLMInterpreter
from app.intake.state import SessionState, SessionStatus, SessionStore
from app.intake.summary import LANG_NAMES, LLMSummarizer, Summarizer, TemplateSummarizer
from app.intake.voicepack import EMPTY_PACK, VoicePack
from app.languages import script_problem
from app.models.clinical import Intake
from app.models.enums import Channel, IntakeTier, Lang, UsagePurpose, VisitStatus
from app.models.metering import UsageEvent
from app.prompts import load
from app.prompts.tools import INTAKE_TOOLS
from app.providers import (
    AudioClip,
    LLMProvider,
    LLMRequest,
    ProviderError,
    ProviderUnavailable,
    STTProvider,
    TTSProvider,
    get_realtime_provider,
    llm_chain,
    stt_chain,
    tts_chain,
    usage_scope,
    with_fallback,
)
from app.providers.costguard import LADDER, CostGuard, downgrade, get_guard
from app.providers.profiles import VoiceProfileSnapshot, resolve_profile
from app.providers.realtime import RealtimeConfig, RealtimeSession, RealtimeVoiceProvider
from app.trees import bank
from app.trees.schema import Tree

logger = logging.getLogger(__name__)

AudioSink = Callable[[AudioClip], Awaitable[None]]

#: The three automated tiers, top to bottom (paper is a human's downtime call, not
#: the engine's — mirrors `costguard.LADDER`).
V1 = IntakeTier.CONVERSATIONAL
V2 = IntakeTier.RULE_BASED
V3 = IntakeTier.PRERECORDED

#: A patient turn cannot spin the V1 event loop forever — a misbehaving script or
#: a model that never completes a turn must surface as a bug, not a hang.
_V1_EVENT_TIMEOUT = 5.0
_V1_MAX_EVENTS = 500


@dataclass(slots=True)
class PatientTurn:
    """One thing the patient did, in whatever form the live tier consumes.

    `audio` is what V1/V2 hear (STT, or the Live model directly); `answer` is the
    option id / number the kiosk taps for V3. A turn carries both so the *same*
    scripted intake survives a downgrade: when V2 falls to V3 mid-session, the
    remaining turns still have the tap value V3 needs. `text` is the patient's own
    words, kept verbatim for the doctor (doc 03 §4) regardless of tier.
    """

    audio: AudioClip | None = None
    answer: Any = None
    text: str | None = None


@runtime_checkable
class TurnSource(Protocol):
    """A live source of patient turns for a streaming channel (voice-gw, S14).

    A phone call does not know its turns up front the way a scripted test does: the
    caller speaks, we detect the end of the utterance, and only then is there a turn
    to feed the pipeline. The channel adapter implements this — one `PatientTurn`
    per utterance, then `None` when the caller hangs up. A fixed
    `Sequence[PatientTurn]` (kiosk replays, unit tests) is adapted to the same shape
    by `_Turns`, so the run loop has exactly one code path for both.
    """

    async def next_turn(self) -> PatientTurn | None: ...


class _Turns:
    """A fixed sequence or a live `TurnSource`, with pushback for the downgrade
    re-ask. The pipeline pulls with `next()`; when a tier fails mid-turn it pushes
    the un-answered turn back so the lower tier re-asks it — no answer is ever lost
    (the invariant the old `deque.appendleft` carried)."""

    def __init__(
        self,
        *,
        turns: Sequence[PatientTurn] = (),
        source: TurnSource | None = None,
    ) -> None:
        self._deque: deque[PatientTurn] = deque(turns)
        self._source = source
        self._pushback: deque[PatientTurn] = deque()

    async def next(self) -> PatientTurn | None:
        if self._pushback:
            return self._pushback.popleft()
        if self._source is not None:
            return await self._source.next_turn()
        return self._deque.popleft() if self._deque else None

    def pushback(self, turn: PatientTurn) -> None:
        self._pushback.appendleft(turn)


class IntakeEngine:
    """Drives intakes across the tier ladder. One instance per process is fine —
    it holds no per-intake state; the `SessionStore` does."""

    def __init__(
        self,
        store: SessionStore,
        *,
        realtime: RealtimeVoiceProvider | None = None,
        llm_providers: Sequence[LLMProvider] | None = None,
        stt_providers: Sequence[STTProvider] | None = None,
        tts_provider: TTSProvider | None = None,
        guard: CostGuard | None = None,
        voicepack: VoicePack = EMPTY_PACK,
        adaptive: bool = False,
        interpreter: Interpreter | None = None,
    ) -> None:
        self._store = store
        self._realtime = realtime
        self._llm = list(llm_providers) if llm_providers is not None else None
        self._stt = list(stt_providers) if stt_providers is not None else None
        self._tts = tts_provider
        self._guard = guard
        self._voicepack = voicepack
        # Adaptive intake (S-ADAPT.1, doc 11): off unless the flag is on AND a real
        # LLM is wired (the lifespan gates on both). `interpreter` lets a test inject
        # the deterministic `FakeInterpreter` without the flag.
        self._adaptive = adaptive
        self._interpreter = interpreter

    @property
    def store(self) -> SessionStore:
        """The session store this engine reads/writes. Channel adapters need it to
        load a state by id before rebuilding a dispatcher on it."""
        return self._store

    # -- provider accessors (resolved lazily so tests can inject) --------------

    def _realtime_provider(self) -> RealtimeVoiceProvider:
        return self._realtime or get_realtime_provider()

    def _llm_chain(self, state: SessionState | None = None) -> list[LLMProvider]:
        if self._llm is not None:
            return self._llm
        if state is not None and state.voice_profile is not None:
            return list(resolve_profile(state.voice_profile).llm)
        return llm_chain()

    def answer_interpreter(self, state: SessionState | None = None) -> Interpreter | None:
        """The adaptive answer interpreter, or None when adaptive intake is off.

        A `None` here is what makes doc 04 law 8 true by construction: the kiosk
        answer route only interprets a spoken answer when this returns something, so
        flag-off / no-LLM is byte-for-byte today's tap flow (doc 11 §5). A test may
        inject a `FakeInterpreter`; otherwise it is the `interpret_answer` prompt on
        the same LLM chain the summariser uses."""
        if self._interpreter is not None:
            return self._interpreter
        if not self._adaptive:
            return None
        return LLMInterpreter(self._llm_chain(state))

    def _stt_chain(self, state: SessionState | None = None) -> list[STTProvider]:
        if self._stt is not None:
            return self._stt
        if state is not None and state.voice_profile is not None:
            return list(resolve_profile(state.voice_profile).stt)
        return stt_chain()

    def _tts_one(self, state: SessionState | None = None) -> TTSProvider:
        if self._tts is not None:
            return self._tts
        if state is not None and state.voice_profile is not None:
            return resolve_profile(state.voice_profile).tts[0]
        return tts_chain()[0]

    def _cost_guard(self) -> CostGuard | None:
        return self._guard or get_guard()

    # -- lifecycle ------------------------------------------------------------

    async def start_session(
        self,
        *,
        tree: Tree,
        channel: Channel,
        lang: Lang | str,
        configured_tier: IntakeTier = V1,
        session_id: str | None = None,
        intake_id: uuid.UUID | None = None,
        visit_id: uuid.UUID | None = None,
        chief_complaint: str | None = None,
        chief_complaint_en: str | None = None,
        voice_profile: VoiceProfileSnapshot | None = None,
    ) -> SessionState:
        """Open an intake and persist it. Active tier respects the cost guard from
        the first turn — a channel already over budget starts on the cheaper tier
        rather than downgrading after the first expensive call."""
        active = configured_tier
        guard = self._cost_guard()
        if guard is not None:
            active = await guard.effective_tier(channel, configured_tier)

        state = SessionState(
            session_id=session_id or uuid.uuid4().hex,
            channel=channel,
            lang=Lang(lang),
            tree_key=tree.key,
            tree_version=tree.version,
            department=tree.department,
            intake_id=intake_id,
            visit_id=visit_id,
            configured_tier=configured_tier,
            active_tier=active,
            chief_complaint=chief_complaint,
            chief_complaint_en=chief_complaint_en,
            voice_profile=voice_profile,
        )
        await self._store.save(state)
        return state

    def _tree(self, state: SessionState) -> Tree:
        """Reload the tree from the bank — never trust a dict thawed from Redis
        (STATE.md: a `Tree` is only valid through `schema.parse`)."""
        return bank.get(state.tree_key)

    def dispatcher(self, state: SessionState, tree: Tree | None = None) -> ToolDispatcher:
        tree = tree or self._tree(state)
        return ToolDispatcher(state, tree, self._store, self._summarizer(state))

    def _summarizer(self, state: SessionState) -> Summarizer:
        """The LLM writes the summary whenever a real one is reachable; the
        deterministic template covers every case where it is not.

        V3 used to be pinned to the template unconditionally, on the reasoning
        that V3 is the offline, zero-AI tier. That conflated two different things:
        *the walk* must be deterministic and offline-capable — and still is, since
        nothing here touches traversal or red flags — but *the summary* is
        presentation, and pinning it meant a cloud kiosk with a configured vendor
        still handed the doctor a transcript of questions and answers instead of a
        summary.

        The offline guarantee survives because it is structural rather than
        remembered: a box with no configured LLM gets the template by the check
        below, and a box whose LLM is merely *down* gets it from
        `_ResilientSummarizer`. Degrade, never deny (doc 02 §5).
        """
        template = TemplateSummarizer()
        chain = self._llm_chain(state)
        if not _has_real_llm(chain):
            return template
        return _ResilientSummarizer(LLMSummarizer(chain), template)

    # -- the run loop ---------------------------------------------------------

    async def run(
        self,
        state: SessionState,
        turns: Sequence[PatientTurn] = (),
        *,
        on_audio: AudioSink | None = None,
        turn_source: TurnSource | None = None,
    ) -> SessionState:
        """Drive the intake to completion (or graceful partial).

        Turns come either as a fixed `turns` sequence (scripted tests, kiosk replays)
        or a live `turn_source` — the streaming case a phone call needs, where the
        next turn only exists once the caller has finished speaking (voice-gw, S14).
        Exactly one is used; `turn_source` wins if both are given.

        `on_audio` is the voice-gw passthrough sink (S14): every chunk of assistant
        speech is handed to it as it is produced. Left None, audio is synthesised
        and metered but discarded — which is what a text-only test or a kiosk that
        plays audio itself wants.
        """
        tree = self._tree(state)
        pending = _Turns(turns=turns, source=turn_source)

        with usage_scope(
            session_id=state.session_id,
            intake_id=state.intake_id,
            visit_id=state.visit_id,
            channel=state.channel,
            voice_profile=state.voice_profile.name.value if state.voice_profile else None,
        ):
            if state.active_tier is V1:
                try:
                    await self._run_v1(state, tree, pending, on_audio)
                    return state
                except ProviderUnavailable as exc:
                    logger.warning("V1 Live session failed (%s); downgrading to V2", exc)
                    await self._downgrade(state)

            await self._run_pipeline(state, tree, pending, on_audio)
        return state

    async def _run_pipeline(
        self,
        state: SessionState,
        tree: Tree,
        pending: _Turns,
        on_audio: AudioSink | None,
    ) -> None:
        """V2 and V3: one patient turn per question, with downgrade between turns.

        Rebuilds the dispatcher each iteration so that a downgrade mid-loop resumes
        from the stored answers on the new tier (the whole point of deriving
        position). The loop ends when the tree completes, the patient input runs
        out (partial save, doc 03 §1b — a hangup or an empty script), or the session
        is otherwise closed.
        """
        guard_steps = 0
        while state.status is SessionStatus.ACTIVE:
            await self._maybe_costguard_downgrade(state)
            dispatcher = self.dispatcher(state, tree)

            if dispatcher.walk.is_complete:
                await self._finish(dispatcher, "complete")
                break
            turn = await pending.next()
            if turn is None:
                await self._finish(dispatcher, "patient_ended")
                break

            try:
                with usage_scope(tier=state.active_tier):
                    if state.active_tier is V3:
                        await self._turn_v3(dispatcher, state, tree, turn, on_audio)
                    else:
                        await self._turn_v2(dispatcher, state, tree, turn, on_audio)
            except ProviderUnavailable as exc:
                logger.warning(
                    "tier %s turn failed (%s); downgrading and retrying the turn",
                    state.active_tier,
                    exc,
                )
                await self._downgrade(state)
                pending.pushback(turn)  # no answer was lost; re-ask on the lower tier

            guard_steps += 1
            if guard_steps > _V1_MAX_EVENTS:  # pragma: no cover - runaway guard
                raise RuntimeError("intake pipeline did not terminate")

    # -- V3: deterministic walker + pre-recorded voice ------------------------

    async def _turn_v3(
        self,
        dispatcher: ToolDispatcher,
        state: SessionState,
        tree: Tree,
        turn: PatientTurn,
        on_audio: AudioSink | None,
    ) -> None:
        node = dispatcher.walk.current
        if node is None:
            return
        # Play the question (pre-recorded if we have it, TTS otherwise). A TTS
        # outage here is not fatal — V3 keeps working when the AI is down.
        speech = await voicepack_mod.resolve(
            node, state.lang, voicepack=self._voicepack, tts=self._maybe_tts(state)
        )
        state.record_turn("assistant", node.ask(state.lang), lang=state.lang)
        if speech is not None and on_audio is not None:
            await on_audio(speech.audio)

        value = turn.answer if turn.answer is not None else turn.text
        result = await dispatcher.save_answer(node.id, value, raw_text=turn.text, lang=state.lang)
        if not result["ok"]:
            # A tap that does not fit the node is a client bug (the kiosk offers
            # only valid options); log and move on rather than wedge the intake.
            logger.warning("V3 answer rejected for %s: %s", node.id, result.get("error"))

    def _maybe_tts(self, state: SessionState | None = None) -> TTSProvider | None:
        try:
            return self._tts_one(state)
        except Exception:  # pragma: no cover - no tts configured at all
            return None

    # -- V2: STT -> dialogue LLM (tool contract) -> TTS -----------------------

    async def _turn_v2(
        self,
        dispatcher: ToolDispatcher,
        state: SessionState,
        tree: Tree,
        turn: PatientTurn,
        on_audio: AudioSink | None,
    ) -> None:
        node = dispatcher.walk.current
        if node is None:
            return

        patient_text = await self._hear(state, turn)
        if patient_text:
            state.record_turn("patient", patient_text, lang=state.lang)

        result = await self._llm_turn(state, node, patient_text)

        saved = False
        for call in result.tool_calls:
            args = {**call.arguments, "session_id": state.session_id}
            await dispatcher.dispatch(call.name, args)
            if call.name == "save_answer":
                saved = True

        # Safety net: the model must record the answer. If it spoke but did not
        # call save_answer, record the patient's words against the current node so
        # the intake still advances rather than re-asking forever. The rules still
        # run in save_answer; nothing clinical is decided here.
        if not saved and patient_text:
            fallback = _coerce_answer(node, patient_text)
            await dispatcher.save_answer(node.id, fallback, raw_text=patient_text, lang=state.lang)

        spoken = result.text.strip()
        # A generated turn in the wrong script is not spoken and not recorded.
        # Unlike a transcript there is an honest replacement to hand: the node's
        # own authored text, which is what every tap-tier intake shows and is in
        # the bank in all four languages by construction (`app.lang_qa`).
        if spoken and (problem := script_problem(spoken, state.lang)):
            logger.warning("intake turn rejected: %s; falling back to the node text", problem)
            spoken = _describe_node(node, state.lang)
        if spoken:
            state.record_turn("assistant", spoken, lang=state.lang)
            await self._speak(spoken, state, on_audio)

    async def _hear(self, state: SessionState, turn: PatientTurn) -> str:
        if turn.audio is None:
            return turn.text or ""
        transcript = await with_fallback(
            self._stt_chain(state),
            lambda p: p.transcribe(turn.audio, str(state.lang), purpose=UsagePurpose.INTAKE_TURN),
        )
        # Same guard as the kiosk's `/stt` route: a recogniser that answers Hindi
        # audio in Urdu script has produced text this patient cannot read back,
        # and transliterating it would be inventing characters over a clinical
        # complaint. Heard-nothing is the honest reading, and the turn loop
        # already knows how to ask again (`app.languages`).
        problem = script_problem(transcript.text, state.lang)
        if problem:
            logger.warning("intake STT rejected: %s (provider %s)", problem, transcript.provider)
            return ""
        return transcript.text

    async def _llm_turn(self, state: SessionState, node, patient_text: str):
        prompt = load("intake")
        system = prompt.system
        current = _describe_node(node, state.lang)
        user = (
            f"Current question (ask this, in {LANG_NAMES.get(str(state.lang), state.lang)}):\n"
            f"{current}\n\n"
            f'The patient just said: "{patient_text}"\n\n'
            "Map their answer onto this node and call save_answer with their exact "
            "words in raw_text. Then say the next question warmly. If nothing was "
            "understood, ask them to repeat instead of guessing."
        )
        request = LLMRequest(
            prompt=user,
            system=system,
            prompt_ref=prompt.ref,
            temperature=0.2,
            max_tokens=300,
            tools=INTAKE_TOOLS,
            history=_history_for_llm(state),
        )
        return await with_fallback(
            self._llm_chain(state),
            lambda p: p.complete(request, purpose=UsagePurpose.INTAKE_TURN),
        )

    async def _speak(self, text: str, state: SessionState, on_audio: AudioSink | None) -> None:
        try:
            speech = await self._tts_one(state).synthesize(text, str(state.lang))
        except ProviderError:
            # TTS is not on the critical path for recording the answer; a failed
            # synthesis should not fail the turn. The channel can re-render text.
            return
        if on_audio is not None:
            await on_audio(speech.audio)

    # -- V1: Gemini Live session bridge ---------------------------------------

    async def _run_v1(
        self,
        state: SessionState,
        tree: Tree,
        pending: _Turns,
        on_audio: AudioSink | None,
    ) -> None:
        """Bridge a live speech-to-speech session to the tool dispatcher.

        The model drives: it calls the tools, we run them against the walk and
        hand the result back (`send_tool_result`), and we stream its audio out
        through `on_audio` — the voice-gw passthrough (S14). We close the session
        when the model calls `finish_and_summarize`, or when its turns run out and
        we finish the (possibly partial) intake ourselves.
        """
        dispatcher = self.dispatcher(state, tree)
        provider = self._realtime_provider()
        prompt = load("intake")
        with usage_scope(tier=V1):
            session = await provider.connect(
                RealtimeConfig(
                    system=prompt.system,
                    lang=str(state.lang),
                    session_id=state.session_id,
                    tools=INTAKE_TOOLS,
                )
            )
            try:
                await self._pump_v1(session, dispatcher, state, pending, on_audio)
            finally:
                await session.close()

        # If the model never called finish (its script ran out), close the intake
        # on whatever answers we have — a dropped Live call still saves a partial.
        if state.status is SessionStatus.ACTIVE:
            reason = "complete" if dispatcher.walk.is_complete else "patient_ended"
            await self._finish(dispatcher, reason)

    async def _pump_v1(
        self,
        session: RealtimeSession,
        dispatcher: ToolDispatcher,
        state: SessionState,
        pending: _Turns,
        on_audio: AudioSink | None,
    ) -> None:
        events = session.events()
        # Kick the model off with the patient's opening audio (their chief
        # complaint was already captured at routing; this starts the turn loop).
        opening = await pending.next() or PatientTurn(audio=AudioClip(data=b""))
        await session.send_audio(opening.audio or AudioClip(data=b""))

        for _ in range(_V1_MAX_EVENTS):
            try:
                event = await asyncio.wait_for(anext(events), timeout=_V1_EVENT_TIMEOUT)
            except (StopAsyncIteration, TimeoutError):
                break

            kind = event.kind.value
            if kind == "tool_call" and event.tool_call is not None:
                call = event.tool_call
                args = {**call.arguments, "session_id": state.session_id}
                result = await dispatcher.dispatch(call.name, args)
                await session.send_tool_result(call, result)
                if call.name == "finish_and_summarize":
                    return
            elif kind == "audio" and event.audio is not None:
                if on_audio is not None:
                    await on_audio(event.audio)
            elif kind == "transcript" and event.text:
                state.record_turn(event.role or "assistant", event.text, lang=state.lang)
            elif kind == "error":
                # A mid-session Live error is the signal to downgrade to V2.
                raise ProviderUnavailable(event.error or "realtime session error")
            # barge_in / turn_complete: nothing to do here — playback stop is the
            # channel's job (S14); the loop just keeps servicing the model.

    # -- finishing ------------------------------------------------------------

    async def _finish(self, dispatcher: ToolDispatcher, reason: str) -> None:
        await dispatcher.finish_and_summarize(reason)

    # -- downgrade ------------------------------------------------------------

    async def _downgrade(self, state: SessionState) -> None:
        """Drop one rung and persist. Answers are untouched — position is derived
        from them, so the next dispatcher on the lower tier resumes in place."""
        before = state.active_tier
        state.active_tier = downgrade(state.active_tier)
        if state.active_tier != before:
            logger.info("intake %s: tier %s -> %s", state.session_id, before, state.active_tier)
        await self._store.save(state)

    async def _maybe_costguard_downgrade(self, state: SessionState) -> None:
        guard = self._cost_guard()
        if guard is None:
            return
        effective = await guard.effective_tier(state.channel, state.configured_tier)
        if _is_lower(effective, state.active_tier):
            logger.info("cost guard forces intake %s down to %s", state.session_id, effective)
            state.active_tier = effective
            await self._store.save(state)

    # -- cost attribution -----------------------------------------------------

    async def finalize_cost(self, state: SessionState, session: AsyncSession) -> Decimal:
        """Sum the usage_events for this intake and write the total (doc 02 §8).

        Called on completion. Requires the meter to have flushed — the caller
        drains it first (the app lifespan runs the drain; tests call
        `meter.flush()`). Reconciles exactly because it sums the same `Decimal`
        rows S18's dashboard sums (STATE.md: money is `Decimal`, never float).
        """
        if state.intake_id is None:
            return state.cost_inr or Decimal("0")

        total = await session.scalar(
            select(func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0)).where(
                UsageEvent.intake_id == state.intake_id
            )
        )
        state.cost_inr = Decimal(total or 0)
        await self._persist_intake(state, session)
        await self._store.save(state)
        return state.cost_inr

    async def _persist_intake(self, state: SessionState, session: AsyncSession) -> None:
        """Write the completed intake onto its `Intake` row (audited).

        Only touches a row that already exists — creating the Visit/Intake is the
        channel adapter's job (it knows the patient); the engine fills the intake's
        result. Missing row is fine for a store-only test.
        """
        intake = await session.get(Intake, state.intake_id)
        if intake is None:
            return
        intake.tier = state.active_tier
        intake.lang = state.lang
        intake.answers = state.answers
        intake.red_flags = state.red_flags
        intake.adaptive_events = state.adaptive_turns
        intake.transcript = state.transcript
        intake.summary_md = state.summary_md
        intake.summary_lang_versions = state.summary_lang_versions
        intake.confirmed_by_patient = state.confirmed
        intake.cost_inr = state.cost_inr
        if state.chief_complaint is not None:
            intake.chief_complaint = state.chief_complaint
        if state.chief_complaint_en is not None:
            intake.chief_complaint_en = state.chief_complaint_en
        if state.status in (SessionStatus.COMPLETE, SessionStatus.ENDED, SessionStatus.HANDOFF):
            from datetime import UTC, datetime

            intake.completed_at = datetime.now(UTC)
            if intake.visit is not None and state.status is SessionStatus.COMPLETE:
                intake.visit.status = VisitStatus.INTAKE_DONE


def _has_real_llm(chain: Sequence[LLMProvider]) -> bool:
    """True when some provider in the chain is a configured, non-fake vendor.

    `configured` is false when a vendor was selected but given no credentials, and
    the fakes announce themselves by name. Either way there is nothing worth
    calling, and asking anyway would cost a round trip to learn what the registry
    already knows.
    """
    return any(
        provider.health.configured and not provider.name.startswith("fake") for provider in chain
    )


class _ResilientSummarizer:
    """LLM summary with a deterministic fallback (degrade, never deny)."""

    def __init__(self, primary: LLMSummarizer, fallback: TemplateSummarizer) -> None:
        self._primary = primary
        self._fallback = fallback

    async def summarize(self, state: SessionState, tree: Tree, walk) -> Any:
        try:
            return await self._primary.summarize(state, tree, walk)
        except (ProviderError, ValueError) as exc:
            logger.warning("LLM summary failed (%s); using the deterministic template", exc)
            return await self._fallback.summarize(state, tree, walk)


# -- small helpers -------------------------------------------------------------


def _is_lower(candidate: IntakeTier, current: IntakeTier) -> bool:
    """True if `candidate` is a cheaper automated tier than `current`."""
    if candidate not in LADDER or current not in LADDER:
        return False
    return LADDER.index(candidate) > LADDER.index(current)


def _history_for_llm(state: SessionState, limit: int = 10) -> list[tuple[str, str]]:
    """Recent transcript as (role, text) pairs for the dialogue model."""
    pairs: list[tuple[str, str]] = []
    for turn in state.transcript[-limit:]:
        role = "user" if turn.get("role") == "patient" else "assistant"
        text = turn.get("text")
        if text:
            pairs.append((role, text))
    return pairs


def _describe_node(node, lang: Lang | str) -> str:
    lines = [node.ask(lang)]
    if node.options:
        opts = ", ".join(
            f"{opt.id}={opt.text.get(str(lang)) or opt.text.get(Lang.EN, opt.id)}"
            for opt in node.options
        )
        lines.append(f"options: {opts}")
    if node.type.value in ("scale", "number"):
        lines.append(f"answer a number between {node.min} and {node.max}")
    return "\n".join(lines)


def _coerce_answer(node, patient_text: str) -> Any:
    """Best-effort mapping when the model spoke but did not call save_answer.

    Deliberately conservative: for a free-text/voice node the words *are* the
    answer; for anything with options or a range we cannot invent a choice, so we
    pass the raw text through and let the walker's validator reject it (which
    re-asks) rather than fabricate a clinical value.
    """
    if node.type.value == "free_voice":
        return patient_text
    if node.type.value == "single":
        lowered = patient_text.strip().lower()
        for opt in node.options:
            if opt.id.lower() in lowered:
                return opt.id
    return patient_text
