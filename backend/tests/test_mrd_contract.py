"""What a model is allowed to say about a scanned document (doc 21 §1.4).

The parser's job is to be forgiving about shape and unforgiving about meaning:
a stray key or a stringified number must not cost a whole report, and no reply
in any shape may ever set a flag.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.enums import Sex, ValueFlag
from app.mrd.contract import Extraction, ExtractionFormatError

REPLY = {
    "document_kind_guess": "lab",
    "report_date": "2026-07-30",
    "tests": [
        {
            "name": "Hemoglobin",
            "value": 8.9,
            "unit": "g/dL",
            "ref_low": 12.0,
            "ref_high": 15.0,
            "page": 1,
            "confidence": "high",
        },
        {
            "name": "Platelet count",
            "value": 210,
            "unit": "10^3/uL",
            "ref_low": 150,
            "ref_high": 410,
            "page": 1,
            "confidence": "high",
        },
    ],
    "narrative_findings": ["Impression: no acute changes."],
    "illegible_regions": ["page 2, bottom table"],
}


def test_a_well_formed_reply_parses_and_flags():
    extraction = Extraction.parse(REPLY).flag_all(sex=Sex.FEMALE)

    assert extraction.report_date == "2026-07-30"
    assert extraction.outlier_count == 1
    assert extraction.outliers[0].name == "Hemoglobin"
    assert extraction.outliers[0].flag is ValueFlag.LOW
    assert extraction.narrative_findings == ["Impression: no acute changes."]
    assert extraction.illegible_regions == ["page 2, bottom table"]


def test_a_flag_in_the_models_reply_is_ignored_entirely():
    """The contract has no flag field. If a model volunteers one — and they do,
    helpfully, when a report prints "H" beside a value — it must not reach the
    stored payload. Here the model calls a normal platelet count critical."""
    reply = {
        "tests": [
            {
                "name": "Platelet count",
                "value": 210,
                "unit": "10^3/uL",
                "ref_low": 150,
                "ref_high": 410,
                "flag": "critical_low",
                "is_abnormal": True,
                "severity": "urgent",
            }
        ]
    }

    extraction = Extraction.parse(reply).flag_all(sex=None)

    assert extraction.tests[0].flag is ValueFlag.NORMAL
    assert extraction.outlier_count == 0
    assert "flag" not in str(reply["tests"][0]["name"])  # sanity: we read name, not verdicts
    stored = extraction.as_payload()["tests"][0]
    assert stored["flag"] == "normal"
    assert "severity" not in stored and "is_abnormal" not in stored


def test_a_row_the_model_mangled_is_dropped_and_counted_not_guessed():
    """One unparseable row must not cost the other eleven, and the loss has to
    be visible — "we dropped 3 rows" is something a doctor can act on."""
    reply = {
        "tests": [
            {"name": "Hemoglobin", "value": 8.9, "unit": "g/dL"},
            {"value": 5, "unit": "g/dL"},  # no name
            "not even an object",
            {"name": "", "value": 1},
        ]
    }

    extraction = Extraction.parse(reply)

    assert [t.name for t in extraction.tests] == ["Hemoglobin"]
    assert extraction.dropped == 3
    assert extraction.as_payload()["dropped_rows"] == 3


def test_a_value_that_is_not_a_number_is_kept_as_text_and_left_unjudged():
    """ "Not detected" is a real lab result. It is shown as printed and flagged
    UNKNOWN — dropping it would hide a finding, parsing it would invent one."""
    extraction = Extraction.parse(
        {"tests": [{"name": "HBsAg", "value": "Non-reactive", "unit": ""}]}
    ).flag_all(sex=None)

    test = extraction.tests[0]
    assert test.value is None
    assert test.value_text == "Non-reactive"
    assert test.flag is ValueFlag.UNKNOWN


def test_stringified_numbers_and_indian_report_spellings_parse():
    extraction = Extraction.parse(
        {
            "tests": [
                {"name": "Platelet count", "value": "1,80,000", "unit": "/cumm"},
                {"name": "Hemoglobin", "value": "10.2", "unit": "gm/dl"},
            ]
        }
    ).flag_all(sex=Sex.FEMALE)

    by_name = {t.name: t for t in extraction.tests}
    assert by_name["Hemoglobin"].value == Decimal("10.2")
    assert by_name["Hemoglobin"].flag is ValueFlag.LOW
    # 180000 /cumm = 180 x10^3/uL — normal, and the conversion is shown.
    assert by_name["Platelet count"].flag is ValueFlag.NORMAL
    assert by_name["Platelet count"].canonical_value == Decimal("180.000")


def test_a_malformed_report_date_is_dropped_rather_than_shown_wrong():
    for bad in ("30-07-2026", "last Tuesday", "", None, 42):
        assert Extraction.parse({"report_date": bad}).report_date is None
    assert Extraction.parse({"report_date": "2026-07-30T10:00:00"}).report_date == "2026-07-30"


def test_an_invented_confidence_folds_down_not_up():
    """A value we under-trust gets checked against the page by a human; one we
    over-trust does not."""
    extraction = Extraction.parse(
        {"tests": [{"name": "Hemoglobin", "value": 9, "confidence": "absolutely certain"}]}
    )

    assert extraction.tests[0].confidence == "low"


def test_a_reply_that_is_not_an_object_is_a_format_error():
    """Retryable, and never partially stored: half a lab report is not a lab
    report, and there is no way for a reader to tell which half is missing."""
    for reply in ([], "sorry, I cannot read this", 42, None):
        with pytest.raises(ExtractionFormatError):
            Extraction.parse(reply)


def test_an_empty_but_well_formed_reply_is_not_an_error():
    """A photograph of a blank page, or a discharge summary with no numbers in
    it, is a legitimate outcome — the document still stores and still shows."""
    extraction = Extraction.parse({"tests": [], "narrative_findings": []}).flag_all(sex=None)

    assert extraction.tests == []
    assert extraction.outlier_count == 0


def test_absurd_input_sizes_are_bounded():
    """A model stuck in a loop must not write a megabyte of JSONB per document."""
    extraction = Extraction.parse(
        {
            "tests": [{"name": f"Test {i}", "value": i} for i in range(500)],
            "narrative_findings": ["x" * 9000],
            "illegible_regions": [f"region {i}" for i in range(200)],
        }
    )

    assert len(extraction.tests) == 200
    assert len(extraction.narrative_findings[0]) == 4000
    assert len(extraction.illegible_regions) == 40


def test_the_payload_stores_decimals_as_strings():
    """A value that round-trips through a screen and back must come out
    identical. JSON floats do not promise that; strings do."""
    payload = Extraction.parse(REPLY).flag_all(sex=Sex.FEMALE).as_payload()

    stored = payload["tests"][0]
    assert stored["value"] == "8.9"
    assert stored["ref_low"] == "12.0"
    assert payload["version"] == 1


def test_the_table_is_ordered_worst_first_and_stably():
    extraction = Extraction.parse(
        {
            "tests": [
                {"name": "Sodium", "value": 140, "unit": "mmol/L"},
                {"name": "Unknown assay", "value": 5, "unit": "u"},
                {"name": "Potassium", "value": 6.9, "unit": "mmol/L"},
                {"name": "Hemoglobin", "value": 10.0, "unit": "g/dL"},
            ]
        }
    ).flag_all(sex=Sex.FEMALE)

    assert [t.name for t in extraction.tests] == [
        "Potassium",  # critical_high
        "Hemoglobin",  # low
        "Unknown assay",  # unjudged
        "Sodium",  # normal
    ]


def test_the_summariser_is_shown_the_flagged_structure_not_the_pages():
    """The summary must be provably about the same numbers the doctor's table
    shows. A second vision call could disagree with the first, and nothing would
    say which reading the prose described."""
    text = Extraction.parse(REPLY).flag_all(sex=Sex.FEMALE).summary_input()

    assert "Hemoglobin: 8.9 g/dL [LOW; reference 12.0 to 15.0]" in text
    assert "Within range: Platelet count" in text
    assert "Could not be read: page 2, bottom table" in text
    assert "Reported finding: Impression: no acute changes." in text


def test_an_unjudged_value_is_labelled_as_unassessed_for_the_summariser():
    """Otherwise the model writes prose about a value nobody checked, in the
    same voice as one we did."""
    text = (
        Extraction.parse({"tests": [{"name": "Mystery marker", "value": 7, "unit": "u"}]})
        .flag_all(sex=None)
        .summary_input()
    )

    assert "not assessed" in text


def test_the_demo_fixture_parses_and_volunteers_no_flag():
    """The fake's canned `mrd_extract` reply is what `make dev` and the
    Playwright runs read a lab report with, so it is a real input to this
    contract and is pinned like one.

    Two things it must never do: fail to parse (which would leave the demo
    stuck on `extraction_failed`, the state it was added to get past), and
    carry a `flag` on any row. There is no flag field in the schema or the
    prompt — whether 8.9 is low is decided here, in Python, on `Decimal` — and
    a demo fixture that volunteered one would teach the wrong shape to
    everyone who reads it.
    """
    import json

    from app.providers.llm import _CANNED_JSON

    raw = json.loads(_CANNED_JSON["mrd_extract"])

    assert all("flag" not in row for row in raw["tests"])

    extraction = Extraction.parse(raw).flag_all(sex=Sex.FEMALE)
    by_name = {test.name: test for test in extraction.tests}

    # Decided by this module, from the range the report printed.
    assert by_name["Hemoglobin"].flag == "low"
    assert by_name["Hemoglobin"].ref_source == "printed"
    # No printed range, so the fallback table decides and says that it did —
    # which is what the doctor's table renders as the weaker signal.
    assert by_name["Absolute Neutrophil Count"].ref_source == "default"
    assert by_name["Platelet Count"].flag == "normal"
