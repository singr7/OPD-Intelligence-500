"""The Exotel Voicebot frame codec (gw.exotel) — parse/encode round-trips."""

from __future__ import annotations

import pytest

from app.providers.audio import AudioClip
from gw import exotel


def test_parse_start_reads_cli_and_custom_params():
    frame = {
        "event": "start",
        "stream_sid": "s-1",
        "start": {
            "call_sid": "c-1",
            "from": "+919876500000",
            "to": "+918000000000",
            "custom_parameters": {"tier": "v1", "lang": "hi", "tree": "gm"},
        },
    }
    start = exotel.parse_inbound(frame)
    assert isinstance(start, exotel.Start)
    assert start.stream_sid == "s-1"
    assert start.call_sid == "c-1"
    assert start.cli == "+919876500000"
    assert start.custom == {"tier": "v1", "lang": "hi", "tree": "gm"}


def test_media_round_trips_audio_bytes():
    audio = AudioClip(data=b"\x01\x02\x03\x04" * 8)
    encoded = exotel.encode_media("s-1", audio)
    parsed = exotel.parse_inbound(encoded)
    assert isinstance(parsed, exotel.Media)
    assert parsed.audio.data == audio.data
    assert parsed.audio.sample_rate == exotel.SAMPLE_RATE


def test_parse_dtmf_and_stop():
    assert exotel.parse_inbound({"event": "dtmf", "dtmf": {"digit": "1"}}) == exotel.Dtmf("1")
    assert isinstance(exotel.parse_inbound({"event": "stop"}), exotel.Stop)
    assert isinstance(exotel.parse_inbound({"event": "connected"}), exotel.Connected)


def test_encode_clear_and_mark_carry_stream_sid():
    assert exotel.encode_clear("s-9") == {"event": "clear", "stream_sid": "s-9"}
    mark = exotel.encode_mark("s-9", "done")
    assert mark["event"] == "mark" and mark["mark"]["name"] == "done"


def test_unknown_and_malformed_frames_raise():
    with pytest.raises(exotel.ProtocolError):
        exotel.parse_inbound({"event": "wat"})
    with pytest.raises(exotel.ProtocolError):
        exotel.parse_inbound({"event": "media", "media": {}})  # no payload
