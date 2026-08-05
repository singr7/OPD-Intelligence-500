"""Outlier flagging is arithmetic, not judgement (doc 21 §1.4).

The model never sees a flag field. Everything asserted here is what stands
between "the machine read 8.9" and "the doctor is told 8.9 is low", and it is
the part of the MRD pipeline that must be right even when the model is wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.enums import Sex, ValueFlag
from app.mrd.ranges import (
    REF_DEFAULT,
    REF_NONE,
    REF_PRINTED,
    flag_value,
    get_reference_table,
    normalize_unit,
    to_decimal,
)


@pytest.fixture
def table():
    return get_reference_table()


# -- the printed range wins ----------------------------------------------------


def test_the_range_printed_on_the_report_is_used_as_is(table):
    """No conversion, no second-guessing: the value and its range came off the
    same page in the same units, and the lab calibrated that range to its own
    analyser. Any normalisation we did could only introduce error."""
    flagged = flag_value(
        name="Haemoglobin", value=8.9, unit="g/dL", ref_low=12.0, ref_high=15.0, table=table
    )

    assert flagged.flag is ValueFlag.LOW
    assert flagged.ref_source == REF_PRINTED
    assert (flagged.ref_low, flagged.ref_high) == (Decimal("12.0"), Decimal("15.0"))


def test_a_printed_range_is_honoured_even_for_a_test_we_have_never_heard_of(table):
    """The fallback table is 18 tests; a lab menu is hundreds. A printed range
    makes any of them flaggable, which is most of why printed wins."""
    flagged = flag_value(
        name="Serum ferritin", value=980, unit="ng/mL", ref_low=30, ref_high=400, table=table
    )

    assert flagged.flag is ValueFlag.HIGH
    assert flagged.ref_source == REF_PRINTED


def test_a_printed_range_beats_our_table_when_they_disagree(table):
    """Our table says female Hb 12–15. A lab printing 11.5–16.0 gets its own
    range respected, and a value of 11.8 is therefore normal, not low."""
    flagged = flag_value(
        name="Hemoglobin",
        value=11.8,
        unit="g/dL",
        ref_low=11.5,
        ref_high=16.0,
        sex=Sex.FEMALE,
        table=table,
    )

    assert flagged.flag is ValueFlag.NORMAL
    assert flagged.ref_source == REF_PRINTED


def test_a_value_on_the_boundary_is_inside_it(table):
    """Inclusive, because that is how every report prints its own range."""
    for value in (12.0, 15.0):
        assert (
            flag_value(
                name="Hemoglobin", value=value, ref_low=12.0, ref_high=15.0, table=table
            ).flag
            is ValueFlag.NORMAL
        )


# -- the fallback table --------------------------------------------------------


def test_the_fallback_table_is_used_only_when_nothing_was_printed(table):
    flagged = flag_value(name="Hemoglobin", value=8.9, unit="g/dL", sex=Sex.FEMALE, table=table)

    assert flagged.flag is ValueFlag.LOW
    assert flagged.ref_source == REF_DEFAULT
    assert (flagged.ref_low, flagged.ref_high) == (Decimal("12.0"), Decimal("15.0"))


def test_the_fallback_range_follows_the_patients_sex(table):
    """12.5 g/dL is normal for a woman (12.0–15.0) and low for a man
    (13.0–17.0). Getting this wrong flags half the OPD's haemoglobins in one
    direction or the other."""
    assert (
        flag_value(name="Hemoglobin", value=12.5, unit="g/dL", sex=Sex.FEMALE, table=table).flag
        is ValueFlag.NORMAL
    )
    assert (
        flag_value(name="Hemoglobin", value=12.5, unit="g/dL", sex=Sex.MALE, table=table).flag
        is ValueFlag.LOW
    )


def test_an_unknown_sex_uses_the_shared_range_or_none_at_all(table):
    """Sex is unknown on a walk-in registered by phone number alone. Where a
    test has a shared range we use it; where it has only male and female rows we
    say nothing, rather than picking one sex's range arbitrarily."""
    # Potassium has a single "any" row — usable without knowing sex.
    assert (
        flag_value(name="Potassium", value=4.0, unit="mmol/L", sex=None, table=table).flag
        is ValueFlag.NORMAL
    )

    # Creatinine has male/female rows only, so there is no honest range to use.
    flagged = flag_value(name="Creatinine", value=1.2, unit="mg/dL", sex=None, table=table)
    assert flagged.flag is ValueFlag.UNKNOWN
    assert flagged.ref_source == REF_NONE


def test_names_are_matched_through_the_spellings_labs_actually_print(table):
    for name in ("SGPT", "ALT (SGPT)", "alt", "Alanine aminotransferase"):
        flagged = flag_value(name=name, value=95, unit="U/L", table=table)
        assert flagged.flag is ValueFlag.HIGH, name
        assert flagged.ref_source == REF_DEFAULT

    for name in ("S. Creatinine", "Serum Creatinine", "creat"):
        assert flag_value(name=name, value=0.9, unit="mg/dL", sex=Sex.MALE, table=table).flag is (
            ValueFlag.NORMAL
        ), name


def test_an_unrecognised_test_name_is_not_fuzzy_matched(table):
    """`app.formulary` fuzzy-matches drug names because it *suggests* to a doctor
    who then chooses. Nobody chooses here — a fuzzy hit would flag one test's
    value against another test's range, silently."""
    flagged = flag_value(name="Haemoglobin A1c", value=8.9, unit="%", table=table)

    assert flagged.flag is ValueFlag.UNKNOWN
    assert flagged.ref_source == REF_NONE
    assert "no reference range" in flagged.reason


# -- units ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("printed", "folded"),
    [
        ("10³/µL", "10^3/ul"),
        ("x10^3/uL", "10^3/ul"),
        ("cells/cu.mm", "cells/cumm"),
        ("  G/DL ", "g/dl"),
        ("10⁹/L", "10^9/l"),
    ],
)
def test_unit_spellings_fold_together(printed, folded):
    assert normalize_unit(printed) == folded


def test_a_convertible_unit_is_converted_and_the_conversion_is_shown(table):
    """0.9 lakh/cumm is 90 ×10³/µL — thrombocytopenia. The doctor sees both the
    printed figure and what we compared, because silently rewriting what the
    report said is how trust in this screen dies."""
    flagged = flag_value(name="Platelet count", value="0.9", unit="lakh/cumm", table=table)

    assert flagged.flag is ValueFlag.LOW
    assert flagged.canonical_value == Decimal("90.0")
    assert flagged.canonical_unit == "10^3/uL"


def test_an_unconvertible_unit_is_refused_rather_than_assumed(table):
    """The one that matters most. A platelet count of 150 is normal in 10³/µL
    and profoundly abnormal in /µL, and nothing on the page says which if we
    guess. `UNKNOWN` shows the number unjudged; a guess shows a wrong flag."""
    flagged = flag_value(name="Platelet count", value=150, unit="fictional/units", table=table)

    assert flagged.flag is ValueFlag.UNKNOWN
    assert flagged.ref_source == REF_NONE
    assert "not convertible" in flagged.reason


def test_a_missing_unit_with_no_printed_range_is_unknown_not_assumed(table):
    assert (
        flag_value(name="Platelet count", value=150, unit=None, table=table).flag
        is ValueFlag.UNKNOWN
    )


# -- critical thresholds -------------------------------------------------------


def test_critical_thresholds_come_only_from_the_curated_table(table):
    """They order what the doctor reads first. They are not an urgency decision
    and they page nobody — escalation is deterministic clinical logic elsewhere."""
    assert (
        flag_value(name="Platelet count", value=30, unit="10^3/uL", table=table).flag
        is ValueFlag.CRITICAL_LOW
    )
    assert (
        flag_value(name="Potassium", value=6.9, unit="mmol/L", table=table).flag
        is ValueFlag.CRITICAL_HIGH
    )


def test_a_printed_range_never_manufactures_a_critical_flag(table):
    """We do not know the lab's critical thresholds, only its reference range.
    Inventing `critical` from a printed range would be us deciding severity."""
    flagged = flag_value(
        name="Platelet count", value=30, unit="10^3/uL", ref_low=150, ref_high=410, table=table
    )

    assert flagged.flag is ValueFlag.LOW


# -- the numbers themselves ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (8.9, Decimal("8.9")),
        ("8.9", Decimal("8.9")),
        ("1,200", Decimal("1200")),
        ("<0.5", Decimal("0.5")),
        (">1000", Decimal("1000")),
        ("  12.0 ", Decimal("12.0")),
        (0, Decimal("0")),
    ],
)
def test_the_shapes_a_report_actually_prints_parse(raw, expected):
    assert to_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["", "not detected", None, "n/a", True, [1]])
def test_anything_that_is_not_a_number_stays_unjudged(raw):
    assert to_decimal(raw) is None


def test_a_float_never_brings_its_binary_repr_into_a_comparison():
    """Decimal via str(), so 0.1 + 0.2 arithmetic cannot make a boundary value
    fall out of its own range."""
    assert to_decimal(0.1) + to_decimal(0.2) == Decimal("0.3")


def test_a_non_numeric_value_is_reported_as_such_not_silently_normal(table):
    flagged = flag_value(name="Hemoglobin", value="not detected", ref_low=12, ref_high=15)

    assert flagged.flag is ValueFlag.UNKNOWN
    assert flagged.reason == "value is not a number"


# -- the table itself ----------------------------------------------------------


def test_the_shipped_table_is_marked_unreviewed(table):
    """It stays `review_pending` until an oncologist signs it off (doc 21 §8.2).
    A flag derived from it is shown as the weaker signal — this flag is what the
    UI keys off, so flipping it by accident silently promotes every grey row."""
    assert not table.reviewed
    assert table.status == "review_pending"


def test_every_row_in_the_shipped_table_is_usable(table):
    """A typo'd unit key or an inverted range would not fail anything at import;
    it would just quietly stop flagging, or flag everything."""
    for key, test in table._tests.items():
        assert test.units, f"{key}: no units"
        assert normalize_unit(test.unit) in test.units, f"{key}: canonical unit has no factor"
        assert test.units[normalize_unit(test.unit)] == 1, f"{key}: canonical factor must be 1"
        assert test.ranges, f"{key}: no ranges"
        for sex, low, high in test.ranges:
            assert sex in {"any", "male", "female"}, f"{key}: bad sex {sex}"
            assert low is None or high is None or low < high, f"{key}: inverted range"
        if test.critical_low is not None:
            low = test.bounds(Sex.FEMALE)[0]
            assert low is None or test.critical_low <= low, f"{key}: critical_low inside range"
        if test.critical_high is not None:
            high = test.bounds(Sex.FEMALE)[1]
            assert high is None or test.critical_high >= high, f"{key}: critical_high inside range"
