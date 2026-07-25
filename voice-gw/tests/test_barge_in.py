"""Barge-in: the caller speaking over playback flushes it and sends `clear` (S14).

The engine's V1 loop notes playback-stop is "the channel's job (S14)"; this is that
job, in the playback pump."""

from __future__ import annotations

import asyncio

from gw.call import PlaybackPump, peak
from gw.fake_exotel import FakeTransport, speech


async def test_pump_barge_in_stops_playback_and_sends_clear():
    transport = FakeTransport()
    pump = PlaybackPump(transport, "s-1")

    # A long clip so playback is still in flight when the caller barges in.
    play = asyncio.create_task(pump.play(speech(seconds=2.0)))
    await asyncio.sleep(0)  # let a few frames go out
    assert pump.is_playing

    await pump.barge_in()
    await play  # play() returns early once interrupted

    assert pump.clears == 1
    events = []
    while (frame := await transport.next_outbound(timeout=0.02)) is not None:
        events.append(frame["event"])
    assert "clear" in events
    # It stopped early: far fewer than the ~800 frames a full 2s clip would send.
    assert events.count("media") < 400


async def test_reader_detects_caller_speech_during_playback():
    """The reader function barges in when loud media arrives mid-playback."""
    from gw.call import ExotelTurnSource, PlaybackPump, read_frames

    transport = FakeTransport()
    pump = PlaybackPump(transport, "s-1")
    source = ExotelTurnSource(lang="hi", pump=pump, say=None, scope={})

    play = asyncio.create_task(pump.play(speech(seconds=2.0)))
    await asyncio.sleep(0)

    # Feed one loud caller frame + a stop through the real reader path.
    loud = speech(seconds=0.1)
    assert peak(loud) > 8
    await transport.push({"event": "media", "stream_sid": "s-1", "media": {"payload": loud.b64()}})
    await transport.push({"event": "stop", "stream_sid": "s-1", "stop": {}})
    await read_frames(transport, source, pump)
    await play

    assert pump.clears == 1
