"""The question-tree schema (doc 03 §3) and the validator that guards it.

> "Trees are JSONB data with this node schema" — doc 03 §3

A tree is the clinical content of an intake: what gets asked, in what order, and
what is alarming. It is data so that an oncologist can review it (S21) and an
admin can edit and publish it without a deploy (S18). This module gives that data
a parsed, typed shape and a validator strict enough that "it published" means "it
is safe to ask a patient".

## What the validator is for

Every check here is one a human would otherwise have to catch by reading JSON.
The ones that matter clinically:

- **Unreachable nodes are rejected.** Dead content reads as reviewed and asked,
  but no patient ever sees it. An oncologist signing off a tree must be signing
  off the questions that actually get asked.
- **Cycles are rejected.** The walker's position is derived from the answers
  (see `walker`), so a loop is not a repeat question — it is a tree with no end.
- **Every declared language must be complete.** Doc 07 §4 makes this a gate. A
  node missing its `hi` text is a patient staring at English on a kiosk in Alwar.
- **Red-flag rules are type-checked against the nodes they read** (see
  `rules.validate`) — a rule that can never fire is worse than no rule.
- **Max 5 options on a tap-to-answer node** (doc 03 §1a: "Max 3–5 options/screen").
  This is a UI law enforced at authoring time because the kiosk cannot enforce it
  at 9am with a queue of forty.

## Language lives inside the node, not in a column

Doc 03 §3's node carries `text:{en,hi,mr,te}` — every language in one tree. That
is a deliberate choice over one row per language:

- `Intake.lang` is per-intake and doc 03 §1 makes language **switchable at any
  time**. Embedded text makes that a re-render of the same node id; per-language
  rows would make it a mid-session swap onto a different tree that has to happen
  to share node ids and branching.
- Branching and red flags are then single-sourced. Four language rows means four
  copies of `red_flag_if` that can drift apart, and a clinical sign-off (S21)
  that is only true of the copy the reviewer happened to read.
- S13 (mr/te) becomes additive: fill in text keys, touch no structure.

`question_trees.lang` (doc 02 §4) is the casualty of this and is dropped in the
S4 migration. See HANDOFF — it wants ratification the way `PriceUnit.CHAR` did.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.models.enums import Lang, Priority
from app.trees import rules as rule_lang

#: Node and tree ids: lowercase, dotted or underscored. They appear in
#: `Intake.answers` JSONB keys, in prompts, and in S18's editor URLs — so they are
#: an interface, and a rename is a migration, not a tidy-up.
ID_PATTERN = re.compile(r"^[a-z0-9]+([._][a-z0-9]+)*$")
KEY_PATTERN = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")

#: doc 03 §1a — "Max 3–5 options/screen". body_map is exempt: it is a picture, not
#: a list of buttons, and a torso has more than five places to hurt.
MAX_OPTIONS = 5

_TREE_KEYS = {
    "key",
    "version",
    "department",
    "title",
    "languages",
    "root",
    "nodes",
    "red_flags",
}
_NODE_KEYS = {
    "id",
    "type",
    "text",
    "audio",
    "options",
    "next",
    "min",
    "max",
    "unit",
    "adaptive_hints",
    "adaptive",
    "red_flag_if",
    "red_flag",
}
_OPTION_KEYS = {"id", "text", "icon", "flag"}
_FLAG_KEYS = {"id", "severity", "when", "label", "instruction"}
#: What a *tree-level* red flag may carry. `source_node` is not authored by hand —
#: `parse` stamps it on flags desugared from a node — but `Tree.to_json` emits it,
#: and `parse(tree.to_json())` must round-trip, so the canonical form has to be
#: re-readable. Node-level sugar still validates against `_FLAG_KEYS`, which keeps
#: `source_node` un-authorable where it would be a lie.
_CANONICAL_FLAG_KEYS = _FLAG_KEYS | {"source_node"}

#: Answers that are a *list* of option ids cannot pick a single branch, so these
#: node types route by `next.default` only.
_LIST_ANSWER_TYPES = frozenset({"multi", "body_map"})


class TreeError(ValueError):
    """A tree that must not be asked to a patient."""


class NodeType(StrEnum):
    SINGLE = "single"
    MULTI = "multi"
    SCALE = "scale"
    NUMBER = "number"
    BODY_MAP = "body_map"
    FREE_VOICE = "free_voice"

    @property
    def wants_options(self) -> bool:
        return self in {NodeType.SINGLE, NodeType.MULTI, NodeType.BODY_MAP}

    @property
    def wants_range(self) -> bool:
        return self in {NodeType.SCALE, NodeType.NUMBER}


@dataclass(frozen=True, slots=True)
class Option:
    """One tap target. `flag: true` marks an option that is itself alarming
    ("blood in the vomit") — the parser turns it into a real red-flag rule, so it
    is never just a decorative boolean."""

    id: str
    text: Mapping[str, str]
    icon: str | None = None
    flag: bool = False

    def to_json(self) -> dict[str, Any]:
        # `flag` is deliberately absent: `parse` has already turned it into a real
        # RedFlagSpec in `tree.red_flags`, and a consumer of the canonical form
        # must read flags from there only. Emitting it would invite a second,
        # divergent way to decide a red flag.
        return {"id": self.id, "text": dict(self.text), "icon": self.icon}


@dataclass(frozen=True, slots=True)
class RedFlagSpec:
    """One named, reviewable clinical rule.

    `instruction` is spoken to the patient verbatim when the flag fires — the
    summarize prompt repeats flags and never invents one (doc 02 §5), and the
    model is told not to substitute its own clinical opinion (see
    `CHECK_RED_FLAGS` in `app.prompts.tools`). So this text is the actual thing a
    frightened patient hears, in their language, and an oncologist owns its
    wording.
    """

    id: str
    severity: Priority
    when: Mapping[str, Any]
    label: Mapping[str, str]
    instruction: Mapping[str, str]
    #: Set when the flag was authored as node-level sugar (`red_flag_if` /
    #: `flag: true`), for error messages and S18's editor.
    source_node: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": str(self.severity),
            "when": _plain(self.when),
            "label": dict(self.label),
            "instruction": dict(self.instruction),
            "source_node": self.source_node,
        }


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    type: NodeType
    text: Mapping[str, str]
    options: tuple[Option, ...] = ()
    audio: Mapping[str, str] = field(default_factory=dict)
    #: option id (single only) or "default" → next node id, or None to end.
    next: Mapping[str, str | None] = field(default_factory=dict)
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    #: Free text for the conversational tiers: "probe radiation to back". Never
    #: shown to the patient, never affects branching — a hint, not a question.
    adaptive_hints: str | None = None
    #: Opt-in adaptive questioning (S-ADAPT.2, doc 11 §3). When true, the answer
    #: interpreter may ask ONE bounded clarifying sub-question not in the tree to
    #: disambiguate before mapping — the tree-authority-bending part, so it is
    #: per-node and S18-editable. Default false ⇒ the node behaves exactly as V1
    #: (map, or clarify only when the answer is too vague).
    adaptive: bool = False

    def option(self, option_id: str) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)

    def option_ids(self) -> tuple[str, ...]:
        return tuple(o.id for o in self.options)

    def ask(self, lang: Lang | str) -> str:
        """The question, in the patient's language. Falls back to English rather
        than raising: a validated tree has every declared language, so a miss here
        means an intake requested a language the tree never claimed — better to
        ask in English than to drop the patient mid-intake."""
        return self.text.get(str(lang)) or self.text.get(Lang.EN, "")

    def audio_clip(self, lang: Lang | str) -> str | None:
        """The pre-recorded clip for V3 (doc 03 §1a). None means "no recording
        yet" — S7/S21 fill these; TTS covers the gap until then."""
        return self.audio.get(str(lang))

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "text": dict(self.text),
            "audio": dict(self.audio),
            "options": [option.to_json() for option in self.options],
            "next": dict(self.next),
            "min": self.min,
            "max": self.max,
            "unit": self.unit,
            "adaptive_hints": self.adaptive_hints,
            "adaptive": self.adaptive,
        }


@dataclass(frozen=True, slots=True)
class Tree:
    key: str
    version: int
    title: Mapping[str, str]
    languages: tuple[Lang, ...]
    root: str
    nodes: Mapping[str, Node]
    red_flags: tuple[RedFlagSpec, ...] = ()
    #: Department code (`departments.code`, e.g. "MEDONC"). None = not tied to a
    #: department; the routing trees all are.
    department: str | None = None

    def node(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise TreeError(f"tree {self.ref}: no node {node_id!r}") from None

    @property
    def ref(self) -> str:
        """`key@vN` — what gets logged onto an intake so an answer set can be read
        back against the exact tree that produced it."""
        return f"{self.key}@v{self.version}"

    def node_kinds(self) -> dict[str, str]:
        return {node_id: node.type.value for node_id, node in self.nodes.items()}

    def speaks(self, lang: Lang | str) -> bool:
        return str(lang) in {str(item) for item in self.languages}

    def to_json(self) -> dict[str, Any]:
        """The **canonical** form of a parsed tree — the offline kiosk's wire shape.

        Deliberately not the authored form (`seeds/trees/*.json`). `parse` has
        already desugared `red_flag_if` / `flag: true` into real `red_flags` and
        proved the tree acyclic, reachable, language-complete and rule-type-safe.
        Shipping *this* is what lets the offline TS walker (S7) be a walker only:
        it never re-implements the sugar or the validator, so there is exactly one
        place that decides what a tree means. A client handed the authored form
        would have to re-derive both, and would drift.

        Round-trips: `parse(tree.to_json())` returns an equal tree (tested).
        """
        return {
            "key": self.key,
            "version": self.version,
            "department": self.department,
            "title": dict(self.title),
            "languages": [str(lang) for lang in self.languages],
            "root": self.root,
            "nodes": [node.to_json() for node in self.nodes.values()],
            "red_flags": [flag.to_json() for flag in self.red_flags],
        }


def _plain(value: Any) -> Any:
    """A rule expression as plain JSON containers (it is stored as nested
    Mappings/Sequences; `json.dumps` will not take a `MappingProxy`)."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    return value


def parse(data: Any) -> Tree:
    """Parse and fully validate a tree. Raises `TreeError` on anything doubtful.

    This is the only way to build a `Tree`, so an in-memory `Tree` is by
    construction one that passed every check — the walker never re-validates.
    """
    if not isinstance(data, Mapping):
        raise TreeError(f"tree must be an object, got {type(data).__name__}")

    if unknown := set(data) - _TREE_KEYS:
        raise TreeError(f"unexpected tree keys: {sorted(unknown)}")

    key = data.get("key")
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise TreeError(f"tree key must match {KEY_PATTERN.pattern!r}, got {key!r}")

    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise TreeError(f"tree {key}: version must be an integer >= 1, got {version!r}")

    languages = _parse_languages(data.get("languages"), key)
    title = _localized(data.get("title"), languages, where=f"tree {key}: title")

    department = data.get("department")
    if department is not None and (not isinstance(department, str) or not department):
        raise TreeError(f"tree {key}: department must be a department code or omitted")

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)) or not raw_nodes:
        raise TreeError(f"tree {key}: 'nodes' must be a non-empty list")

    nodes: dict[str, Node] = {}
    node_flags: list[RedFlagSpec] = []
    for index, raw in enumerate(raw_nodes):
        node, flags = _parse_node(raw, languages, where=f"tree {key}: nodes[{index}]")
        if node.id in nodes:
            raise TreeError(f"tree {key}: duplicate node id {node.id!r}")
        nodes[node.id] = node
        node_flags.extend(flags)

    root = data.get("root")
    if not isinstance(root, str) or root not in nodes:
        raise TreeError(f"tree {key}: root {root!r} is not one of the tree's nodes")

    _validate_edges(key, nodes)
    _validate_acyclic_and_reachable(key, nodes, root)

    kinds = {node_id: node.type.value for node_id, node in nodes.items()}
    tree_flags = _parse_red_flags(data.get("red_flags"), languages, kinds, where=f"tree {key}")

    flags = tuple(node_flags) + tree_flags
    seen: set[str] = set()
    for flag in flags:
        if flag.id in seen:
            raise TreeError(f"tree {key}: duplicate red flag id {flag.id!r}")
        seen.add(flag.id)
        try:
            rule_lang.validate(flag.when, kinds, where=f"tree {key}: red_flag {flag.id!r}")
        except rule_lang.RuleError as exc:
            # `rules` is standalone (schema imports it, not the reverse) and raises
            # its own error. Callers of `parse` should not have to know that: a
            # malformed rule is a malformed tree.
            raise TreeError(str(exc)) from exc

    return Tree(
        key=key,
        version=version,
        title=title,
        languages=languages,
        root=root,
        nodes=nodes,
        red_flags=flags,
        department=department,
    )


def _parse_languages(value: Any, key: str) -> tuple[Lang, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise TreeError(f"tree {key}: 'languages' must be a non-empty list")
    langs: list[Lang] = []
    for item in value:
        try:
            lang = Lang(item)
        except ValueError:
            raise TreeError(
                f"tree {key}: unknown language {item!r}; expected one of "
                f"{[str(item) for item in Lang]}"
            ) from None
        if lang in langs:
            raise TreeError(f"tree {key}: language {item!r} listed twice")
        langs.append(lang)
    if Lang.EN not in langs:
        # The doctor-facing summary is English (doc 03 §4) and every error message
        # and eval fixture leans on it; an en-less tree has no fallback text.
        raise TreeError(f"tree {key}: 'languages' must include 'en'")
    return tuple(langs)


def _localized(value: Any, languages: Sequence[Lang], *, where: str) -> dict[str, str]:
    """Text in every language the tree declares — no more, no fewer.

    Rejecting *extra* languages matters as much as rejecting missing ones: a tree
    declaring [en, hi] with a stray `te` key means someone translated content that
    will never be shown, and believes Telugu is live when it is not (S13).
    """
    if not isinstance(value, Mapping):
        raise TreeError(f"{where}: must be an object of language → text")
    wanted = {str(lang) for lang in languages}
    got = set(value)
    if missing := wanted - got:
        raise TreeError(f"{where}: missing text for {sorted(missing)}")
    if extra := got - wanted:
        raise TreeError(
            f"{where}: text for {sorted(extra)} but the tree only declares {sorted(wanted)}"
        )
    for lang, text in value.items():
        if not isinstance(text, str) or not text.strip():
            raise TreeError(f"{where}: {lang} text is empty")
    return dict(value)


def _parse_node(
    raw: Any, languages: Sequence[Lang], *, where: str
) -> tuple[Node, list[RedFlagSpec]]:
    if not isinstance(raw, Mapping):
        raise TreeError(f"{where}: node must be an object")
    if unknown := set(raw) - _NODE_KEYS:
        raise TreeError(f"{where}: unexpected node keys: {sorted(unknown)}")

    node_id = raw.get("id")
    if not isinstance(node_id, str) or not ID_PATTERN.match(node_id):
        raise TreeError(f"{where}: node id must match {ID_PATTERN.pattern!r}, got {node_id!r}")
    where = f"{where} ({node_id})"

    try:
        node_type = NodeType(raw.get("type"))
    except ValueError:
        raise TreeError(
            f"{where}: unknown node type {raw.get('type')!r}; expected one of "
            f"{[str(item) for item in NodeType]}"
        ) from None

    text = _localized(raw.get("text"), languages, where=f"{where}: text")
    audio = _parse_audio(raw.get("audio"), languages, where=where)
    options = _parse_options(raw.get("options"), node_type, languages, where=where)
    minimum, maximum = _parse_range(raw, node_type, where=where)
    next_map = _parse_next(raw.get("next"), node_type, options, where=where)

    unit = raw.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise TreeError(f"{where}: unit must be a string")
    hints = raw.get("adaptive_hints")
    if hints is not None and not isinstance(hints, str):
        raise TreeError(f"{where}: adaptive_hints must be a string")
    adaptive = raw.get("adaptive", False)
    if not isinstance(adaptive, bool):
        raise TreeError(f"{where}: adaptive must be a boolean")

    node = Node(
        id=node_id,
        type=node_type,
        text=text,
        options=options,
        audio=audio,
        next=next_map,
        min=minimum,
        max=maximum,
        unit=unit,
        adaptive_hints=hints,
        adaptive=adaptive,
    )
    return node, _parse_node_flags(raw, node, languages, where=where)


def _parse_audio(value: Any, languages: Sequence[Lang], *, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TreeError(f"{where}: audio must be an object of language → filename")
    declared = {str(lang) for lang in languages}
    for lang, clip in value.items():
        if lang not in declared:
            raise TreeError(f"{where}: audio for undeclared language {lang!r}")
        if not isinstance(clip, str) or not clip.strip():
            raise TreeError(f"{where}: audio filename for {lang!r} is empty")
    return dict(value)


def _parse_options(
    value: Any, node_type: NodeType, languages: Sequence[Lang], *, where: str
) -> tuple[Option, ...]:
    if not node_type.wants_options:
        if value:
            raise TreeError(f"{where}: a {node_type} node takes no options")
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise TreeError(f"{where}: a {node_type} node needs a non-empty 'options' list")
    if node_type is not NodeType.BODY_MAP and len(value) > MAX_OPTIONS:
        raise TreeError(
            f"{where}: {len(value)} options exceeds the {MAX_OPTIONS}-option limit "
            "(doc 03 §1a: max 3–5 options/screen). Split the question."
        )

    options: list[Option] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TreeError(f"{where}: options[{index}] must be an object")
        if unknown := set(raw) - _OPTION_KEYS:
            raise TreeError(f"{where}: options[{index}] unexpected keys: {sorted(unknown)}")
        option_id = raw.get("id")
        if not isinstance(option_id, str) or not ID_PATTERN.match(option_id):
            raise TreeError(
                f"{where}: options[{index}] id must match {ID_PATTERN.pattern!r}, got {option_id!r}"
            )
        if any(o.id == option_id for o in options):
            raise TreeError(f"{where}: duplicate option id {option_id!r}")
        icon = raw.get("icon")
        if icon is not None and not isinstance(icon, str):
            raise TreeError(f"{where}: options[{index}] icon must be a string")
        flag = raw.get("flag", False)
        if not isinstance(flag, bool):
            raise TreeError(f"{where}: options[{index}] flag must be true/false")
        options.append(
            Option(
                id=option_id,
                text=_localized(
                    raw.get("text"), languages, where=f"{where}: options[{index}] text"
                ),
                icon=icon,
                flag=flag,
            )
        )
    return tuple(options)


def _parse_range(
    raw: Mapping[str, Any], node_type: NodeType, *, where: str
) -> tuple[float | None, float | None]:
    minimum, maximum = raw.get("min"), raw.get("max")
    if not node_type.wants_range:
        if minimum is not None or maximum is not None:
            raise TreeError(f"{where}: min/max only apply to scale and number nodes")
        return None, None
    numbers: list[float | None] = []
    for label, value in (("min", minimum), ("max", maximum)):
        if value is None:
            numbers.append(None)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TreeError(f"{where}: {label} must be a number, got {value!r}")
        numbers.append(float(value))
    low, high = numbers
    if node_type is NodeType.SCALE and (low is None or high is None):
        # An ungrounded scale is uninterpretable later: "6" means nothing without
        # knowing it was out of 10, and the doctor screen renders it as severity.
        raise TreeError(f"{where}: a scale node needs both min and max")
    if low is not None and high is not None and low >= high:
        raise TreeError(f"{where}: min ({low}) must be less than max ({high})")
    return low, high


def _parse_next(
    value: Any, node_type: NodeType, options: Sequence[Option], *, where: str
) -> dict[str, str | None]:
    """`{"default": "next.node", "<option_id>": "other.node"}`.

    Option-keyed branching is `single`-only. A multi-select or body_map answer is
    a *list* — two selected options would name two branches, and picking one is
    the kind of quiet non-determinism that makes an intake unreproducible. Those
    types branch on `default` and let red flags carry the clinical consequence.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TreeError(f"{where}: next must be an object")

    allowed = {"default"}
    if node_type is NodeType.SINGLE:
        allowed |= {o.id for o in options}

    for edge, target in value.items():
        if edge not in allowed:
            if node_type in _LIST_ANSWER_TYPES and any(o.id == edge for o in options):
                raise TreeError(
                    f"{where}: a {node_type} answer is a list and cannot pick a branch; "
                    f"route with next.default and use a red flag for {edge!r}"
                )
            raise TreeError(
                f"{where}: next key {edge!r} is neither 'default' nor an option of this node "
                f"(allowed: {sorted(allowed)})"
            )
        if target is not None and (not isinstance(target, str) or not target):
            raise TreeError(f"{where}: next[{edge!r}] must be a node id or null")
    return dict(value)


def _parse_node_flags(
    raw: Mapping[str, Any], node: Node, languages: Sequence[Lang], *, where: str
) -> list[RedFlagSpec]:
    """Expand node-level sugar (`red_flag_if`, `flag: true`) into real flags.

    Doc 03 §3 puts `red_flag_if` on the node, which is the ergonomic place to
    author it. But a flag also needs a name, a severity and the words to say —
    so the node carries a `red_flag` block, and everything normalises into the
    same list the tree-level `red_flags` produce. One evaluator, one reviewable
    bank, two places to write it.
    """
    condition = raw.get("red_flag_if")
    flagged = [o.id for o in node.options if o.flag]
    meta = raw.get("red_flag")

    if condition is None and not flagged:
        if meta is not None:
            raise TreeError(
                f"{where}: 'red_flag' is set but nothing raises it — add 'red_flag_if' "
                "or mark an option with flag: true"
            )
        return []

    if not isinstance(meta, Mapping):
        raise TreeError(
            f"{where}: this node raises a red flag but has no 'red_flag' block "
            "(needs id, severity, label, instruction)"
        )
    if unknown := set(meta) - (_FLAG_KEYS - {"when"}):
        raise TreeError(f"{where}: unexpected red_flag keys: {sorted(unknown)}")

    clauses: list[Mapping[str, Any]] = []
    if flagged:
        # "Selecting any of these is itself the flag."
        op = "contains" if node.type in _LIST_ANSWER_TYPES else "in"
        if node.type in _LIST_ANSWER_TYPES:
            clauses.extend({"node": node.id, "op": op, "value": item} for item in flagged)
        else:
            clauses.append({"node": node.id, "op": "in", "value": list(flagged)})
    if condition is not None:
        clauses.append(condition)

    when = clauses[0] if len(clauses) == 1 else {"op": "or", "rules": clauses}
    return [_build_flag(meta, when, languages, where=where, source_node=node.id)]


def _parse_red_flags(
    value: Any, languages: Sequence[Lang], kinds: Mapping[str, str], *, where: str
) -> tuple[RedFlagSpec, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TreeError(f"{where}: 'red_flags' must be a list")
    flags: list[RedFlagSpec] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TreeError(f"{where}: red_flags[{index}] must be an object")
        if unknown := set(raw) - _CANONICAL_FLAG_KEYS:
            raise TreeError(f"{where}: red_flags[{index}] unexpected keys: {sorted(unknown)}")
        if "when" not in raw:
            raise TreeError(f"{where}: red_flags[{index}] needs a 'when' rule")
        source_node = raw.get("source_node")
        if source_node is not None and (
            not isinstance(source_node, str) or source_node not in kinds
        ):
            raise TreeError(
                f"{where}: red_flags[{index}] source_node {source_node!r} "
                "is not a node of this tree"
            )
        flags.append(
            _build_flag(
                raw,
                raw["when"],
                languages,
                where=f"{where}: red_flags[{index}]",
                source_node=source_node,
            )
        )
    return tuple(flags)


def _build_flag(
    meta: Mapping[str, Any],
    when: Any,
    languages: Sequence[Lang],
    *,
    where: str,
    source_node: str | None = None,
) -> RedFlagSpec:
    flag_id = meta.get("id")
    if not isinstance(flag_id, str) or not ID_PATTERN.match(flag_id):
        raise TreeError(f"{where}: red flag id must match {ID_PATTERN.pattern!r}, got {flag_id!r}")

    raw_severity = meta.get("severity", Priority.URGENT.value)
    try:
        severity = Priority(raw_severity)
    except ValueError:
        raise TreeError(f"{where}: unknown severity {raw_severity!r}") from None
    if severity is Priority.ROUTINE:
        # A "routine red flag" is a contradiction that would quietly do nothing:
        # doc 03 §1 says a red flag sets priority=urgent and alerts a nurse.
        raise TreeError(
            f"{where}: severity 'routine' is not a red flag — use 'semi' or 'urgent', "
            "or delete the rule"
        )

    return RedFlagSpec(
        id=flag_id,
        severity=severity,
        when=when,
        label=_localized(meta.get("label"), languages, where=f"{where}: label"),
        instruction=_localized(meta.get("instruction"), languages, where=f"{where}: instruction"),
        source_node=source_node,
    )


def _validate_edges(key: str, nodes: Mapping[str, Node]) -> None:
    for node in nodes.values():
        for edge, target in node.next.items():
            if target is not None and target not in nodes:
                raise TreeError(
                    f"tree {key}: node {node.id!r} next[{edge!r}] points at unknown node {target!r}"
                )
            if target == node.id:
                raise TreeError(f"tree {key}: node {node.id!r} points at itself")


def _validate_acyclic_and_reachable(key: str, nodes: Mapping[str, Node], root: str) -> None:
    """Depth-first from the root: no back edges, and nothing left over.

    Both failures are silent in production if allowed through — a cycle makes an
    intake that never reaches `finish_and_summarize`, and an orphan makes a
    question an oncologist approved that no patient is ever asked.
    """
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def walk(node_id: str) -> None:
        if node_id in done:
            return
        if node_id in visiting:
            cycle = " → ".join(stack[stack.index(node_id) :] + [node_id])
            raise TreeError(f"tree {key}: cycle in the tree: {cycle}")
        visiting.add(node_id)
        stack.append(node_id)
        for target in nodes[node_id].next.values():
            if target is not None:
                walk(target)
        stack.pop()
        visiting.discard(node_id)
        done.add(node_id)

    walk(root)
    if orphans := set(nodes) - done:
        raise TreeError(
            f"tree {key}: {len(orphans)} node(s) unreachable from root {root!r}: {sorted(orphans)}"
        )
