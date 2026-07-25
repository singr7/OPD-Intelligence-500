"""A fake Exotel Voicebot client (S14) — the AC instrument.

It speaks the exact `gw.exotel` wire protocol over an in-memory transport, so the
call driver runs byte-for-byte the way it would over a live Exotel websocket, but
deterministically and without a carrier. It replays a script of caller utterances,
captures the assistant audio played back, and **measures per-turn latency** (time
from finishing an utterance to the first assistant audio) so the V1<1.5s / V2<3.5s
budgets can be asserted (doc 02 §5).

The audio is synthetic PCM — its *content* is irrelevant because the fake providers
(`FakeSTTProvider` / `FakeLLMProvider` / `FakeRealtimeProvider`) are scripted; what
matters is the frame *shape* and the turn *count*. `speech`, `silence` and `mumble`
give the three energy bands the driver keys on (utterance boundary, and the DTMF
"could not understand" trigger).
"""

from __future__ import annotations

import array
import asyncio
import math
import time
from dataclasses import dataclass, field

from app.providers.audio import PCM16, AudioClip

from gw.exotel import ExotelTransport

SAMPLE_RATE = 8000
FRAME_BYTES = 320  # ~20 ms, matching gw.call.FRAME_BYTES


# -- synthetic caller audio ---------------------------------------------------


def _pcm(seconds: float, amplitude: int) -> AudioClip:
    n = int(SAMPLE_RATE * seconds)
    samples = array.array("h", [0] * n)
    if amplitude:
        for i in range(n):
            samples[i] = int(amplitude * math.sin(2 * math.pi * 220 * i / SAMPLE_RATE))
    return AudioClip(data=samples.tobytes(), mime=PCM16, sample_rate=SAMPLE_RATE, channels=1)


def speech(seconds: float = 0.4) -> AudioClip:
    """A clearly-voiced utterance (well above the unclear threshold)."""
    return _pcm(seconds, amplitude=9000)


def mumble(seconds: float = 0.3) -> AudioClip:
    """Non-silent but low energy — the driver reads this as 'could not understand'
    (a proxy for STT confidence <0.5), which drives the DTMF fallback."""
    return _pcm(seconds, amplitude=300)


def silence(seconds: float = 0.1) -> AudioClip:
    return _pcm(seconds, amplitude=0)


# -- the in-memory transport (driver side) ------------------------------------


class FakeTransport(ExotelTransport):
    """Driver-side transport backed by two queues. The client pushes inbound frames
    and reads outbound ones through the helper methods below."""

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[dict | None] = asyncio.Queue()
        self._outbound: asyncio.Queue[dict] = asyncio.Queue()

    # ExotelTransport (driver side)
    async def send(self, frame: dict) -> None:
        await self._outbound.put(frame)

    async def receive(self) -> dict | None:
        return await self._inbound.get()

    # client side
    async def push(self, frame: dict) -> None:
        await self._inbound.put(frame)

    async def next_outbound(self, timeout: float) -> dict | None:
        try:
            return await asyncio.wait_for(self._outbound.get(), timeout=timeout)
        except TimeoutError:
            return None


# -- the client ---------------------------------------------------------------


@dataclass
class CallResult:
    turn_latencies: list[float] = field(default_factory=list)
    assistant_frames: int = 0
    clears: int = 0  # barge-in acks the driver sent
    outbound_events: list[str] = field(default_factory=list)

    def p90_latency(self) -> float:
        if not self.turn_latencies:
            return 0.0
        ordered = sorted(self.turn_latencies)
        idx = max(0, math.ceil(0.9 * len(ordered)) - 1)
        return ordered[idx]


class FakeExotelClient:
    """Drives one call over a `FakeTransport`. Push frames with the utterance helpers;
    the driver task runs concurrently."""

    def __init__(self, transport: FakeTransport, *, stream_sid: str = "stream-1") -> None:
        self._t = transport
        self._sid = stream_sid
        self._result = CallResult()

    async def start(
        self,
        *,
        cli: str = "+919876500000",
        tier: str | None = None,
        lang: str = "hi",
        tree: str | None = None,
    ) -> None:
        custom: dict[str, str] = {"lang": lang}
        if tier:
            custom["tier"] = tier
        if tree:
            custom["tree"] = tree
        await self._t.push({"event": "connected"})
        await self._t.push(
            {
                "event": "start",
                "stream_sid": self._sid,
                "start": {
                    "call_sid": "call-1",
                    "from": cli,
                    "to": "+918000000000",
                    "custom_parameters": custom,
                },
            }
        )

    async def _push_audio(self, clip: AudioClip) -> None:
        data = clip.data
        for i in range(0, len(data), FRAME_BYTES):
            await self._t.push(
                {
                    "event": "media",
                    "stream_sid": self._sid,
                    "media": {"payload": AudioClip(data=data[i : i + FRAME_BYTES]).b64()},
                }
            )

    async def send_utterance(self, clip: AudioClip, *, silence_ms: int = 100) -> None:
        """Push one utterance + silence boundary without waiting for a response — for
        the unclear/mumble turns of the DTMF test, which produce no assistant turn."""
        await self._push_audio(clip)
        await self._push_audio(silence(silence_ms / 1000))

    async def dtmf(self, digit: str) -> None:
        await self._t.push({"event": "dtmf", "stream_sid": self._sid, "dtmf": {"digit": digit}})

    async def hangup(self) -> None:
        await self._t.push({"event": "stop", "stream_sid": self._sid, "stop": {}})
        await self._t.push(None)  # socket closed

    async def drain(self, *, quiet: float = 0.05) -> None:
        """Read assistant frames until a quiet gap (e.g. after the consent line)."""
        while True:
            frame = await self._t.next_outbound(timeout=quiet)
            if frame is None:
                return
            self._record(frame)

    async def say(
        self, clip: AudioClip, *, silence_ms: int = 100, response_timeout: float = 5.0
    ) -> float:
        """Push one utterance (voiced audio + a trailing silence boundary) and measure
        the latency to the first assistant audio that follows. Returns the latency."""
        await self._push_audio(clip)
        await self._push_audio(silence(silence_ms / 1000))
        t0 = time.perf_counter()
        latency = await self._await_audio(response_timeout)
        if latency is not None:
            self._result.turn_latencies.append(latency - t0)
        await self.drain()  # consume the rest of this turn's audio before the next
        return (latency - t0) if latency is not None else 0.0

    async def _await_audio(self, timeout: float) -> float | None:
        """Read outbound frames until the first assistant `media`; return its time."""
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            frame = await self._t.next_outbound(timeout=max(0.001, deadline - time.perf_counter()))
            if frame is None:
                return None
            self._record(frame)
            if frame.get("event") == "media":
                return time.perf_counter()
        return None

    def _record(self, frame: dict) -> None:
        event = frame.get("event", "")
        self._result.outbound_events.append(event)
        if event == "media":
            self._result.assistant_frames += 1
        elif event == "clear":
            self._result.clears += 1

    @property
    def result(self) -> CallResult:
        return self._result
