"""What may leave the box in a model call (doc 21 §5.3).

One shared minimiser, so there is one place this rule can be got right. These
tests are the reason it can be trusted by a second caller (the research
assistant) without re-reading it.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import Sex
from app.models.patient import Patient
from app.phi import PHILeak, age_band, assert_clean, patient_context, scrub_text


@pytest.fixture
def patient():
    """A patient object, never flushed — this module is pure functions over one."""
    return Patient(
        hospital_id=uuid.uuid4(),
        mrn="MRN-48901",
        name="Lakshmi Nair",
        phone="+919876543210",
        age=52,
        sex=Sex.FEMALE,
        village="Ramgarh",
        district="Alwar",
        external_id="UHC-48901",
    )


def test_the_context_carries_what_a_model_needs_and_nothing_that_names_anyone(patient):
    context = patient_context(patient, diagnosis="Carcinoma breast, post AC-T cycle 3")

    assert context == {
        "age_band": "50-59",
        "sex": "female",
        "diagnosis": "Carcinoma breast, post AC-T cycle 3",
    }
    # Everything identifying about a real patient object stayed behind.
    for absent in ("Lakshmi", "9876543210", "UHC-48901", "Ramgarh", str(patient.id)):
        assert absent not in str(context)


def test_the_context_is_built_from_named_fields_not_filtered(patient):
    """The load-bearing structural choice. A column added to `Patient` next year
    must not reach a vendor by default — it should be invisible until somebody
    writes a line for it. Simulate that: a new attribute appears, and the
    context is unchanged."""
    patient.insurance_policy_number = "POL-99188"  # type: ignore[attr-defined]

    context = patient_context(patient)

    assert "POL-99188" not in str(context)
    assert set(context) == {"age_band", "sex"}


@pytest.mark.parametrize(
    ("age", "band"),
    [(52, "50-59"), (50, "50-59"), (59, "50-59"), (8, "under 18"), (94, "90+"), (None, "unknown")],
)
def test_ages_go_out_as_bands(age, band):
    """A year of birth is a quasi-identifier; a decade is not, and no oncology
    summary reads worse for the rounding."""
    assert age_band(age) == band


def test_an_unknown_sex_does_not_become_a_guess(patient):
    patient.sex = None

    assert patient_context(patient)["sex"] == "unknown"


# -- the guard -----------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"patient_name": "Lakshmi"},
        {"name": "Lakshmi"},
        {"mrn": "MRN-48901"},
        {"uhc_id": "UHC-1"},
        {"caregiver_phone": "+919876543210"},
        {"village": "Ramgarh"},
        {"dob": "1974-02-01"},
        {"patient_id": "3f2a"},
        {"id": "3f2a"},
        {"abha_number": "11-1111"},
        {"email": "a@b.com"},
    ],
)
def test_identifier_shaped_keys_are_refused(payload):
    with pytest.raises(PHILeak):
        assert_clean(payload)


def test_the_guard_reaches_every_depth():
    """A value nested three levels down leaves the box as completely as a
    top-level one."""
    with pytest.raises(PHILeak, match=r"tests\[0\].meta.patient_name"):
        assert_clean({"tests": [{"meta": {"patient_name": "Lakshmi"}}]})


def test_a_phone_number_in_free_text_is_refused_wherever_it_hides():
    """Keys are ours to name; free text is not. A histopath impression can carry
    a header line straight off the scanned page."""
    with pytest.raises(PHILeak, match="phone number"):
        assert_clean({"finding": "Referred by Dr Rao, contact 9876543210"})

    with pytest.raises(PHILeak):
        assert_clean({"findings": ["ok", "call +91 98765 43210"]})


def test_clinical_content_is_not_mistaken_for_an_identifier():
    """The guard has to leave real clinical payloads alone, or callers will
    route around it. Values, units, dates of reports, cycle numbers all pass."""
    assert_clean(
        {
            "age_band": "50-59",
            "sex": "female",
            "diagnosis": "Carcinoma breast",
            "report_date": "2026-07-30",
            "cycle": 3,
            "tests": [
                {"test": "Hemoglobin", "value": "8.9", "unit": "g/dL", "flag": "low"},
                {"test": "Platelet count", "value": "150000", "unit": "/cumm", "valid": True},
            ],
            "confidence": "high",
        }
    )


def test_a_six_digit_number_is_not_a_phone_number():
    """Lab values, token numbers and dates must not trip the phone matcher."""
    assert_clean({"value": "180000", "token": 14, "note": "WBC 11200 cells/cumm"})


@pytest.mark.parametrize(
    "text",
    [
        "700 800 900 1000",  # a row of lab numbers, stitched into 10 digits
        "150 410 250 300 900 12",
        "accession 982345678901234",  # a longer digit run
        "2026-07-30",
        "counts: 4500 6700 8900 1200",
    ],
)
def test_rows_of_lab_numbers_do_not_trip_the_phone_matcher(text):
    """A guard that fires on a table of platelet counts is a guard callers route
    around. This is the false-positive direction, and it matters as much as the
    other one."""
    assert_clean({"note": text})


def test_patient_context_guards_its_own_output(patient):
    """`extra` is caller-supplied, so the constructor cannot assume it is clean."""
    with pytest.raises(PHILeak):
        patient_context(patient, extra={"referring_doctor_phone": "9876543210"})


def test_scrub_text_redacts_a_number_a_model_copied_off_a_page():
    text = scrub_text("Impression: ductal carcinoma. Ref Dr Rao 98765 43210.")

    assert "98765" not in text
    assert "ductal carcinoma" in text
