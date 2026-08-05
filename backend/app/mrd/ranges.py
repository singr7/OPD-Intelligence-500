"""Deciding that a number is out of range — in Python, never in the model.

> "Red flags, intake traversal, check-in grading, and escalation are
> deterministic. A model may interpret or summarize; it may not decide clinical
> urgency." — CODEBASE_MEMORY, Non-Negotiable Invariants

This module is that invariant applied to lab values. The extraction contract
(`app.mrd.contract`) has **no flag field**: a model may read "8.9" and "g/dL"
off a page, and that is the end of its authority. Everything below is arithmetic
on `Decimal`.

## Two sources of truth, in a deliberate order

1. **The range printed on the report.** Preferred always, and compared with *no
   unit conversion at all* — the value and its range came off the same page in
   the same units, so any normalisation we did could only introduce error. It
   also means a lab whose analyser reads differently from the textbook gets its
   own calibration respected, which is the whole reason labs print ranges.

2. **`seeds/lab_reference_ranges.json`**, only when the report printed nothing.
   Adult, sex-aware where it matters, and marked `review_pending` until an
   oncologist signs it off — which is why every flag carries `ref_source`, so
   the doctor's UI can show a fallback-derived flag as the weaker signal it is.

If neither is available, or the units are ones we cannot convert, the answer is
`UNKNOWN` and the value is shown plainly. A guess here is worse than a blank:
an unflagged value invites a doctor to read the number, a wrongly-flagged one
invites them to trust our arithmetic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from typing import Any

from app.models.enums import Sex, ValueFlag

SEEDS_DIR = Path(__file__).resolve().parents[3] / "seeds"
RANGES_FILE = SEEDS_DIR / "lab_reference_ranges.json"

#: Where a range came from, carried onto every flagged value.
REF_PRINTED = "printed"
REF_DEFAULT = "default"
REF_NONE = "none"


def to_decimal(value: Any) -> Decimal | None:
    """A model's number → Decimal, or None if it is not one.

    Via `str()` so a JSON float never brings its binary repr along, and tolerant
    of the shapes a lab report actually prints: "8.9", "<0.5", "1,200", "12.0 ".
    A leading `<` or `>` is dropped and the bound taken as the value — a report
    that says "<0.5" is reporting below-assay, and treating that as unreadable
    would silently discard exactly the values that tend to matter.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("<>=~").replace(",", "").strip()
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def normalize_unit(unit: str | None) -> str:
    """Fold a printed unit to a comparable key.

    Labs print the same unit a dozen ways — `10^3/µL`, `10³/uL`, `x10^3/ul`,
    `cells/cu.mm`. This is spelling normalisation only; it converts nothing.
    """
    if not unit:
        return ""
    text = unit.strip().lower()
    text = text.replace("µ", "u").replace("μ", "u")
    text = text.replace("³", "^3").replace("⁹", "^9").replace("^ ", "^")
    text = re.sub(r"\bx\s*10", "10", text)
    text = re.sub(r"\bcu\.?\s*mm\b", "cumm", text)
    text = re.sub(r"\bcells?\b", "cells", text)
    text = re.sub(r"\s+", "", text)
    return text


@dataclass(frozen=True, slots=True)
class ReferenceTest:
    """One test in the fallback table."""

    key: str
    display: str
    aliases: frozenset[str]
    unit: str
    #: normalised unit → multiplier onto the canonical unit.
    units: dict[str, Decimal]
    #: (sex, low, high); `sex` is "any" or a `Sex` value.
    ranges: tuple[tuple[str, Decimal | None, Decimal | None], ...]
    critical_low: Decimal | None = None
    critical_high: Decimal | None = None

    def convert(self, value: Decimal, unit: str | None) -> Decimal | None:
        """`value` in `unit` → the canonical unit, or None if we cannot say.

        An unrecognised unit returns None rather than assuming the canonical
        one. A platelet count of "150" is normal in 10^3/µL and profoundly
        abnormal in /µL, and nothing on the page tells us which if we guess.
        """
        factor = self.units.get(normalize_unit(unit))
        return None if factor is None else value * factor

    def bounds(self, sex: Sex | None) -> tuple[Decimal | None, Decimal | None]:
        """The range for this patient, preferring a sex-specific row."""
        wanted = sex.value if sex else None
        for row_sex, low, high in self.ranges:
            if row_sex == wanted:
                return low, high
        for row_sex, low, high in self.ranges:
            if row_sex == "any":
                return low, high
        return None, None


class ReferenceTable:
    """The fallback table, indexed by name and alias."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.version = int(payload.get("version", 0))
        self.status = str(payload.get("status", "review_pending"))
        self._tests: dict[str, ReferenceTest] = {}
        self._by_name: dict[str, ReferenceTest] = {}
        for raw in payload.get("tests", []):
            test = ReferenceTest(
                key=raw["key"],
                display=raw["display"],
                aliases=frozenset(_fold(a) for a in raw.get("aliases", [])),
                unit=raw["unit"],
                units={
                    normalize_unit(u): Decimal(str(f)) for u, f in (raw.get("units") or {}).items()
                },
                ranges=tuple(
                    (
                        row.get("sex", "any"),
                        to_decimal(row.get("low")),
                        to_decimal(row.get("high")),
                    )
                    for row in raw.get("ranges", [])
                ),
                critical_low=to_decimal((raw.get("critical") or {}).get("low")),
                critical_high=to_decimal((raw.get("critical") or {}).get("high")),
            )
            self._tests[test.key] = test
            for name in {_fold(test.key), _fold(test.display), *test.aliases}:
                self._by_name[name] = test

    @property
    def reviewed(self) -> bool:
        """False until an oncologist signs the file off (doc 21 §8.2)."""
        return self.status == "reviewed"

    def find(self, name: str | None) -> ReferenceTest | None:
        """Exact match on the folded name or an alias. Deliberately not fuzzy.

        `app.formulary` fuzzy-matches drug names because it is *suggesting* to a
        doctor who then chooses. Nobody chooses here: a fuzzy hit would silently
        flag one test's value against another test's range. An unmatched name
        costs a grey "no range available" row, which is a true statement.
        """
        return self._by_name.get(_fold(name or ""))


def _fold(name: str) -> str:
    """Lowercase, strip punctuation and honorifics labs sprinkle on test names."""
    text = name.strip().lower()
    text = re.sub(r"^(s|b|p)\.\s*", "", text)  # "S. Creatinine" → "creatinine"
    text = re.sub(r"^serum\s+", "", text)
    text = re.sub(r"[^a-z0-9()+^/%. -]", "", text)
    return re.sub(r"\s+", " ", text).strip()


@cache
def get_reference_table() -> ReferenceTable:
    if not RANGES_FILE.exists():
        return ReferenceTable({"version": 0, "status": "missing", "tests": []})
    return ReferenceTable(json.loads(RANGES_FILE.read_text()))


@dataclass(frozen=True, slots=True)
class Flagged:
    """The verdict on one value. Everything here is computed, nothing asked."""

    flag: ValueFlag
    ref_source: str
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    #: Set only when the fallback table was used *and* the value had to be
    #: converted — so the UI can show "0.15 lakh/cumm ≈ 15 ×10³/µL" honestly
    #: rather than silently rewriting what the report said.
    canonical_value: Decimal | None = None
    canonical_unit: str | None = None
    #: Why there is no verdict. Shown to the doctor, not swallowed.
    reason: str = ""


def flag_value(
    *,
    name: str | None,
    value: Any,
    unit: str | None = None,
    ref_low: Any = None,
    ref_high: Any = None,
    sex: Sex | None = None,
    table: ReferenceTable | None = None,
) -> Flagged:
    """One value against its range. Pure, total, and the only place flags exist."""
    table = table or get_reference_table()
    measured = to_decimal(value)
    if measured is None:
        return Flagged(ValueFlag.UNKNOWN, REF_NONE, reason="value is not a number")

    printed_low, printed_high = to_decimal(ref_low), to_decimal(ref_high)
    if printed_low is not None or printed_high is not None:
        # Same page, same units. No conversion, and no second-guessing the lab.
        return Flagged(
            _compare(measured, printed_low, printed_high),
            REF_PRINTED,
            ref_low=printed_low,
            ref_high=printed_high,
        )

    test = table.find(name)
    if test is None:
        return Flagged(ValueFlag.UNKNOWN, REF_NONE, reason="no reference range for this test")

    canonical = test.convert(measured, unit)
    if canonical is None:
        return Flagged(
            ValueFlag.UNKNOWN,
            REF_NONE,
            reason=f"unit {unit or '(none)'} not convertible to {test.unit}",
        )

    low, high = test.bounds(sex)
    if low is None and high is None:
        return Flagged(ValueFlag.UNKNOWN, REF_NONE, reason="no reference range for this test")

    flag = _compare(canonical, low, high, test.critical_low, test.critical_high)
    return Flagged(
        flag,
        REF_DEFAULT,
        ref_low=low,
        ref_high=high,
        canonical_value=canonical if canonical != measured else None,
        canonical_unit=test.unit if canonical != measured else None,
    )


def _compare(
    value: Decimal,
    low: Decimal | None,
    high: Decimal | None,
    critical_low: Decimal | None = None,
    critical_high: Decimal | None = None,
) -> ValueFlag:
    """Inclusive bounds: a value *equal* to the limit is inside it, which is how
    every lab report prints its own ranges."""
    if critical_low is not None and value < critical_low:
        return ValueFlag.CRITICAL_LOW
    if critical_high is not None and value > critical_high:
        return ValueFlag.CRITICAL_HIGH
    if low is not None and value < low:
        return ValueFlag.LOW
    if high is not None and value > high:
        return ValueFlag.HIGH
    return ValueFlag.NORMAL


#: Flags that mean "show this first". `UNKNOWN` is not among them: a value we
#: could not judge is not an abnormal value, and counting it as one would inflate
#: every "N values flagged" badge with tests we simply do not have ranges for.
OUTLIER_FLAGS = frozenset(
    {ValueFlag.LOW, ValueFlag.HIGH, ValueFlag.CRITICAL_LOW, ValueFlag.CRITICAL_HIGH}
)

#: Sort key for presentation: critical first, then out-of-range, then unknown,
#: then normal. Ordering only — it decides what the doctor reads first, never
#: what happens next.
_FLAG_ORDER = {
    ValueFlag.CRITICAL_LOW: 0,
    ValueFlag.CRITICAL_HIGH: 0,
    ValueFlag.LOW: 1,
    ValueFlag.HIGH: 1,
    ValueFlag.UNKNOWN: 2,
    ValueFlag.NORMAL: 3,
}


def flag_rank(flag: ValueFlag | str) -> int:
    return _FLAG_ORDER.get(ValueFlag(flag), 3)
