"""V-OSS — the local open-source voice tier (doc 08).

Two properties this session owes, tested the way S3 tests every provider:

- **Config-only switching.** V-OSS is just another set of provider adapters:
  `LLM_PROVIDER=local_vllm`, `STT_PROVIDER=local_whisper`,
  `TTS_PROVIDER=local_tts|voicebox` select them, and nothing else changes
  (`test_..._is_config_only`). The realtime GPU pipeline honestly refuses to
  build until S-OSS.2 (`test_local_pipecat_realtime_refuses_until_gpu`).
- **A free provider still meters.** doc 02 §9 is not waived for local models:
  every adapter emits a priced `usage_event` with `provider=local-*`, so the S18
  dashboard shows a true V-OSS cost-per-intake instead of a flat zero
  (`test_every_local_provider_is_metered_and_priced`).

The HTTP adapters run through `httpx.MockTransport` — real request-building and
response-parsing against the OpenAI-compatible / local server shapes, mocked
wire (same discipline as `test_providers_vendors.py`). The GPU is never touched.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.models.enums import Channel
from app.models.metering import UsageEvent
from app.prompts.tools import INTAKE_TOOLS
from app.providers.audio import AudioClip
from app.providers.llm import LLMRequest
from app.providers.local_oss import (
    AdmissionController,
    AdmissionFull,
    LocalLLMProvider,
    LocalSTTProvider,
    LocalTTSProvider,
    VoiceboxTTSProvider,
)
from app.providers.registry import (
    UnknownProvider,
    get_llm_provider,
    get_realtime_provider,
    get_stt_provider,
    get_tts_provider,
    reset_providers,
)
from app.tiers import (
    OSS_PROFILE,
    TierConfig,
    TierConfigError,
    load_tier_config,
    parse_tier_config,
)

pytestmark = pytest.mark.usefixtures("providers", "seeded_prices")


def _client(handler, *, base_url: str = "http://gpu.local", **kwargs) -> httpx.AsyncClient:
    # A base_url so the adapters' relative paths ("/chat/completions", "/tts", …)
    # resolve; the MockTransport ignores the host and just runs the handler.
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url, **kwargs)


def _captures(response: httpx.Response):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return seen, handler


def _wav_b64() -> str:
    return base64.b64encode(b"\x00\x00" * 8000).decode()


def _settings(**overrides) -> Settings:
    base = dict(env="test", jwt_secret="x" * 40)
    return Settings(**(base | overrides))


# -- LocalLLMProvider (vLLM, OpenAI-compatible) --------------------------------


async def test_local_vllm_speaks_openai_wire_without_a_key(meter):
    """vLLM is OpenAI-compatible, so the adapter reuses the whole OpenAI wire —
    and drops the Authorization header, because a local box needs no key."""
    seen, handler = _captures(
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Namaste"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )
    )
    llm = LocalLLMProvider(base_url="http://10.8.0.2:8000/v1", client=_client(handler))

    result = await llm.complete(LLMRequest(prompt="hello", tools=INTAKE_TOOLS))

    assert result.text == "Namaste"
    assert result.model == "qwen3-8b-awq"
    assert result.tokens_in == 100 and result.tokens_out == 20
    # No key configured -> no auth header (a gateway-fronted deploy would add one).
    assert "authorization" not in {k.lower() for k in seen[0].headers}
    # It really hit the chat-completions path, and passed our tool contract through.
    assert seen[0].url.path.endswith("/chat/completions")


async def test_local_vllm_sends_a_bearer_only_when_a_key_is_set():
    seen, handler = _captures(
        httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})
    )
    llm = LocalLLMProvider(
        base_url="http://gw/v1", api_key="gateway-token", client=_client(handler)
    )
    await llm.complete(LLMRequest(prompt="hi"))
    assert seen[0].headers["authorization"] == "Bearer gateway-token"


async def test_local_vllm_parses_function_calls_into_the_tool_contract(meter):
    """The dialogue model drives the intake through the same four tools as Gemini
    Live — the parsing must yield our `ToolCall`, not a vendor shape."""
    seen, handler = _captures(
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "save_answer",
                                        "arguments": '{"node_id": "pain", "value": 7}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10},
            },
        )
    )
    llm = LocalLLMProvider(base_url="http://x/v1", client=_client(handler))
    result = await llm.complete(LLMRequest(prompt="my pain is 7", tools=INTAKE_TOOLS))
    assert result.tool_calls[0].name == "save_answer"
    assert result.tool_calls[0].arguments == {"node_id": "pain", "value": 7}


# -- LocalSTTProvider (Whisper) ------------------------------------------------


async def test_local_whisper_transcribes_and_never_fakes_confidence(meter):
    seen, handler = _captures(httpx.Response(200, json={"text": "sar dard hai", "language": "hi"}))
    stt = LocalSTTProvider(base_url="http://10.8.0.2:8010", client=_client(handler))

    transcript = await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")

    assert transcript.text == "sar dard hai"
    assert transcript.provider == "local-whisper"
    # Whisper reports no calibrated confidence — honestly None, never 1.0.
    assert transcript.confidence is None
    assert seen[0].url.path.endswith("/v1/audio/transcriptions")


async def test_local_whisper_meters_audio_even_on_rejection(meter, session):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad audio")

    stt = LocalSTTProvider(base_url="http://x", client=_client(handler))
    with pytest.raises(Exception):
        await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
    await meter.flush()

    # A rejected upload still spent GPU seconds; the row exists and is priced.
    rows = [e for e in await _events(session) if e.provider == "local-whisper"]
    assert rows and rows[0].computed_cost_inr > 0


# -- LocalTTSProvider + VoiceboxTTSProvider ------------------------------------


async def test_local_tts_synthesizes_the_dhara_voice(meter):
    seen, handler = _captures(httpx.Response(200, json={"audio": _wav_b64()}))
    tts = LocalTTSProvider(base_url="http://10.8.0.2:8020", client=_client(handler))

    speech = await tts.synthesize("namaste", "hi")

    assert speech.provider == "local-tts"
    assert speech.voice == "dhara_hi_v1"  # the cloned identity (doc 08 §1)
    assert speech.audio.data  # decoded from base64
    assert seen[0].url.path.endswith("/tts")


async def test_voicebox_batch_synthesizes_for_pack_generation(meter):
    seen, handler = _captures(httpx.Response(200, json={"audio": _wav_b64()}))
    tts = VoiceboxTTSProvider(base_url="http://voicebox:7000", client=_client(handler))

    speech = await tts.synthesize("aapka token number hai", "hi")

    assert speech.provider == "voicebox"
    assert seen[0].url.path.endswith("/api/tts")


# -- the metering AC: every local provider is priced ---------------------------


async def _events(session) -> list[UsageEvent]:
    return list((await session.execute(select(UsageEvent))).scalars())


async def test_every_local_provider_is_metered_and_priced(session, meter):
    """doc 02 §9 for free providers: a local model is amortized GPU time, not
    zero. Every adapter must emit a priced row so the dashboard can compare
    V-OSS against V1/V2 (doc 08 §3)."""
    json_audio = {"audio": _wav_b64()}
    llm = LocalLLMProvider(
        base_url="http://x/v1",
        client=_client(
            lambda r: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                },
            )
        ),
    )
    stt = LocalSTTProvider(
        base_url="http://x", client=_client(lambda r: httpx.Response(200, json={"text": "haan"}))
    )
    local_tts = LocalTTSProvider(
        base_url="http://x", client=_client(lambda r: httpx.Response(200, json=json_audio))
    )
    voicebox = VoiceboxTTSProvider(
        base_url="http://x", client=_client(lambda r: httpx.Response(200, json=json_audio))
    )

    # A realistic-length prompt: the amortized local char rate is deliberately
    # tiny, so a whole utterance's worth of characters (not a 7-char "namaste")
    # is what registers above the paisa-and-then-some rounding — which is exactly
    # the per-intake granularity the dashboard sums at.
    prompt = "Namaste, aapka token number das hai. Kripya apni baari ka intezaar karein."
    await llm.complete(LLMRequest(prompt="hi"))
    await stt.transcribe(AudioClip(data=b"\x00\x00" * 8000), "hi")
    await local_tts.synthesize(prompt, "hi")
    await voicebox.synthesize(prompt, "hi")
    await meter.flush()

    priced = {e.provider: e.computed_cost_inr for e in await _events(session)}
    for provider in ("local-vllm", "local-whisper", "local-tts", "voicebox"):
        assert provider in priced, f"{provider} emitted no usage_event"
        assert priced[provider] > 0, f"{provider} priced at zero — missing price_book row?"


# -- config-only switching (the S3 promise, extended to V-OSS) -----------------


def test_swapping_to_local_providers_is_config_only():
    """A hospital chooses V-OSS with three env vars and nothing else. Same call
    sites, same interfaces, local impls."""
    reset_providers()
    settings = _settings(
        llm_provider="local_vllm",
        local_vllm_base_url="http://10.8.0.2:8000/v1",
        stt_provider="local_whisper",
        local_stt_url="http://10.8.0.2:8010",
        tts_provider="local_tts",
        local_tts_url="http://10.8.0.2:8020",
    )
    assert isinstance(get_llm_provider(settings), LocalLLMProvider)
    assert isinstance(get_stt_provider(settings), LocalSTTProvider)
    assert isinstance(get_tts_provider(settings), LocalTTSProvider)


def test_tts_can_be_voicebox():
    reset_providers()
    settings = _settings(tts_provider="voicebox", voicebox_url="http://voicebox:7000")
    assert isinstance(get_tts_provider(settings), VoiceboxTTSProvider)


def test_local_providers_report_configured_from_their_url():
    reset_providers()
    configured = get_llm_provider(
        _settings(llm_provider="local_vllm", local_vllm_base_url="http://x/v1")
    )
    assert configured.health.configured is True
    reset_providers()
    # No URL => not deployed => reports unconfigured, exactly like a keyless vendor.
    unconfigured = get_llm_provider(_settings(llm_provider="local_vllm"))
    assert unconfigured.health.configured is False


def test_local_pipecat_realtime_refuses_until_gpu():
    """The realtime GPU pipeline is the S-OSS.2 half — naming it must refuse, the
    same honesty gemini-live keeps, not silently fall to the fake."""
    reset_providers()
    with pytest.raises(UnknownProvider, match="S-OSS.2"):
        get_realtime_provider(_settings(realtime_provider="local-pipecat"))


# -- AdmissionController (doc 08 §3) -------------------------------------------


async def test_admission_admits_up_to_the_cap_then_routes_to_fallback():
    admission = AdmissionController({OSS_PROFILE: 2})
    async with admission.slot(OSS_PROFILE) as a, admission.slot(OSS_PROFILE) as b:
        assert a is True and b is True
        assert admission.active(OSS_PROFILE) == 2
        # Session #3 is not queued on the GPU — it is refused so the caller can
        # route to the next ladder tier (doc 08 §3).
        async with admission.slot(OSS_PROFILE) as c:
            assert c is False
        assert admission.active(OSS_PROFILE) == 2  # the refusal held no seat


async def test_admission_releases_the_seat_even_on_crash():
    """Per-session isolation (doc 08 §3): a crashed call must free its seat, or
    the count pins at the cap and every later caller is pushed to fallback."""
    admission = AdmissionController({OSS_PROFILE: 1})
    with pytest.raises(RuntimeError):
        async with admission.slot(OSS_PROFILE) as admitted:
            assert admitted is True
            raise RuntimeError("call crashed mid-turn")
    assert admission.active(OSS_PROFILE) == 0
    # The freed seat is immediately reusable.
    async with admission.slot(OSS_PROFILE) as again:
        assert again is True


async def test_admission_uncapped_profile_always_admits():
    """A missing/zero cap means uncapped, never 'always full' — an unconfigured
    limit must not silently push every call to fallback."""
    admission = AdmissionController({OSS_PROFILE: 0})
    assert admission.limit(OSS_PROFILE) is None
    async with admission.slot(OSS_PROFILE) as a, admission.slot(OSS_PROFILE) as b:
        assert a is True and b is True


async def test_admission_reserve_raises_when_full():
    admission = AdmissionController({OSS_PROFILE: 1})
    await admission.reserve(OSS_PROFILE)
    with pytest.raises(AdmissionFull):
        await admission.reserve(OSS_PROFILE)


# -- TierConfig (config/tiers.yaml) -------------------------------------------


def test_real_tiers_yaml_loads_and_ladders_are_ordered():
    config = load_tier_config()
    # doc 08 §5 example: phone prefers local, then cloud, then zero-AI.
    assert config.ladder_for(Channel.PHONE) == ("v_oss", "v2", "v3")
    assert config.ladder_for(Channel.KIOSK) == ("v_oss", "v3")
    assert config.max_oss_sessions == 12


def test_tier_config_builds_a_capped_admission_controller():
    config = load_tier_config()
    admission = config.admission_controller()
    assert admission.limit(OSS_PROFILE) == 12


def test_unknown_channel_in_ladder_config_is_a_boot_error():
    with pytest.raises(TierConfigError, match="unknown channel"):
        parse_tier_config({"channels": {"telegram": {"ladder": ["v2"]}}})


def test_unknown_tier_label_in_ladder_is_a_boot_error():
    with pytest.raises(TierConfigError, match="unknown tier"):
        parse_tier_config({"channels": {"kiosk": {"ladder": ["v_oss", "v9"]}}})


def test_empty_ladder_is_a_boot_error():
    with pytest.raises(TierConfigError, match="ladder"):
        parse_tier_config({"channels": {"kiosk": {"ladder": []}}})


def test_negative_admission_cap_is_a_boot_error():
    with pytest.raises(TierConfigError, match="max_oss_sessions"):
        parse_tier_config({"admission": {"max_oss_sessions": -1}})


def test_channel_without_a_ladder_falls_back_to_cloud_then_zero_ai():
    """A new channel that forgot a ladder keeps working on the safe cloud path
    rather than failing to start."""
    config = TierConfig(ladders={}, max_oss_sessions=0)
    assert config.ladder_for(Channel.APP) == ("v2", "v3")
