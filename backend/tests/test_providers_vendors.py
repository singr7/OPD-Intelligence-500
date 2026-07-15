"""The real vendor impls, driven through a mocked HTTP transport.

No live vendor call ever happens in tests (doc 07 §4), but "we wrote the HTTP
code and never ran it" is not much better. `httpx.MockTransport` runs the real
request-building and response-parsing code and asserts on the bytes we would put
on the wire — which is where the bugs in this layer actually live (a wrong header
name, tokens read from the wrong key, a 200 that means failure).

What this cannot prove: that the vendor agrees. Endpoint shapes, DLT template
ids and auth are per-account, and the first live send needs a human watching a
handset. Registered in STATE.md → Stubs & fakes.

**The point of the SMS pair**: MSG91 and Exotel are interchangeable behind
`SMSProvider`, so the pilot's open decision is `SMS_PROVIDER=` and nothing else.
`test_registry.py::test_swapping_the_sms_vendor_is_config_only` is the other half.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.prompts.tools import INTAKE_TOOLS
from app.providers.audio import AudioClip
from app.providers.llm import GeminiFlashProvider, LLMRequest, OpenAIProvider
from app.providers.messaging import Button, MetaWhatsAppProvider, OutboundMessage
from app.providers.resilience import ProviderBadRequest, ProviderUnavailable, RetryPolicy
from app.providers.sms import ExotelSMSProvider, Msg91SMSProvider, SmsMessage
from app.providers.stt import GoogleSTTProvider, SarvamSTTProvider
from app.providers.telephony import CallRequest, CallState, ExotelTelephonyProvider
from app.providers.tts import GoogleTTSProvider, SarvamTTSProvider

pytestmark = pytest.mark.usefixtures("seeded_prices")


def _client(handler, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def _captures(response: httpx.Response):
    """Record the request we sent, and reply with `response`."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return seen, handler


# -- MSG91 ---------------------------------------------------------------------


async def test_msg91_sends_template_id_and_variables(meter):
    """Flow API transmits the template id + variables; MSG91 renders from the
    registered DLT template, so our body is deliberately not sent."""
    seen, handler = _captures(httpx.Response(200, json={"type": "success", "request_id": "r1"}))
    sms = Msg91SMSProvider(
        auth_key="key-123",
        sender_id="OPDALW",
        template_ids={"otp_login": "tmpl-99"},
        client=_client(handler, base_url=Msg91SMSProvider.BASE_URL),
    )

    result = await sms.send(
        SmsMessage(
            to="+91 98765 43210",
            body="123456 is your code",
            template_key="otp_login",
            variables={"otp": "123456", "minutes": "5"},
        )
    )

    assert result.accepted and result.message_id == "r1"
    request = seen[0]
    assert request.headers["authkey"] == "key-123"
    body = json.loads(request.content)
    assert body["template_id"] == "tmpl-99"
    assert body["recipients"][0]["mobiles"] == "919876543210"  # normalised
    assert body["recipients"][0]["otp"] == "123456"


async def test_msg91_treats_a_200_with_type_error_as_a_failure(meter):
    """MSG91 returns HTTP 200 with {"type": "error"} for some failures. Reading
    the status code alone would report a never-delivered OTP as sent."""
    _, handler = _captures(httpx.Response(200, json={"type": "error", "message": "invalid"}))
    sms = Msg91SMSProvider(
        auth_key="k",
        sender_id="S",
        template_ids={"otp_login": "t"},
        client=_client(handler, base_url=Msg91SMSProvider.BASE_URL),
        retry=RetryPolicy(attempts=1),
    )

    with pytest.raises(ProviderUnavailable):
        await sms.send(SmsMessage(to="+919876543210", body="x", template_key="otp_login"))


async def test_msg91_refuses_to_send_without_a_configured_template(meter):
    """DLT drops unregistered transactional text silently. Failing loudly here
    beats an OTP that vanishes with a 200."""
    _, handler = _captures(httpx.Response(200, json={"type": "success"}))
    sms = Msg91SMSProvider(
        auth_key="k",
        sender_id="S",
        template_ids={},
        client=_client(handler, base_url=Msg91SMSProvider.BASE_URL),
    )

    with pytest.raises(ProviderBadRequest, match="MSG91_TEMPLATE_IDS"):
        await sms.send(SmsMessage(to="+919876543210", body="x", template_key="otp_login"))


# -- Exotel SMS ----------------------------------------------------------------


async def test_exotel_sms_sends_the_rendered_body_and_dlt_ids(meter):
    """The other half of the pair: Exotel takes the body and matches it against
    the registered template — the mirror image of MSG91's contract."""
    seen, handler = _captures(
        httpx.Response(200, json={"SMSMessage": {"Sid": "sms-1", "Status": "queued"}})
    )
    sms = ExotelSMSProvider(
        sid="acct",
        api_key="k",
        api_token="t",
        sender_id="OPDALW",
        dlt_entity_id="ent-1",
        dlt_template_ids={"otp_login": "dlt-7"},
        client=_client(handler, base_url="https://api.exotel.com/v1/Accounts/acct"),
    )

    result = await sms.send(
        SmsMessage(to="+919876543210", body="123456 is your code", template_key="otp_login")
    )

    assert result.accepted and result.message_id == "sms-1"
    form = dict(httpx.QueryParams(seen[0].content.decode()))
    assert form["Body"] == "123456 is your code"
    assert form["To"] == "919876543210"
    assert form["DltTemplateId"] == "dlt-7"
    assert form["DltEntityId"] == "ent-1"


async def test_exotel_sms_rejects_auth_failures_as_bad_requests(meter):
    """A 401 is our credentials, not their outage: retrying and tripping the
    breaker would hide the real problem behind a circuit-open error."""
    _, handler = _captures(httpx.Response(401, text="unauthorized"))
    sms = ExotelSMSProvider(
        sid="a",
        api_key="k",
        api_token="t",
        sender_id="S",
        client=_client(handler, base_url="https://api.exotel.com/v1/Accounts/a"),
    )

    with pytest.raises(ProviderBadRequest):
        await sms.send(SmsMessage(to="+919876543210", body="x"))


async def test_both_sms_vendors_satisfy_the_same_interface(meter):
    """The decision the pilot has open is which of these to buy. This is the test
    that keeps it a purchasing decision instead of an engineering one."""
    ok_msg91 = httpx.Response(200, json={"type": "success", "request_id": "r"})
    ok_exotel = httpx.Response(200, json={"SMSMessage": {"Sid": "s", "Status": "queued"}})

    msg91 = Msg91SMSProvider(
        auth_key="k",
        sender_id="S",
        template_ids={"otp_login": "t"},
        client=_client(lambda r: ok_msg91, base_url=Msg91SMSProvider.BASE_URL),
    )
    exotel = ExotelSMSProvider(
        sid="a",
        api_key="k",
        api_token="t",
        sender_id="S",
        dlt_template_ids={"otp_login": "d"},
        client=_client(lambda r: ok_exotel, base_url="https://api.exotel.com/v1/Accounts/a"),
    )

    message = SmsMessage(
        to="+919876543210",
        body="123456 is your code",
        template_key="otp_login",
        variables={"otp": "123456", "minutes": "5"},
    )
    for provider in (msg91, exotel):
        result = await provider.send(message)
        assert result.accepted, provider.name


# -- Gemini / OpenAI -----------------------------------------------------------


async def test_gemini_builds_system_instruction_and_reads_usage(meter):
    """Wire shape that bites on a version bump: system prompt is its own field,
    not a message role, and tokens come from `usageMetadata`."""
    seen, handler = _captures(
        httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "med_onc"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 5},
            },
        )
    )
    llm = GeminiFlashProvider(
        api_key="g-key", client=_client(handler, base_url=GeminiFlashProvider.BASE_URL)
    )

    result = await llm.complete(LLMRequest(prompt="chest me gaanth", system="You route patients"))

    assert result.text == "med_onc"
    assert (result.tokens_in, result.tokens_out) == (200, 5)
    assert result.usage_reported

    body = json.loads(seen[0].content)
    assert body["systemInstruction"]["parts"][0]["text"] == "You route patients"
    assert seen[0].headers["x-goog-api-key"] == "g-key"


async def test_gemini_strips_additional_properties_from_tool_schemas(meter):
    """Gemini's schema dialect rejects `additionalProperties`; our contract keeps
    it for our own validation. The adapter is what reconciles the two."""
    seen, handler = _captures(
        httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )
    )
    llm = GeminiFlashProvider(
        api_key="k", client=_client(handler, base_url=GeminiFlashProvider.BASE_URL)
    )

    await llm.complete(LLMRequest(prompt="x", tools=INTAKE_TOOLS))

    declarations = json.loads(seen[0].content)["tools"][0]["functionDeclarations"]
    assert {d["name"] for d in declarations} == {t.name for t in INTAKE_TOOLS}
    assert all("additionalProperties" not in d["parameters"] for d in declarations)


async def test_gemini_returns_function_calls(meter):
    """V1/V2 drive the intake through tool calls; Gemini nests them in parts."""
    _, handler = _captures(
        httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "save_answer",
                                        "args": {"node_id": "n1", "value": "yes"},
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
            },
        )
    )
    llm = GeminiFlashProvider(
        api_key="k", client=_client(handler, base_url=GeminiFlashProvider.BASE_URL)
    )

    result = await llm.complete(LLMRequest(prompt="x", tools=INTAKE_TOOLS))
    assert result.tool_calls[0].name == "save_answer"
    assert result.tool_calls[0].arguments == {"node_id": "n1", "value": "yes"}


async def test_gemini_safety_block_is_an_outage_not_a_silent_empty_answer(meter):
    """A blocked prompt returns no candidate, only promptFeedback. Reading that
    as an empty summary would hand the doctor a blank screen."""
    _, handler = _captures(httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    llm = GeminiFlashProvider(
        api_key="k",
        client=_client(handler, base_url=GeminiFlashProvider.BASE_URL),
        retry=RetryPolicy(attempts=1),
    )

    with pytest.raises(ProviderUnavailable, match="no candidate"):
        await llm.complete(LLMRequest(prompt="x"))


async def test_openai_does_not_double_count_cached_tokens(meter):
    """OpenAI counts cached tokens *inside* prompt_tokens; our metering treats
    them as additive. Without the subtraction, cached calls bill twice."""
    _, handler = _captures(
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 40},
                },
            },
        )
    )
    llm = OpenAIProvider(api_key="k", client=_client(handler, base_url=OpenAIProvider.BASE_URL))

    result = await llm.complete(LLMRequest(prompt="x"))
    assert (result.tokens_in, result.cached_tokens) == (100, 40)
    # What gets metered: 60 fresh + 40 cached = 100 total, not 140.
    assert llm.health.calls == 1


async def test_openai_sends_tools_in_its_own_shape(meter):
    seen, handler = _captures(
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "get_next_node",
                                        "arguments": '{"session_id": "s1"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
        )
    )
    llm = OpenAIProvider(api_key="k", client=_client(handler, base_url=OpenAIProvider.BASE_URL))

    result = await llm.complete(LLMRequest(prompt="x", tools=INTAKE_TOOLS))

    sent = json.loads(seen[0].content)["tools"]
    assert sent[0]["type"] == "function"
    assert result.tool_calls[0].name == "get_next_node"
    assert result.tool_calls[0].call_id == "call_1"  # OpenAI needs this echoed back


# -- Sarvam / Google -----------------------------------------------------------


async def test_sarvam_stt_uploads_audio_and_does_not_invent_confidence(meter):
    """Saarika reports no confidence. A fabricated 1.0 here is how a misheard
    drug name reaches a doctor unflagged (doc 03 §4)."""
    seen, handler = _captures(
        httpx.Response(200, json={"transcript": "mujhe bukhar hai", "language_code": "hi-IN"})
    )
    stt = SarvamSTTProvider(
        api_key="s-key", client=_client(handler, base_url=SarvamSTTProvider.BASE_URL)
    )

    result = await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")

    assert result.text == "mujhe bukhar hai"
    assert result.confidence is None
    assert seen[0].headers["api-subscription-key"] == "s-key"
    assert b"hi-IN" in seen[0].content  # bare `hi` would be rejected by the vendor


async def test_google_stt_reports_real_confidence(meter):
    """Why Google is worth its price on dictation: it tells us when it is unsure."""
    _, handler = _captures(
        httpx.Response(
            200,
            json={
                "results": [{"alternatives": [{"transcript": "Tab Augmentin", "confidence": 0.42}]}]
            },
        )
    )
    stt = GoogleSTTProvider(
        api_key="k", client=_client(handler, base_url=GoogleSTTProvider.BASE_URL)
    )

    result = await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
    assert result.confidence == 0.42
    assert result.is_uncertain


async def test_google_stt_empty_result_is_not_an_error(meter):
    """Silence is a fact, not a fault: the intake engine re-prompts."""
    _, handler = _captures(httpx.Response(200, json={"results": []}))
    stt = GoogleSTTProvider(
        api_key="k", client=_client(handler, base_url=GoogleSTTProvider.BASE_URL)
    )

    result = await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
    assert result.text == "" and result.confidence == 0.0


async def test_sarvam_tts_meters_characters_not_seconds(session, meter):
    """Bulbul bills per character. This is the whole reason `PriceUnit.CHAR` and
    `usage_events.characters` exist — see the S3 session log."""
    import base64

    from sqlalchemy import select

    from app.models.metering import UsageEvent

    _, handler = _captures(
        httpx.Response(200, json={"audios": [base64.b64encode(b"\x00\x00" * 100).decode()]})
    )
    tts = SarvamTTSProvider(
        api_key="k", client=_client(handler, base_url=SarvamTTSProvider.BASE_URL)
    )

    text = "Aapko bukhar kab se hai?"
    await tts.synthesize(text, "hi")
    await meter.flush()

    event = (await session.execute(select(UsageEvent))).scalars().first()
    assert event.characters == len(text)
    assert event.audio_seconds == 0  # priced on chars, not on output duration
    assert event.computed_cost_inr > 0


async def test_google_tts_decodes_audio_content(meter):
    import base64

    _, handler = _captures(
        httpx.Response(200, json={"audioContent": base64.b64encode(b"\x01\x02").decode()})
    )
    tts = GoogleTTSProvider(
        api_key="k", client=_client(handler, base_url=GoogleTTSProvider.BASE_URL)
    )

    speech = await tts.synthesize("namaste", "hi")
    assert speech.audio.data == b"\x01\x02"


# -- Meta WhatsApp -------------------------------------------------------------


async def test_meta_sends_interactive_buttons(meter):
    seen, handler = _captures(httpx.Response(200, json={"messages": [{"id": "wamid.1"}]}))
    wa = MetaWhatsAppProvider(
        access_token="tok",
        phone_number_id="123",
        client=_client(handler, base_url=f"{MetaWhatsAppProvider.BASE_URL}/v21.0"),
    )

    result = await wa.send(
        OutboundMessage(
            to="919876543210",
            text="Aap aaye the?",
            buttons=[Button(id="yes", title="Haan"), Button(id="no", title="Nahi")],
        )
    )

    assert result.message_id == "wamid.1"
    body = json.loads(seen[0].content)
    assert body["type"] == "interactive"
    assert body["interactive"]["action"]["buttons"][0]["reply"]["title"] == "Haan"
    assert seen[0].headers["authorization"] == "Bearer tok"


async def test_meta_out_of_window_uses_a_template(meter):
    """After 24h Meta only accepts a registered template — the check-in ladder
    (S17) depends on this path existing."""
    seen, handler = _captures(httpx.Response(200, json={"messages": [{"id": "wamid.2"}]}))
    wa = MetaWhatsAppProvider(
        access_token="t",
        phone_number_id="1",
        client=_client(handler, base_url=f"{MetaWhatsAppProvider.BASE_URL}/v21.0"),
    )

    await wa.send(
        OutboundMessage(
            to="919876543210",
            template_name="checkin_d2",
            template_lang="hi",
            template_variables=["Ramesh"],
        )
    )

    body = json.loads(seen[0].content)
    assert body["type"] == "template"
    assert body["template"]["name"] == "checkin_d2"
    assert body["template"]["components"][0]["parameters"][0]["text"] == "Ramesh"


async def test_meta_surfaces_its_nested_error_message(meter):
    _, handler = _captures(
        httpx.Response(400, json={"error": {"message": "Recipient not in allowed list"}})
    )
    wa = MetaWhatsAppProvider(
        access_token="t",
        phone_number_id="1",
        client=_client(handler, base_url=f"{MetaWhatsAppProvider.BASE_URL}/v21.0"),
    )

    with pytest.raises(ProviderBadRequest, match="Recipient not in allowed list"):
        await wa.send(OutboundMessage(to="919876543210", text="hi"))


# -- Exotel telephony ----------------------------------------------------------


async def test_exotel_places_a_call_and_maps_state(meter):
    seen, handler = _captures(
        httpx.Response(200, json={"Call": {"Sid": "call-1", "Status": "queued"}})
    )
    telephony = ExotelTelephonyProvider(
        sid="acct",
        api_key="k",
        api_token="t",
        caller_id="08040001234",
        client=_client(handler, base_url="https://api.exotel.com/v1/Accounts/acct"),
    )

    handle = await telephony.place_call(
        CallRequest(
            to="+919876543210",
            applet_url="http://voice-gw/applet",
            status_callback="http://api/webhooks/exotel",
            reference="intake-42",
        )
    )

    assert handle.call_sid == "call-1" and handle.state is CallState.QUEUED
    form = dict(httpx.QueryParams(seen[0].content.decode()))
    assert form["Url"] == "http://voice-gw/applet"
    assert form["CustomField"] == "intake-42"  # how the cost finds its intake


async def test_exotel_unknown_call_state_does_not_crash(meter):
    """A vendor inventing a state must not take the campaign down; the callback
    settles it."""
    _, handler = _captures(
        httpx.Response(200, json={"Call": {"Sid": "c", "Status": "some-new-thing"}})
    )
    telephony = ExotelTelephonyProvider(
        sid="a",
        api_key="k",
        api_token="t",
        caller_id="0",
        client=_client(handler, base_url="https://api.exotel.com/v1/Accounts/a"),
    )

    handle = await telephony.get_call("c")
    assert handle.state is CallState.IN_PROGRESS


async def test_transport_errors_become_provider_unavailable(meter):
    """Connection refused must read as "vendor is out" so the tier ladder moves,
    not as an httpx traceback surfacing in an intake."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    llm = GeminiFlashProvider(
        api_key="k",
        client=_client(handler, base_url=GeminiFlashProvider.BASE_URL),
        retry=RetryPolicy(attempts=1),
    )

    with pytest.raises(ProviderUnavailable, match="transport error"):
        await llm.complete(LLMRequest(prompt="x"))
