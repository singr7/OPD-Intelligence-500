"""Intake Engine (doc 02 §5, doc 03 §1) — the shared engine behind every channel.

One `IntakeEngine` drives an intake across the three-tier ladder (V1 Gemini Live →
V2 STT→LLM→TTS → V3 deterministic), all calling the same four-tool contract over
the same `Walk`, with session state in Redis and answers — never a cursor — as the
only stored position. Channels (kiosk S6, telephony S14, WhatsApp S12) are thin
adapters over this.

    engine = IntakeEngine(build_session_store(settings))
    state = await engine.start_session(tree=tree, channel=Channel.KIOSK, lang="hi")
    await engine.run(state, patient_turns, on_audio=play)
    await engine.finalize_cost(state, db_session)

Layout:
  state       SessionState + the Redis/in-memory session store
  dispatch    ToolDispatcher — the four tools, run over one Walk
  summary     IntakeSummary (doc 03 §4) + LLM and deterministic summarizers
  voicepack   V3 pre-recorded audio resolution (TTS fallback)
  engine      IntakeEngine — start, run, downgrade, finalize
"""

from app.intake.dispatch import ToolDispatcher, ToolError
from app.intake.engine import IntakeEngine, PatientTurn
from app.intake.state import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionState,
    SessionStatus,
    SessionStore,
    build_session_store,
)
from app.intake.summary import (
    IntakeSummary,
    LLMSummarizer,
    Summarizer,
    SummaryError,
    TemplateSummarizer,
)
from app.intake.voicepack import EMPTY_PACK, VoicePack

__all__ = [
    "EMPTY_PACK",
    "IntakeEngine",
    "IntakeSummary",
    "InMemorySessionStore",
    "LLMSummarizer",
    "PatientTurn",
    "RedisSessionStore",
    "SessionState",
    "SessionStatus",
    "SessionStore",
    "Summarizer",
    "SummaryError",
    "TemplateSummarizer",
    "ToolDispatcher",
    "ToolError",
    "VoicePack",
    "build_session_store",
]
