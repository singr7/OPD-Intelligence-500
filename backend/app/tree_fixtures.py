"""Golden walk traces — the drift gate between the Python and TS walkers (S7).

`make tree-fixtures` runs this and writes `web/e2e/fixtures/walk-conformance.json`.

## Why this exists

S7 gave the kiosk an offline walker in TypeScript (doc 01 §5: the kiosk must keep
working with the server unreachable). That makes two implementations of the one
thing STATE.md says no model and no vendor may ever decide — what is clinically
alarming. Two implementations drift, and a drift is silent: a flag that fires in
Python and not in TypeScript is a patient who is urgent on the server and routine
on the kiosk during exactly the outage the offline mode exists for.

So the TS port is not trusted, it is **tested against this file**. Here we drive
the *real* Python walker over the *real* seeded trees and record, at every step,
everything the walker decides: the next question, the live path, the pruned
answer set, and the red flags. `web/e2e/conformance.spec.ts` replays the same
answers through the TS walker and demands byte-identical results.

`make test` regenerates and diffs, so a change to `walker.py` or `rules.py`
without a matching TS change fails the build rather than a kiosk.

## What makes a trace worth recording

Random walks alone would miss the cases that matter, so `_answer_choices` is
exhaustive per node (every option, both range ends, midpoints) and the driver
takes a **deterministic** seeded sample of branches. On top of the plain walks we
force the three behaviours most likely to diverge:

- **amendments** — re-answering an early node, which reroutes and must prune the
  abandoned branch (the flags must drop with it);
- **rejected answers** — every AnswerError path, since a client that accepts what
  the server refuses writes answers the server will reject at sync;
- **partial walks** — stopping early, where `unanswered` leaves and half-met
  `and` groups decide whether a flag fires.
"""

from __future__ import annotations

import json
import random
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.trees import rules as rule_lang
from app.trees.schema import Node, NodeType, Tree, parse
from app.trees.walker import AnswerError, Walk

#: Bumped when the trace format changes, so a stale fixture cannot pass quietly.
FIXTURE_VERSION = 1

#: One seed, one file. The sample must be identical on every machine and in CI —
#: a fixture that differs per run would make the diff gate meaningless.
SEED = 20260717

#: Walks recorded per tree. Enough to cover every branch of the authored trees
#: several times over; the file stays a few hundred KB.
WALKS_PER_TREE = 24

#: How many of those are systematic rather than random (see `_record_walk`).
#: Must exceed the largest `_answer_choices` list so the cycle covers every
#: option — the widest range node currently yields ~12.
CYCLING_WALKS = 16

#: Answers carry a timestamp the walker never reads. Fixing it keeps the trace
#: byte-stable across runs.
FIXED_AT = datetime.fromisoformat("2026-07-17T09:00:00+00:00")

REPO = Path(__file__).resolve().parents[2]
TREES_DIR = REPO / "seeds" / "trees"
OUT_PATH = REPO / "web" / "e2e" / "fixtures" / "walk-conformance.json"


def _coverage_tree() -> dict[str, Any]:
    """A synthetic tree that exercises every op in the rule language.

    The 11 authored trees are clinical content, not a test suite: between them
    they never use `unanswered`, and a mutation run proved the fixture could not
    tell `unanswered` from `answered` because of it. Trees are data an admin edits
    (S18) and an oncologist signs off (S21) — the day someone authors the first
    `unanswered` rule, the port must already be known-correct for it, not tested
    for the first time by a patient during an outage.

    So this tree is deliberately not clinical. It exists to make every leaf op,
    both group ops, both list-answer node types and the severity ordering appear
    in the golden trace. It is generated into the fixture only; it is never
    seeded, published or asked.
    """
    langs = {"en": "coverage", "hi": "कवरेज"}
    return {
        "key": "op_coverage",
        "version": 1,
        "languages": ["en", "hi"],
        "title": {"en": "Rule-language coverage", "hi": "नियम-भाषा कवरेज"},
        "root": "a.single",
        "nodes": [
            {
                "id": "a.single",
                "type": "single",
                "text": dict(langs),
                "options": [
                    {"id": "yes", "text": {"en": "Yes", "hi": "हाँ"}},
                    {"id": "no", "text": {"en": "No", "hi": "नहीं"}},
                    {"id": "maybe", "text": {"en": "Maybe", "hi": "शायद"}},
                ],
                # The `no` branch skips b.multi entirely — that is what gives
                # `unanswered` something true to say.
                "next": {"yes": "b.multi", "no": "c.scale", "default": "c.scale"},
            },
            {
                "id": "b.multi",
                "type": "multi",
                "text": dict(langs),
                "options": [
                    {"id": "x", "text": {"en": "X", "hi": "क्ष"}},
                    {"id": "y", "text": {"en": "Y", "hi": "य"}},
                    {"id": "z", "text": {"en": "Z", "hi": "ज़"}, "flag": True},
                ],
                "next": {"default": "c.scale"},
                # Option-level sugar: picking `z` is itself the flag. `parse`
                # desugars this into a real rule, and the canonical form the kiosk
                # receives must carry it.
                "red_flag": {
                    "id": "sugar.option",
                    "severity": "urgent",
                    "label": {"en": "Picked Z", "hi": "ज़ चुना"},
                    "instruction": {"en": "Z fired.", "hi": "ज़ सक्रिय।"},
                },
            },
            {
                "id": "c.scale",
                "type": "scale",
                "text": dict(langs),
                "min": 0,
                "max": 10,
                "next": {"default": "d.number"},
            },
            {
                "id": "d.number",
                "type": "number",
                "text": dict(langs),
                "min": 30,
                "max": 45,
                "unit": "C",
                "next": {"default": "e.body"},
            },
            {
                "id": "e.body",
                "type": "body_map",
                "text": dict(langs),
                "options": [
                    {"id": "head", "text": {"en": "Head", "hi": "सिर"}},
                    {"id": "chest", "text": {"en": "Chest", "hi": "छाती"}},
                    {"id": "belly", "text": {"en": "Belly", "hi": "पेट"}},
                ],
                "next": {"default": "f.voice"},
            },
            {
                "id": "f.voice",
                "type": "free_voice",
                "text": dict(langs),
            },
        ],
        "red_flags": [
            _flag("op.eq", "semi", {"node": "a.single", "op": "eq", "value": "yes"}),
            _flag("op.ne", "semi", {"node": "a.single", "op": "ne", "value": "no"}),
            _flag("op.in", "semi", {"node": "a.single", "op": "in", "value": ["maybe", "yes"]}),
            _flag("op.gt", "semi", {"node": "c.scale", "op": "gt", "value": 7}),
            _flag("op.gte", "urgent", {"node": "c.scale", "op": "gte", "value": 7}),
            _flag("op.lt", "semi", {"node": "d.number", "op": "lt", "value": 35}),
            _flag("op.lte", "urgent", {"node": "d.number", "op": "lte", "value": 35}),
            _flag("op.contains", "urgent", {"node": "b.multi", "op": "contains", "value": "x"}),
            _flag(
                "op.contains.body", "semi", {"node": "e.body", "op": "contains", "value": "chest"}
            ),
            _flag("op.answered", "semi", {"node": "b.multi", "op": "answered"}),
            # True exactly on the branch that never asks b.multi.
            _flag("op.unanswered", "urgent", {"node": "b.multi", "op": "unanswered"}),
            _flag("op.answered.voice", "semi", {"node": "f.voice", "op": "answered"}),
            _flag(
                "group.and",
                "urgent",
                {
                    "op": "and",
                    "rules": [
                        {"node": "d.number", "op": "gte", "value": 38},
                        {"node": "a.single", "op": "eq", "value": "yes"},
                    ],
                },
            ),
            _flag(
                "group.or",
                "semi",
                {
                    "op": "or",
                    "rules": [
                        {"node": "c.scale", "op": "lte", "value": 1},
                        {"node": "d.number", "op": "gte", "value": 44},
                    ],
                },
            ),
            _flag(
                "group.not",
                "semi",
                {"op": "not", "rules": [{"node": "a.single", "op": "eq", "value": "yes"}]},
            ),
            _flag(
                "group.nested",
                "urgent",
                {
                    "op": "and",
                    "rules": [
                        {
                            "op": "or",
                            "rules": [
                                {"node": "c.scale", "op": "gte", "value": 5},
                                {"node": "b.multi", "op": "contains", "value": "y"},
                            ],
                        },
                        {"op": "not", "rules": [{"node": "a.single", "op": "eq", "value": "no"}]},
                    ],
                },
            ),
        ],
    }


def _flag(flag_id: str, severity: str, when: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": flag_id,
        "severity": severity,
        "when": when,
        "label": {"en": flag_id, "hi": flag_id},
        "instruction": {"en": f"{flag_id} fired.", "hi": f"{flag_id} सक्रिय।"},
    }


def _thresholds(tree: Tree) -> dict[str, set[float]]:
    """node id → every numeric threshold the tree's red-flag rules compare it to.

    This is the difference between a gate and a decoration. A sampler that picks
    "sensible" numbers (the ends, the midpoint) almost never lands on 38.0, so a
    `gte` ported as `gt` fires identically on every value tried and the suite goes
    green on a real clinical divergence — which is exactly what happened when this
    was first written. The thresholds live in the tree, so we read them out of the
    tree and answer with *them*, and with their neighbours either side.
    """
    found: dict[str, set[float]] = {}

    def visit(rule: Any) -> None:
        if not isinstance(rule, Mapping):
            return
        if rule_lang.is_group(rule):
            for child in rule.get("rules") or ():
                visit(child)
            return
        node_id = rule.get("node")
        value = rule.get("value")
        if not isinstance(node_id, str):
            return
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                found.setdefault(node_id, set()).add(float(item))

    for spec in tree.red_flags:
        visit(spec.when)
    return found


def _answer_choices(node: Node, thresholds: dict[str, set[float]] | None = None) -> list[Any]:
    """Every answer worth trying for a node — the values a real patient could give.

    For ranges this is the boundary set: every threshold a red-flag rule compares
    this node to, one step either side of it, and the range's own ends. An
    off-by-one between `>=` and `>` is only ever visible when the answer equals
    the threshold exactly.
    """
    if node.type is NodeType.SINGLE:
        return [option.id for option in node.options]

    if node.type in (NodeType.MULTI, NodeType.BODY_MAP):
        ids = [option.id for option in node.options]
        # Each option alone (an option-level flag must fire on its own), all of
        # them, and — where there are enough — a pair.
        choices: list[Any] = [[option_id] for option_id in ids]
        if len(ids) > 1:
            choices.append(ids[:2])
            choices.append(ids)
            # A repeated id: `validate_answer` de-duplicates, and that
            # normalisation lands in the answers JSONB the server reads at sync.
            # Without this the two walkers can disagree about what was stored and
            # the suite would not notice.
            choices.append([ids[0], ids[0]])
            choices.append([ids[1], ids[0], ids[1]])
        return choices

    if node.type in (NodeType.SCALE, NodeType.NUMBER):
        low = node.min if node.min is not None else 0
        high = node.max if node.max is not None else 10
        values = {float(low), float(high), (low + high) / 2}
        for threshold in (thresholds or {}).get(node.id, set()):
            # The threshold itself is the case that matters; ±1 and ±0.5 pin the
            # direction of the comparison and catch int-vs-float normalisation.
            for candidate in (
                threshold,
                threshold - 1,
                threshold + 1,
                threshold - 0.5,
                threshold + 0.5,
            ):
                if low <= candidate <= high:
                    values.add(candidate)
        return sorted(_normalise_number(value) for value in values)

    return ["patient's own words, recorded verbatim"]


def _normalise_number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _bad_answers(node: Node) -> list[Any]:
    """Values `validate_answer` must refuse. The TS port must refuse the same
    set — a kiosk that accepts what the server rejects fills IndexedDB with
    intakes that will fail at sync, hours later, with the patient long gone."""
    bad: list[Any] = [None]
    if node.type is NodeType.SINGLE:
        bad += ["not_an_option", 42, ["chest"]]
    elif node.type in (NodeType.MULTI, NodeType.BODY_MAP):
        bad += ["chest", ["not_an_option"], 42]
    elif node.type in (NodeType.SCALE, NodeType.NUMBER):
        bad += ["7", True]
        if node.min is not None:
            bad.append(node.min - 1)
        if node.max is not None:
            bad.append(node.max + 1)
    else:
        bad += ["", "   ", 42]
    return bad


def _snapshot(walk: Walk) -> dict[str, Any]:
    """Everything the walker decides, as the TS side will report it."""
    current = walk.current
    return {
        "current": current.id if current else None,
        "complete": walk.is_complete,
        "path": list(walk.path()),
        "values": walk.values(),
        "answers": sorted(walk.answers),
        "red_flags": [
            {"id": hit.id, "severity": str(hit.severity), "source_node": hit.source_node}
            for hit in walk.red_flags()
        ],
        "priority": str(walk.priority()),
    }


def _record_walk(
    tree: Tree,
    rng: random.Random,
    thresholds: dict[str, set[float]],
    *,
    stop_early: bool,
    amend: bool,
    cycle: int | None,
) -> dict[str, Any]:
    """Drive one walk, recording a snapshot after every step.

    `cycle` makes the choice systematic rather than random: walk *n* answers each
    node with `choices[(n + depth) % len(choices)]`. Random walks alone leave
    coverage to luck, and the values that matter (the exact rule thresholds) are
    a small fraction of a range node's choices — so the boundary case would be
    sampled rarely or never, and the suite would pass on a real divergence.
    Cycling guarantees every choice of every node on the path is recorded
    eventually, and keeps the file deterministic.
    """
    walk = Walk(tree)
    steps: list[dict[str, Any]] = []

    while (current := walk.current) is not None:
        choices = _answer_choices(current, thresholds)
        if not choices:  # pragma: no cover - every node type yields choices
            break
        value = (
            choices[(cycle + len(steps)) % len(choices)]
            if cycle is not None
            else rng.choice(choices)
        )
        walk.save(current.id, value, at=FIXED_AT)
        steps.append({"node_id": current.id, "value": value, "after": _snapshot(walk)})
        if stop_early and len(steps) >= 2 and rng.random() < 0.5:
            break

    amendment: dict[str, Any] | None = None
    if amend and steps:
        # Re-answer the first node with a *different* value where one exists: the
        # branch moves and `_prune` must drop the stranded answers.
        first = tree.node(steps[0]["node_id"])
        alternatives = [c for c in _answer_choices(first, thresholds) if c != steps[0]["value"]]
        if alternatives:
            value = rng.choice(alternatives)
            walk.save(first.id, value, at=FIXED_AT)
            amendment = {"node_id": first.id, "value": value, "after": _snapshot(walk)}

    return {
        "initial": _snapshot(Walk(tree)),
        "steps": steps,
        "amendment": amendment,
        "answers_json": _strip_at(walk.to_json()),
    }


def _strip_at(answers: dict[str, Any]) -> dict[str, Any]:
    """`at` is a timestamp, not a decision — the walkers need not agree on its
    formatting (Python emits +00:00, JS emits Z), only on everything else."""
    return {
        node_id: {key: value for key, value in answer.items() if key != "at"}
        for node_id, answer in answers.items()
    }


def _record_rejections(
    tree: Tree, rng: random.Random, thresholds: dict[str, set[float]]
) -> list[dict[str, Any]]:
    """For each node reachable on one sampled walk, the answers it must refuse."""
    walk = Walk(tree)
    rejections: list[dict[str, Any]] = []

    while (current := walk.current) is not None:
        for bad in _bad_answers(current):
            try:
                Walk(tree, walk.answers).save(current.id, bad)
            except AnswerError:
                rejections.append(
                    {"answers": _strip_at(walk.to_json()), "node_id": current.id, "value": bad}
                )
            else:  # pragma: no cover - a value we expect refused was accepted
                raise AssertionError(
                    f"{tree.ref}: node {current.id!r} accepted {bad!r}; "
                    "the fixture claims it is invalid"
                )
        walk.save(current.id, rng.choice(_answer_choices(current, thresholds)))

    # Answering a node that is not current and not already answered.
    finished = Walk(tree, walk.answers)
    off_path = [
        node_id
        for node_id in tree.nodes
        if node_id not in finished.answers and node_id not in finished.path()
    ]
    for node_id in off_path[:2]:
        rejections.append(
            {
                "answers": _strip_at(finished.to_json()),
                "node_id": node_id,
                "value": _answer_choices(tree.node(node_id), thresholds)[0],
                "reason": "not the current question",
            }
        )
    return rejections


def _trees() -> list[Tree]:
    """Every tree the fixture covers: the authored ones, plus the synthetic
    op-coverage tree (the real trees do not use the whole rule language)."""
    paths = sorted(TREES_DIR.glob("*.json"))
    if not paths:
        raise SystemExit(f"no trees in {TREES_DIR}")
    trees = [parse(json.loads(path.read_text())) for path in paths]
    trees.append(parse(_coverage_tree()))
    return trees


def build() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for tree in _trees():
        rng = random.Random(f"{SEED}:{tree.key}")
        thresholds = _thresholds(tree)
        walks = [
            _record_walk(
                tree,
                rng,
                thresholds,
                stop_early=index % 3 == 2,
                amend=index % 2 == 1,
                # The first CYCLING_WALKS are systematic (every choice of every
                # node on the path, including the rule thresholds); the rest are
                # random, which mixes branch combinations the cycle would not.
                cycle=index if index < CYCLING_WALKS else None,
            )
            for index in range(WALKS_PER_TREE)
        ]
        cases.append(
            {
                "tree": tree.to_json(),
                "ref": tree.ref,
                "walks": walks,
                "rejections": _record_rejections(
                    tree, random.Random(f"{SEED}:reject:{tree.key}"), thresholds
                ),
            }
        )

    return {
        "version": FIXTURE_VERSION,
        "seed": SEED,
        "generated_by": "backend/app/tree_fixtures.py (make tree-fixtures)",
        "note": (
            "Golden traces from the Python walker. Do not hand-edit: regenerate "
            "with `make tree-fixtures` when walker.py or rules.py changes."
        ),
        "cases": cases,
    }


def _render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _summary(payload: dict[str, Any]) -> str:
    steps = sum(len(walk["steps"]) for case in payload["cases"] for walk in case["walks"])
    rejections = sum(len(case["rejections"]) for case in payload["cases"])
    return f"{len(payload['cases'])} trees, {steps} recorded steps, {rejections} rejections"


def main(argv: list[str] | None = None) -> int:
    check = "--check" in (argv if argv is not None else sys.argv[1:])
    rendered = _render(build())

    if check:
        # Deliberately compares against the file on disk, not against git: the
        # question is "do the golden traces describe the walker as it is right
        # now", which is true or false regardless of what has been committed. A
        # git-based check would cry stale at a developer who has regenerated but
        # not yet committed, and stay silent in a fresh checkout where the file
        # is untracked.
        current = OUT_PATH.read_text() if OUT_PATH.exists() else ""
        if current == rendered:
            return 0
        print(
            f"ERROR: {OUT_PATH.relative_to(REPO)} is stale.\n\n"
            "The golden traces no longer match what the Python walker does, so the\n"
            "offline TS walker is being checked against behaviour that no longer\n"
            "exists. Someone changed app/trees/ or seeds/trees/ without regenerating.\n\n"
            "  make tree-fixtures   # then re-run the conformance suite and commit\n",
            file=sys.stderr,
        )
        return 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(rendered)
    print(f"wrote {OUT_PATH.relative_to(REPO)}: {_summary(build())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
