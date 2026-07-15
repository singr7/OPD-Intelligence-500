"""The shared intake tool contract (doc 02 §5).

> "The **same function-call tool contract** across V1/V2 is what makes tier
> switching mid-session lossless." — doc 02 §5

This module is that contract, and it is the load-bearing piece of the tier
ladder. V1 (Gemini Live) and V2 (STT→Flash/gpt-4o-mini→TTS) are different
vendors, different protocols and different latency budgets, but they drive the
intake through *these four functions* and nothing else. So when a provider dies
or the cost-guard trips mid-sentence, the session moves down a tier and the tool
calls keep meaning the same thing — no answer is re-asked, none is lost.

The other half of why this exists: **the model never free-styles clinically**
(doc 02 §5). It cannot invent a question, decide a red flag, or write a summary
on its own. It can only ask what `get_next_node` hands it, record what the
patient said via `save_answer`, and hand off. The clinical logic lives in the
tree (S4) and the deterministic red-flag rules — data an oncologist can review
and sign off (S21), not weights.

Vendor-neutral by construction: JSON Schema in, adapters in the provider impls
out (`app.providers.llm._to_gemini_tools` / `_to_openai_tools`). Nothing in this
file imports a vendor.

**Versioned.** `TOOL_CONTRACT_VERSION` goes into prompts and, from S5, into
session state. A session that started on one version keeps it; changing a tool's
shape means a new version, not an edit — a half-finished intake resuming against
a redefined `save_answer` is a silent data-corruption bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TOOL_CONTRACT_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One callable tool, described in vendor-neutral JSON Schema.

    `description` is prompt text — the model reads it to decide when to call.
    Keep it behavioural ("call this after every patient answer"), not decorative.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def required(self) -> list[str]:
        return list(self.parameters.get("required", []))


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        # Vendors differ on whether they honour this; it is here so our own
        # validation (S5) can reject a hallucinated extra argument uniformly.
        "additionalProperties": False,
    }


GET_NEXT_NODE = ToolSpec(
    name="get_next_node",
    description=(
        "Get the next question to ask. Call this at the start of the intake and "
        "after every save_answer. Ask the patient exactly the question text this "
        "returns, in the patient's language. Never invent a question, never "
        "reorder, never skip ahead — the tree decides what comes next."
    ),
    parameters=_object(
        {
            "session_id": {
                "type": "string",
                "description": "The intake session id given to you at session start.",
            }
        },
        ["session_id"],
    ),
)

SAVE_ANSWER = ToolSpec(
    name="save_answer",
    description=(
        "Record the patient's answer to the current question. Call this once per "
        "answer, immediately, before asking anything else. Put the patient's own "
        "words in raw_text even when you mapped them to an option — the doctor "
        "reads those words later."
    ),
    parameters=_object(
        {
            "session_id": {"type": "string"},
            "node_id": {
                "type": "string",
                "description": "The id of the node returned by get_next_node.",
            },
            "value": {
                "description": (
                    "The answer mapped onto the node's option ids / scale value / "
                    "boolean, per the node's answer_type."
                ),
                "type": ["string", "number", "boolean", "array", "null"],
            },
            "raw_text": {
                "type": "string",
                "description": "What the patient actually said, verbatim, in their language.",
            },
            "lang": {
                "type": "string",
                "description": "BCP-47-ish language of raw_text: en, hi, mr, te.",
            },
        },
        ["session_id", "node_id", "value"],
    ),
)

CHECK_RED_FLAGS = ToolSpec(
    name="check_red_flags",
    description=(
        "Check the answers so far against the clinical red-flag rules. Call this "
        "whenever the patient describes something that alarms you, and always "
        "before finish_and_summarize. If it returns an urgent flag, follow the "
        "instruction it returns verbatim and do not reassure the patient with "
        "your own clinical opinion."
    ),
    parameters=_object({"session_id": {"type": "string"}}, ["session_id"]),
)

FINISH_AND_SUMMARIZE = ToolSpec(
    name="finish_and_summarize",
    description=(
        "End the intake and produce the summary for the doctor. Call this only "
        "when get_next_node reports the tree is complete, or when the patient "
        "asks to stop. Returns a read-back script to speak to the patient for "
        "confirmation."
    ),
    parameters=_object(
        {
            "session_id": {"type": "string"},
            "reason": {
                "type": "string",
                "enum": ["complete", "patient_ended", "handoff"],
                "description": "Why the intake ended. 'handoff' = escalated to a human.",
            },
        },
        ["session_id", "reason"],
    ),
)

#: The contract, in the order a session uses them. V1 and V2 both get exactly this.
INTAKE_TOOLS: tuple[ToolSpec, ...] = (
    GET_NEXT_NODE,
    SAVE_ANSWER,
    CHECK_RED_FLAGS,
    FINISH_AND_SUMMARIZE,
)

INTAKE_TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in INTAKE_TOOLS}


def tool(name: str) -> ToolSpec:
    """Look up a tool by name, loudly.

    A KeyError here means a model called something we never declared, or a tier
    was wired with a stale contract — both worth a crash in S5's dispatcher
    rather than a shrug.
    """
    try:
        return INTAKE_TOOLS_BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"unknown tool {name!r}; contract v{TOOL_CONTRACT_VERSION} declares "
            f"{sorted(INTAKE_TOOLS_BY_NAME)}"
        ) from None
