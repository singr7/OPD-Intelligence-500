"""The intake engine (S5) — one intake, driven through all three tiers.

The load-bearing AC is doc 03 §1's: *the same `answers` JSONB from every tier,
and a mid-session downgrade that loses nothing*. So the spine of this file is a
single scripted intake replayed on V1, V2 and V3, plus a kill-mid-session test
that asserts the answers collected before the failure survive the drop to a
lower tier.

Everything runs against the provider fakes — no live vendor, per doc 07 §4.
The tree is a small synthetic one built through `schema.parse` (so it is as
validated as any real tree) and injected via the bank, keeping the assertions
about node ids and values legible.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio

from app.intake import InMemorySessionStore, IntakeEngine, PatientTurn, SessionState, SessionStatus
from app.intake.interpret import FakeInterpreter, LLMInterpreter
from app.intake.summary import IntakeSummary, LLMSummarizer, SummaryError, TemplateSummarizer
from app.models.enums import Channel, IntakeTier, Priority
from app.providers import AudioClip, FakeLLMProvider, FakeSTTProvider, FakeTTSProvider, ToolCall
from app.providers.costguard import InMemoryTierOverrideStore
from app.providers.llm import FakeLLMScript
from app.providers.realtime import FakeRealtimeProvider, FakeRealtimeScript
from app.providers.resilience import ProviderUnavailable
from app.trees import bank
from app.trees.schema import parse
from app.trees.walker import Walk

pytestmark = pytest.mark.asyncio

V1 = IntakeTier.CONVERSATIONAL
V2 = IntakeTier.RULE_BASED
V3 = IntakeTier.PRERECORDED


# -- a small, fully validated synthetic tree -----------------------------------

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
            "text": {"en": "Anything else you want to tell the doctor?", "hi": "और कुछ?"},
            "next": {"default": None},
        },
    ],
    "red_flags": [
        {
            "id": "severe_pain",
            "severity": "urgent",
            "when": {"node": "pain", "op": "gte", "value": 8},
            "label": {"en": "Severe pain", "hi": "तेज़ दर्द"},
            "instruction": {
                "en": "Please tell the staff at the desk right now.",
                "hi": "कृपया अभी डेस्क पर स्टाफ़ को बताइए।",
            },
        }
    ],
}


@pytest.fixture
def tree(monkeypatch):
    parsed = parse(TREE_DATA)
    # The engine reloads the tree from the bank by key (never a dict from Redis),
    # so inject ours there rather than passing it around.
    monkeypatch.setattr(bank, "get", lambda key, root=None: parsed)
    return parsed


@pytest.fixture
def store():
    return InMemorySessionStore()


def summary_payload(concern="Fever and pain", readback="Aapne bataya bukhaar hai. Sahi hai?"):
    """A valid doc 03 §4 summary, as the fake LLM would return it."""
    return json.dumps(
        {
            "chief_concern": concern,
            "hpi": ["Fever present", "Pain rated high"],
            "symptoms": [{"symptom": "pain", "duration": "", "severity": "8/10"}],
            "red_flags": ["ignored — the engine overwrites this from the rules"],
            "history_meds": [],
            "since_last_visit": [],
            "patient_words": {"quote": "bukhaar hai", "lang": "hi", "english": "I have fever"},
            "readback": readback,
            "unclear": [],
        }
    )


# The one scripted intake, as answers. Replayed on every tier.
CANONICAL_TURNS = [
    PatientTurn(audio=AudioClip(data=b"\x00" * 320), answer="yes", text="haan bukhaar hai"),
    PatientTurn(audio=AudioClip(data=b"\x00" * 320), answer=8, text="bahut dard, aath"),
    PatientTurn(audio=AudioClip(data=b"\x00" * 320), answer=None, text="kuch nahi"),
]

EXPECTED_VALUES = {"fever": "yes", "pain": 8, "detail": "kuch nahi"}


# -- session state -------------------------------------------------------------


async def test_session_state_roundtrips_through_json(tree, store):
    state = SessionState(
        session_id="s1",
        channel=Channel.PHONE,
        lang="hi",
        tree_key=tree.key,
        tree_version=tree.version,
        configured_tier=V1,
        active_tier=V2,
        intake_id=uuid.uuid4(),
        cost_inr=Decimal("1.2345"),
    )
    state.record_turn("patient", "haan", lang="hi")
    restored = SessionState.from_json(json.loads(json.dumps(state.to_json())))

    assert restored.session_id == "s1"
    assert restored.channel is Channel.PHONE
    assert restored.active_tier is V2
    assert restored.intake_id == state.intake_id
    assert restored.cost_inr == Decimal("1.2345")
    assert restored.transcript[0]["text"] == "haan"


async def test_in_memory_store_hands_back_copies(store, tree):
    state = SessionState(
        session_id="s1",
        channel=Channel.KIOSK,
        lang="hi",
        tree_key=tree.key,
        tree_version=tree.version,
        configured_tier=V3,
        active_tier=V3,
    )
    await store.save(state)
    fetched = await store.get("s1")
    fetched.status = SessionStatus.COMPLETE  # mutating the copy must not leak
    assert (await store.get("s1")).status is SessionStatus.ACTIVE


# -- the dispatcher (the four tools over a Walk) -------------------------------


async def test_dispatcher_walks_the_tree_and_finishes(tree, store):
    engine = IntakeEngine(store)
    state = await engine.start_session(
        tree=tree, channel=Channel.KIOSK, lang="hi", configured_tier=V3
    )
    disp = engine.dispatcher(state, tree)

    first = await disp.get_next_node()
    assert first["node"]["id"] == "fever"
    assert first["node"]["text"] == "क्या आपको बुखार है?"  # patient language
    assert {o["id"] for o in first["node"]["options"]} == {"yes", "no"}

    saved = await disp.save_answer("fever", "yes", raw_text="haan", lang="hi")
    assert saved["ok"] and not saved["complete"]

    assert (await disp.get_next_node())["node"]["id"] == "pain"
    await disp.save_answer("pain", 8, raw_text="aath")

    flags = await disp.check_red_flags()
    assert flags["any"] and flags["priority"] == "urgent"
    assert flags["red_flags"][0]["id"] == "severe_pain"

    await disp.save_answer("detail", "kuch nahi", raw_text="kuch nahi")
    assert (await disp.get_next_node())["complete"] is True

    done = await disp.finish_and_summarize("complete")
    assert done["complete"] and done["readback"]
    assert state.status is SessionStatus.COMPLETE
    assert state.answers.keys() == {"fever", "pain", "detail"}
    # finish returns rule-engine flags as {id, severity} dicts — the FinishOut
    # contract + the shape /answer and /confirm use — NOT the summary's human
    # strings (regression: a real LLM emits flag strings that a str list would
    # leak into the dict-typed route, 500ing /finish on the live box).
    assert done["red_flags"] == [{"id": "severe_pain", "severity": "urgent"}]


async def test_dispatcher_rejects_a_foreign_session_id(tree, store):
    engine = IntakeEngine(store)
    state = await engine.start_session(
        tree=tree, channel=Channel.KIOSK, lang="hi", configured_tier=V3
    )
    disp = engine.dispatcher(state, tree)
    from app.intake.dispatch import ToolError

    with pytest.raises(ToolError):
        await disp.dispatch("get_next_node", {"session_id": "someone-else"})


async def test_dispatcher_reports_a_bad_answer_without_crashing(tree, store):
    engine = IntakeEngine(store)
    state = await engine.start_session(
        tree=tree, channel=Channel.KIOSK, lang="hi", configured_tier=V3
    )
    disp = engine.dispatcher(state, tree)
    result = await disp.save_answer("fever", "maybe")  # not an option
    assert result["ok"] is False and "option" in result["error"]


# -- V3: deterministic, no model ----------------------------------------------


async def test_v3_full_intake(tree, store):
    engine = IntakeEngine(store, tts_provider=FakeTTSProvider())
    state = await engine.start_session(
        tree=tree, channel=Channel.KIOSK, lang="hi", configured_tier=V3
    )
    await engine.run(state, CANONICAL_TURNS)

    assert state.status is SessionStatus.COMPLETE
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
    # V3 summarises deterministically — no LLM needed, works offline.
    assert state.summary_md
    assert state.red_flags[0]["id"] == "severe_pain"
    assert "hi" in state.summary_lang_versions


async def test_v3_plays_prompt_audio_through_the_sink(tree, store):
    played: list[AudioClip] = []
    engine = IntakeEngine(store, tts_provider=FakeTTSProvider())
    state = await engine.start_session(
        tree=tree, channel=Channel.KIOSK, lang="hi", configured_tier=V3
    )

    async def sink(clip: AudioClip) -> None:
        played.append(clip)

    await engine.run(state, CANONICAL_TURNS, on_audio=sink)
    assert len(played) == 3  # one spoken question per node


# -- V2: STT -> dialogue LLM (tool contract) -> TTS ---------------------------


def v2_dialogue_scripts() -> list[FakeLLMScript]:
    """One LLM turn per question: map the answer via save_answer, say the next."""
    return [
        FakeLLMScript(
            text="How much pain?",
            tool_calls=(
                ToolCall("save_answer", {"node_id": "fever", "value": "yes", "raw_text": "haan"}),
            ),
        ),
        FakeLLMScript(
            text="Anything else?",
            tool_calls=(
                ToolCall("save_answer", {"node_id": "pain", "value": 8, "raw_text": "aath"}),
            ),
        ),
        FakeLLMScript(
            text="Thank you.",
            tool_calls=(
                ToolCall(
                    "save_answer",
                    {"node_id": "detail", "value": "kuch nahi", "raw_text": "kuch nahi"},
                ),
            ),
        ),
    ]


async def test_v2_full_intake(tree, store):
    llm = FakeLLMProvider()
    llm.queue(*v2_dialogue_scripts(), FakeLLMScript(text=summary_payload()))
    engine = IntakeEngine(
        store,
        llm_providers=[llm],
        stt_providers=[FakeSTTProvider(script=["haan", "aath", "kuch nahi"])],
        tts_provider=FakeTTSProvider(),
    )
    state = await engine.start_session(
        tree=tree, channel=Channel.PHONE, lang="hi", configured_tier=V2
    )
    await engine.run(state, CANONICAL_TURNS)

    assert state.status is SessionStatus.COMPLETE
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
    # The LLM summary was used (its readback), but the flag list comes from the rules.
    assert state.summary_lang_versions["hi"]["structured"]["red_flags"] == ["Severe pain"]
    roles = [t["role"] for t in state.transcript]
    assert "patient" in roles and "assistant" in roles


async def test_v2_safety_net_records_when_model_forgets_to_save(tree, store):
    # The model speaks but never calls save_answer on a free-text-ish turn; the
    # engine must still record the answer rather than loop forever.
    llm = FakeLLMProvider()
    llm.queue(
        FakeLLMScript(text="ok"),  # no tool call for the 'fever' single node
    )
    engine = IntakeEngine(
        store,
        llm_providers=[llm],
        stt_providers=[FakeSTTProvider(script=["yes"])],
        tts_provider=FakeTTSProvider(),
    )
    state = await engine.start_session(
        tree=tree, channel=Channel.PHONE, lang="en", configured_tier=V2
    )
    disp = engine.dispatcher(state, tree)
    # Drive one turn directly to observe the fallback save on the 'fever' node.
    await engine._turn_v2(disp, state, tree, PatientTurn(text="yes"), None)
    assert state.answers["fever"]["value"] == "yes"


# -- V1: Gemini Live bridge ----------------------------------------------------


def v1_live_script() -> list[FakeRealtimeScript]:
    """One tool call per scripted turn — each `send_tool_result` releases the next
    (that is how `FakeRealtimeSession` advances)."""
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


async def test_v1_full_intake_bridges_tools_and_streams_audio(tree, store):
    played: list[AudioClip] = []
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text=summary_payload()))  # V1 still summarises via the LLM
    engine = IntakeEngine(
        store,
        realtime=FakeRealtimeProvider(script=v1_live_script()),
        llm_providers=[llm],
        tts_provider=FakeTTSProvider(),
    )
    state = await engine.start_session(
        tree=tree, channel=Channel.PHONE, lang="hi", configured_tier=V1
    )

    async def sink(clip: AudioClip) -> None:
        played.append(clip)

    await engine.run(state, CANONICAL_TURNS, on_audio=sink)

    assert state.status is SessionStatus.COMPLETE
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
    assert played, "assistant audio should have streamed through the passthrough sink"


# -- downgrade: the whole point -----------------------------------------------


async def test_v1_connect_failure_downgrades_to_v2_without_data_loss(tree, store):
    realtime = FakeRealtimeProvider(script=v1_live_script())
    realtime.fail_with = ProviderUnavailable("gemini live is down")
    llm = FakeLLMProvider()
    llm.queue(*v2_dialogue_scripts(), FakeLLMScript(text=summary_payload()))
    engine = IntakeEngine(
        store,
        realtime=realtime,
        llm_providers=[llm],
        stt_providers=[FakeSTTProvider(script=["haan", "aath", "kuch nahi"])],
        tts_provider=FakeTTSProvider(),
    )
    state = await engine.start_session(
        tree=tree, channel=Channel.PHONE, lang="hi", configured_tier=V1
    )
    await engine.run(state, CANONICAL_TURNS)

    assert state.active_tier is V2  # dropped one rung
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
    assert state.status is SessionStatus.COMPLETE


async def test_v2_provider_kill_midsession_downgrades_to_v3_preserving_answers(tree, store):
    # Answer the first question on V2, then the LLM dies; the remaining questions
    # complete on V3 and the first answer is still there.
    llm = FakeLLMProvider()
    llm.queue(v2_dialogue_scripts()[0])  # only enough to answer 'fever'
    engine = IntakeEngine(
        store,
        llm_providers=[llm],
        stt_providers=[FakeSTTProvider(script=["haan"])],
        tts_provider=FakeTTSProvider(),
    )
    state = await engine.start_session(
        tree=tree, channel=Channel.PHONE, lang="hi", configured_tier=V2
    )

    # First turn on V2 records 'fever'.
    disp = engine.dispatcher(state, tree)
    await engine._turn_v2(disp, state, tree, CANONICAL_TURNS[0], None)
    assert state.answers["fever"]["value"] == "yes"

    # Now the LLM is dead; running the rest must downgrade and still finish.
    llm.fail_with = ProviderUnavailable("flash and openai both down")
    await engine.run(state, CANONICAL_TURNS[1:])

    assert state.active_tier is V3  # V2 -> V3
    assert {k: v["value"] for k, v in state.answers.items()} == EXPECTED_VALUES
    assert state.status is SessionStatus.COMPLETE


async def test_cost_guard_starts_a_session_on_the_forced_tier(tree, store):
    from app.providers.costguard import CostGuard

    override = InMemoryTierOverrideStore()
    await override.set(Channel.PHONE, V3, ttl_seconds=900)
    guard = CostGuard(
        session_factory=None,  # effective_tier only reads the override store
        store=override,
        budgets={},
    )
    engine = IntakeEngine(store, guard=guard, tts_provider=FakeTTSProvider())
    state = await engine.start_session(
        tree=tree, channel=Channel.PHONE, lang="hi", configured_tier=V1
    )
    assert state.active_tier is V3  # cost guard forced it down before the first turn

    await engine.run(state, CANONICAL_TURNS)
    assert state.status is SessionStatus.COMPLETE


# -- the identical-answers AC (doc 03 §1) -------------------------------------


async def test_answers_shape_identical_across_tiers(tree, store):
    # V3
    e3 = IntakeEngine(InMemorySessionStore(), tts_provider=FakeTTSProvider())
    s3 = await e3.start_session(tree=tree, channel=Channel.KIOSK, lang="hi", configured_tier=V3)
    await e3.run(s3, CANONICAL_TURNS)

    # V2
    llm = FakeLLMProvider()
    llm.queue(*v2_dialogue_scripts(), FakeLLMScript(text=summary_payload()))
    e2 = IntakeEngine(
        InMemorySessionStore(),
        llm_providers=[llm],
        stt_providers=[FakeSTTProvider(script=["haan", "aath", "kuch nahi"])],
        tts_provider=FakeTTSProvider(),
    )
    s2 = await e2.start_session(tree=tree, channel=Channel.PHONE, lang="hi", configured_tier=V2)
    await e2.run(s2, CANONICAL_TURNS)

    values3 = {k: v["value"] for k, v in s3.answers.items()}
    values2 = {k: v["value"] for k, v in s2.answers.items()}
    assert values3 == values2 == EXPECTED_VALUES


# -- summarizer ---------------------------------------------------------------


async def test_llm_summarizer_validates_the_contract(tree, store):
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text=summary_payload(concern="Fever 3 days")))
    summarizer = LLMSummarizer([llm])
    state = SessionState(
        session_id="s",
        channel=Channel.KIOSK,
        lang="hi",
        tree_key=tree.key,
        tree_version=tree.version,
        configured_tier=V2,
        active_tier=V2,
    )
    walk = Walk(tree)
    walk.save("fever", "yes")
    walk.save("pain", 8)
    summary = await summarizer.summarize(state, tree, walk)
    assert summary.chief_concern == "Fever 3 days"
    # Rules, not the model, decide the flags.
    assert summary.red_flags == ("Severe pain",)


async def test_llm_summary_rejects_malformed_output(tree):
    with pytest.raises(SummaryError):
        IntakeSummary.parse({"hpi": ["no chief concern here"]})


async def test_template_summarizer_needs_no_vendor(tree):
    state = SessionState(
        session_id="s",
        channel=Channel.KIOSK,
        lang="hi",
        tree_key=tree.key,
        tree_version=tree.version,
        configured_tier=V3,
        active_tier=V3,
        chief_complaint="bukhaar aur dard",
    )
    walk = Walk(tree)
    walk.save("fever", "yes")
    walk.save("pain", 9)
    summary = await TemplateSummarizer().summarize(state, tree, walk)
    assert summary.chief_concern == "bukhaar aur dard"
    assert summary.red_flags == ("Severe pain",)
    # The read-back speaks the oncologist-authored instruction verbatim.
    assert "स्टाफ़" in summary.readback


async def test_summary_priority_reflects_red_flags(tree):
    walk = Walk(tree)
    walk.save("fever", "no")
    walk.save("pain", 3)
    assert walk.priority() is Priority.ROUTINE
    walk.save("pain", 9)
    assert walk.priority() is Priority.URGENT


# -- adaptive answer interpreter (S-ADAPT.1, doc 11) --------------------------


async def test_fake_interpreter_maps_single_and_scale(tree):
    """The deterministic interpreter maps voice → a value the node allows (AC §2)."""
    interp = FakeInterpreter()
    fever = tree.node("fever")
    pain = tree.node("pain")

    yes = await interp.interpret(fever, "haan Yes ji", "en")
    assert yes.value == "yes" and yes.has_value

    eight = await interp.interpret(pain, "मुझे लगता है 8 hoga", "hi")
    assert eight.value == 8


async def test_fake_interpreter_clarifies_when_vague(tree):
    """A vague utterance earns a clarify, never a guessed option (doc 11 §5)."""
    interp = FakeInterpreter()
    out = await interp.interpret(tree.node("fever"), "pata nahi", "hi")
    assert out.value is None
    assert out.clarify  # a follow-up, not a value


async def test_fake_interpreter_never_invents_an_option(tree):
    """Whatever the fake returns as a value passes the node validator (doc 11 §5)."""
    from app.trees.walker import validate_answer

    interp = FakeInterpreter()
    for utterance in ["Yes", "No", "nonsense words", "हाँ", "42"]:
        out = await interp.interpret(tree.node("fever"), utterance, "en")
        if out.has_value:
            # Must round-trip through the real validator — the whole safety story.
            validate_answer(tree.node("fever"), out.value)


async def test_llm_interpreter_returns_value(tree):
    """LLMInterpreter on the fake LLM: a `{"value": ...}` reply → that value."""
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text=json.dumps({"value": "yes", "confidence": 0.9})))
    interp = LLMInterpreter([llm])
    out = await interp.interpret(tree.node("fever"), "haan bukhaar hai", "hi")
    assert out.value == "yes"
    assert out.confidence == pytest.approx(0.9)
    # The node's own option ids are handed to the model (never-invent constraint).
    assert 'id "yes"' in llm.last.prompt


async def test_llm_interpreter_returns_clarify(tree):
    """A `{"clarify": ...}` reply → a clarify, no value."""
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text=json.dumps({"clarify": "बुखार है या नहीं?"})))
    interp = LLMInterpreter([llm])
    out = await interp.interpret(tree.node("fever"), "hmm", "hi")
    assert out.value is None
    assert out.clarify == "बुखार है या नहीं?"


async def test_llm_interpreter_degrades_on_garbage(tree):
    """Unparseable JSON degrades to a clarify — never crashes an intake (doc 11 §5)."""
    llm = FakeLLMProvider()
    llm.queue(FakeLLMScript(text="not json at all"))
    interp = LLMInterpreter([llm])
    out = await interp.interpret(tree.node("fever"), "haan", "hi")
    assert out.value is None  # falls back; the kiosk shows taps


# -- cost attribution (DB-backed) ---------------------------------------------


@pytest_asyncio.fixture
async def clinic(session):
    from tests.factories import build_clinic, make_intake, make_visit

    parts = await build_clinic(session)
    visit = make_visit(parts["patient"], parts["department"], channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    intake = make_intake(visit, tier=V3)
    session.add(intake)
    await session.flush()
    return {"visit": visit, "intake": intake, **parts}


async def test_completed_intake_carries_an_accurate_cost(
    tree, store, session, meter, seeded_prices, clinic
):
    engine = IntakeEngine(store, tts_provider=FakeTTSProvider())
    state = await engine.start_session(
        tree=tree,
        channel=Channel.KIOSK,
        lang="hi",
        configured_tier=V3,
        intake_id=clinic["intake"].id,
        visit_id=clinic["visit"].id,
        chief_complaint="bukhaar",
    )
    await engine.run(state, CANONICAL_TURNS)

    # Drain the buffered usage into the test transaction, then finalise.
    await meter.flush()
    total = await engine.finalize_cost(state, session)

    from sqlalchemy import func, select

    from app.models.metering import UsageEvent

    summed = await session.scalar(
        select(func.coalesce(func.sum(UsageEvent.computed_cost_inr), 0)).where(
            UsageEvent.intake_id == clinic["intake"].id
        )
    )
    assert total == Decimal(summed)
    assert total > 0  # the V3 TTS prompts were metered and priced

    # The Intake row now carries the finalised intake.
    await session.refresh(clinic["intake"])
    assert clinic["intake"].cost_inr == total
    assert clinic["intake"].answers.keys() == {"fever", "pain", "detail"}
    assert clinic["intake"].summary_md
    assert clinic["intake"].tier is V3
    assert clinic["intake"].completed_at is not None


async def test_usage_events_are_tagged_with_intake_and_tier(
    tree, store, session, meter, seeded_prices, clinic
):
    engine = IntakeEngine(store, tts_provider=FakeTTSProvider())
    state = await engine.start_session(
        tree=tree,
        channel=Channel.KIOSK,
        lang="hi",
        configured_tier=V3,
        intake_id=clinic["intake"].id,
    )
    await engine.run(state, CANONICAL_TURNS)
    await meter.flush()

    from sqlalchemy import select

    from app.models.metering import UsageEvent

    rows = (
        await session.scalars(select(UsageEvent).where(UsageEvent.intake_id == clinic["intake"].id))
    ).all()
    assert rows, "the intake produced metered usage"
    assert all(row.tier is V3 for row in rows)
    assert all(row.channel is Channel.KIOSK for row in rows)
