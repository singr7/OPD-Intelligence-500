"""Shared fixtures for the voice-gw call tests (S14).

Mirrors `backend/tests/test_intake.py`: one small validated tree, and scripted fake
providers so a V1 (Gemini Live) and a V2 (STT→LLM→TTS) intake run deterministically.
The audio bytes the fake Exotel client sends are irrelevant to these fakes — they are
scripted — so a call test asserts on the *channel* behaviour (frames, latency,
barge-in, DTMF, partial save), not on transcription accuracy.
"""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.intake import InMemorySessionStore, IntakeEngine
from app.providers import FakeLLMProvider, FakeSTTProvider, FakeTTSProvider, ToolCall
from app.providers.llm import FakeLLMScript
from app.providers.realtime import FakeRealtimeProvider, FakeRealtimeScript
from app.trees import bank
from app.trees.schema import parse

from gw import call as call_mod
from gw.fake_exotel import FakeExotelClient, FakeTransport, speech

TREE_DATA = {
    "key": "test_intake",
    "version": 3,
    "department": "GENMED",
    "languages": ["en", "hi"],
    "title": {"en": "Test intake", "hi": "जाँच"},
    "root": "fever",
    "nodes": [
        {
            "id": "fever",
            "type": "single",
            "text": {"en": "Do you have fever?", "hi": "क्या आपको बुखार है?"},
            "options": [
                {"id": "yes", "text": {"en": "Yes", "hi": "हाँ"}},
                {"id": "no", "text": {"en": "No", "hi": "नहीं"}},
            ],
            "next": {"default": "pain"},
        },
        {
            "id": "pain",
            "type": "scale",
            "min": 0,
            "max": 10,
            "text": {"en": "Rate your pain from 0 to 10", "hi": "दर्द 0 से 10 में बताइए"},
            "next": {"default": "detail"},
        },
        {
            "id": "detail",
            "type": "free_voice",
            "text": {"en": "Anything else?", "hi": "और कुछ?"},
            "next": {"default": None},
        },
    ],
    "red_flags": [],
}

#: The three questions the scripted intake answers, in order.
EXPECTED_VALUES = {"fever": "yes", "pain": 8, "detail": "kuch nahi"}
N_QUESTIONS = 3


@pytest.fixture
def tree(monkeypatch):
    parsed = parse(TREE_DATA)
    # The engine reloads the tree from the bank by key (never a dict from Redis).
    monkeypatch.setattr(bank, "get", lambda key, root=None: parsed)
    return parsed


@pytest.fixture
def store():
    return InMemorySessionStore()


@pytest.fixture
def settings():
    return get_settings()


def summary_payload() -> str:
    return json.dumps(
        {
            "chief_concern": "Fever and pain",
            "hpi": ["Fever present", "Pain rated high"],
            "symptoms": [{"symptom": "pain", "duration": "", "severity": "8/10"}],
            "red_flags": [],
            "history_meds": [],
            "since_last_visit": [],
            "patient_words": {"quote": "bukhaar hai", "lang": "hi", "english": "fever"},
            "readback": "Aapne bataya bukhaar hai. Sahi hai?",
            "unclear": [],
        }
    )


def v2_dialogue_scripts() -> list[FakeLLMScript]:
    """One LLM turn per question: map the answer via save_answer, say the next."""
    return [
        FakeLLMScript(
            text="Dard kitna hai?",
            tool_calls=[
                ToolCall("save_answer", {"node_id": "fever", "value": "yes", "raw_text": "haan"})
            ],
        ),
        FakeLLMScript(
            text="Aur kuch?",
            tool_calls=[
                ToolCall("save_answer", {"node_id": "pain", "value": 8, "raw_text": "aath"})
            ],
        ),
        FakeLLMScript(
            text="Shukriya.",
            tool_calls=[
                ToolCall(
                    "save_answer",
                    {"node_id": "detail", "value": "kuch nahi", "raw_text": "kuch nahi"},
                )
            ],
        ),
    ]


def v1_live_script() -> list[FakeRealtimeScript]:
    return [
        FakeRealtimeScript(say="Namaste.", tool_calls=(ToolCall("get_next_node", {}),)),
        FakeRealtimeScript(
            say="Kitna dard?",
            tool_calls=(ToolCall("save_answer", {"node_id": "fever", "value": "yes"}),),
        ),
        FakeRealtimeScript(say="", tool_calls=(ToolCall("get_next_node", {}),)),
        FakeRealtimeScript(
            say="Aur kuch?",
            tool_calls=(ToolCall("save_answer", {"node_id": "pain", "value": 8}),),
        ),
        FakeRealtimeScript(say="", tool_calls=(ToolCall("get_next_node", {}),)),
        FakeRealtimeScript(
            say="Shukriya.",
            tool_calls=(ToolCall("save_answer", {"node_id": "detail", "value": "kuch nahi"}),),
        ),
        FakeRealtimeScript(say="", tool_calls=(ToolCall("get_next_node", {}),)),
        FakeRealtimeScript(
            say="Readback.",
            tool_calls=(ToolCall("finish_and_summarize", {"reason": "complete"}),),
        ),
    ]


def make_v2_engine(store) -> IntakeEngine:
    llm = FakeLLMProvider()
    llm.queue(*v2_dialogue_scripts(), FakeLLMScript(text=summary_payload()))
    return IntakeEngine(
        store,
        llm_providers=[llm],
        stt_providers=[FakeSTTProvider(script=["haan", "aath", "kuch nahi"])],
        tts_provider=FakeTTSProvider(),
    )


def make_v1_engine(store) -> IntakeEngine:
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text=summary_payload()))  # V1 still summarises via the LLM
    return IntakeEngine(
        store,
        realtime=FakeRealtimeProvider(script=v1_live_script()),
        llm_providers=[llm],
        stt_providers=[FakeSTTProvider(script=["haan", "aath", "kuch nahi"])],
        tts_provider=FakeTTSProvider(),
    )


async def drive_call(
    engine: IntakeEngine,
    *,
    tree,
    tier: str,
    settings,
    utterances=None,
    lang: str = "hi",
):
    """Run one fake Exotel call to completion and return (record, client.result).

    Sends `utterances` (default: one clear utterance per question) and hangs up.
    Uses the injected fake TTS for the consent line so no real vendor is touched.
    """
    transport = FakeTransport()
    client = FakeExotelClient(transport)
    store = call_mod.PhoneCallStore()

    async def run_driver():
        return await call_mod.handle_call(
            transport,
            engine=engine,
            sessionmaker=None,  # DB-less: assert on state + metering, not Postgres
            settings=settings,
            tts=FakeTTSProvider(),
            phonecall_store=store,
            tree=tree,
        )

    import asyncio

    driver = asyncio.create_task(run_driver())
    await client.start(tier=tier, lang=lang)
    await client.drain()  # consent line

    for clip in utterances if utterances is not None else [speech()] * N_QUESTIONS:
        await client.say(clip)

    await client.hangup()
    record = await driver
    return record, client.result
