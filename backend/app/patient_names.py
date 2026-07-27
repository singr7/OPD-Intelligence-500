"""Patient-name handling shared by online and offline kiosk intake."""

from __future__ import annotations

import unicodedata

MAX_PATIENT_NAME_LENGTH = 200
WALK_IN_FALLBACK_NAME = "Walk-in patient"


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
