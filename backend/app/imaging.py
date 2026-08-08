"""What the PACS has for this patient (plan §2).

The thin service between the doctor's console and `app.providers.pacs`: resolve
the patient, check the department scope, look the studies up by UHC ID, and tell
the truth about every way that can come back empty.

## Four empty states, and none of them is "no studies"

This is most of the module. A list with nothing in it can mean four different
things, and collapsing them is how a doctor gets told something false about
their patient:

1. **The patient has no UHC ID on file.** Nothing was asked, because there is
   nothing to ask with. The desk can fix this; the doctor should know that.
2. **The PACS is switched off here.** An operator's decision. Nothing was asked.
3. **The PACS could not be reached.** We asked and do not know. This is the one
   that must never render as "no imaging" — a doctor who reads that will stop
   looking.
4. **The PACS answered and had nothing.** The only one that is a fact about the
   patient rather than about us.

`Lookup.state` carries which, and every surface renders it verbatim.

## The join key

`Patient.external_id` is the UHC ID and is matched against the DICOM
`PatientID`. That equivalence is an operational contract with the imaging centre
(plan §8.1) — agreed and enforced as of SESSION-M3, which is what made this
buildable. It is still the first thing to check when a doctor says a scan is
missing: if the modality registered the study under a hospital MRN instead,
lookup returns state 4 for a patient who has had ten scans.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import Visit
from app.models.org import Doctor
from app.models.patient import Patient
from app.providers.pacs import PacsProvider, PacsUnavailable, Report, Study


class ImagingError(Exception):
    """The caller may not do this."""


class LookupState(StrEnum):
    """Why the list is the length it is. Rendered, never inferred from `len`."""

    #: The PACS answered. `studies` may still be empty — that is a fact about
    #: the patient, and the only one of these that is.
    OK = "ok"
    #: `PACS_ENABLED=false`. Nothing was asked.
    DISABLED = "disabled"
    #: No `Patient.external_id`. Nothing was asked, and the desk can fix it.
    NO_UHC_ID = "no_uhc_id"
    #: We asked and do not know. Never render this as "no imaging".
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class Lookup:
    state: LookupState
    studies: tuple[Study, ...] = ()
    #: The vendor's complaint, for the log. Not for the doctor's screen — see
    #: SESSION-M5's finding about showing a clinician "gemini http 503".
    detail: str = ""

    @property
    def answered(self) -> bool:
        return self.state is LookupState.OK


@dataclass(slots=True)
class ImagingConfig:
    """The settings this module actually reads, so tests need not build a whole
    `Settings` to say "the PACS is off"."""

    enabled: bool = False
    viewer_url: str = ""
    aet: str = ""
    dicom_port: int = 0
    extras: dict[str, str] = field(default_factory=dict)


async def studies_for_visit(
    session: AsyncSession,
    *,
    visit_id: uuid.UUID,
    doctor: Doctor,
    provider: PacsProvider,
    config: ImagingConfig,
) -> Lookup:
    """This visit's patient's studies, or the honest reason there are none.

    Scoped by visit rather than by patient id, deliberately. The console always
    has a visit open, and a visit carries the department the S9 card checks
    against — a patient-id-scoped route would let any doctor enumerate any
    patient's imaging by guessing a uuid, which is exactly the check
    `assert_visit_scope` exists to make.
    """
    visit = await assert_visit_scope(session, visit_id=visit_id, doctor=doctor)

    if not config.enabled:
        return Lookup(state=LookupState.DISABLED)

    patient = await session.get(Patient, visit.patient_id)
    uhc_id = (getattr(patient, "external_id", "") or "").strip() if patient else ""
    if not uhc_id:
        return Lookup(state=LookupState.NO_UHC_ID)

    try:
        found = await provider.studies(patient_id=uhc_id)
    except PacsUnavailable as exc:
        return Lookup(state=LookupState.UNREACHABLE, detail=str(exc))

    return Lookup(state=LookupState.OK, studies=tuple(found))


async def report_for_study(
    session: AsyncSession,
    *,
    visit_id: uuid.UUID,
    study_uid: str,
    doctor: Doctor,
    provider: PacsProvider,
    config: ImagingConfig,
) -> Report | None:
    """The study's radiology report, or None if it has not been reported.

    **The study is re-checked against this patient's own list before it is
    fetched.** A StudyInstanceUID is a bearer token otherwise: it is not secret,
    it appears in the viewer URL and in this console's own HTML, and a route
    that streamed any UID to any authenticated doctor would be an enumeration
    hole dressed as a convenience. Costing one extra QIDO call per report view
    is the right trade.
    """
    lookup = await studies_for_visit(
        session, visit_id=visit_id, doctor=doctor, provider=provider, config=config
    )
    if not lookup.answered:
        raise ImagingError(f"cannot fetch a report right now: {lookup.state.value}")
    if not any(study.study_uid == study_uid for study in lookup.studies):
        raise ImagingError("that study does not belong to this patient")

    try:
        return await provider.report(study_uid=study_uid)
    except PacsUnavailable as exc:
        raise ImagingError(f"the pacs could not be reached: {exc}") from exc


def viewer_url(config: ImagingConfig, study_uid: str) -> str:
    """The popup handoff. A study UID and nothing else.

    No token, no patient id, no return URL. The viewer is its own authenticated
    product already connected to the same PACS (plan decision 5); it
    authenticates the doctor itself, and anything more we appended would be a
    credential in a URL that outlives the tab it was opened from.
    """
    if not config.viewer_url or not study_uid:
        return ""
    joiner = "&" if "?" in config.viewer_url else "?"
    return f"{config.viewer_url}{joiner}StudyInstanceUIDs={study_uid}"


async def assert_visit_scope(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> Visit:
    """Your department, or an error that says so — the S9 card's boundary.

    A local copy of the check `app.notes` and `app.research` also carry, and for
    the same reason each of them gives: eight duplicated lines keep these paths
    genuinely separate, where a shared helper would couple them. `app.doctor`
    already carries four copies, so this is the house pattern.
    """
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise ImagingError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise ImagingError("that patient is in another department")
    return visit


__all__ = [
    "ImagingConfig",
    "ImagingError",
    "Lookup",
    "LookupState",
    "assert_visit_scope",
    "report_for_study",
    "studies_for_visit",
    "viewer_url",
]
