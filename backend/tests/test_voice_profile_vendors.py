"""Mock-transport contracts for the VOICE1 cloud components."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.providers.audio import AudioClip
from app.providers.llm import LLMRequest, SarvamLLMProvider
from app.providers.resilience import ProviderBadRequest, ProviderUnavailable, RetryPolicy
from app.providers.stt import OpenAISTTProvider, SarvamSTTProvider
from app.providers.tts import OpenAITTSProvider, SarvamTTSProvider

pytestmark = pytest.mark.asyncio


def _client(handler, base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


def _capture(response: httpx.Response):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return seen, handler


async def test_openai_stt_uses_current_transcription_contract(meter):
    seen, handler = _capture(
        httpx.Response(200, json={"text": "मुझे बुखार है", "language": "hi"})
    )
    provider = OpenAISTTProvider(
        api_key="openai-key",
        client=_client(handler, OpenAISTTProvider.BASE_URL),
    )

    result = await provider.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")

    assert result.text == "मुझे बुखार है"
    assert result.lang == "hi"
    assert seen[0].headers["authorization"] == "Bearer openai-key"
    assert b"gpt-4o-mini-transcribe" in seen[0].content
    assert b"response_format" in seen[0].content


async def test_openai_stt_silence_is_an_empty_transcript(meter):
    _, handler = _capture(httpx.Response(200, json={"text": ""}))
    provider = OpenAISTTProvider(
        api_key="k", client=_client(handler, OpenAISTTProvider.BASE_URL)
    )
    result = await provider.transcribe(AudioClip(data=b"\x00\x00" * 8000), "en")
    assert result.text == ""


async def test_openai_tts_posts_speech_contract_and_returns_wav(meter):
    seen, handler = _capture(httpx.Response(200, content=b"RIFF-wav"))
    provider = OpenAITTSProvider(
        api_key="openai-key",
        client=_client(handler, OpenAITTSProvider.BASE_URL),
    )

    result = await provider.synthesize("Namaste", "hi")

    body = json.loads(seen[0].content)
    assert body == {
        "model": "gpt-4o-mini-tts",
        "input": "Namaste",
        "voice": "alloy",
        "response_format": "wav",
    }
    assert result.audio.data == b"RIFF-wav"


async def test_sarvam_stt_uses_saaras_v3_and_returned_language(meter):
    seen, handler = _capture(
        httpx.Response(
            200,
            json={
                "transcript": "मला ताप आहे",
                "language_code": "mr-IN",
                "language_probability": 0.98,
            },
        )
    )
    provider = SarvamSTTProvider(
        api_key="sarvam-key",
        client=_client(handler, SarvamSTTProvider.BASE_URL),
    )

    result = await provider.transcribe(AudioClip(data=b"\x00\x00" * 8000), "mr")

    assert result.lang == "mr-IN"
    assert result.confidence is None  # language probability is not word confidence
    assert seen[0].headers["api-subscription-key"] == "sarvam-key"
    assert b"saaras%3Av3" in seen[0].content or b"saaras:v3" in seen[0].content
    assert b"transcribe" in seen[0].content


async def test_sarvam_llm_is_openai_compatible_with_sarvam_auth(meter):
    seen, handler = _capture(
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"value":"yes"}'}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )
    )
    provider = SarvamLLMProvider(
        api_key="sarvam-key",
        client=_client(handler, SarvamLLMProvider.BASE_URL),
    )
    result = await provider.complete(LLMRequest(prompt="haan", json_output=True))

    assert result.json() == {"value": "yes"}
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["api-subscription-key"] == "sarvam-key"
    assert json.loads(seen[0].content)["model"] == "sarvam-30b"


async def test_sarvam_tts_uses_current_singular_text_shape(meter):
    audio = base64.b64encode(b"wav").decode()
    seen, handler = _capture(httpx.Response(200, json={"audios": [audio]}))
    provider = SarvamTTSProvider(
        api_key="sarvam-key",
        client=_client(handler, SarvamTTSProvider.BASE_URL),
    )
    await provider.synthesize("नमस्ते", "hi")

    body = json.loads(seen[0].content)
    assert body["text"] == "नमस्ते"
    assert "inputs" not in body
    assert body["model"] == "bulbul:v2"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_openai_stt_distinguishes_bad_requests(status, meter):
    _, handler = _capture(httpx.Response(status, text="bad request"))
    provider = OpenAISTTProvider(
        api_key="k",
        client=_client(handler, OpenAISTTProvider.BASE_URL),
        retry=RetryPolicy(attempts=1),
    )
    with pytest.raises(ProviderBadRequest):
        await provider.transcribe(AudioClip(data=b"\x00\x00"), "en")


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_sarvam_stt_treats_service_failures_as_unavailable(status, meter):
    _, handler = _capture(httpx.Response(status, text="unavailable"))
    provider = SarvamSTTProvider(
        api_key="k",
        client=_client(handler, SarvamSTTProvider.BASE_URL),
        retry=RetryPolicy(attempts=1),
    )
    with pytest.raises(ProviderUnavailable):
        await provider.transcribe(AudioClip(data=b"\x00\x00"), "hi")


@pytest.mark.parametrize("provider_cls", [OpenAISTTProvider, SarvamSTTProvider])
async def test_cloud_stt_refuses_missing_audio(provider_cls, meter):
    provider = provider_cls(api_key="k")
    with pytest.raises(ProviderBadRequest, match="missing audio"):
        await provider.transcribe(AudioClip(data=b""), "en")


async def test_openai_timeout_is_provider_unavailable(meter):
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("late", request=request)

    provider = OpenAITTSProvider(
        api_key="k",
        client=_client(timeout, OpenAITTSProvider.BASE_URL),
        retry=RetryPolicy(attempts=1),
    )
    with pytest.raises(ProviderUnavailable, match="transport"):
        await provider.synthesize("hello", "en")


async def test_malformed_structured_tool_output_is_bad_request(meter):
    _, handler = _capture(
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"id": "c1", "function": {"name": "save_answer", "arguments": "{"}}
                            ]
                        }
                    }
                ]
            },
        )
    )
    provider = SarvamLLMProvider(
        api_key="k",
        client=_client(handler, SarvamLLMProvider.BASE_URL),
        retry=RetryPolicy(attempts=1),
    )
    with pytest.raises(ProviderBadRequest, match="malformed tool"):
        await provider.complete(LLMRequest(prompt="x"))
