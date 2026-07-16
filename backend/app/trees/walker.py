"""The deterministic tree-walker — the engine behind the intake tool contract.

> "The clinical logic lives in the tree and the deterministic red-flag rules —
> data an oncologist can review and sign off, not weights." — `app.prompts.tools`

A `Walk` is one patient's position in one tree. It is what `get_next_node`,
`save_answer` and `check_red_flags` (`app.prompts.tools`) actually do once S5
strips off the session plumbing, and it is the *whole* of the V3 tier — no model
in the loop at all.

## Position is derived, never stored

`Walk` holds a tree and a bag of answers. "Which question now?" is recomputed by
walking from the root each time, following each answered node's branch. There is
no cursor.

That is what makes the tier ladder work. When Gemini Live dies mid-sentence and
S5 rebuilds the session on V2 — or on V3, offline — it hands the same answers to
a new `Walk` and gets the same next question. A stored cursor would be a second
source of truth about where the patient is, and the two would disagree exactly
when a provider was failing over, which is the worst possible moment.

It also makes an amendment cheap and correct. Doc 03 §1 ends every intake with
"Yes that's right / I want to change something": changing an early answer moves
the branch, and `save` drops the answers stranded on the abandoned branch (see
`_prune`) so they cannot surface in a summary the patient never gave.

## Answers are the contract

`to_json` is the `answers JSONB` shape doc 03 §1's AC demands be identical across
every tier and channel, and it matches `Intake.answers` ({node_id: {value, text,
text_en, at}}). Because all three tiers walk this same object, that AC is
structural rather than something each tier has to remember.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.models.enums import Lang, Priority
from app.trees import rules as rule_lang
from app.trees.schema import Node, NodeType, RedFlagSpec, Tree

_SEVERITY_ORDER = {Priority.ROUTINE: 0, Priority.SEMI: 1, Priority.URGENT: 2}


class AnswerError(ValueError):
    """The answer does not fit the question.

    Distinct from `TreeError` on purpose: a `TreeError` means the content is
    broken and nobody should be asked it, while this means *this* answer needs
    re-asking. S5 turns it into a re-prompt, not a 500.
    """


@dataclass(frozen=True, slots=True)
class Answer:
    """One recorded answer.

    `text` is the patient's own words — `save_answer` insists on them even when
    the model mapped the speech onto an option, because the doctor reads the quote
    (doc 03 §4: "Patient's own words") and because a mis-mapped option is only
    recoverable if what was actually said survived.
    """

    node_id: str
    value: Any
    text: str | None = None
    #: English translation of `text`. S5 fills it for the doctor screen; the
    #: walker never needs it and never invents it.
    text_en: str | None = None
    lang: Lang | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "text": self.text,
            "text_en": self.text_en,
            "lang": str(self.lang) if self.lang else None,
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_json(cls, node_id: str, data: Mapping[str, Any]) -> Answer:
        raw_at = data.get("at")
        try:
            at = datetime.fromisoformat(raw_at) if isinstance(raw_at, str) else datetime.now(UTC)
        except ValueError:
            at = datetime.now(UTC)
        raw_lang = data.get("lang")
        return cls(
            node_id=node_id,
            value=data.get("value"),
            text=data.get("text"),
            text_en=data.get("text_en"),
            lang=Lang(raw_lang) if raw_lang else None,
            at=at,
        )


@dataclass(frozen=True, slots=True)
class RedFlagHit:
    """A fired flag, ready for `Intake.red_flags` and the doctor's red-flag strip."""

    id: str
    severity: Priority
    label: Mapping[str, str]
    instruction: Mapping[str, str]
    source_node: str | None = None

    def say(self, lang: Lang | str) -> str:
        """The words to speak to the patient, verbatim (doc 02 §5)."""
        return self.instruction.get(str(lang)) or self.instruction.get(Lang.EN, "")

    def name(self, lang: Lang | str = Lang.EN) -> str:
        return self.label.get(str(lang)) or self.label.get(Lang.EN, self.id)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": str(self.severity),
            "label": dict(self.label),
            "instruction": dict(self.instruction),
            "source_node": self.source_node,
        }


class Walk:
    """One patient's traversal of one tree."""

    def __init__(self, tree: Tree, answers: Mapping[str, Answer] | None = None) -> None:
        self.tree = tree
        self._answers: dict[str, Answer] = dict(answers or {})
        self._prune()

    # ---- position -------------------------------------------------------

    @property
    def current(self) -> Node | None:
        """The node to ask now, or None when the tree is done.

        None is what makes `finish_and_summarize` legal — the model is told to
        call it only "when get_next_node reports the tree is complete".
        """
        for node in self._traverse():
            if node.id not in self._answers:
                return node
        return None

    @property
    def is_complete(self) -> bool:
        return self.current is None

    def path(self) -> tuple[str, ...]:
        """Node ids on the live path, in ask order, including the current one.

        The branch actually taken — not every node in the tree, and not the
        abandoned ones. S18's test-run mode renders this.
        """
        return tuple(node.id for node in self._traverse())

    def _traverse(self):
        """Yield nodes from the root along the branch the answers select."""
        node_id: str | None = self.tree.root
        seen: set[str] = set()
        while node_id is not None:
            if node_id in seen:  # pragma: no cover - parse() rejects cycles
                raise AssertionError(f"cycle at {node_id!r} in validated tree {self.tree.ref}")
            seen.add(node_id)
            node = self.tree.node(node_id)
            yield node
            answer = self._answers.get(node_id)
            if answer is None:
                return
            node_id = self._edge(node, answer.value)

    def _edge(self, node: Node, value: Any) -> str | None:
        """Which branch an answer selects. Option-keyed for `single` (the schema
        allows it nowhere else), `default` otherwise."""
        if node.type is NodeType.SINGLE and isinstance(value, str) and value in node.next:
            return node.next[value]
        return node.next.get("default")

    # ---- answers --------------------------------------------------------

    @property
    def answers(self) -> Mapping[str, Answer]:
        return dict(self._answers)

    def save(
        self,
        node_id: str,
        value: Any,
        *,
        text: str | None = None,
        text_en: str | None = None,
        lang: Lang | str | None = None,
        at: datetime | None = None,
    ) -> Answer:
        """Record an answer to `node_id`, which must be the current question or one
        already answered (an amendment).

        Answering some other node is refused rather than accepted quietly: it means
        the model skipped ahead or a stale client replayed an old screen, and a tree
        that can be answered out of order is one whose branch conditions were never
        really asked.
        """
        node = self.tree.node(node_id)
        current = self.current
        if node_id not in self._answers and (current is None or current.id != node_id):
            asked = (
                "the intake is complete" if current is None else f"the question is {current.id!r}"
            )
            raise AnswerError(
                f"cannot answer {node_id!r}: it is not the current question ({asked}). "
                "Answer the current node, or amend one already answered."
            )

        normalized = validate_answer(node, value)
        answer = Answer(
            node_id=node_id,
            value=normalized,
            text=text,
            text_en=text_en,
            lang=Lang(lang) if lang else None,
            at=at or datetime.now(UTC),
        )
        self._answers[node_id] = answer
        self._prune()
        return answer

    def _prune(self) -> None:
        """Drop answers that are no longer on the path.

        An amendment ("actually the pain is in my chest, not my belly") reroutes
        the walk; the answers gathered down the abandoned branch are now answers to
        questions this patient was never asked on the branch they are on. Leaving
        them in would put them in the doctor's summary. Deriving position from
        answers is what makes this a three-line fix rather than a rollback log.
        """
        live = {node.id for node in self._traverse()}
        for node_id in list(self._answers):
            if node_id not in live:
                del self._answers[node_id]

    def values(self) -> dict[str, Any]:
        """node_id → value, the shape the rule evaluator reads."""
        return {node_id: answer.value for node_id, answer in self._answers.items()}

    # ---- red flags ------------------------------------------------------

    def red_flags(self) -> tuple[RedFlagHit, ...]:
        """Every flag the answers so far raise, worst first.

        Deterministic and tier-independent (doc 02 §5): recomputed from the
        answers on every call rather than accumulated, so an amendment that
        removes the alarming answer also removes the flag. A flag that outlived
        its evidence would put a patient at the front of the queue for something
        they corrected thirty seconds later.
        """
        hits = [
            RedFlagHit(
                id=spec.id,
                severity=spec.severity,
                label=spec.label,
                instruction=spec.instruction,
                source_node=spec.source_node,
            )
            for spec in self.tree.red_flags
            if self._fires(spec)
        ]
        hits.sort(key=lambda hit: (-_SEVERITY_ORDER[hit.severity], hit.id))
        return tuple(hits)

    def _fires(self, spec: RedFlagSpec) -> bool:
        return rule_lang.evaluate(spec.when, self.values())

    def priority(self) -> Priority:
        """The visit priority these answers earn (doc 03 §1: red flag → urgent)."""
        flags = self.red_flags()
        return flags[0].severity if flags else Priority.ROUTINE

    # ---- serialisation --------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """`Intake.answers` — the one shape every tier and channel produces."""
        return {node_id: answer.to_json() for node_id, answer in self._answers.items()}

    @classmethod
    def from_json(cls, tree: Tree, data: Mapping[str, Any] | None) -> Walk:
        """Rebuild a walk from stored answers.

        Unknown node ids are dropped, not fatal: a published tree can gain a
        version while an intake is in flight (S18 publishes without a deploy), and
        a patient mid-answer is not the person to punish for it. `_prune` in the
        constructor discards anything off the current path anyway.
        """
        answers: dict[str, Answer] = {}
        for node_id, raw in (data or {}).items():
            if node_id in tree.nodes and isinstance(raw, Mapping):
                answers[node_id] = Answer.from_json(node_id, raw)
        return cls(tree, answers)


def validate_answer(node: Node, value: Any) -> Any:
    """Check a value against its node's type, returning the normalised value.

    Normalisation is small but load-bearing: `2.0` for a scale becomes `2`, so the
    JSONB an intake writes does not depend on whether the answer arrived from a
    kiosk tap or a model's function call.
    """
    if value is None:
        raise AnswerError(f"node {node.id!r}: an answer is required")

    if node.type is NodeType.SINGLE:
        if not isinstance(value, str):
            raise AnswerError(
                f"node {node.id!r}: expected one option id, got {type(value).__name__}"
            )
        if node.option(value) is None:
            raise AnswerError(
                f"node {node.id!r}: {value!r} is not an option (have: {list(node.option_ids())})"
            )
        return value

    if node.type in (NodeType.MULTI, NodeType.BODY_MAP):
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise AnswerError(f"node {node.id!r}: expected a list of option ids")
        chosen: list[str] = []
        for item in value:
            if not isinstance(item, str) or node.option(item) is None:
                raise AnswerError(
                    f"node {node.id!r}: {item!r} is not an option (have: {list(node.option_ids())})"
                )
            if item not in chosen:
                chosen.append(item)
        return chosen

    if node.type in (NodeType.SCALE, NodeType.NUMBER):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AnswerError(f"node {node.id!r}: expected a number, got {value!r}")
        number = float(value)
        if node.min is not None and number < node.min:
            raise AnswerError(f"node {node.id!r}: {number} is below the minimum {node.min}")
        if node.max is not None and number > node.max:
            raise AnswerError(f"node {node.id!r}: {number} is above the maximum {node.max}")
        return int(number) if number.is_integer() else number

    if not isinstance(value, str) or not value.strip():
        raise AnswerError(f"node {node.id!r}: expected the patient's words")
    return value.strip()
