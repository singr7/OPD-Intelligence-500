"""What may leave the box in a model call, and what may not (doc 21 §5.3).

One implementation, used by every cloud-facing clinical prompt: MRD
summarisation now, the research assistant next. Two copies of this rule is two
places for it to drift, and the failure mode of drift here is a patient's name
in a vendor's logs.

## The rule

A cloud model needs enough to be clinically useful and nothing that identifies a
person. So:

  **out**  age band, sex, diagnosis text, lab values, units, flags, cycle number
  **never** name, phone, MRN, UHC/ABHA id, village, district, caregiver details,
            dates of birth, our own row ids

Ages go out as a band ("50-59") rather than a number: a year of birth is a
quasi-identifier, and no oncology summary is worse for the rounding. Dates of a
*report* stay, because "Hb was 8.9 on the 30th" is the clinical content; a date
of birth does not.

## Why a denylist would be the wrong shape

This builds a new dict from named fields. It never takes a patient object and
removes the dangerous keys, because that inverts the failure: a column added to
`Patient` next year would arrive at the vendor by default, and nobody would
notice until it was already in a log. Here a new field is invisible until
someone writes a line for it — the safe direction to fail.

`assert_clean` is the belt to that braces: it is called on every context this
module builds, and it refuses on identifier-shaped keys and on anything that
looks like a phone number, so a future edit that adds `"patient_name"` to a
payload fails a test rather than a patient.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.enums import Sex
from app.models.patient import Patient

#: Key names that must never appear in a payload bound for a vendor, at any
#: depth. Substring-matched: `patient_name`, `caregiver_phone` and `mrn_number`
#: all have to fail, and enumerating exact spellings is how one gets missed.
FORBIDDEN_KEY_PARTS: frozenset[str] = frozenset(
    {
        "name",
        "phone",
        "mobile",
        "mrn",
        "uhc",
        "abha",
        "external_id",
        "village",
        "district",
        "address",
        "dob",
        "birth",
        "caregiver",
        "email",
        "aadhaar",
        "aadhar",
        "patient_id",
        "id",
    }
)

#: Keys allowed to survive the `id` substring check — they are not identifiers.
_ID_EXEMPT: frozenset[str] = frozenset({"valid", "avoid", "confidence"})

#: An Indian mobile number in any spacing a free-text field might carry:
#: `9876543210`, `98765 43210`, `98765-43210`, `+91 98765 43210`.
#:
#: Ten digits opening 6-9, written as one run or split 5+5 — which is how these
#: are actually written here, and what an earlier 3+3+4 pattern (a US habit, not
#: an Indian one) missed.
#:
#: Deliberately *not* "ten digits with separators anywhere": that version matched
#: a row of lab numbers like "700 800 900 1000" by stitching them together, and
#: a guard that fires on a table of platelet counts is a guard callers route
#: around. The digit-boundary assertions keep longer runs (a 12-digit accession
#: number) out from the other side.
_PHONE = re.compile(r"(?<!\d)(?:\+?\s*91[\s-]*)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")


class PHILeak(AssertionError):
    """A payload bound for a vendor carried something identifying.

    Deliberately an assertion: it means a *coding* error in this repo, not a bad
    input. It should stop a test, not be caught and handled at runtime.
    """


def age_band(age: int | None) -> str:
    """A year of birth is a quasi-identifier; a decade is not, and no oncology
    summary reads worse for it."""
    if age is None or age < 0:
        return "unknown"
    if age < 18:
        # Not banded into decades: paediatric vs adult is clinically load-bearing
        # in a way that 34 vs 37 is not, and this platform's population is adult.
        return "under 18"
    if age >= 90:
        return "90+"
    decade = (age // 10) * 10
    return f"{decade}-{decade + 9}"


def patient_context(
    patient: Patient,
    *,
    diagnosis: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The cloud-safe view of one patient. Built from named fields, never filtered.

    `diagnosis` is passed in rather than read off the patient because there is
    no diagnosis column: it comes from the latest *signed* note, and only the
    caller knows whether it has one.
    """
    context: dict[str, Any] = {
        "age_band": age_band(patient.age),
        "sex": patient.sex.value if isinstance(patient.sex, Sex) else "unknown",
    }
    if diagnosis:
        context["diagnosis"] = diagnosis.strip()[:400]
    if extra:
        context.update(extra)
    assert_clean(context)
    return context


def assert_clean(payload: Any, *, path: str = "context") -> None:
    """Refuse a payload carrying an identifier-shaped key or a phone number.

    Walks dicts and lists to any depth: a value nested three levels down in a
    lab payload leaves the box just as completely as a top-level one.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if lowered not in _ID_EXEMPT:
                for part in FORBIDDEN_KEY_PARTS:
                    # `id` only as a whole word or suffix — `valid_until` is fine,
                    # `patient_id` and `id` are not.
                    if part == "id":
                        if lowered == "id" or lowered.endswith("_id"):
                            raise PHILeak(f"{path}.{key}: identifier field bound for a vendor")
                    elif part in lowered:
                        raise PHILeak(f"{path}.{key}: identifying field bound for a vendor")
            assert_clean(value, path=f"{path}.{key}")
    elif isinstance(payload, list | tuple):
        for index, item in enumerate(payload):
            assert_clean(item, path=f"{path}[{index}]")
    elif isinstance(payload, str) and _PHONE.search(payload):
        raise PHILeak(f"{path}: value contains something shaped like a phone number")


def scrub_text(text: str) -> str:
    """Last-ditch redaction for free text we did not construct.

    Used on text a *model* wrote about a document — a histopath impression can
    carry the patient's name in the report header, and that header was on a page
    we sent for extraction, not something we typed. Structured context should
    never need this; it exists because prose is not structured.
    """
    return _PHONE.sub("[redacted]", text)
