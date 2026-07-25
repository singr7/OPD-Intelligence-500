"""The Exotel Voicebot Applet websocket protocol (S14).

Exotel's Voicebot streams a call as a sequence of JSON frames over one websocket,
each tagged with an `event`. It is modelled on the Twilio Media Streams shape (Exotel
followed it): `connected` → `start` → many `media` (+ `dtmf`) → `stop`, with the
server sending `media` back to play audio, `clear` to flush playback (barge-in), and
`mark` to learn when a clip finished. Audio is base64 **8 kHz 16-bit mono PCM**
(`audio/l16`) both ways — the telephony-native format `app.providers.audio.PCM16`.

This module is *only* the codec: parse an inbound frame into a typed value, encode an
outbound one. It imports nothing from the call driver or FastAPI, so the real WS route
and the fake replay client (`gw.fake_exotel`) share exactly one wire format — the thing
a protocol bug would otherwise hide between. Frames are plain dicts on the wire; the
transport (a real `WebSocket` or the in-memory fake) only moves dicts, it does not know
their meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.providers.audio import PCM16, AudioClip

#: Exotel Voicebot media: 8 kHz, 16-bit, mono PCM. Fixed by the carrier.
SAMPLE_RATE = 8000
CHANNELS = 1


class EventType(StrEnum):
    # Inbound (Exotel → us)
    CONNECTED = "connected"
    START = "start"
    MEDIA = "media"
    DTMF = "dtmf"
    MARK = "mark"
    STOP = "stop"
    # Outbound uses MEDIA / MARK plus:
    CLEAR = "clear"


# -- inbound frames -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Connected:
    """Handshake ack; carries nothing we act on."""


@dataclass(frozen=True, slots=True)
class Start:
    """The call began. `cli` is the caller's number (doc 03 §1b: patient looked up
    by CLI). `custom` carries applet parameters — we read `tier` and `lang` from it
    so a test / the Exotel flow can pin the path without a code change."""

    stream_sid: str
    call_sid: str
    cli: str | None = None
    to: str | None = None
    custom: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Media:
    """A chunk of caller audio."""

    audio: AudioClip
    chunk: int = 0


@dataclass(frozen=True, slots=True)
class Dtmf:
    """A keypad digit — the yes/no fallback when STT keeps failing (doc 03 §1b)."""

    digit: str


@dataclass(frozen=True, slots=True)
class Mark:
    """Exotel finished playing the clip we tagged with this name."""

    name: str


@dataclass(frozen=True, slots=True)
class Stop:
    """The caller hung up (or the applet ended). Save whatever we have."""


InboundFrame = Connected | Start | Media | Dtmf | Mark | Stop


class ProtocolError(ValueError):
    """A frame we cannot parse. Raised rather than guessed — a malformed frame on a
    live call is a bug in the applet config or our codec, and swallowing it loses
    audio silently."""


def parse_inbound(frame: dict[str, Any]) -> InboundFrame:
    """One wire dict → one typed inbound frame."""
    event = frame.get("event")
    if event == EventType.CONNECTED:
        return Connected()
    if event == EventType.START:
        start = frame.get("start") or {}
        return Start(
            stream_sid=str(frame.get("stream_sid") or start.get("stream_sid") or ""),
            call_sid=str(start.get("call_sid") or ""),
            cli=start.get("from"),
            to=start.get("to"),
            custom=dict(start.get("custom_parameters") or {}),
        )
    if event == EventType.MEDIA:
        media = frame.get("media") or {}
        payload = media.get("payload")
        if not isinstance(payload, str):
            raise ProtocolError("media frame missing base64 payload")
        clip = AudioClip.from_b64(payload, mime=PCM16, sample_rate=SAMPLE_RATE, channels=CHANNELS)
        return Media(audio=clip, chunk=int(media.get("chunk") or 0))
    if event == EventType.DTMF:
        digit = (frame.get("dtmf") or {}).get("digit")
        if digit is None:
            raise ProtocolError("dtmf frame missing digit")
        return Dtmf(digit=str(digit))
    if event == EventType.MARK:
        return Mark(name=str((frame.get("mark") or {}).get("name") or ""))
    if event == EventType.STOP:
        return Stop()
    raise ProtocolError(f"unknown inbound event {event!r}")


# -- outbound frames ----------------------------------------------------------


def encode_media(stream_sid: str, audio: AudioClip) -> dict[str, Any]:
    """Play `audio` to the caller. Exotel expects one media frame per chunk; the
    driver's playback pump paces these, it does not dump a whole utterance at once."""
    return {
        "event": EventType.MEDIA,
        "stream_sid": stream_sid,
        "media": {"payload": audio.b64()},
    }


def encode_clear(stream_sid: str) -> dict[str, Any]:
    """Flush everything we have queued for playback — barge-in (doc 03 §1b). Sent the
    moment the caller starts speaking so we stop talking over them."""
    return {"event": EventType.CLEAR, "stream_sid": stream_sid}


def encode_mark(stream_sid: str, name: str) -> dict[str, Any]:
    """Tag the end of a clip; Exotel echoes a `mark` back when it finishes playing."""
    return {"event": EventType.MARK, "stream_sid": stream_sid, "mark": {"name": name}}


# -- transport ----------------------------------------------------------------


@runtime_checkable
class ExotelTransport(Protocol):
    """The duplex channel under the codec. A real `starlette.websockets.WebSocket`
    adapter and the in-memory fake client both satisfy this, so the call driver is
    identical over a live call and a replay test."""

    async def send(self, frame: dict[str, Any]) -> None: ...

    async def receive(self) -> dict[str, Any] | None:
        """Next inbound frame, or None when the socket is closed."""
        ...
