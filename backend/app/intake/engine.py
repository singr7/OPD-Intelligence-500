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
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.intake import voicepack as voicepack_mod
from app.intake.dispatch import ToolDispatcher
from app.intake.state import SessionState, SessionStatus, SessionStore
from app.intake.summary import LANG_NAMES, LLMSummarizer, Summarizer, TemplateSummarizer
from app.intake.voicepack import EMPTY_PACK, VoicePack
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
    ) -> None:
        self._store = store
        self._realtime = realtime
        self._llm = list(llm_providers) if llm_providers is not None else None
        self._stt = list(stt_providers) if stt_providers is not None else None
        self._tts = tts_provider
        self._guard = guard
        self._voicepack = voicepack

    @property
    def store(self) -> SessionStore:
        """The session store this engine reads/writes. Channel adapters need it to
        load a state by id before rebuilding a dispatcher on it."""
        return self._store

    # -- provider accessors (resolved lazily so tests can inject) --------------

    def _realtime_provider(self) -> RealtimeVoiceProvider:
        return self._realtime or get_realtime_provider()

    def _llm_chain(self) -> list[LLMProvider]:
        return self._llm if self._llm is not None else llm_chain()

    def _stt_chain(self) -> list[STTProvider]:
        return self._stt if self._stt is not None else stt_chain()

    def _tts_one(self) -> TTSProvider:
        return self._tts if self._tts is not None else tts_chain()[0]

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
        """V3 summarises deterministically (offline); V1/V2 use the LLM but fall
        back to the template if it is down — degrade, never deny (doc 02 §5)."""
        template = TemplateSummarizer()
        if state.active_tier is V3:
            return template
        return _ResilientSummarizer(LLMSummarizer(self._llm_chain()), template)

    # -- the run loop ---------------------------------------------------------

    async def run(
        self,
        state: SessionState,
        turns: Sequence[PatientTurn] = (),
        *,
        on_audio: AudioSink | None = None,
    ) -> SessionState:
        """Drive the intake to completion (or graceful partial) over `turns`.

        `on_audio` is the voice-gw passthrough sink (S14): every chunk of assistant
        speech is handed to it as it is produced. Left None, audio is synthesised
        and metered but discarded — which is what a text-only test or a kiosk that
        plays audio itself wants.
        """
        tree = self._tree(state)
        pending: deque[PatientTurn] = deque(turns)

        with usage_scope(
            session_id=state.session_id,
            intake_id=state.intake_id,
            visit_id=state.visit_id,
            channel=state.channel,
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
        pending: deque[PatientTurn],
        on_audio: AudioSink | None,
    ) -> None:
        """V2 and V3: one patient turn per question, with downgrade between turns.

        Rebuilds the dispatcher each iteration so that a downgrade mid-loop resumes
        from the stored answers on the new tier (the whole point of deriving
        position). The loop ends when the tree completes, the patient input runs
        out (partial save, doc 03 §1b), or the session is otherwise closed.
        """
        guard_steps = 0
        while state.status is SessionStatus.ACTIVE:
            await self._maybe_costguard_downgrade(state)
            dispatcher = self.dispatcher(state, tree)

            if dispatcher.walk.is_complete:
                await self._finish(dispatcher, "complete")
                break
            if not pending:
                await self._finish(dispatcher, "patient_ended")
                break

            turn = pending.popleft()
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
                pending.appendleft(turn)  # no answer was lost; re-ask on the lower tier

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
            node, state.lang, voicepack=self._voicepack, tts=self._maybe_tts()
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

    def _maybe_tts(self) -> TTSProvider | None:
        try:
            return self._tts_one()
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
        if spoken:
            state.record_turn("assistant", spoken, lang=state.lang)
            await self._speak(spoken, state, on_audio)

    async def _hear(self, state: SessionState, turn: PatientTurn) -> str:
        if turn.audio is None:
            return turn.text or ""
        transcript = await with_fallback(
            self._stt_chain(),
            lambda p: p.transcribe(turn.audio, str(state.lang), purpose=UsagePurpose.INTAKE_TURN),
        )
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
            self._llm_chain(),
            lambda p: p.complete(request, purpose=UsagePurpose.INTAKE_TURN),
        )

    async def _speak(self, text: str, state: SessionState, on_audio: AudioSink | None) -> None:
        try:
            speech = await self._tts_one().synthesize(text, str(state.lang))
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
        pending: deque[PatientTurn],
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
        pending: deque[PatientTurn],
        on_audio: AudioSink | None,
    ) -> None:
        events = session.events()
        # Kick the model off with the patient's opening audio (their chief
        # complaint was already captured at routing; this starts the turn loop).
        opening = pending.popleft() if pending else PatientTurn(audio=AudioClip(data=b""))
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
