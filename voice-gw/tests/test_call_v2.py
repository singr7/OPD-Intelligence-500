"""V2 (STT→LLM→TTS) phone intake, end to end over the fake Exotel client (S14 AC)."""

from __future__ import annotations

from app.intake import SessionStatus

from tests.conftest import EXPECTED_VALUES, drive_call, make_v2_engine
from gw.fake_exotel import speech

V2_TURN_BUDGET = 3.5  # doc 02 §5


async def test_v2_full_intake_completes_over_the_phone(tree, store, settings):
    engine = make_v2_engine(store)
    record, result = await drive_call(engine, tree=tree, tier="v2", settings=settings)

    state = await store.get(record.session_id)
    assert state.status is SessionStatus.COMPLETE
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
    assert record.tier == "rule_based"
    assert result.assistant_frames > 0, "assistant audio should stream back to Exotel"


async def test_v2_consent_is_played_and_recorded(tree, store, settings):
    engine = make_v2_engine(store)
    record, result = await drive_call(engine, tree=tree, tier="v2", settings=settings)
    assert record.consent_at is not None
    # Consent audio plays before any question, so frames were sent from the start.
    assert result.outbound_events[0] == "media"


async def test_v2_turn_latency_within_budget(tree, store, settings):
    engine = make_v2_engine(store)
    _, result = await drive_call(engine, tree=tree, tier="v2", settings=settings)
    assert len(result.turn_latencies) == 3
    assert result.p90_latency() < V2_TURN_BUDGET, result.turn_latencies


async def test_v2_hangup_saves_a_partial_intake(tree, store, settings):
    engine = make_v2_engine(store)
    # Only the first question is answered, then the caller hangs up.
    record, _ = await drive_call(
        engine, tree=tree, tier="v2", settings=settings, utterances=[speech()]
    )
    state = await store.get(record.session_id)
    assert state.status is SessionStatus.ENDED
    assert set(state.answers) == {"fever"}, "the one answer given must be saved"
    assert record.end_reason == "patient_ended"
