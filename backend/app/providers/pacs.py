"""The PACS, over DICOMweb (plan §2).

The imaging centre's Orthanc holds the studies. This module is how the backend
asks it what a patient has and fetches a radiology report; the *viewing* is done
by a web viewer somebody else already built and already connected to the same
PACS, and this repo does not build or vendor one (plan decision 5).

## The browser never talks to Orthanc

Credentials stay server-side, department scope is checked here, and every fetch
is audited. The one exception is the viewer popup, which is its own
authenticated product — we hand it a StudyInstanceUID in a URL and nothing else,
no token, no patient identifier.

## Why this is not a metered `Provider`

`base.Provider` exists to make vendor calls impossible to leave unmetered, and
that is right for anything billed per token, per second or per message. Orthanc
is our own server on our own account: there is no per-unit price to record, and
metering it would put rows into `usage_events` that reconcile to nothing on the
S18 dashboard. So this follows `app.providers.objectstore` — one interface,
config-selected, with a fake — and skips the billing machinery. The same
argument, written down once there and once here because the next person will
ask.

## The join key is the UHC ID, and that is an operational contract

`Patient.external_id` is matched against the DICOM `PatientID` the modality
registered the study under. If the imaging centre registers studies under
anything else, every lookup returns empty for every patient — correctly, and
indistinguishably from "this patient has had no scans". That was plan §8.1's
open gate; it is now agreed and enforced, which is what made this module
buildable. It is still the thing to check first when a doctor says the scans are
missing.

## QIDO-RS and WADO-RS, and the tags this reads

Study search is QIDO-RS (`/studies?PatientID=…`), which answers with a JSON
array of DICOM attribute objects keyed by hex tag. The five this module reads:

    0020000D  StudyInstanceUID        the handle the viewer takes
    00080020  StudyDate               yyyymmdd, no separators
    00080061  ModalitiesInStudy       CT, MR, CR…
    00081030  StudyDescription        free text, often absent
    00201206  NumberOfStudyRelatedSeries

Everything else Orthanc returns is dropped: a QIDO response carries the
patient's name and birth date, and there is no reason for either to travel any
further into this system than the parse below.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar

import httpx

logger = logging.getLogger(__name__)

#: DICOM attribute tags, by the names this module uses for them.
STUDY_UID = "0020000D"
STUDY_DATE = "00080020"
MODALITIES = "00080061"
DESCRIPTION = "00081030"
SERIES_COUNT = "00201206"

#: A study with more series than this is a research acquisition, not an OPD
#: scan; the count is displayed, never used to limit anything.
_MAX_REASONABLE_SERIES = 10_000


class PacsError(RuntimeError):
    """The PACS could not answer."""


class PacsUnavailable(PacsError):
    """Unreachable, timed out, or refused our credentials.

    Distinct from "no studies" on purpose, all the way up to the screen: a
    doctor told "no imaging on file" when the truth is "we could not ask" has
    been told something false about their patient.
    """


@dataclass(frozen=True, slots=True)
class Study:
    """One study, as the doctor's list renders it.

    No patient identifiers. QIDO returns the patient's name and birth date in
    the same object these come from, and they are dropped at the parse rather
    than carried and ignored — the safe direction to fail (`app.phi`).
    """

    study_uid: str
    #: `None` when the study carried no StudyDate, or an unparseable one. Shown
    #: as "date not recorded"; never defaulted to today, which would sort a
    #: decade-old scan to the top of the list.
    study_date: date | None
    modality: str
    description: str
    series_count: int | None
    #: Whether the study contains an encapsulated report we could stream.
    has_report: bool

    @classmethod
    def parse(cls, raw: Any) -> Study | None:
        """One QIDO-RS attribute object → a study, or None if it has no UID.

        Tolerant of everything except the UID: `StudyDescription` is frequently
        absent, `ModalitiesInStudy` is sometimes a list and sometimes a string,
        and a study with none of them is still a study a doctor can open. A
        study with no UID is not — there would be nothing to hand the viewer.
        """
        if not isinstance(raw, dict):
            return None
        uid = _first(raw.get(STUDY_UID))
        if not uid:
            return None
        return cls(
            study_uid=str(uid),
            study_date=_dicom_date(_first(raw.get(STUDY_DATE))),
            modality=_join(raw.get(MODALITIES)),
            description=str(_first(raw.get(DESCRIPTION)) or "").strip()[:200],
            series_count=_count(_first(raw.get(SERIES_COUNT))),
            # QIDO does not say. Determined at report-fetch time, and the UI
            # asks rather than promising — see `PacsProvider.report`.
            has_report=False,
        )


def _first(attribute: Any) -> Any:
    """DICOM JSON wraps every value: `{"vr": "UI", "Value": ["1.2.840…"]}`."""
    if not isinstance(attribute, dict):
        return None
    values = attribute.get("Value")
    if isinstance(values, list) and values:
        first = values[0]
        # PN (person name) values are objects; nothing this module reads is a
        # PN, and returning the dict would be how one leaked into a log.
        return None if isinstance(first, dict) else first
    return None


def _join(attribute: Any) -> str:
    """`ModalitiesInStudy` is multi-valued: "CT" or ["CT", "PT"]."""
    if not isinstance(attribute, dict):
        return ""
    values = attribute.get("Value")
    if not isinstance(values, list):
        return ""
    parts = [str(v).strip() for v in values if v and not isinstance(v, dict)]
    return "/".join(dict.fromkeys(parts))[:40]


def _dicom_date(value: Any) -> date | None:
    """`yyyymmdd`, DICOM's format. Anything else is None rather than a guess."""
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return None


def _count(value: Any) -> int | None:
    try:
        count = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return count if 0 <= count <= _MAX_REASONABLE_SERIES else None


@dataclass(frozen=True, slots=True)
class Report:
    """An encapsulated radiology report, as bytes to stream."""

    content: bytes
    media_type: str
    filename: str


class PacsProvider(ABC):
    """Study discovery and report retrieval. Two verbs, and no third.

    There is deliberately no `series`, no `instances` and no pixel access. The
    viewer does the viewing (plan decision 5), and every method added here is a
    step towards this repo owning a DICOM client it has no reason to own.
    """

    name: ClassVar[str] = "pacs"

    @abstractmethod
    async def studies(self, *, patient_id: str) -> list[Study]:
        """Every study registered under this DICOM PatientID, newest first.

        An empty list means the PACS answered and had nothing. Unreachable
        raises `PacsUnavailable` — the two must never collapse into one.
        """

    @abstractmethod
    async def report(self, *, study_uid: str) -> Report | None:
        """The study's encapsulated report, or None if it carries none.

        None is an ordinary answer: plenty of studies are acquired before the
        radiologist has reported them, and "not reported yet" is a fact the
        doctor needs rather than an error.
        """


class FakePacsProvider(PacsProvider):
    """The offline PACS. Its acceptance test is the whole module's (plan §2.2).

    Seeded per-patient so a test can say "this UHC ID has two studies" without
    a fixture file, and `fail_with` makes the unreachable path drivable — the
    state that is hardest to produce against a real server and most important to
    render honestly.
    """

    name: ClassVar[str] = "fake"

    def __init__(
        self,
        studies: dict[str, list[Study]] | None = None,
        reports: dict[str, Report] | None = None,
    ):
        self._studies = studies or {}
        self._reports = reports or {}
        self.fail_with: PacsError | None = None
        #: Every lookup this provider was asked to make, for assertions.
        self.calls: list[str] = []

    async def studies(self, *, patient_id: str) -> list[Study]:
        self.calls.append(patient_id)
        if self.fail_with:
            raise self.fail_with
        return list(self._studies.get(patient_id, []))

    async def report(self, *, study_uid: str) -> Report | None:
        if self.fail_with:
            raise self.fail_with
        return self._reports.get(study_uid)


class DicomWebPacsProvider(PacsProvider):
    """Orthanc over DICOMweb — QIDO-RS to search, WADO-RS to retrieve.

    One `httpx` client per provider instance, basic auth from settings, and a
    timeout that is deliberately short: this call sits in the request path of a
    doctor opening a tab, and a PACS that takes twenty seconds should show
    "unreachable" rather than hold the console.
    """

    name: ClassVar[str] = "dicomweb"

    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        password: str = "",
        timeout_seconds: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._auth = (username, password) if username or password else None
        self._timeout = timeout_seconds
        self._client = client

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        client = self._client
        owned = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self._timeout, auth=self._auth)
        try:
            return await client.get(f"{self._base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise PacsUnavailable(f"pacs unreachable: {exc}") from exc
        finally:
            if owned:
                await client.aclose()

    async def studies(self, *, patient_id: str) -> list[Study]:
        response = await self._get(
            "/studies",
            params={
                "PatientID": patient_id,
                # Ask for exactly the tags we parse. Without `includefield`
                # Orthanc returns its default set, which is both larger and
                # missing the series count.
                "includefield": [DESCRIPTION, SERIES_COUNT, MODALITIES],
                "limit": 100,
            },
            headers={"Accept": "application/dicom+json"},
        )
        if response.status_code == 204:
            # QIDO's "no match". Not an error, and not an empty body to parse.
            return []
        if response.status_code >= 400:
            raise PacsUnavailable(f"pacs http {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise PacsUnavailable("pacs returned a body that is not JSON") from exc
        if not isinstance(payload, list):
            raise PacsUnavailable("pacs returned something that is not a study list")

        found = [study for raw in payload if (study := Study.parse(raw))]
        # Newest first: a doctor asking about imaging almost always means the
        # most recent one. Undated studies sort last rather than first — an
        # unknown date is not a recent date.
        found.sort(key=lambda s: (s.study_date is not None, s.study_date), reverse=True)
        return found

    async def report(self, *, study_uid: str) -> Report | None:
        """The encapsulated PDF for this study, if it has one.

        Scoped to `SeriesInstanceUID`-less WADO-RS rendering of encapsulated
        documents: Orthanc serves them from the study endpoint with a PDF
        Accept. A study with no document answers 404 or 406, both of which mean
        "not reported", not "broken".
        """
        response = await self._get(
            f"/studies/{study_uid}",
            headers={"Accept": "application/pdf"},
        )
        if response.status_code in (404, 406, 204):
            return None
        if response.status_code >= 400:
            raise PacsUnavailable(f"pacs http {response.status_code}")

        content = response.content
        if not content:
            return None
        return Report(
            content=content,
            media_type=response.headers.get("content-type", "application/pdf").split(";")[0],
            # Named for the study, not the patient. A file called
            # `sunita-devi-ct.pdf` in a doctor's Downloads folder is a
            # disclosure waiting for a shared laptop.
            filename=f"report-{study_uid[-12:]}.pdf",
        )


__all__ = [
    "DicomWebPacsProvider",
    "FakePacsProvider",
    "PacsError",
    "PacsProvider",
    "PacsUnavailable",
    "Report",
    "Study",
]
