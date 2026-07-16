"""Red-flag rule expressions (doc 03 §1/§3) — deterministic, no model involved.

> "Red-flag rules (config, not code) evaluated on every answer." — doc 03 §1

This module is the "not code" half of that sentence: a tiny boolean expression
language over the answers collected so far, authored inside the tree JSONB and
evaluated here. It exists so that the question of *what is clinically alarming*
is answerable by reading data an oncologist can sign off (S21), not by reading
Python.

## Why the model never decides a flag

The tier ladder (doc 02 §5) swaps the conversational brain from Gemini Live to
gpt-4o-mini to nothing at all (V3, offline). If a red flag depended on the model,
"is this patient's fever dangerous?" would have three different answers depending
on which vendor happened to be up — and none of them reviewable. So the model may
only *report what the patient said* (`save_answer`); the rules here turn those
answers into flags identically on every tier, including the zero-AI one.

## Shape

A group::

    {"op": "and" | "or" | "not", "rules": [ ... ]}

A leaf, which addresses one node's answer::

    {"node": "onc.fever.temp", "op": "gte", "value": 38}

Leaf ops: eq, ne, gt, gte, lt, lte (numbers), in (membership in a list),
contains (a multi-select holds an option), answered / unanswered.

An unanswered node makes every leaf false except `unanswered` — a flag must be
*earned* by something the patient actually said. A half-finished intake that
hangs up early therefore raises only the flags its answers support, which is what
lets S5 partial-save a dropped call without inventing urgency.

## free_voice is deliberately unmatchable

You cannot write `contains "blood"` against a free_voice node, and
`validate` rejects it. That text is ASR output: substring-matching it would make
a red flag depend on how Sarvam happened to transcribe an accent, and would fire
"no blood in my stool" as bleeding. Free speech gets *asked about* by a real
node; only structured answers raise flags.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

#: Node kinds whose answers a given leaf op may address. Keyed by op; the values
#: are `NodeType` values (plain strings here so `schema` can import `rules` and
#: not the reverse).
_OP_APPLIES_TO: dict[str, frozenset[str]] = {
    "eq": frozenset({"single", "scale", "number"}),
    "ne": frozenset({"single", "scale", "number"}),
    "gt": frozenset({"scale", "number"}),
    "gte": frozenset({"scale", "number"}),
    "lt": frozenset({"scale", "number"}),
    "lte": frozenset({"scale", "number"}),
    "in": frozenset({"single", "scale", "number"}),
    "contains": frozenset({"multi", "body_map"}),
    "answered": frozenset({"single", "multi", "scale", "number", "body_map", "free_voice"}),
    "unanswered": frozenset({"single", "multi", "scale", "number", "body_map", "free_voice"}),
}

_GROUP_OPS = frozenset({"and", "or", "not"})
_NUMERIC_OPS = frozenset({"gt", "gte", "lt", "lte"})
#: Ops that take no `value` — they ask about presence, not content.
_NULLARY_OPS = frozenset({"answered", "unanswered"})


class RuleError(ValueError):
    """A rule that cannot be trusted to mean what it says.

    Raised at validation time only. Evaluation never raises: a live intake with a
    bad rule should not 500 on a patient mid-sentence, and `validate` has already
    run at seed/publish time to make that unreachable.
    """


class LeafOp(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    CONTAINS = "contains"
    ANSWERED = "answered"
    UNANSWERED = "unanswered"


def is_group(rule: Mapping[str, Any]) -> bool:
    return "rules" in rule


def evaluate(rule: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    """Is this rule true of `values` (node_id → answered value)?

    Total by construction — an unknown op or a type mismatch is False, never an
    exception. See `RuleError`: the loud failure happens in `validate`.
    """
    if is_group(rule):
        op = rule.get("op")
        sub: Sequence[Mapping[str, Any]] = rule.get("rules") or ()
        if op == "and":
            return all(evaluate(r, values) for r in sub)
        if op == "or":
            return any(evaluate(r, values) for r in sub)
        if op == "not":
            return not any(evaluate(r, values) for r in sub)
        return False
    return _evaluate_leaf(rule, values)


def _evaluate_leaf(rule: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    op = rule.get("op")
    node_id = rule.get("node")
    if not isinstance(node_id, str) or not isinstance(op, str):
        return False

    present = node_id in values and values[node_id] is not None
    if op == LeafOp.ANSWERED:
        return present
    if op == LeafOp.UNANSWERED:
        return not present
    if not present:
        # Silence is not evidence. Every content op is false without an answer.
        return False

    answer = values[node_id]
    expected = rule.get("value")

    if op in _NUMERIC_OPS:
        left, right = _as_number(answer), _as_number(expected)
        if left is None or right is None:
            return False
        if op == LeafOp.GT:
            return left > right
        if op == LeafOp.GTE:
            return left >= right
        if op == LeafOp.LT:
            return left < right
        return left <= right

    if op == LeafOp.EQ:
        return _scalar_eq(answer, expected)
    if op == LeafOp.NE:
        return not _scalar_eq(answer, expected)
    if op == LeafOp.IN:
        if not isinstance(expected, (list, tuple)):
            return False
        return any(_scalar_eq(answer, item) for item in expected)
    if op == LeafOp.CONTAINS:
        if not isinstance(answer, (list, tuple)):
            return False
        return any(_scalar_eq(item, expected) for item in answer)
    return False


def _as_number(value: Any) -> float | None:
    """Numbers only. `True` is not 1 here — a bool sneaking into a temperature
    comparison is an authoring bug, not a 1°C fever."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _scalar_eq(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def validate(
    rule: Any,
    node_kinds: Mapping[str, str],
    *,
    where: str,
    _depth: int = 0,
) -> None:
    """Reject a rule that is malformed, points at a node that does not exist, or
    asks a question its node's type cannot answer.

    `node_kinds` maps node id → `NodeType` value. Type-checking leaves against it
    is what catches the authoring mistakes that would otherwise be invisible: a
    `contains` against a single-select silently never fires, and a red flag that
    never fires is worse than no red flag — it reads as reviewed and safe.
    """
    if _depth > 8:
        raise RuleError(f"{where}: rule nested deeper than 8 levels")
    if not isinstance(rule, Mapping):
        raise RuleError(f"{where}: rule must be an object, got {type(rule).__name__}")

    op = rule.get("op")
    if not isinstance(op, str):
        raise RuleError(f"{where}: rule needs a string 'op'")

    if is_group(rule):
        if op not in _GROUP_OPS:
            raise RuleError(
                f"{where}: unknown group op {op!r}; expected one of {sorted(_GROUP_OPS)}"
            )
        sub = rule.get("rules")
        if not isinstance(sub, Sequence) or isinstance(sub, (str, bytes)) or not sub:
            raise RuleError(f"{where}: group op {op!r} needs a non-empty 'rules' list")
        if op == "not" and len(sub) != 1:
            raise RuleError(f"{where}: 'not' takes exactly one rule, got {len(sub)}")
        for index, child in enumerate(sub):
            validate(child, node_kinds, where=f"{where}.rules[{index}]", _depth=_depth + 1)
        if unknown := set(rule) - {"op", "rules"}:
            raise RuleError(f"{where}: unexpected keys on group: {sorted(unknown)}")
        return

    if op in _GROUP_OPS:
        raise RuleError(f"{where}: group op {op!r} used without a 'rules' list")
    if op not in _OP_APPLIES_TO:
        raise RuleError(f"{where}: unknown op {op!r}; expected one of {sorted(_OP_APPLIES_TO)}")

    node_id = rule.get("node")
    if not isinstance(node_id, str) or not node_id:
        raise RuleError(f"{where}: leaf rule needs a 'node' id")
    if node_id not in node_kinds:
        raise RuleError(f"{where}: rule points at unknown node {node_id!r}")

    kind = node_kinds[node_id]
    if kind not in _OP_APPLIES_TO[op]:
        detail = ""
        if kind == "free_voice":
            detail = (
                " — free_voice answers are raw ASR text; matching on them would make the "
                "flag depend on the transcriber. Ask a structured question instead."
            )
        raise RuleError(
            f"{where}: op {op!r} cannot apply to node {node_id!r} of type {kind!r}"
            f" (valid types: {sorted(_OP_APPLIES_TO[op])}){detail}"
        )

    has_value = "value" in rule
    if op in _NULLARY_OPS and has_value:
        raise RuleError(f"{where}: op {op!r} takes no 'value'")
    if op not in _NULLARY_OPS and not has_value:
        raise RuleError(f"{where}: op {op!r} needs a 'value'")

    if op in _NUMERIC_OPS and _as_number(rule.get("value")) is None:
        raise RuleError(f"{where}: op {op!r} needs a numeric 'value', got {rule.get('value')!r}")
    if op == LeafOp.IN:
        value = rule.get("value")
        if not isinstance(value, list) or not value:
            raise RuleError(f"{where}: op 'in' needs a non-empty list 'value'")

    if unknown := set(rule) - {"op", "node", "value"}:
        raise RuleError(f"{where}: unexpected keys on leaf: {sorted(unknown)}")


def referenced_nodes(rule: Mapping[str, Any]) -> set[str]:
    """Every node id a rule reads. Used by the validator to report which nodes a
    flag depends on, and by S18's tree editor to refuse to delete one of them."""
    if is_group(rule):
        found: set[str] = set()
        for child in rule.get("rules") or ():
            if isinstance(child, Mapping):
                found |= referenced_nodes(child)
        return found
    node_id = rule.get("node")
    return {node_id} if isinstance(node_id, str) else set()
