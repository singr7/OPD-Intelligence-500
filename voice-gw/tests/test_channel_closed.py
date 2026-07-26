"""A shut phone channel answers, says where to go, and hangs up (S-GL.1, doc 12 §7).

Both applets, because they are two halves of one channel: the intake bridge
(S14) and the appointment receptionist (S15). A hospital that has not opened
phone has not opened either, and an AI receptionist booking real slots on a
number nobody has announced is exactly the half-configured channel doc 12 §4
describes.

What these prove beyond "it refuses": the caller *hears* something (dead air on a
connected call is worse than a refusal), and nothing clinical is written — no
consent for an intake that will not happen, no visit, no appointment.
"""

from __future__ import annotations

import asyncio

from app.channels.state import CLOSED_MESSAGE
from app.models.content import ChannelConfigVersion
from app.models.enums import Channel, ContentStatus, Lang
from app.providers import FakeSTTProvider, FakeTTSProvider
from app.receptionist import Intent

from gw import call as call_mod
from gw import reception
from gw.fake_exotel import FakeExotelClient, FakeTransport
from tests.conftest import make_v2_engine
from tests.test_receptionist_call import ScriptedReceptionist

CLOSED_PHONE = {
    "channels": {
        "kiosk": {"ladder": ["v_oss", "v3"], "enabled": True},
        "phone": {"ladder": ["v2", "v3"], "enabled": False},
    },
    "admission": {"max_oss_sessions": 12},
}


async def _close_phone(db_session) -> None:
    db_session.add(
        ChannelConfigVersion(version=1, config=CLOSED_PHONE, status=ContentStatus.PUBLISHED)
    )
    await db_session.flush()


async def test_a_closed_phone_channel_refuses_an_intake_call_out_loud(
    db_session, call_sessionmaker, tree, store, settings
):
    await _close_phone(db_session)

    transport = FakeTransport()
    client = FakeExotelClient(transport)
    calls = call_mod.PhoneCallStore()

    async def run_driver():
        return await call_mod.handle_call(
            transport,
            engine=make_v2_engine(store),
            sessionmaker=call_sessionmaker,
            settings=settings,
            tts=FakeTTSProvider(),
            phonecall_store=calls,
            tree=tree,
        )

    driver = asyncio.create_task(run_driver())
    await client.start(tier="v2", lang="hi")
    await client.drain()
    record = await driver

    assert record.end_reason == "channel_closed"
    assert record.state == "completed"
    # She hears the refusal: dead air on a connected call is the one thing worse.
    assert client.result.assistant_frames > 0
    # Nothing clinical happened — no consent taken, no intake opened.
    assert record.consent_at is None
    assert record.intake_id is None
    assert record.session_id is None


async def test_a_closed_phone_channel_refuses_the_appointment_line_too(
    db_session, call_sessionmaker, settings, providers
):
    await _close_phone(db_session)

    transport = FakeTransport()
    client = FakeExotelClient(transport)

    async def run_driver():
        return await reception.handle_receptionist_call(
            transport,
            sessionmaker=call_sessionmaker,
            settings=settings,
            tts=FakeTTSProvider(),
            stt=FakeSTTProvider(script=["appointment chahiye"]),
            receptionist=ScriptedReceptionist(Intent.BOOK),
        )

    driver = asyncio.create_task(run_driver())
    await client.start(cli="+919812300077", lang="hi")
    await client.drain()
    record = await driver

    assert record.end_reason == "channel_closed"
    assert record.booked_appointment_id is None
    assert record.turns == 0
    assert client.result.assistant_frames > 0


async def test_the_refusal_is_spoken_in_the_callers_language(
    db_session, call_sessionmaker, tree, store, settings, monkeypatch
):
    """The line she hears is her own. A refusal in English to a Telugu caller is
    a refusal she cannot act on."""
    await _close_phone(db_session)

    spoken: list[tuple[str, str]] = []
    tts = FakeTTSProvider()
    original = tts.synthesize

    async def recording(text: str, lang: str, **kwargs):
        spoken.append((text, lang))
        return await original(text, lang, **kwargs)

    monkeypatch.setattr(tts, "synthesize", recording)

    transport = FakeTransport()
    client = FakeExotelClient(transport)

    async def run_driver():
        return await call_mod.handle_call(
            transport,
            engine=make_v2_engine(store),
            sessionmaker=call_sessionmaker,
            settings=settings,
            tts=tts,
            phonecall_store=call_mod.PhoneCallStore(),
            tree=tree,
        )

    driver = asyncio.create_task(run_driver())
    await client.start(tier="v2", lang="te")
    await client.drain()
    await driver

    assert spoken == [(CLOSED_MESSAGE[Channel.PHONE][Lang.TE], "te")]
