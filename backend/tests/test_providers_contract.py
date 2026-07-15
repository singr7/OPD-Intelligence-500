"""Contract tests: the behaviour every provider must share (doc 02 §9).

S3 AC: "contract tests pass against fakes". The point is not that the fakes work
— it is that the *interface* has behaviour, and the fakes are how we assert it
without a vendor account. Anything added later (a real MSG91, an S5 Gemini Live)
inherits `Provider` and therefore inherits these guarantees; the parametrised
tests below are the ones that would catch a subclass quietly opting out.

Tests never make a live vendor call (doc 07 §4). The real impls are exercised
through `httpx.MockTransport` in `test_providers_vendors.py`.
"""

from __future__ import annotations

import pytest

from app.providers.audio import PCM16, AudioClip
from app.providers.base import Provider, with_fallback
from app.providers.llm import FakeLLMProvider, FakeLLMScript, LLMRequest, ToolCall
from app.providers.messaging import Button, FakeMessagingProvider, OutboundMessage
from app.providers.realtime import (
    EventKind,
    FakeRealtimeProvider,
    FakeRealtimeScript,
    RealtimeConfig,
)
from app.providers.resilience import (
    BreakerState,
    CircuitBreaker,
    ProviderBadRequest,
    ProviderUnavailable,
    RetryPolicy,
)
from app.providers.sms import FakeSMSProvider, SmsMessage, normalise_msisdn
from app.providers.stt import FakeSTTProvider
from app.providers.telephony import CallRequest, CallState, FakeTelephonyProvider
from app.providers.tts import FakeTTSProvider

pytestmark = pytest.mark.usefixtures("seeded_prices")


def _all_fakes() -> list[Provider]:
    return [
        FakeSMSProvider(),
        FakeLLMProvider(),
        FakeSTTProvider(),
        FakeTTSProvider(),
        FakeRealtimeProvider(),
        FakeMessagingProvider(),
        FakeTelephonyProvider(),
    ]


async def _exercise(provider: Provider) -> None:
    """Make one call against whichever interface `provider` implements."""
    match provider.kind:
        case "sms":
            await provider.send(SmsMessage(to="+919876543210", body="hi", template_key="otp_login"))
        case "llm":
            await provider.complete(LLMRequest(prompt="hi"))
        case "stt":
            await provider.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
        case "tts":
            await provider.synthesize("namaste", "hi")
        case "realtime":
            session = await provider.connect(RealtimeConfig(system="s", lang="hi"))
            await session.close()
        case "messaging":
            await provider.send(OutboundMessage(to="+919876543210", text="hi"))
        case "telephony":
            await provider.place_call(CallRequest(to="+919876543210", applet_url="http://x"))
        case _:
            raise AssertionError(f"unexercised provider kind {provider.kind!r}")


# -- every interface has a fake, and every fake obeys the base contract ---------


def test_every_interface_has_a_fake():
    """Doc 07 §4: "every provider has a fake". Seven interfaces in doc 02 §9."""
    kinds = {p.kind for p in _all_fakes()}
    assert kinds == {"sms", "llm", "stt", "tts", "realtime", "messaging", "telephony"}


@pytest.mark.parametrize("provider", _all_fakes(), ids=lambda p: p.kind)
async def test_fake_names_are_distinct_per_interface(provider):
    """Every fake once shared the name "fake", which collided in `price_book` —
    an STT call matched telephony's per-minute rate. Names are the join key for
    both pricing and the dashboard, so they must be unique per interface."""
    assert provider.name.startswith("fake-")


@pytest.mark.parametrize("provider", _all_fakes(), ids=lambda p: p.kind)
async def test_successful_call_updates_health(provider, meter):
    assert provider.health.status == "ok"
    await _exercise(provider)

    assert provider.health.calls == 1
    assert provider.health.failures == 0
    assert provider.health.last_ok_at is not None
    assert provider.health.status == "ok"


@pytest.mark.parametrize("provider", _all_fakes(), ids=lambda p: p.kind)
async def test_provider_reports_its_kind_and_name(provider):
    """Both are wire values: they land in `usage_events.provider` and drive the
    price book. An empty one silently unprices a whole vendor."""
    assert provider.kind and provider.name


# -- failure semantics ---------------------------------------------------------


async def test_vendor_fault_raises_provider_unavailable(meter):
    """The tier ladder's signal (doc 02 §2). Everything above the provider layer
    treats this one exception as "fall back / downgrade"."""
    llm = FakeLLMProvider(retry=RetryPolicy(attempts=2, base_delay_seconds=0))
    llm.fail_with = RuntimeError("vendor exploded")

    with pytest.raises(ProviderUnavailable):
        await llm.complete(LLMRequest(prompt="x"))

    assert llm.health.status == "degraded"
    assert llm.health.failures == 2


async def test_bad_request_is_not_retried_and_does_not_trip_the_breaker(meter):
    """A malformed request is a bug, not an outage. Retrying it burns budget, and
    tripping the breaker would take a healthy provider offline for everyone."""
    llm = FakeLLMProvider(retry=RetryPolicy(attempts=3, base_delay_seconds=0))
    llm.fail_with = ProviderBadRequest("bad prompt")

    with pytest.raises(ProviderBadRequest):
        await llm.complete(LLMRequest(prompt="x"))

    assert len(llm.calls) == 1  # not retried
    assert llm.health.failures == 0  # not counted against the vendor
    assert llm.breaker.state is BreakerState.CLOSED


async def test_retry_succeeds_on_a_later_attempt(meter):
    """Transient blips should not reach the caller at all."""

    class _RecoverOnSecond(FakeLLMProvider):
        async def _complete(self, request, call):
            if len(self.calls) == 0:
                self.calls.append(request)
                raise RuntimeError("blip")
            return await super()._complete(request, call)

    llm = _RecoverOnSecond(retry=RetryPolicy(attempts=3, base_delay_seconds=0))
    result = await llm.complete(LLMRequest(prompt="x"))

    assert result.text == "ok"
    assert llm.health.failures == 1
    assert llm.health.status == "ok"  # recovered: last_ok_at is newer than the error


async def test_sms_rejects_an_implausible_number_without_calling_the_vendor(meter):
    """A mangled number silently sent is a doctor who cannot log in and no error
    anywhere. Reject it at the boundary."""
    sms = FakeSMSProvider()
    with pytest.raises(ProviderBadRequest):
        await sms.send(SmsMessage(to="12", body="hi"))
    assert sms.sent == []


def test_msisdn_normalisation():
    """Both vendors want bare digits with a country code."""
    assert normalise_msisdn("+91 98765 43210") == "919876543210"
    assert normalise_msisdn("9876543210") == "919876543210"
    assert normalise_msisdn("09876543210") == "919876543210"
    with pytest.raises(ProviderBadRequest):
        normalise_msisdn("abc")


# -- circuit breaker -----------------------------------------------------------


async def test_breaker_opens_after_consecutive_failures_then_rejects_fast(meter):
    """Once open, calls fail immediately rather than making a patient wait to
    discover the provider is down — that latency is the whole point."""
    breaker = CircuitBreaker(failure_threshold=2, reset_after_seconds=60)
    llm = FakeLLMProvider(retry=RetryPolicy(attempts=1), breaker=breaker)
    llm.fail_with = RuntimeError("down")

    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await llm.complete(LLMRequest(prompt="x"))

    assert breaker.state is BreakerState.OPEN
    assert llm.health.status == "down"

    calls_before = len(llm.calls)
    with pytest.raises(ProviderUnavailable, match="circuit open"):
        await llm.complete(LLMRequest(prompt="x"))
    assert len(llm.calls) == calls_before  # never reached the vendor


async def test_breaker_half_opens_and_closes_on_a_good_probe(meter):
    breaker = CircuitBreaker(failure_threshold=1, reset_after_seconds=0)
    llm = FakeLLMProvider(retry=RetryPolicy(attempts=1), breaker=breaker)

    llm.fail_with = RuntimeError("down")
    with pytest.raises(ProviderUnavailable):
        await llm.complete(LLMRequest(prompt="x"))
    assert breaker.state is BreakerState.HALF_OPEN  # reset_after=0 → probe immediately

    llm.fail_with = None
    result = await llm.complete(LLMRequest(prompt="x"))
    assert result.text == "ok"
    assert breaker.state is BreakerState.CLOSED


def test_retry_backoff_is_bounded_and_jittered():
    """Shallow on purpose (doc 02 §2): the tier ladder recovers an intake faster
    than a retry loop can, and 60-80 concurrent sessions retrying in lockstep is
    how you re-kill a recovering provider."""
    policy = RetryPolicy(attempts=3, base_delay_seconds=0.1, max_delay_seconds=2.0)
    assert policy.delay_for(1) == 0.0
    assert all(0 <= policy.delay_for(n) <= 2.0 for n in range(1, 10))

    fixed = RetryPolicy(base_delay_seconds=0.1, max_delay_seconds=2.0, jitter=False)
    assert fixed.delay_for(2) == 0.1
    assert fixed.delay_for(3) == 0.2
    assert fixed.delay_for(9) == 2.0  # capped


# -- fallback chains (doc 02 §2) -----------------------------------------------


async def test_fallback_moves_to_the_next_provider_on_unavailable(meter):
    primary = FakeLLMProvider(retry=RetryPolicy(attempts=1))
    primary.fail_with = RuntimeError("sarvam is down")
    secondary = FakeLLMProvider()
    secondary.queue(FakeLLMScript(text="from the fallback"))

    result = await with_fallback([primary, secondary], lambda p: p.complete(LLMRequest(prompt="x")))
    assert result.text == "from the fallback"


async def test_fallback_does_not_retry_a_bad_request_on_the_next_provider(meter):
    """If Sarvam rejected it as malformed, Google will too — trying twice just
    doubles the cost of the same bug."""
    primary = FakeLLMProvider(retry=RetryPolicy(attempts=1))
    primary.fail_with = ProviderBadRequest("malformed")
    secondary = FakeLLMProvider()

    with pytest.raises(ProviderBadRequest):
        await with_fallback([primary, secondary], lambda p: p.complete(LLMRequest(prompt="x")))
    assert secondary.calls == []


async def test_fallback_raises_when_every_provider_is_out(meter):
    providers = []
    for _ in range(2):
        p = FakeLLMProvider(retry=RetryPolicy(attempts=1))
        p.fail_with = RuntimeError("down")
        providers.append(p)

    with pytest.raises(ProviderUnavailable, match="exhausted"):
        await with_fallback(providers, lambda p: p.complete(LLMRequest(prompt="x")))


# -- per-interface behaviour worth pinning -------------------------------------


async def test_llm_parses_json_through_a_code_fence(meter):
    """Models wrap JSON in ```json despite instructions. Failing a patient's
    intake over three backticks is a bad trade."""
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text='```json\n{"dept_key": "med_onc"}\n```'))
    result = await llm.complete(LLMRequest(prompt="x", json_output=True))
    assert result.json() == {"dept_key": "med_onc"}


async def test_llm_reports_non_json_as_a_bad_request(meter):
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text="I'm sorry, I can't do that"))
    result = await llm.complete(LLMRequest(prompt="x", json_output=True))
    with pytest.raises(ProviderBadRequest):
        result.json()


async def test_llm_returns_tool_calls(meter):
    """V2's dialogue loop drives the intake through tool calls, not prose."""
    llm = FakeLLMProvider()
    llm.queue(
        FakeLLMScript(
            text="", tool_calls=(ToolCall(name="save_answer", arguments={"node_id": "n1"}),)
        )
    )
    result = await llm.complete(LLMRequest(prompt="x"))
    assert result.tool_calls[0].name == "save_answer"


async def test_stt_returns_scripted_utterances_in_order(meter):
    stt = FakeSTTProvider()
    stt.queue("mujhe bukhar hai", "do din se")
    clip = AudioClip(data=b"\x00\x00" * 8000)

    assert (await stt.transcribe(clip, "hi")).text == "mujhe bukhar hai"
    assert (await stt.transcribe(clip, "hi")).text == "do din se"


async def test_stt_confidence_flags_uncertainty(meter):
    """Doc 03 §4: uncertain spans get marked `[unclear: ...]`, never guessed."""
    stt = FakeSTTProvider(confidence=0.3)
    result = await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
    assert result.is_uncertain

    confident = FakeSTTProvider(confidence=0.95)
    assert not (await confident.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")).is_uncertain


async def test_unknown_confidence_is_not_treated_as_certain(meter):
    """Sarvam reports no confidence. `None` must mean unknown, not 1.0 — the
    difference is whether a misheard drug name gets flagged."""
    stt = FakeSTTProvider(confidence=None)
    result = await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
    assert result.confidence is None
    assert not result.is_uncertain  # unknown is not *evidence* of uncertainty either


async def test_tts_refuses_empty_text(meter):
    tts = FakeTTSProvider()
    with pytest.raises(ProviderBadRequest):
        await tts.synthesize("   ", "hi")


async def test_tts_audio_length_tracks_the_text(meter):
    tts = FakeTTSProvider()
    short = await tts.synthesize("haan", "hi")
    long = await tts.synthesize("haan " * 40, "hi")
    assert long.audio.duration() > short.audio.duration()


async def test_whatsapp_button_titles_are_length_checked():
    """Meta truncates over 20 chars silently — a clipped Hindi word on a button
    a patient must tap."""
    Button(id="yes", title="Haan")
    with pytest.raises(ProviderBadRequest):
        Button(id="x", title="a" * 21)


async def test_telephony_meters_only_when_the_call_completes(session, meter):
    """Placing a call bills nothing; the minutes are known later, from a status
    callback (S15). Metering at dial time would invent the duration."""
    from sqlalchemy import select

    from app.models.metering import UsageEvent

    telephony = FakeTelephonyProvider()
    handle = await telephony.place_call(CallRequest(to="+919876543210", applet_url="http://x"))
    assert handle.state is CallState.QUEUED

    await meter.flush()
    placed = list((await session.execute(select(UsageEvent))).scalars())
    assert all(e.computed_cost_inr == 0 for e in placed)

    telephony.complete(handle.call_sid, seconds=60)
    await meter.flush()
    events = list((await session.execute(select(UsageEvent))).scalars())
    assert any(e.computed_cost_inr > 0 for e in events)


async def test_realtime_session_streams_events_and_tool_calls(meter):
    """The V1 shape S5 and S14 will build against: audio in, events out, tools
    driving the intake."""
    provider = FakeRealtimeProvider(
        script=[
            FakeRealtimeScript(
                say="Namaste",
                tool_calls=(ToolCall(name="get_next_node", arguments={"session_id": "s1"}),),
            )
        ]
    )
    session = await provider.connect(RealtimeConfig(system="s", lang="hi", session_id="s1"))
    await session.send_audio(AudioClip(data=b"\x00\x00" * 8000, mime=PCM16))

    kinds = []
    async for event in session.events():
        kinds.append(event.kind)
        if event.kind is EventKind.TURN_COMPLETE:
            break
    await session.close()

    assert EventKind.TOOL_CALL in kinds
    assert EventKind.AUDIO in kinds


async def test_realtime_meters_long_calls_as_they_go_not_at_hangup(session, meter):
    """Doc 02 §5: per-minute audio metering. A 9-minute intake metered at hangup
    is 9 minutes the cost-guard cannot see — the guard exists to act *during* the
    spend, not to file a report afterwards."""
    from sqlalchemy import select

    from app.models.metering import UsageEvent

    provider = FakeRealtimeProvider(script=[FakeRealtimeScript(say="")] * 5)
    provider.meter_every_seconds = 2
    live = await provider.connect(RealtimeConfig(system="s", lang="hi"))

    # 3 seconds of audio, still mid-call — no close(), no hangup.
    await live.send_audio(AudioClip(data=b"\x00\x00" * 8000 * 3))
    await meter.flush()

    events = list((await session.execute(select(UsageEvent))).scalars())
    assert any(e.audio_seconds > 0 for e in events), "long call metered nothing before hangup"
