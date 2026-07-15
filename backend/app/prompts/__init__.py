"""Prompt library (doc 02 §2) and the shared V1/V2 intake tool contract (doc 02 §5).

`loader` reads versioned, vendor-neutral prompt text from `backend/prompts/`;
`tools` declares the four functions every voice tier drives the intake through.
Neither imports a vendor SDK — that is the point of both.
"""

from app.prompts.loader import Prompt, PromptError, all_prompts, load
from app.prompts.tools import (
    CHECK_RED_FLAGS,
    FINISH_AND_SUMMARIZE,
    GET_NEXT_NODE,
    INTAKE_TOOLS,
    SAVE_ANSWER,
    TOOL_CONTRACT_VERSION,
    ToolSpec,
    tool,
)

__all__ = [
    "CHECK_RED_FLAGS",
    "FINISH_AND_SUMMARIZE",
    "GET_NEXT_NODE",
    "INTAKE_TOOLS",
    "SAVE_ANSWER",
    "TOOL_CONTRACT_VERSION",
    "Prompt",
    "PromptError",
    "ToolSpec",
    "all_prompts",
    "load",
    "tool",
]
