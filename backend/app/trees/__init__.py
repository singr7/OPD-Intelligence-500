"""Question trees (doc 03 §3): the schema, the validator, and the walker.

Trees are the clinical content of an intake — data, not code, so an oncologist can
review them (S21) and an admin can publish a change without a deploy (S18).

- `schema` — the node schema of doc 03 §3, parsed into typed objects by a
  validator strict enough that a parsed `Tree` is safe to ask a patient.
- `rules` — the deterministic red-flag expression language. No model decides a
  flag, on any tier.
- `walker` — `Walk`, one patient's position in one tree, derived from their
  answers. This is what S5's four intake tools drive, and the whole of tier V3.
- `bank` — the authored pilot trees in `seeds/trees/`.
"""

from app.trees.bank import for_department, get, load_bank, load_file
from app.trees.rules import RuleError
from app.trees.schema import (
    MAX_OPTIONS,
    Node,
    NodeType,
    Option,
    RedFlagSpec,
    Tree,
    TreeError,
    parse,
)
from app.trees.walker import Answer, AnswerError, RedFlagHit, Walk, validate_answer

__all__ = [
    "MAX_OPTIONS",
    "Answer",
    "AnswerError",
    "Node",
    "NodeType",
    "Option",
    "RedFlagHit",
    "RedFlagSpec",
    "RuleError",
    "Tree",
    "TreeError",
    "Walk",
    "for_department",
    "get",
    "load_bank",
    "load_file",
    "parse",
    "validate_answer",
]
