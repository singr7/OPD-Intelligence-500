"""The adaptive answer interpreter (doc 11 §2) — a spoken answer → a node value.

> "Given a node (its question + its allowed answers) and a patient's spoken
> utterance, produce **either** a *candidate value the node accepts*, **or** *one
> short clarifying question* in the patient's language." — doc 11 §1

This is the intelligence layer under any future full-duplex V2V (doc 11 §1): the
patient answers a structured tap node *by voice*, the interpreter maps the words
onto one of the node's own allowed answers, and when the words are too vague it
asks exactly one clarifying follow-up instead of guessing. It is deliberately
narrow and it never decides anything — it *proposes* a candidate value, and the
**existing `walk.save()` validator + rule-based red-flag evaluator still decide**
(doc 11 §5). That is the whole safety story: an interpretation that the node
rejects becomes a clarify, never an error and never an invented option.

## Two interpreters, one interface — mirrors `summary.py`

`LLMInterpreter` is the real path: the `interpret_answer` prompt on the LLM chain
(local vLLM / Qwen3 on the box, doc 10), with `with_fallback` over the chain.
`FakeInterpreter` is the deterministic path for tests — it maps by matching the
node's own option labels and pulling a number out of the utterance, no vendor.
Both return the same `Interpretation`, so the route branch that calls them does
not know or care which is wired (doc 11 §7: mirror the Summarizer shape).

## The `extra` field is reserved for V2 (doc 11 §3)

V1 fills only `value`/`clarify`. `extra` — volunteered facts for *other* nodes —
is parsed if the model sends it but never acted on yet, so V2 enrichment is an
additive change to the caller, not a re-shape of this type (doc 11 §2 "what V1
must expose").
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.models.enums import Lang, UsagePurpose
from app.prompts import load
from app.providers import LLMProvider, LLMRequest, with_fallback
from app.providers.resilience import ProviderBadRequest
from app.trees.schema import Node, NodeType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Interpretation:
    """The interpreter's proposal for one spoken answer.

    Exactly one of `value` / `clarify` carries the outcome: a candidate value the
    node *might* accept (still validated by `walk.save`), or one short clarifying
    question in the patient's language. `confidence` is recorded for the V2
    telemetry (doc 11 §3), not gated on in V1. `extra` is reserved for V2
    enrichment (doc 11 §3) and unused here.
    """

    value: Any | None = None
    clarify: str | None = None
    confidence: float = 0.0
    #: Reserved for V2 — (node_id, value) facts volunteered for OTHER nodes.
    extra: tuple[tuple[str, Any], ...] = ()

    @property
    def has_value(self) -> bool:
        return self.value is not None

    @property
    def outcome(self) -> str:
        """The per-node telemetry label (doc 11 §2 §6): what this turn produced."""
        return "interpreted" if self.has_value else "clarify"


class Interpreter(Protocol):
    async def interpret(
        self, node: Node, utterance: str, lang: Lang | str
    ) -> Interpretation: ...


# -- the answer spec the model (and the fake) reason over ----------------------


def answer_spec(node: Node, lang: Lang | str) -> str:
    """The node's allowed answers, as constrained text for the prompt.

    Options come with their ids **and** patient-language labels so the model maps
    onto an id it may actually return; scale/number come as a numeric range. The
    prompt is told to choose only from this — anything else must clarify (doc 11
    §2: "never invents options").
    """
    if node.type.wants_options:
        lines = []
        for opt in node.options:
            label = opt.text.get(str(lang)) or opt.text.get(Lang.EN, opt.id)
            en = opt.text.get(Lang.EN, "")
            gloss = f" ({en})" if en and en != label else ""
            lines.append(f'- id "{opt.id}": {label}{gloss}')
        kind = "one option id" if node.type is NodeType.SINGLE else "a list of option ids"
        return f"Choose {kind} from:\n" + "\n".join(lines)
    if node.type.wants_range:
        unit = f" ({node.unit})" if node.unit else ""
        lo = "" if node.min is None else f" from {node.min:g}"
        hi = "" if node.max is None else f" to {node.max:g}"
        return f"A single number{lo}{hi}{unit}."
    # free_voice / body_map with no options: nothing to constrain to; the kiosk
    # keeps these on taps/text, so the interpreter should never be asked.
    return "Free text — do not interpret; ask the patient to use the screen."


# -- the LLM path --------------------------------------------------------------


class LLMInterpreter:
    """V1/V2 path — the `interpret_answer` prompt on the LLM chain (doc 11 §2)."""

    def __init__(
        self, providers: Sequence[LLMProvider], *, prompt_version: int | None = None
    ) -> None:
        self._providers = list(providers)
        self._prompt = load("interpret_answer", prompt_version)

    async def interpret(self, node: Node, utterance: str, lang: Lang | str) -> Interpretation:
        rendered = self._prompt.render(
            question=node.ask(lang),
            answer_spec=answer_spec(node, lang),
            utterance=utterance,
            lang=str(lang),
        )
        request = LLMRequest(
            prompt=rendered,
            system=self._prompt.system,
            prompt_ref=self._prompt.ref,
            json_output=True,
            temperature=0.1,
            max_tokens=200,
        )
        result = await with_fallback(
            self._providers,
            lambda provider: provider.complete(request, purpose=UsagePurpose.INTAKE_TURN),
        )
        try:
            return _parse(result.json())
        except (ValueError, TypeError, ProviderBadRequest) as exc:
            # A malformed interpretation must never crash an intake — it degrades
            # to a clarify, and the patient can always tap (doc 11 §5). Logged so
            # the S18 telemetry sees a real mis-map rate, not a silent swallow.
            logger.warning("interpret_answer returned unparseable JSON: %s", exc)
            return Interpretation(clarify=None, confidence=0.0)


def _parse(payload: Any) -> Interpretation:
    """Model JSON → `Interpretation`. Tolerant: an empty/odd object ⇒ clarify."""
    if not isinstance(payload, Mapping):
        raise ValueError(f"interpretation must be a JSON object, got {type(payload).__name__}")

    confidence = payload.get("confidence")
    conf = float(confidence) if isinstance(confidence, (int, float)) else 0.0

    clarify = payload.get("clarify")
    if isinstance(clarify, str) and clarify.strip():
        return Interpretation(clarify=clarify.strip(), confidence=conf)

    if "value" in payload and payload["value"] is not None:
        extra_raw = payload.get("extra") or []
        extra: list[tuple[str, Any]] = []
        if isinstance(extra_raw, Sequence) and not isinstance(extra_raw, str):
            for row in extra_raw:
                if isinstance(row, Mapping) and "node_id" in row and "value" in row:
                    extra.append((str(row["node_id"]), row["value"]))
        return Interpretation(value=payload["value"], confidence=conf, extra=tuple(extra))

    # Neither a usable value nor a clarify string — treat as "please clarify" with
    # no text, so the caller falls back rather than accepting nothing.
    return Interpretation(confidence=conf)


# -- the deterministic path (tests + offline sanity) ---------------------------

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class FakeInterpreter:
    """Deterministic interpreter — no vendor, for tests and the doc 11 §2 AC.

    Maps by matching the node's own option labels/ids against the utterance, and
    pulls a number out for scale/number nodes. It cannot invent an option (it only
    ever returns one the node declared), which is exactly the property the LLM path
    is constrained to and the walker enforces. Anything it cannot map ⇒ a fixed
    clarify string, so the "one clarify then taps" path is testable offline.
    """

    #: The stand-in clarify — a real deployment speaks the LLM's, in-language.
    clarify_text: str = "Sorry, I did not catch that. Please say it again or tap your answer."

    async def interpret(self, node: Node, utterance: str, lang: Lang | str) -> Interpretation:
        text = utterance.strip().lower()
        if not text:
            return Interpretation(clarify=self.clarify_text)

        if node.type.wants_options:
            matched: list[str] = []
            for opt in node.options:
                labels = [opt.id, *opt.text.values()]
                if any(label and label.lower() in text for label in labels):
                    matched.append(opt.id)
            if node.type is NodeType.SINGLE:
                if len(matched) == 1:
                    return Interpretation(value=matched[0], confidence=1.0)
                # Zero or ambiguous (>1) ⇒ clarify, never a guess.
                return Interpretation(clarify=self.clarify_text)
            if matched:  # multi / body_map
                return Interpretation(value=matched, confidence=1.0)
            return Interpretation(clarify=self.clarify_text)

        if node.type.wants_range:
            found = _NUMBER.search(text)
            if found:
                raw = found.group(0)
                value: Any = float(raw) if "." in raw else int(raw)
                return Interpretation(value=value, confidence=1.0)
            return Interpretation(clarify=self.clarify_text)

        return Interpretation(clarify=self.clarify_text)


#: Named so the engine/route and tests can share one instance without importing
#: the class everywhere; it is stateless.
FAKE_INTERPRETER = FakeInterpreter()
