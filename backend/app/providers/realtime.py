"""RealtimeVoiceProvider — native speech-to-speech (doc 02 §2, tier V1).

Gemini Live: audio in, audio out, one hop, with function calls into the intake
engine. Doc 02 §2 picks it for lowest turn latency (<1.5s p90) and natural
barge-in; doc 02 §5 is emphatic that it "NEVER free-styles clinically" — it can
only drive the intake through the four tools in `app.prompts.tools`.

## Scope: S3 ships the interface and the fake

The concrete Gemini Live impl deliberately is **not** here. Per doc 06, the Live
session manager is S5's job (`IntakeEngine`'s V1 loop) and the Exotel↔Live audio
bridge is S14's (`voice-gw`). A websocket session protocol built here, two
sessions before anything can drive it, would be written blind against an
interface nobody has used yet — and rewritten twice. What S3 owes those sessions
is the *shape*: this interface, a fake that behaves like the real thing, and the
metering hooks. Registered in STATE.md → Stubs & fakes.

## What the shape has to get right

- **Metering is per-minute, not per-call** (doc 02 §5). A 9-minute phone intake
  that meters at hangup is 9 minutes the cost-guard could not see. Sessions call
  `_meter_stream` as audio flows.
- **Barge-in is an event, not a flag.** A patient interrupting is the normal case
  for a nervous person on a phone; the event stream models it directly so the
  engine can stop playback rather than talk over them.
- **The tool loop is the contract.** Same four functions as V2 — that identity is
  what makes a mid-session V1→V2 downgrade lossless (doc 02 §5).
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from app.models.enums import UsagePurpose
from app.prompts.tools import INTAKE_TOOLS, ToolSpec
from app.providers.audio import PCM16, AudioClip
from app.providers.base import Provider
from app.providers.llm import ToolCall
from app.providers.metering import UsageDelta

logger = logging.getLogger(__name__)


class EventKind(StrEnum):
    AUDIO = "audio"  # a chunk of the assistant's speech, play it
    TRANSCRIPT = "transcript"  # what the model heard / said, for the record
    TOOL_CALL = "tool_call"  # the model wants an intake tool run
    BARGE_IN = "barge_in"  # the patient started talking; stop playback now
    TURN_COMPLETE = "turn_complete"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    kind: EventKind
    audio: AudioClip | None = None
    text: str | None = None
    role: str | None = None  # "user" | "assistant", for TRANSCRIPT
    tool_call: ToolCall | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RealtimeConfig:
    """How a session starts. `system` comes from `prompts/`, never a literal here."""

    system: str
    lang: str
    voice: str | None = None
    tools: Sequence[ToolSpec] = INTAKE_TOOLS
    sample_rate: int = 8000
    mime: str = PCM16
    session_id: str | None = None


class RealtimeSession(ABC):
    """One live conversation. Full-duplex: send audio while receiving events."""

    @abstractmethod
    async def send_audio(self, clip: AudioClip) -> None:
        """Push patient audio. Called continuously by the channel adapter."""

    @abstractmethod
    async def send_tool_result(self, call: ToolCall, result: dict[str, Any]) -> None:
        """Return an intake tool's result so the model can continue the turn."""

    @abstractmethod
    def events(self) -> AsyncIterator[RealtimeEvent]:
        """The model's side of the conversation."""

    @abstractmethod
    async def close(self) -> None:
        """End the session and flush its final usage."""


class RealtimeVoiceProvider(Provider):
    kind: ClassVar[str] = "realtime"
    model: ClassVar[str] = ""

    #: How much audio accumulates before a usage_event is emitted. 60s matches
    #: doc 02 §5's "per-minute audio metering"; lower costs rows, higher costs
    #: the cost-guard its reaction time.
    meter_every_seconds: ClassVar[int] = 60

    @abstractmethod
    async def connect(self, config: RealtimeConfig) -> RealtimeSession:
        """Open a session. Raises `ProviderUnavailable` if the vendor is out —
        which is S5's signal to downgrade the intake to V2."""


@dataclass
class FakeRealtimeScript:
    """One scripted assistant turn: say this, optionally call these tools."""

    say: str = "Namaste. Aap kaise hain?"
    tool_calls: tuple[ToolCall, ...] = ()
    barge_in: bool = False


class FakeRealtimeSession(RealtimeSession):
    """Deterministic Live session. Drives the same event stream, no vendor.

    Turn-taking is explicit: each `send_audio` releases the next scripted turn,
    so an S5 test can walk an intake end to end and assert on what the patient
    heard, without timing races.
    """

    def __init__(self, provider: FakeRealtimeProvider, config: RealtimeConfig) -> None:
        self._provider = provider
        self._config = config
        self._queue: asyncio.Queue[RealtimeEvent | None] = asyncio.Queue()
        self._script: list[FakeRealtimeScript] = list(provider.script)
        self.received: list[AudioClip] = []
        self.tool_results: list[tuple[ToolCall, dict[str, Any]]] = []
        self._unmetered = UsageDelta()
        self.closed = False

    async def send_audio(self, clip: AudioClip) -> None:
        self.received.append(clip)
        self._accumulate(clip.duration())
        await self._emit_turn()

    async def send_tool_result(self, call: ToolCall, result: dict[str, Any]) -> None:
        self.tool_results.append((call, result))
        await self._emit_turn()

    async def _emit_turn(self) -> None:
        if not self._script:
            return
        step = self._script.pop(0)
        if step.barge_in:
            await self._queue.put(RealtimeEvent(kind=EventKind.BARGE_IN))
        for call in step.tool_calls:
            await self._queue.put(RealtimeEvent(kind=EventKind.TOOL_CALL, tool_call=call))
        if step.say:
            audio = AudioClip(
                data=b"\x00\x00" * int(self._config.sample_rate * len(step.say) / 14),
                sample_rate=self._config.sample_rate,
            )
            self._accumulate(audio.duration())
            await self._queue.put(
                RealtimeEvent(kind=EventKind.TRANSCRIPT, text=step.say, role="assistant")
            )
            await self._queue.put(RealtimeEvent(kind=EventKind.AUDIO, audio=audio))
        await self._queue.put(RealtimeEvent(kind=EventKind.TURN_COMPLETE))

    def _accumulate(self, seconds: Decimal) -> None:
        """Buffer audio seconds, emitting a usage_event once per metering window.

        Same behaviour the real impl must have: bill as you go, not at hangup.
        """
        self._unmetered += UsageDelta(audio_seconds=seconds)
        if self._unmetered.audio_seconds >= self._provider.meter_every_seconds:
            self._flush_usage()

    def _flush_usage(self) -> None:
        if self._unmetered.audio_seconds <= 0:
            return
        self._provider._meter_stream(
            UsagePurpose.INTAKE_TURN, self._unmetered, model=self._provider.model
        )
        self._unmetered = UsageDelta()

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # The tail of the last minute still costs money.
        self._flush_usage()
        await self._queue.put(None)


class FakeRealtimeProvider(RealtimeVoiceProvider):
    """Deterministic tier-V1 provider for tests and local dev."""

    name: ClassVar[str] = "fake-live"
    model: ClassVar[str] = "fake-live-1"

    def __init__(self, *, script: Sequence[FakeRealtimeScript] = (), **kwargs) -> None:
        super().__init__(**kwargs)
        self.script: list[FakeRealtimeScript] = list(script) or [FakeRealtimeScript()]
        self.sessions: list[FakeRealtimeSession] = []
        #: Set to make `connect` raise — how S5 tests the V1→V2 downgrade.
        self.fail_with: Exception | None = None

    async def connect(self, config: RealtimeConfig) -> RealtimeSession:
        return await self._invoke(
            UsagePurpose.INTAKE_TURN, lambda call: self._connect(config, call), model=self.model
        )

    async def _connect(self, config: RealtimeConfig, call) -> RealtimeSession:
        if self.fail_with is not None:
            raise self.fail_with
        session = FakeRealtimeSession(self, config)
        self.sessions.append(session)
        # Connecting costs nothing; the session meters the audio as it flows.
        return session

    @property
    def last_session(self) -> FakeRealtimeSession | None:
        return self.sessions[-1] if self.sessions else None
