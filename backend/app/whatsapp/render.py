"""Render one intake Node as a WhatsApp message (S12, doc 03 §1d).

The kiosk draws a node as tap cards; WhatsApp has two native interactive shapes,
and which one fits is decided by Meta's limits, not ours:

- **reply buttons** — up to 3, so a node with 1–3 options becomes buttons.
- **a list** — up to 10 rows, for a node with 4–5 options (the tree validator caps
  options at 5, doc 03 §1a, so a list is never overfull here).

A number or free-text node has no options, so it becomes a plain prompt and the
patient replies by typing (or, for the chief complaint, a voice note). The option
id is carried verbatim as the button/row id, because Meta echoes that id back in
the webhook — that is how a tap becomes an answer without trusting a title the
patient's client may have localised or truncated.

Titles are hard-truncated to Meta's caps (20 for a button, 24 for a list row):
the full question is always in the body text, and a clipped button label beats a
silent Meta rejection of the whole message.
"""

from __future__ import annotations

from typing import Any

from app.models.enums import Lang
from app.providers.messaging import Button, ListRow, OutboundMessage

#: Meta's hard caps on interactive labels (truncation is silent otherwise).
_BUTTON_TITLE_MAX = 20
_LIST_ROW_TITLE_MAX = 24
#: Above this many options a button set will not fit; switch to a list.
_MAX_BUTTONS = 3


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _prompt_suffix(lang: Lang) -> str:
    """A one-line nudge telling the patient how to answer a non-option node."""
    if lang is Lang.HI:
        return "कृपया अपना उत्तर टाइप करें।"
    return "Please type your answer."


def _number_suffix(node: dict[str, Any], lang: Lang) -> str:
    lo, hi, unit = node.get("min"), node.get("max"), node.get("unit")
    unit_txt = f" {unit}" if unit else ""
    if lo is not None and hi is not None:
        rng = f"{_num(lo)}–{_num(hi)}{unit_txt}"
        return (
            f"कृपया एक संख्या भेजें ({rng})।"
            if lang is Lang.HI
            else f"Please reply with a number ({rng})."
        )
    return "कृपया एक संख्या भेजें।" if lang is Lang.HI else "Please reply with a number."


def _num(value: Any) -> str:
    """Trim a float that is really an integer (5.0 → "5") for the prompt."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_question(to: str, node: dict[str, Any], lang: Lang) -> OutboundMessage:
    """Build the outbound message for one intake question.

    `node` is the dispatcher's `get_next_node()["node"]` dict (id/type/text/options
    /min/max/unit). Returns a single `OutboundMessage` addressed to `to`.
    """
    text = node.get("text") or ""
    options = node.get("options") or []

    if options:
        if len(options) <= _MAX_BUTTONS:
            return OutboundMessage(
                to=to,
                text=text,
                buttons=tuple(
                    Button(id=opt["id"], title=_truncate(opt["text"], _BUTTON_TITLE_MAX))
                    for opt in options
                ),
            )
        return OutboundMessage(
            to=to,
            text=text,
            list_rows=tuple(
                ListRow(id=opt["id"], title=_truncate(opt["text"], _LIST_ROW_TITLE_MAX))
                for opt in options
            ),
            list_button="चुनें" if lang is Lang.HI else "Choose",
        )

    # No options: a number/scale wants a figure, everything else free text (and
    # for the chief complaint, a voice note — the webhook accepts either).
    if node.get("type") in {"number", "scale"}:
        suffix = _number_suffix(node, lang)
    else:
        suffix = _prompt_suffix(lang)
    return OutboundMessage(to=to, text=f"{text}\n\n{suffix}")
