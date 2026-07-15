"""Usage metering and pricing (doc 02 §8/§9).

The S3 acceptance criterion these own: **every fake call produces a priced
usage_event**. That is not a test of the fakes — it is the test that the metering
path (`Provider._invoke` → `record` → drain → `price_book`) actually works, using
the fakes as the thing that exercises it. If a real provider is ever added
without metering, `test_every_fake_call_is_metered_and_priced` is what notices.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.enums import Channel, IntakeTier, PriceUnit, UsagePurpose
from app.models.metering import UsageEvent
from app.providers.audio import AudioClip
from app.providers.llm import FakeLLMProvider, LLMRequest
from app.providers.messaging import FakeMessagingProvider, OutboundMessage
from app.providers.metering import UsageDelta, usage_scope
from app.providers.pricing import UNIT_QUANTUM, WILDCARD_MODEL
from app.providers.realtime import FakeRealtimeProvider, RealtimeConfig
from app.providers.sms import FakeSMSProvider, SmsMessage
from app.providers.stt import FakeSTTProvider
from app.providers.telephony import CallRequest, FakeTelephonyProvider
from app.providers.tts import FakeTTSProvider

pytestmark = pytest.mark.usefixtures("seeded_prices")


async def _events(session) -> list[UsageEvent]:
    return list((await session.execute(select(UsageEvent))).scalars())


# -- the headline AC -----------------------------------------------------------


async def test_every_fake_call_is_metered_and_priced(session, meter):
    """Doc 02 §9: "every provider wrapper must emit usage_events".

    One call per interface, then assert every one produced a row with real money
    on it. A provider that meters nothing shows up here as a missing row; one
    that meters usage nobody priced shows up as cost 0.
    """
    sms = FakeSMSProvider()
    llm = FakeLLMProvider()
    stt = FakeSTTProvider()
    tts = FakeTTSProvider()
    messaging = FakeMessagingProvider()
    telephony = FakeTelephonyProvider()
    realtime = FakeRealtimeProvider()

    await sms.send(SmsMessage(to="+919876543210", body="hi", template_key="otp_login"))
    await llm.complete(LLMRequest(prompt="hello"))
    await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
    await tts.synthesize("namaste", "hi")
    await messaging.send(OutboundMessage(to="+919876543210", text="hi"))

    handle = await telephony.place_call(CallRequest(to="+919876543210", applet_url="http://x"))
    telephony.complete(handle.call_sid, seconds=120)

    session_ = await realtime.connect(RealtimeConfig(system="s", lang="hi"))
    await session_.send_audio(AudioClip(data=b"\x00\x00" * 8000))
    await session_.close()

    await meter.flush()
    events = await _events(session)

    priced = {e.provider: e.computed_cost_inr for e in events if e.computed_cost_inr > 0}
    assert priced.keys() >= {
        "fake-sms",
        "fake-llm",
        "fake-stt",
        "fake-tts",
        "fake-whatsapp",
        "fake-telephony",
        "fake-live",
    }, f"unpriced or unmetered providers; got {[(e.provider, e.computed_cost_inr) for e in events]}"


async def test_no_provider_is_left_unpriced(session, meter, prices):
    """A provider whose usage has no price_book row reports ₹0 forever — which the
    cost-guard reads as "budget to spare". Catch that here, not in an invoice."""
    llm = FakeLLMProvider()
    await llm.complete(LLMRequest(prompt="hello"))
    await meter.flush()

    assert prices.unpriced == set()


# -- pricing arithmetic --------------------------------------------------------


async def test_token_prices_are_per_thousand(session, meter, prices):
    """The quantum is a contract with `seeds/price_book.json`, not a detail: read
    it per-token and every LLM cost is 1000x wrong, in our favour."""
    assert UNIT_QUANTUM[PriceUnit.TOKEN_IN] == Decimal("1000")
    assert UNIT_QUANTUM[PriceUnit.TOKEN_OUT] == Decimal("1000")
    assert UNIT_QUANTUM[PriceUnit.CHAR] == Decimal("1000")

    llm = FakeLLMProvider()
    await llm.complete(LLMRequest(prompt="x"))
    await meter.flush()

    event = (await _events(session))[0]
    # Seeded: fake-llm token_in ₹0.02/1k, token_out ₹0.08/1k. Default fake script
    # is 120 in / 40 out => 120/1000*0.02 + 40/1000*0.08 = 0.0024 + 0.0032.
    assert event.tokens_in == 120
    assert event.tokens_out == 40
    assert event.computed_cost_inr == Decimal("0.0056")


async def test_call_minutes_price_from_seconds(session, meter):
    """Telephony meters seconds and bills minutes; the conversion lives in one
    place (`UNIT_QUANTUM[CALL_MIN] = 60`) and this is it."""
    telephony = FakeTelephonyProvider()
    handle = await telephony.place_call(CallRequest(to="+919876543210", applet_url="http://x"))
    telephony.complete(handle.call_sid, seconds=120)
    await meter.flush()

    event = next(e for e in await _events(session) if e.audio_seconds > 0)
    # fake-telephony: ₹0.75/minute, 120s = 2 minutes = ₹1.50
    assert event.computed_cost_inr == Decimal("1.5000")


async def test_audio_is_never_billed_as_both_seconds_and_minutes(session, meter, prices):
    """Regression: `audio_seconds` is the quantity for both `audio_sec` and
    `call_min`. A provider with rows for both units must be charged once — the
    naive version double-billed every voice minute."""
    from datetime import date

    from app.models.metering import PriceBook

    # Give one provider both an audio_sec and a call_min rate.
    session.add_all(
        [
            PriceBook(
                provider="fake-stt",
                model=WILDCARD_MODEL,
                unit=PriceUnit.CALL_MIN,
                price_inr=Decimal("60"),
                effective_from=date(2026, 1, 1),
            ),
        ]
    )
    await session.flush()
    prices.invalidate()

    stt = FakeSTTProvider()
    await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")  # exactly 1 second
    await meter.flush()

    event = (await _events(session))[0]
    # call_min wins (₹60/min ÷ 60 = ₹1.00). If both units charged, it would be
    # ₹1.005 — the audio_sec rate silently added on top.
    assert event.computed_cost_inr == Decimal("1.0000")


async def test_price_in_force_is_the_one_at_the_time_of_the_call(session, meter, prices):
    """Costs are recomputable (doc 02 §8): a future rate must not price today's
    call, or a scheduled price rise would retroactively rewrite the month."""
    from datetime import date

    from app.models.metering import PriceBook

    session.add(
        PriceBook(
            provider="fake-llm",
            model="fake-llm-1",
            unit=PriceUnit.TOKEN_IN,
            price_inr=Decimal("999"),
            effective_from=date(2099, 1, 1),  # not yet in force
        )
    )
    await session.flush()
    prices.invalidate()

    llm = FakeLLMProvider()
    await llm.complete(LLMRequest(prompt="x"))
    await meter.flush()

    event = (await _events(session))[0]
    assert event.computed_cost_inr == Decimal("0.0056")  # today's rate, not 2099's


async def test_unit_cost_ref_points_at_a_real_price_row(session, meter):
    """`unit_cost_ref` is how S18 re-prices history. A dangling ref would make
    that silently impossible."""
    from app.models.metering import PriceBook

    llm = FakeLLMProvider()
    await llm.complete(LLMRequest(prompt="x"))
    await meter.flush()

    event = (await _events(session))[0]
    assert event.unit_cost_ref is not None
    row = await session.get(PriceBook, event.unit_cost_ref)
    assert row is not None and row.provider == "fake-llm"


# -- attribution ---------------------------------------------------------------


async def test_usage_scope_attributes_calls_to_an_intake(session, meter):
    """Cost per intake (doc 02 §8) is a GROUP BY on intake_id — which only works
    if every provider call inside the intake carries it, without any of them
    having been passed it."""
    intake_id = uuid.uuid4()
    llm = FakeLLMProvider()

    with usage_scope(
        intake_id=intake_id,
        session_id="sess-1",
        channel=Channel.PHONE,
        tier=IntakeTier.RULE_BASED,
    ):
        await llm.complete(LLMRequest(prompt="x"), purpose=UsagePurpose.INTAKE_TURN)

    await meter.flush()
    event = (await _events(session))[0]

    assert event.intake_id == intake_id
    assert event.session_id == "sess-1"
    assert event.channel is Channel.PHONE
    assert event.tier is IntakeTier.RULE_BASED
    assert event.purpose is UsagePurpose.INTAKE_TURN


async def test_nested_scope_overrides_only_what_it_names(session, meter):
    """A mid-session tier downgrade (S5) nests a scope naming only `tier`; the
    intake_id must survive it or the downgraded half of the call loses its cost."""
    intake_id = uuid.uuid4()
    llm = FakeLLMProvider()

    with usage_scope(intake_id=intake_id, channel=Channel.KIOSK, tier=IntakeTier.CONVERSATIONAL):
        with usage_scope(tier=IntakeTier.RULE_BASED):
            await llm.complete(LLMRequest(prompt="x"))

    await meter.flush()
    event = (await _events(session))[0]

    assert event.intake_id == intake_id
    assert event.channel is Channel.KIOSK
    assert event.tier is IntakeTier.RULE_BASED


async def test_scope_does_not_leak_after_the_block(session, meter):
    llm = FakeLLMProvider()
    with usage_scope(intake_id=uuid.uuid4()):
        pass
    await llm.complete(LLMRequest(prompt="x"))
    await meter.flush()

    assert (await _events(session))[0].intake_id is None


async def test_minute_bucket_is_truncated_on_write(session, meter):
    """S18's per-minute rollup is a plain GROUP BY on this column."""
    llm = FakeLLMProvider()
    await llm.complete(LLMRequest(prompt="x"))
    await meter.flush()

    event = (await _events(session))[0]
    assert event.minute_bucket.second == 0
    assert event.minute_bucket.microsecond == 0
    assert event.minute_bucket <= event.at


# -- the "never break the call path" guarantees --------------------------------


async def test_recording_never_raises_without_a_meter(session):
    """Scripts and one-shot jobs run with no meter installed. That must be a
    no-op, not an exception in the middle of someone's OTP."""
    from app.providers.metering import set_meter

    set_meter(None)
    sms = FakeSMSProvider()
    result = await sms.send(SmsMessage(to="+919876543210", body="hi"))
    assert result.accepted


async def test_meter_drops_oldest_when_the_buffer_is_full(session, prices):
    """Back-pressure on a live phone call is worse than a gap in the dashboard,
    so a stalled drain drops rows and counts them — it must not grow forever."""
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime

    from app.providers.metering import UsageContext, UsageDraft, UsageMeter

    @asynccontextmanager
    async def factory():
        yield session

    meter = UsageMeter(factory, prices, max_buffer=2)
    for i in range(5):
        meter.record(
            UsageDraft(
                at=datetime.now(UTC),
                provider="fake-llm",
                model="fake-llm-1",
                purpose=UsagePurpose.OTHER,
                usage=UsageDelta(tokens_in=i),
                context=UsageContext(),
            )
        )

    assert meter.dropped == 3
    written = await meter.flush()
    assert written == 2


async def test_failed_calls_are_still_metered(session, meter):
    """A vendor that 500s after burning input tokens still bills for them. Only
    counting successes understates exactly the days that need explaining."""
    from app.providers.resilience import ProviderUnavailable, RetryPolicy

    llm = FakeLLMProvider(retry=RetryPolicy(attempts=2, base_delay_seconds=0))
    llm.fail_with = RuntimeError("boom")

    with pytest.raises(ProviderUnavailable):
        await llm.complete(LLMRequest(prompt="x"))

    await meter.flush()
    events = await _events(session)
    # One row per attempt, not one per call.
    assert len(events) == 2
