"""V1 (Gemini Live) phone intake, end to end over the fake Exotel client (S14 AC).

The fake realtime session scripts the whole intake from the opening kick, so one
caller utterance is enough; the driver bridges its tool loop and streams its audio
back out through the Exotel passthrough."""

from __future__ import annotations

from app.intake import SessionStatus

from tests.conftest import EXPECTED_VALUES, drive_call, make_v1_engine
from gw.fake_exotel import speech

V1_TURN_BUDGET = 1.5  # doc 02 §5


async def test_v1_full_intake_bridges_tools_and_streams_audio(tree, store, settings):
    engine = make_v1_engine(store)
    record, result = await drive_call(
        engine, tree=tree, tier="v1", settings=settings, utterances=[speech()]
    )
    state = await store.get(record.session_id)
    assert state.status is SessionStatus.COMPLETE
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
    assert record.tier == "conversational"
    assert result.assistant_frames > 0


async def test_v1_turn_latency_within_budget(tree, store, settings):
    engine = make_v1_engine(store)
    _, result = await drive_call(
        engine, tree=tree, tier="v1", settings=settings, utterances=[speech()]
    )
    assert result.turn_latencies, "at least the opening turn latency is measured"
    assert result.p90_latency() < V1_TURN_BUDGET, result.turn_latencies
