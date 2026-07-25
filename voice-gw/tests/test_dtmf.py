"""DTMF fallback: two utterances we can't make out → offer the keypad, take the
digit as the answer (doc 03 §1b, S14 AC)."""

from __future__ import annotations

import asyncio

from app.providers import FakeTTSProvider

from tests.conftest import EXPECTED_VALUES, make_v2_engine
from gw import call as call_mod
from gw.fake_exotel import FakeExotelClient, FakeTransport, mumble, speech

from app.intake import SessionStatus


async def test_two_unclear_answers_fall_back_to_the_keypad(tree, store, settings):
    engine = make_v2_engine(store)
    transport = FakeTransport()
    client = FakeExotelClient(transport)
    pcstore = call_mod.PhoneCallStore()

    driver = asyncio.create_task(
        call_mod.handle_call(
            transport,
            engine=engine,
            sessionmaker=None,
            settings=settings,
            tts=FakeTTSProvider(),
            phonecall_store=pcstore,
            tree=tree,
        )
    )

    await client.start(tier="v2", lang="hi")
    await client.drain()  # consent

    # The caller mumbles twice on the fever question — neither is understood.
    await client.send_utterance(mumble())
    await client.send_utterance(mumble())
    # …so we offer the keypad; they press 1 (= yes). That unblocks the intake.
    await client.dtmf("1")
    # The rest of the questions are answered clearly.
    await client.say(speech())  # pain
    await client.say(speech())  # detail
    await client.hangup()

    record = await driver
    state = await store.get(record.session_id)

    assert record.keypad_prompts == 1, "two unclear tries should trigger exactly one keypad prompt"
    assert state.status is SessionStatus.COMPLETE
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
