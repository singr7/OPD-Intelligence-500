"""Protocol templates — the clinical skeleton of a check-in plan (doc 03 §9/§10).

> "On dictation sign: `treatment_events` + protocol templates (per regimen
>  family, admin-editable) → LLM personalizes a check-in plan" — doc 03 §9

`seeds/protocols.json` holds six regimen families (platinum, taxane,
anthracycline, radiotherapy, post-op, palliative). A **protocol** says which days
after treatment a patient is asked something and which question set is asked on
each; a **question set** says what the questions are, in four languages, and
carries the deterministic rules that turn the answers into green / amber / red.

## Why this is data, and why the model may not touch it

Same argument as the tree bank (doc 02 §4): *which day a chemotherapy patient is
asked about fever* is clinical policy an oncologist signs off, not a thing a
language model should be deciding per patient. So the LLM personalisation step
(`app.checkins.plan`) may only rewrite the covering **message** — the days, the
question sets and every grading rule come from here unchanged, and
`plan.draft_plan` re-reads them rather than trusting what came back from the
model.

## Grading reuses the red-flag rule language

A grading `when` is an `app.trees.rules` expression over the question ids in its
own set — the exact language, the exact evaluator, validated against the question
*types* at load. That is deliberate: the property S4 bought ("no model ever
decides a red flag, on any tier") is the same property a check-in grade needs,
and a second rule dialect would be a second thing to review. It also means
`free_voice` answers cannot be matched by a rule, so "no blood in my stool"
cannot fire a bleeding grade off the transcriber's punctuation. Free text is
graded, if at all, by the bounded LLM assist in `app.checkins.grading`, which may
only *raise* a grade a rule already allows.

## Grades are two rules and a default

A rule fires `red` or `amber`. **Green is the absence of a fired rule**, never a
rule of its own — so a question set with a missing answer grades green only
because nothing alarming was said, and adding a question can never silently turn
an existing green into a red for a patient who was never asked.

## Text obligations

Every patient-facing string (question prompts, option labels, protocol and set
titles) carries all four pilot languages; `app.lang_qa` checks the bank the same
way it checks the trees. Grading `reason` is **English clinical text a nurse
reads on the review queue** — the same stance as the queue's priority chips, and
deliberately not shown to a patient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from app.languages import PILOT_LANGUAGES
from app.models.enums import CheckinGrade, Lang
from app.trees import rules as rule_lang

SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds"
PROTOCOLS_PATH = SEEDS_DIR / "protocols.json"

#: Question types a check-in may use. A strict subset of the tree's `NodeType`
#: (no `body_map` — a body map is a kiosk touch interaction, and a check-in is
#: answered on a phone keypad, three WhatsApp buttons or an SMS reply).
QUESTION_TYPES = frozenset({"single", "number", "scale", "free_voice"})

#: Types whose answers a grading rule may address, by the option/number split.
#: Passed straight to `app.trees.rules.validate`, which type-checks each leaf.
_TYPES_WITH_OPTIONS = frozenset({"single"})


class ProtocolError(ValueError):
    """A protocol bank that cannot be trusted to mean what it says.

    Raised at load time only, exactly like `RuleError` — a live check-in must
    never fail on a patient because the bank is malformed, and every entry point
    loads through `get_bank()`, which validates once and caches.
    """


@dataclass(frozen=True, slots=True)
class Option:
    id: str
    label: dict[Lang, str]


@dataclass(frozen=True, slots=True)
class Question:
    """One thing a patient is asked. Flat — a check-in is three or four questions
    with no branching, unlike an intake walk: a patient answering an SMS at D+2
    gets one message, and a walk over SMS would be five."""

    id: str
    type: str
    prompt: dict[Lang, str]
    options: tuple[Option, ...] = ()
    min: float | None = None
    max: float | None = None

    def text(self, lang: Lang) -> str:
        return self.prompt.get(lang) or self.prompt[Lang.EN]

    def option_labels(self, lang: Lang) -> list[tuple[str, str]]:
        return [(o.id, o.label.get(lang) or o.label[Lang.EN]) for o in self.options]

    def to_json(self) -> dict[str, Any]:
        """The wire/snapshot shape. A `Checkin` freezes the questions it asked
        (`Checkin.asked`) in this shape, so editing the bank can never change what
        a patient was asked last week."""
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "prompt": {str(k): v for k, v in self.prompt.items()},
        }
        if self.options:
            payload["options"] = [
                {"id": o.id, "label": {str(k): v for k, v in o.label.items()}} for o in self.options
            ]
        if self.min is not None:
            payload["min"] = self.min
        if self.max is not None:
            payload["max"] = self.max
        return payload


@dataclass(frozen=True, slots=True)
class GradingRule:
    id: str
    grade: CheckinGrade
    reason: str
    when: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        """The snapshot shape. A `Checkin` freezes the rules it will be graded by
        (`Checkin.grading_rules`) in this shape, so publishing a new bank cannot
        re-grade answers a patient has already given — the same argument as
        `Question.to_json` next door, and it became load-bearing the moment the
        bank became editable from a console (S18-late)."""
        return {
            "id": self.id,
            "grade": str(self.grade),
            "reason": self.reason,
            "when": self.when,
        }


@dataclass(frozen=True, slots=True)
class QuestionSet:
    key: str
    title: dict[Lang, str]
    questions: tuple[Question, ...]
    grading: tuple[GradingRule, ...]

    def question(self, question_id: str) -> Question | None:
        return next((q for q in self.questions if q.id == question_id), None)

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(q.id for q in self.questions)


@dataclass(frozen=True, slots=True)
class ScheduledCheckin:
    """One rung of a protocol: "on day N, ask this set"."""

    day_offset: int
    question_set: str


@dataclass(frozen=True, slots=True)
class Protocol:
    key: str
    label: dict[Lang, str]
    #: Formulary drug classes that put a patient on this protocol.
    drug_classes: frozenset[str]
    #: Lowercase substrings matched against the regimen / diagnosis / advice text.
    keywords: tuple[str, ...]
    #: Which family wins when a note matches two (a carboplatin/paclitaxel
    #: doublet matches both). Highest wins, and the order is a clinical call
    #: authored in the bank: anthracycline (cardiac) over taxane (neuropathy)
    #: over platinum (renal/GI), because that is the order in which missing the
    #: signal is irreversible. `app.checkins.plan` records every family that
    #: matched, so the choice is visible to the doctor approving the plan.
    precedence: int
    #: Days between cycles, for the next-cycle reminders. 0 = not a cycled
    #: regimen (radiotherapy, post-op, palliative), so no cycle reminder is due.
    cycle_days: int
    checkins: tuple[ScheduledCheckin, ...]

    def title(self, lang: Lang) -> str:
        return self.label.get(lang) or self.label[Lang.EN]


@dataclass(frozen=True, slots=True)
class ProtocolBank:
    version: int
    question_sets: dict[str, QuestionSet]
    protocols: dict[str, Protocol]

    def protocol(self, key: str) -> Protocol:
        try:
            return self.protocols[key]
        except KeyError:
            raise ProtocolError(f"no protocol template {key!r}") from None

    def question_set(self, key: str) -> QuestionSet:
        try:
            return self.question_sets[key]
        except KeyError:
            raise ProtocolError(f"no question set {key!r}") from None

    def prompt_payload(self, key: str) -> dict[str, Any]:
        """The skeleton as the personalisation prompt sees it — days and question
        set keys, no grading rules. The model is not shown the rules because it
        has no business reasoning about what would escalate; showing them invites
        a message that pre-empts the nurse ("if your fever is over 38, go to
        casualty"), which the prompt already forbids in words."""
        protocol = self.protocol(key)
        return {
            "key": protocol.key,
            "label": protocol.label[Lang.EN],
            "cycle_days": protocol.cycle_days,
            "checkins": [
                {
                    "day_offset": c.day_offset,
                    "question_set": c.question_set,
                    "asks_about": self.question_set(c.question_set).title[Lang.EN],
                }
                for c in protocol.checkins
            ],
        }


# -- parsing / validation ------------------------------------------------------


def _langs(payload: Any, *, where: str) -> dict[Lang, str]:
    if not isinstance(payload, dict):
        raise ProtocolError(f"{where}: expected an object of language → text")
    out: dict[Lang, str] = {}
    for lang in PILOT_LANGUAGES:
        text = payload.get(str(lang))
        if not isinstance(text, str) or not text.strip():
            raise ProtocolError(f"{where}: missing {lang} text")
        out[lang] = text.strip()
    return out


def _parse_option_sets(payload: Any) -> dict[str, tuple[Option, ...]]:
    if not isinstance(payload, dict):
        raise ProtocolError("option_sets must be an object")
    sets: dict[str, tuple[Option, ...]] = {}
    for key, options in payload.items():
        if not isinstance(options, list) or not options:
            raise ProtocolError(f"option set {key!r}: expected a non-empty list")
        parsed: list[Option] = []
        seen: set[str] = set()
        for i, option in enumerate(options):
            if not isinstance(option, dict) or not isinstance(option.get("id"), str):
                raise ProtocolError(f"option set {key!r}[{i}]: needs a string id")
            oid = option["id"]
            if oid in seen:
                raise ProtocolError(f"option set {key!r}: duplicate option id {oid!r}")
            seen.add(oid)
            parsed.append(Option(id=oid, label=_langs(option.get("label"), where=f"{key}.{oid}")))
        sets[key] = tuple(parsed)
    return sets


def _parse_question(
    payload: Any, *, where: str, option_sets: dict[str, tuple[Option, ...]]
) -> Question:
    if not isinstance(payload, dict):
        raise ProtocolError(f"{where}: expected an object")
    qid = payload.get("id")
    if not isinstance(qid, str) or not qid:
        raise ProtocolError(f"{where}: needs a string id")
    qtype = payload.get("type")
    if qtype not in QUESTION_TYPES:
        raise ProtocolError(f"{where}: type {qtype!r} not one of {sorted(QUESTION_TYPES)}")

    options: tuple[Option, ...] = ()
    if qtype in _TYPES_WITH_OPTIONS:
        ref = payload.get("options")
        if not isinstance(ref, str):
            raise ProtocolError(f"{where}: a {qtype} question needs an option_set name")
        if ref not in option_sets:
            raise ProtocolError(f"{where}: unknown option set {ref!r}")
        options = option_sets[ref]
    elif payload.get("options") is not None:
        raise ProtocolError(f"{where}: a {qtype} question may not carry options")

    low, high = payload.get("min"), payload.get("max")
    for name, value in (("min", low), ("max", high)):
        if value is not None and not isinstance(value, (int, float)):
            raise ProtocolError(f"{where}: {name} must be a number")
    if low is not None and high is not None and low >= high:
        raise ProtocolError(f"{where}: min must be below max")
    if qtype in {"number", "scale"} and (low is None or high is None):
        # An unbounded number is how "37" arrives as a temperature and "370" as
        # a typo nobody catches; the bounds are what let `record_answer` refuse.
        raise ProtocolError(f"{where}: a {qtype} question needs min and max")

    return Question(
        id=qid,
        type=qtype,
        prompt=_langs(payload.get("prompt"), where=f"{where}.prompt"),
        options=options,
        min=low,
        max=high,
    )


def _parse_grading(payload: Any, *, where: str, kinds: dict[str, str]) -> GradingRule:
    if not isinstance(payload, dict):
        raise ProtocolError(f"{where}: expected an object")
    rid = payload.get("id")
    if not isinstance(rid, str) or not rid:
        raise ProtocolError(f"{where}: needs a string id")
    grade = payload.get("grade")
    if grade not in {CheckinGrade.RED.value, CheckinGrade.AMBER.value}:
        # Green is the default, not a rule: see the module docstring.
        raise ProtocolError(f"{where}: grade must be 'red' or 'amber', got {grade!r}")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ProtocolError(f"{where}: needs a 'reason' — the line a nurse reads")
    when = payload.get("when")
    rule_lang.validate(when, kinds, where=f"{where}.when")
    return GradingRule(
        id=rid,
        grade=CheckinGrade(grade),
        reason=reason.strip(),
        when=dict(when),  # type: ignore[arg-type]
    )


def _parse_question_set(
    key: str, payload: Any, *, option_sets: dict[str, tuple[Option, ...]]
) -> QuestionSet:
    if not isinstance(payload, dict):
        raise ProtocolError(f"question set {key!r}: expected an object")
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ProtocolError(f"question set {key!r}: needs a non-empty 'questions' list")

    questions: list[Question] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_questions):
        question = _parse_question(raw, where=f"{key}.questions[{i}]", option_sets=option_sets)
        if question.id in seen:
            raise ProtocolError(f"question set {key!r}: duplicate question id {question.id!r}")
        seen.add(question.id)
        questions.append(question)

    kinds = {q.id: q.type for q in questions}
    raw_grading = payload.get("grading")
    if not isinstance(raw_grading, list) or not raw_grading:
        # A set with no grading is a set that can never escalate — the one
        # failure mode of this whole session that looks like success.
        raise ProtocolError(f"question set {key!r}: needs a non-empty 'grading' list")

    grading: list[GradingRule] = []
    rule_ids: set[str] = set()
    for i, raw in enumerate(raw_grading):
        rule = _parse_grading(raw, where=f"{key}.grading[{i}]", kinds=kinds)
        if rule.id in rule_ids:
            raise ProtocolError(f"question set {key!r}: duplicate grading id {rule.id!r}")
        rule_ids.add(rule.id)
        grading.append(rule)

    return QuestionSet(
        key=key,
        title=_langs(payload.get("title"), where=f"{key}.title"),
        questions=tuple(questions),
        grading=tuple(grading),
    )


def _parse_protocol(key: str, payload: Any, *, sets: dict[str, QuestionSet]) -> Protocol:
    if not isinstance(payload, dict):
        raise ProtocolError(f"protocol {key!r}: expected an object")
    match = payload.get("match")
    if not isinstance(match, dict):
        raise ProtocolError(f"protocol {key!r}: needs a 'match' object")
    classes = match.get("drug_classes") or []
    keywords = match.get("keywords") or []
    if not isinstance(classes, list) or not isinstance(keywords, list):
        raise ProtocolError(f"protocol {key!r}: match.drug_classes/keywords must be lists")
    if not classes and not keywords:
        raise ProtocolError(f"protocol {key!r}: matches nothing — it could never be chosen")
    for word in keywords:
        if not isinstance(word, str) or word != word.lower():
            raise ProtocolError(f"protocol {key!r}: keyword {word!r} must be a lowercase string")

    precedence = payload.get("precedence")
    if not isinstance(precedence, int):
        raise ProtocolError(f"protocol {key!r}: needs an integer 'precedence'")

    cycle_days = payload.get("cycle_days", 0)
    if not isinstance(cycle_days, int) or cycle_days < 0:
        raise ProtocolError(f"protocol {key!r}: cycle_days must be a non-negative integer")

    raw_checkins = payload.get("checkins")
    if not isinstance(raw_checkins, list) or not raw_checkins:
        raise ProtocolError(f"protocol {key!r}: needs a non-empty 'checkins' list")
    checkins: list[ScheduledCheckin] = []
    offsets: set[int] = set()
    for i, raw in enumerate(raw_checkins):
        if not isinstance(raw, dict):
            raise ProtocolError(f"protocol {key!r}.checkins[{i}]: expected an object")
        offset = raw.get("day_offset")
        if not isinstance(offset, int) or offset < 1:
            raise ProtocolError(
                f"protocol {key!r}.checkins[{i}]: day_offset must be a positive integer "
                "(a check-in on the day of treatment is the consult itself)"
            )
        if offset in offsets:
            raise ProtocolError(f"protocol {key!r}: two check-ins on day {offset}")
        offsets.add(offset)
        set_key = raw.get("question_set")
        if set_key not in sets:
            raise ProtocolError(f"protocol {key!r}.checkins[{i}]: unknown question set {set_key!r}")
        checkins.append(ScheduledCheckin(day_offset=offset, question_set=str(set_key)))

    return Protocol(
        key=key,
        label=_langs(payload.get("label"), where=f"protocol {key}.label"),
        drug_classes=frozenset(str(c) for c in classes),
        keywords=tuple(str(w) for w in keywords),
        precedence=precedence,
        cycle_days=cycle_days,
        checkins=tuple(sorted(checkins, key=lambda c: c.day_offset)),
    )


def parse(payload: dict[str, Any]) -> ProtocolBank:
    """The only way to build a `ProtocolBank` — so a bank in hand is validated.

    Same stance as `app.trees.schema.parse`: reading the JSON and using the dict
    directly would skip every check here, including the ones that catch a grading
    rule that can never fire.
    """
    option_sets = _parse_option_sets(payload.get("option_sets"))
    raw_sets = payload.get("question_sets")
    if not isinstance(raw_sets, dict) or not raw_sets:
        raise ProtocolError("protocols.json needs a non-empty 'question_sets'")
    sets = {
        key: _parse_question_set(key, raw, option_sets=option_sets) for key, raw in raw_sets.items()
    }

    raw_protocols = payload.get("protocols")
    if not isinstance(raw_protocols, dict) or not raw_protocols:
        raise ProtocolError("protocols.json needs a non-empty 'protocols'")
    protocols = {key: _parse_protocol(key, raw, sets=sets) for key, raw in raw_protocols.items()}

    precedences = [p.precedence for p in protocols.values()]
    if len(set(precedences)) != len(precedences):
        # A tie makes protocol choice depend on dict ordering — which is to say,
        # on the order somebody happened to paste the JSON in.
        raise ProtocolError("two protocols share a precedence; the tie-break must be total")

    used = {c.question_set for p in protocols.values() for c in p.checkins}
    orphans = sorted(set(sets) - used)
    if orphans:
        # An unreachable question set is authored text nobody will ever be asked
        # — the same class of mistake the tree validator catches as an
        # unreachable node, and the same reason to fail loudly at load.
        raise ProtocolError(f"question sets no protocol uses: {orphans}")

    version = payload.get("version")
    if not isinstance(version, int):
        raise ProtocolError("protocols.json needs an integer 'version'")
    return ProtocolBank(version=version, question_sets=sets, protocols=protocols)


def rules_from_snapshot(payload: Any, *, kinds: dict[str, str]) -> tuple[GradingRule, ...]:
    """Rebuild frozen grading rules from `Checkin.grading_rules`.

    Goes through `_parse_grading` — the same validator the bank is loaded with —
    so a snapshot cannot become the one path into the grader that skips the
    checks (a `green` rule, a rule over a `free_voice` answer, a rule addressing
    a question that was never asked). `kinds` comes from the frozen questions on
    the same row, so the type-check is against what the patient actually saw.

    Raises `ProtocolError`, which `app.checkins.grading` turns into an amber and
    a line for the nurse rather than a failed answer submission.
    """
    if not isinstance(payload, list):
        raise ProtocolError("grading snapshot: expected a list of rules")
    return tuple(
        _parse_grading(raw, where=f"grading snapshot[{i}]", kinds=kinds)
        for i, raw in enumerate(payload)
    )


@cache
def get_bank(path: Path | None = None) -> ProtocolBank:
    """Load and validate the bank once per process."""
    source = path or PROTOCOLS_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:  # pragma: no cover - a missing seed is a deploy fault
        raise ProtocolError(f"no protocol bank at {source}") from None
    return parse(payload)
