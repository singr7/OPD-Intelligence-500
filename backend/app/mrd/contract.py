"""What the model is allowed to say about a document, and what we do with it.

The extraction contract (doc 21 §1.4) is deliberately narrow. The model reports
what is *printed*: a test name, a number, a unit, the range beside it if the lab
printed one, which page it was on, and how sure it is. It does not report
whether the value is abnormal — there is no field for that, here or in the
prompt — because that decision is `app.mrd.ranges`, in Python.

## Verbatim or absent

A value the model cannot read is **omitted**, and the region named in
`illegible_regions`. This is `dictation`'s "never silently corrects or invents a
drug" applied to a lab report: a hallucinated platelet count is worse than a
missing one, because a missing one sends the doctor to the original page — which
is one tap away on every screen that shows this — and a wrong one does not.

Parsing here is total and forgiving of *shape* while being strict about
*meaning*: a model that returns a string where we wanted a number, or an extra
key we never asked for, must not cost the whole document. A model that returns a
value we cannot parse as a number simply gets that value dropped, and the count
of dropped rows is carried out with the result so the pipeline can say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.enums import Sex, ValueFlag
from app.mrd.ranges import Flagged, ReferenceTable, flag_rank, flag_value, to_decimal

#: Bumped when the stored `payload` shape changes, so a reader can tell a v1 row
#: from a v2 one without guessing. Same contract as `dictation.STRUCTURED_VERSION`.
PAYLOAD_VERSION = 1

#: How sure the model says it is. Anything else it invents folds to "low", which
#: is the safe direction: a value we under-trust gets read off the page by a
#: human, a value we over-trust does not.
_CONFIDENCE = {"high", "medium", "low"}

MAX_TESTS = 200
MAX_FINDINGS = 40
MAX_TEXT = 4000


def _clean(value: Any, *, limit: int = 300) -> str:
    """Any scalar → a trimmed string. Models return numbers where text was asked."""
    if value is None or isinstance(value, dict | list):
        return ""
    return str(value).strip()[:limit]


def _iso_date(value: Any) -> str | None:
    """Keep a date only if it is one. A report date is shown to a doctor beside
    values that are only meaningful in time order; a malformed one is worse than
    none, because none is visibly absent."""
    text = _clean(value, limit=32)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


@dataclass(slots=True)
class ExtractedTest:
    """One measurement as printed, plus the verdict we computed about it."""

    name: str
    value_text: str
    value: Decimal | None = None
    unit: str = ""
    ref_low: Decimal | None = None
    ref_high: Decimal | None = None
    page: int | None = None
    confidence: str = "low"

    # Computed. Never parsed from the model's reply — see the module docstring.
    flag: ValueFlag = ValueFlag.UNKNOWN
    ref_source: str = "none"
    flag_reason: str = ""
    canonical_value: Decimal | None = None
    canonical_unit: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> ExtractedTest | None:
        if not isinstance(raw, dict):
            return None
        name = _clean(raw.get("name"))
        if not name:
            return None
        value_text = _clean(raw.get("value"), limit=64)
        page = raw.get("page")
        confidence = _clean(raw.get("confidence"), limit=16).lower()
        return cls(
            name=name,
            value_text=value_text,
            value=to_decimal(raw.get("value")),
            unit=_clean(raw.get("unit"), limit=32),
            ref_low=to_decimal(raw.get("ref_low")),
            ref_high=to_decimal(raw.get("ref_high")),
            page=page if isinstance(page, int) and 0 < page <= 100 else None,
            confidence=confidence if confidence in _CONFIDENCE else "low",
        )

    def apply(self, flagged: Flagged) -> None:
        self.flag = flagged.flag
        self.ref_source = flagged.ref_source
        self.flag_reason = flagged.reason
        self.canonical_value = flagged.canonical_value
        self.canonical_unit = flagged.canonical_unit
        # A printed range the model reported is echoed back as we used it; a
        # fallback range is recorded too, so the doctor's table can show which
        # numbers the flag was actually computed against.
        self.ref_low, self.ref_high = flagged.ref_low, flagged.ref_high

    @property
    def is_outlier(self) -> bool:
        from app.mrd.ranges import OUTLIER_FLAGS

        return self.flag in OUTLIER_FLAGS

    def as_dict(self) -> dict[str, Any]:
        """JSONB-safe. Decimals become strings, not floats: a value that has to
        round-trip through a doctor's screen and back must come out identical."""
        return {
            "name": self.name,
            "value_text": self.value_text,
            "value": str(self.value) if self.value is not None else None,
            "unit": self.unit,
            "ref_low": str(self.ref_low) if self.ref_low is not None else None,
            "ref_high": str(self.ref_high) if self.ref_high is not None else None,
            "page": self.page,
            "confidence": self.confidence,
            "flag": self.flag.value,
            "ref_source": self.ref_source,
            "flag_reason": self.flag_reason,
            "canonical_value": (
                str(self.canonical_value) if self.canonical_value is not None else None
            ),
            "canonical_unit": self.canonical_unit,
        }


@dataclass(slots=True)
class Extraction:
    """One document's reading: values, prose findings, and what was unreadable."""

    document_kind_guess: str = ""
    report_date: str | None = None
    tests: list[ExtractedTest] = field(default_factory=list)
    narrative_findings: list[str] = field(default_factory=list)
    illegible_regions: list[str] = field(default_factory=list)
    #: Rows the model returned that we could not parse at all. Surfaced rather
    #: than swallowed: "we dropped 3 rows" is a fact the doctor should be able
    #: to learn before trusting a table.
    dropped: int = 0

    @classmethod
    def parse(cls, raw: Any) -> Extraction:
        if not isinstance(raw, dict):
            raise ExtractionFormatError(f"expected a JSON object, got {type(raw).__name__}")

        tests: list[ExtractedTest] = []
        dropped = 0
        for row in (raw.get("tests") or [])[:MAX_TESTS]:
            parsed = ExtractedTest.parse(row)
            if parsed is None:
                dropped += 1
            else:
                tests.append(parsed)

        return cls(
            document_kind_guess=_clean(raw.get("document_kind_guess"), limit=32),
            report_date=_iso_date(raw.get("report_date")),
            tests=tests,
            narrative_findings=[
                text
                for item in (raw.get("narrative_findings") or [])[:MAX_FINDINGS]
                if (text := _clean(item, limit=MAX_TEXT))
            ],
            illegible_regions=[
                text
                for item in (raw.get("illegible_regions") or [])[:MAX_FINDINGS]
                if (text := _clean(item, limit=300))
            ],
            dropped=dropped,
        )

    def flag_all(self, *, sex: Sex | None, table: ReferenceTable | None = None) -> Extraction:
        """Compute every flag. The only path by which a flag ever gets set."""
        for test in self.tests:
            test.apply(
                flag_value(
                    name=test.name,
                    value=test.value,
                    unit=test.unit,
                    ref_low=test.ref_low,
                    ref_high=test.ref_high,
                    sex=sex,
                    table=table,
                )
            )
        # Presentation order: critical, then out of range, then unjudged, then
        # normal — stable within each band so a re-run does not reshuffle a
        # table the doctor was reading.
        self.tests.sort(key=lambda t: flag_rank(t.flag))
        return self

    @property
    def outliers(self) -> list[ExtractedTest]:
        return [t for t in self.tests if t.is_outlier]

    @property
    def outlier_count(self) -> int:
        return len(self.outliers)

    def as_payload(self) -> dict[str, Any]:
        return {
            "version": PAYLOAD_VERSION,
            "document_kind_guess": self.document_kind_guess,
            "report_date": self.report_date,
            "tests": [t.as_dict() for t in self.tests],
            "narrative_findings": self.narrative_findings,
            "illegible_regions": self.illegible_regions,
            "dropped_rows": self.dropped,
        }

    def summary_input(self) -> str:
        """What the summariser is shown — the flagged structure, never the pages.

        Two reasons this is not a second vision call. It costs a fraction as
        much, and more importantly the summary is then provably *about* the same
        numbers the doctor's table shows: a second reading of the images could
        disagree with the first, and there would be no way to tell which one the
        prose was describing.
        """
        lines: list[str] = []
        if self.report_date:
            lines.append(f"Report date: {self.report_date}")
        for test in self.tests:
            if test.flag is ValueFlag.NORMAL:
                continue
            shown = f"{test.name}: {test.value_text} {test.unit}".strip()
            if test.flag is ValueFlag.UNKNOWN:
                lines.append(f"{shown} (no reference range available — not assessed)")
                continue
            span = " to ".join(
                str(bound) for bound in (test.ref_low, test.ref_high) if bound is not None
            )
            lines.append(f"{shown} [{test.flag.value.upper()}; reference {span or 'unstated'}]")
        normal = [t.name for t in self.tests if t.flag is ValueFlag.NORMAL]
        if normal:
            lines.append(f"Within range: {', '.join(normal)}")
        for finding in self.narrative_findings:
            lines.append(f"Reported finding: {finding}")
        if self.illegible_regions:
            lines.append(f"Could not be read: {'; '.join(self.illegible_regions)}")
        return "\n".join(lines)


class ExtractionFormatError(ValueError):
    """The model's reply was not the contract. Retryable; never partially stored."""
