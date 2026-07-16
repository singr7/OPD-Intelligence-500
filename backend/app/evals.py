"""Eval harness for the routing classifier (doc 06 S4: "eval set of 60 labeled
utterances", AC: "classifier ≥85% on eval set").

    python -m app.evals                    # score the configured LLM provider
    python -m app.evals --provider gemini  # score one by name
    python -m app.evals --verbose          # print every case

Exits non-zero below the set's threshold, so it can gate a prompt change.

## What this measures, and what it cannot

The number this prints is only as real as the provider behind it. Run it with
`LLM_PROVIDER=fake` (the default everywhere) and you are scoring a fake that
replies from a script — the result is meaningful for the *harness*, not the model.

**As of S4 the ≥85% AC is unverified**, because no vendor key has ever been used
in this repo (STATE.md: "No live vendor has ever accepted a call"). The eval set,
the harness and the gate exist; the number needs someone with a `GEMINI_API_KEY`
to run the line above once. That is a five-minute job and it is in HANDOFF.

The test suite deliberately does **not** assert an accuracy figure against the
fake. A test that scripted the fake with the right answers and then asserted 100%
would be a green tick measuring nothing — worse than no test, because it reads
like the AC is met. `tests/test_routing.py` tests the harness arithmetic and the
classifier's handling of what a model returns; the accuracy of a real model is
measured here, live, or not at all.

## Why an eval set rather than unit tests

Routing is the one place a model's judgement reaches a patient. There is no
assertion that "kimo ke liye aaya hoon" → MEDONC that is worth writing as a unit
test — the model either generalises across the way people in Alwar actually speak
or it does not, and the only way to know is a labelled sample of it. When the
prompt changes (a `v2.md`), this is what says whether it got better or just
different.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.models.enums import Lang
from app.providers import LLMProvider
from app.routing import DepartmentGuess, DepartmentOption, classify_department, pilot_departments

logger = logging.getLogger("evals")

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


class EvalError(RuntimeError):
    """A malformed eval set. Loud, because a broken eval silently scores 0%."""


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    lang: Lang
    utterance: str
    expect: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class EvalSet:
    id: str
    description: str
    threshold: float
    cases: tuple[EvalCase, ...]


@dataclass(frozen=True, slots=True)
class Outcome:
    case: EvalCase
    guess: DepartmentGuess

    @property
    def correct(self) -> bool:
        return self.guess.dept_key == self.case.expect


@dataclass(frozen=True, slots=True)
class EvalReport:
    eval_set: EvalSet
    outcomes: tuple[Outcome, ...]
    #: The provider that actually answered — the number means nothing without it.
    provider: str

    @property
    def accuracy(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.correct) / len(self.outcomes)

    @property
    def passed(self) -> bool:
        return self.accuracy >= self.eval_set.threshold

    @property
    def failures(self) -> tuple[Outcome, ...]:
        return tuple(o for o in self.outcomes if not o.correct)

    @property
    def handed_to_a_human(self) -> tuple[Outcome, ...]:
        """Cases the classifier refused to answer confidently.

        Tracked separately from failures because they are not the same thing: a
        low-confidence answer sends the patient to a coordinator, which the prompt
        calls "a good outcome, not a failure". A *confident* wrong answer is the
        one that walks someone up to the wrong floor.
        """
        return tuple(o for o in self.outcomes if o.guess.needs_human)

    @property
    def confidently_wrong(self) -> tuple[Outcome, ...]:
        """The set that actually hurts: wrong, and sure of itself."""
        return tuple(o for o in self.outcomes if not o.correct and not o.guess.needs_human)

    def confusion(self) -> Counter[tuple[str, str]]:
        """(expected, guessed) → count, for the pairs that went wrong."""
        return Counter((o.case.expect, o.guess.dept_key) for o in self.failures)

    def summary(self) -> str:
        lines = [
            f"eval:      {self.eval_set.id} ({len(self.outcomes)} cases)",
            f"provider:  {self.provider}",
            f"accuracy:  {self.accuracy:.1%}  (threshold {self.eval_set.threshold:.0%})",
            f"result:    {'PASS' if self.passed else 'FAIL'}",
            f"to human:  {len(self.handed_to_a_human)}  "
            f"(low confidence — a coordinator checks; not counted as failure)",
            f"confidently wrong: {len(self.confidently_wrong)}",
        ]
        if self.failures:
            lines.append("\nmisses:")
            for outcome in self.failures:
                confidence = f"{outcome.guess.confidence:.2f}"
                lines.append(
                    f"  {outcome.case.id}  expected {outcome.case.expect:<8} "
                    f"got {outcome.guess.dept_key:<8} conf={confidence}  "
                    f"{outcome.case.utterance}"
                )
        if pairs := self.confusion():
            lines.append("\nconfusion (expected → guessed):")
            for (expected, guessed), count in pairs.most_common():
                lines.append(f"  {expected:<8} → {guessed:<8} ×{count}")
        return "\n".join(lines)


def load_eval(name: str = "routing", *, root: Path | None = None) -> EvalSet:
    path = (root or EVALS_DIR) / f"{name}_eval.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise EvalError(f"no eval set at {path}") from None
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path.name}: not valid JSON: {exc}") from exc

    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvalError(f"{path.name}: 'cases' must be a non-empty list")

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases):
        where = f"{path.name}: cases[{index}]"
        if not isinstance(raw, dict):
            raise EvalError(f"{where}: must be an object")
        case_id = raw.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise EvalError(f"{where}: needs an 'id'")
        if case_id in seen:
            raise EvalError(f"{where}: duplicate case id {case_id!r}")
        seen.add(case_id)
        utterance = raw.get("utterance")
        if not isinstance(utterance, str) or not utterance.strip():
            raise EvalError(f"{where}: needs an 'utterance'")
        expect = raw.get("expect")
        if not isinstance(expect, str) or not expect:
            raise EvalError(f"{where}: needs an 'expect' department code")
        try:
            lang = Lang(raw.get("lang", "hi"))
        except ValueError:
            raise EvalError(f"{where}: unknown lang {raw.get('lang')!r}") from None
        cases.append(
            EvalCase(
                id=case_id, lang=lang, utterance=utterance, expect=expect, note=raw.get("note")
            )
        )

    threshold = data.get("threshold", 0.85)
    if not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise EvalError(f"{path.name}: threshold must be between 0 and 1")

    return EvalSet(
        id=data.get("id", name),
        description=data.get("description", ""),
        threshold=float(threshold),
        cases=tuple(cases),
    )


def check_labels(eval_set: EvalSet, departments: Sequence[DepartmentOption]) -> None:
    """Every label must be a department that exists.

    A typo'd label is a case the classifier can never pass, which drags the score
    down for a reason that has nothing to do with the model.
    """
    known = {option.key for option in departments}
    if unknown := {case.expect for case in eval_set.cases} - known:
        raise EvalError(
            f"eval {eval_set.id}: labels name departments that do not exist: {sorted(unknown)}"
        )


async def run_eval(
    eval_set: EvalSet,
    *,
    departments: Sequence[DepartmentOption] | None = None,
    providers: Sequence[LLMProvider] | None = None,
    provider_label: str | None = None,
) -> EvalReport:
    """Run every case. Sequential on purpose — this is a handful of calls run by a
    human, and a burst of 60 parallel requests is how you meet a rate limit."""
    options = tuple(departments if departments is not None else pilot_departments())
    check_labels(eval_set, options)

    outcomes: list[Outcome] = []
    for case in eval_set.cases:
        guess = await classify_department(
            case.utterance, lang=case.lang, departments=options, providers=providers
        )
        outcomes.append(Outcome(case=case, guess=guess))

    if provider_label is None:
        provider_label = providers[0].name if providers else "configured"
    return EvalReport(eval_set=eval_set, outcomes=tuple(outcomes), provider=provider_label)


async def _main(name: str, provider_name: str | None, verbose: bool) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    from app.providers.registry import _get, llm_chain  # local: CLI-only dependency

    eval_set = load_eval(name)
    if provider_name:
        providers = [_get("llm", name=provider_name)]
    else:
        providers = llm_chain()

    label = providers[0].name
    if label.startswith("fake"):
        print(
            "WARNING: scoring the fake provider — this measures the harness, not a "
            "model. Set LLM_PROVIDER (and its key) for a real number.\n",
            file=sys.stderr,
        )

    report = await run_eval(eval_set, providers=providers, provider_label=label)
    if verbose:
        for outcome in report.outcomes:
            mark = "ok  " if outcome.correct else "MISS"
            print(f"{mark} {outcome.case.id} {outcome.guess.dept_key:<8} {outcome.case.utterance}")
        print()
    print(report.summary())
    return 0 if report.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a prompt against its labelled eval set.")
    parser.add_argument("--set", dest="name", default="routing", help="eval set name")
    parser.add_argument("--provider", help="LLM provider name (default: the configured chain)")
    parser.add_argument("--verbose", action="store_true", help="print every case")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.name, args.provider, args.verbose)))


if __name__ == "__main__":
    main()
