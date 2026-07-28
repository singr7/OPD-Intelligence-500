"""Patient identity handling shared by online and offline kiosk intake.

The kiosk collects four registration facts before the clinical walk starts —
name, age, sex and phone (S-UX.6). They are normalised here, once, so the online
route and the offline sync replay land the identical row: a demographic that
differs between the two paths is a patient the desk cannot reconcile.

Everything except the name is optional, and an unparseable optional value becomes
``None`` rather than an error. A rejected intake at a kiosk is a patient sent back
to a paper queue; a missing age is a question the doctor asks in the room.
"""

from __future__ import annotations

import re
import unicodedata

from app.models.enums import Sex

MAX_PATIENT_NAME_LENGTH = 200
WALK_IN_FALLBACK_NAME = "Walk-in patient"

#: Nobody at an OPD desk is 0 or 130. Outside this the number is a typo or a
#: year of birth, and a wrong age on a prescription is worse than a blank one.
MIN_PATIENT_AGE = 0
MAX_PATIENT_AGE = 120

#: Indian mobile numbers, with or without the +91/0 prefix the patient typed.
_PHONE_DIGITS = re.compile(r"\D+")


class PatientNameError(ValueError):
    """A supplied patient name is unsafe or outside the persisted contract."""


def normalize_patient_name(value: str | None) -> str:
    """Preserve the patient's script while enforcing the shared-terminal boundary.

    ``None`` is the rolling-deploy compatibility path for older kiosks. New
    clients always send a name; an explicitly supplied blank or control-bearing
    value is invalid rather than silently becoming an anonymous patient.
    """

    if value is None:
        return WALK_IN_FALLBACK_NAME
    normalized = value.strip()
    if not normalized:
        raise PatientNameError("patient name must not be blank")
    if len(normalized) > MAX_PATIENT_NAME_LENGTH:
        raise PatientNameError(
            f"patient name must be at most {MAX_PATIENT_NAME_LENGTH} characters"
        )
    if any(unicodedata.category(char) == "Cc" for char in normalized):
        raise PatientNameError("patient name must not contain control characters")
    return normalized


def normalize_patient_age(value: int | str | None) -> int | None:
    """Years, or ``None`` when the patient did not say. Never raises.

    An out-of-range or unreadable age is dropped rather than stored: the kiosk's
    number pad can produce a fat-fingered ``222``, and an invented age travels
    onto a prescription.
    """

    if value is None or value == "":
        return None
    try:
        age = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if age < MIN_PATIENT_AGE or age > MAX_PATIENT_AGE:
        return None
    return age


def normalize_patient_sex(value: str | None) -> Sex | None:
    """The kiosk's sex chip → the stored enum, or ``None`` when not answered."""

    if not value:
        return None
    candidate = str(value).strip().lower()
    try:
        return Sex(candidate)
    except ValueError:
        return None


def normalize_patient_phone(value: str | None) -> str:
    """Digits only, without a country/trunk prefix. Empty when not given.

    ``Patient.phone`` is a non-null column with an empty-string default for
    anonymous walk-ins, so this returns ``""`` rather than ``None`` — the column,
    not this function, is where that contract lives.
    """

    if not value:
        return ""
    digits = _PHONE_DIGITS.sub("", str(value))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    # A number too short to dial is a half-typed one; keep the record honest.
    if len(digits) < 10:
        return ""
    return digits[:20]
