"""Chief complaint → department (doc 03 §1a, doc 06 S4).

> "Then NLP (Gemini Flash) extracts department + seeds the tree." — doc 03 §1a

The kiosk's first question is open: the patient says what is wrong in their own
words, in Hindi or English or a mix of both. This module turns that sentence into
one of the hospital's department codes, which is what decides the tree they are
then asked (`app.trees.bank.for_department`) and the desk they are sent to.

The prompt is `prompts/routing/v1.md` — authored in S3, loaded here, and never
edited (a published version is immutable; changing it means `v2.md`).

## Everything the model says is checked

A classifier is the one place in the intake where a model's *judgement* reaches a
patient — the trees and the red flags are data, deliberately. So none of its
output is trusted on its own:

- **An invented department is refused.** Told to pick from nine keys, a model will
  occasionally return "ONCOLOGY". That is not a department; the patient goes to
  triage with a human, not to a floor that does not exist.
- **Low confidence routes to a human, not a guess.** The prompt is explicit that
  below 0.6 a coordinator checks the answer and that this "is a good outcome, not
  a failure". `needs_human` carries that decision out of this module.
- **A failed call is a triage referral, not an error.** Degrade, never deny (doc
  02 §5): a patient standing at a kiosk in Alwar cannot be told the AI is down.
  They get the general desk and a person.

Wrong is expensive here in a way that is easy to forget from a desk: doc 01 §2's
patients travel 50–200km, and a confident wrong answer sends them up a floor to
queue for a speciality that cannot help them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.models.enums import Lang, UsagePurpose
from app.prompts import load
from app.providers import LLMProvider, LLMRequest, ProviderError, llm_chain, with_fallback

logger = logging.getLogger(__name__)

#: Where a patient goes when we do not know. doc 03 §3's General Medicine tree is
#: the thin one that asks enough to route them properly.
TRIAGE_DEPARTMENT = "GENMED"

#: The prompt's own line: "Below 0.6, a human coordinator will check your answer —
#: that is a good outcome, not a failure." Kept in sync with `prompts/routing/v1.md`.
CONFIDENCE_FLOOR = 0.6

_SEEDS_DIR = Path(__file__).resolve().parents[2] / "seeds"


@dataclass(frozen=True, slots=True)
class DepartmentOption:
    """A department the classifier may choose. `key` is `departments.code`."""

    key: str
    name: str
    note: str | None = None

    def render(self) -> str:
        return f"- {self.key}: {self.name}" + (f" — {self.note}" if self.note else "")


@dataclass(frozen=True, slots=True)
class DepartmentGuess:
    """Where to send this patient, and how much to trust it."""

    dept_key: str
    confidence: float
    reason: str
    #: True when a coordinator should confirm before the patient walks anywhere:
    #: low confidence, an invented department, or a classifier that never answered.
    needs_human: bool
    #: False when this is a fallback rather than the model's own answer — S18
    #: should not count a triage referral as the classifier's opinion.
    from_model: bool = True


def pilot_departments(seeds_dir: Path | None = None) -> tuple[DepartmentOption, ...]:
    """The pilot's nine departments, from `seeds/hospital.json`.

    A convenience for the eval harness and for callers without a session. S5 has a
    database and should pass its own `Department` rows — the codes are the same,
    and this file is what seeded them.
    """
    path = (seeds_dir or _SEEDS_DIR) / "hospital.json"
    hospital = json.loads(path.read_text())
    return tuple(
        DepartmentOption(key=dept["code"], name=dept["name"], note=dept.get("note"))
        for dept in hospital["departments"]
    )


async def classify_department(
    complaint: str,
    *,
    lang: Lang | str = Lang.HI,
    departments: Sequence[DepartmentOption] | None = None,
    providers: Sequence[LLMProvider] | None = None,
) -> DepartmentGuess:
    """Route one chief complaint to one department.

    Never raises for a routing failure — see the module docstring. The only way
    this returns something a patient cannot act on is if `departments` is empty,
    which is a deployment bug, not a runtime one.
    """
    # `is None` rather than falsy: an explicitly empty list is a caller that
    # believes it has departments and does not, which must not quietly become the
    # seed file's nine.
    options = tuple(departments if departments is not None else pilot_departments())
    if not options:
        raise ValueError("no departments to route to")

    prompt = load("routing")
    rendered = prompt.render(
        complaint=complaint,
        lang=str(lang),
        departments="\n".join(option.render() for option in options),
    )
    request = LLMRequest(
        prompt=rendered,
        system=prompt.system,
        prompt_ref=prompt.ref,
        json_output=True,
        # Routing is a lookup, not a creative act: the same sentence must reach the
        # same desk on Tuesday as it did on Monday.
        temperature=0.0,
        max_tokens=200,
    )

    chain = list(providers) if providers is not None else llm_chain()

    # The vendor answering and the vendor making sense are separate failures, and
    # conflating them sends whoever reads the log hunting an outage that never
    # happened. Both still end the same way for the patient: a desk with a human.
    try:
        result = await with_fallback(
            chain, lambda provider: provider.complete(request, purpose=UsagePurpose.ROUTING)
        )
    except ProviderError as exc:
        logger.warning("routing classifier unavailable, sending to triage: %s", exc)
        return _triage("classifier unavailable")

    try:
        payload = result.json()
    except Exception as exc:  # noqa: BLE001 - a reply we cannot read is not an outage
        logger.warning(
            "routing classifier (%s) returned unreadable output, sending to triage: %s",
            result.model,
            exc,
        )
        return _triage("classifier returned no usable answer")

    return _interpret(payload, options)


def _interpret(payload: object, options: Sequence[DepartmentOption]) -> DepartmentGuess:
    """Turn the model's JSON into a decision, distrusting all of it."""
    if not isinstance(payload, dict):
        return _triage("classifier returned no usable answer")

    known = {option.key for option in options}
    dept_key = payload.get("dept_key")
    reason = payload.get("reason")
    reason = reason.strip() if isinstance(reason, str) else ""

    if not isinstance(dept_key, str) or dept_key not in known:
        # Told to pick from a list, a model still occasionally invents a key. The
        # patient is not the right person to discover that.
        logger.warning("routing classifier invented department %r; sending to triage", dept_key)
        return _triage(f"classifier chose {dept_key!r}, which is not a department")

    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        # No confidence is not high confidence.
        return DepartmentGuess(dept_key, 0.0, reason, needs_human=True)
    confidence = min(1.0, max(0.0, float(confidence)))

    return DepartmentGuess(
        dept_key=dept_key,
        confidence=confidence,
        reason=reason,
        needs_human=confidence < CONFIDENCE_FLOOR,
    )


def _triage(reason: str) -> DepartmentGuess:
    return DepartmentGuess(
        dept_key=TRIAGE_DEPARTMENT,
        confidence=0.0,
        reason=reason,
        needs_human=True,
        from_model=False,
    )
