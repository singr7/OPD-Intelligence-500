"""The PACS stub (M3, plan §2).

Most of this file is about the four ways the study list can be empty, because
that is most of the module: a doctor told "no imaging on file" when the truth is
"we could not reach the PACS" has been told something false about their patient.

The DICOMweb provider is driven against a **fake Orthanc** — an `httpx`
`MockTransport` speaking real QIDO-RS shapes — which is the acceptance test plan
§2.2 asks for. No test here reaches a network.
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import imaging as im
from app import queue as q
from app.models.audit import AuditLog
from app.models.enums import AuditAction, Channel
from app.providers.pacs import (
    DESCRIPTION,
    MODALITIES,
    SERIES_COUNT,
    STUDY_DATE,
    STUDY_UID,
    DicomWebPacsProvider,
    FakePacsProvider,
    PacsUnavailable,
    Report,
    Study,
)

TODAY = q.today()

UHC = "UHC-99001"

CT = Study(
    study_uid="1.2.840.113619.2.55.3.1",
    study_date=date(2026, 7, 30),
    modality="CT",
    description="CT Thorax with contrast",
    series_count=4,
    has_report=False,
)
MR = Study(
    study_uid="1.2.840.113619.2.55.3.2",
    study_date=date(2026, 3, 11),
    modality="MR",
    description="MRI Brain",
    series_count=7,
    has_report=False,
)


def _config(**overrides) -> im.ImagingConfig:
    base = {
        "enabled": True,
        "viewer_url": "https://viewer.example.org/viewer",
        "aet": "RAD-RENVA-PACS",
        "dicom_port": 4242,
    }
    return im.ImagingConfig(**{**base, **overrides})


async def _clinic_with_visit(session: AsyncSession, *, uhc: str | None = UHC):
    clinic = await f.build_clinic(session)
    if uhc is not None:
        clinic["patient"].external_id = uhc
        clinic["patient"].external_id_kind = "uhc"
    else:
        clinic["patient"].external_id = None
    await session.flush()
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    return clinic, visit


# =============================================================================
# The four empty states, which are the module
# =============================================================================


@pytest.mark.asyncio
async def test_the_pacs_answering_with_nothing_is_a_fact_about_the_patient(
    session: AsyncSession,
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    lookup = await im.studies_for_visit(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        provider=FakePacsProvider({UHC: []}),
        config=_config(),
    )
    assert lookup.state is im.LookupState.OK
    assert lookup.studies == ()
    assert lookup.answered


@pytest.mark.asyncio
async def test_an_unreachable_pacs_is_never_no_imaging(session: AsyncSession) -> None:
    """The one that matters most. A doctor who reads "no imaging on file" will
    stop looking, and here we do not know."""
    clinic, visit = await _clinic_with_visit(session)
    provider = FakePacsProvider({UHC: [CT]})
    provider.fail_with = PacsUnavailable("pacs http 502")

    lookup = await im.studies_for_visit(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        provider=provider,
        config=_config(),
    )
    assert lookup.state is im.LookupState.UNREACHABLE
    assert not lookup.answered
    assert lookup.studies == ()
    assert "502" in lookup.detail


@pytest.mark.asyncio
async def test_a_patient_with_no_uhc_id_is_never_asked_about(session: AsyncSession) -> None:
    """Nothing to ask with. The desk can fix this, and the doctor should know
    that rather than conclude the patient has never been scanned."""
    clinic, visit = await _clinic_with_visit(session, uhc=None)
    provider = FakePacsProvider({UHC: [CT]})

    lookup = await im.studies_for_visit(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        provider=provider,
        config=_config(),
    )
    assert lookup.state is im.LookupState.NO_UHC_ID
    assert provider.calls == [], "a patient with no UHC ID must not reach the PACS"


@pytest.mark.asyncio
async def test_the_switch_being_off_asks_nothing(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    provider = FakePacsProvider({UHC: [CT]})

    lookup = await im.studies_for_visit(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        provider=provider,
        config=_config(enabled=False),
    )
    assert lookup.state is im.LookupState.DISABLED
    assert provider.calls == []


@pytest.mark.asyncio
async def test_the_uhc_id_is_the_join_key(session: AsyncSession) -> None:
    """Plan §8.1's contract, as a property. The lookup is by
    `Patient.external_id` and by nothing else — not the MRN, not our row id."""
    clinic, visit = await _clinic_with_visit(session)
    provider = FakePacsProvider({UHC: [CT, MR]})

    lookup = await im.studies_for_visit(
        session,
        visit_id=visit.id,
        doctor=clinic["doctor"],
        provider=provider,
        config=_config(),
    )
    assert provider.calls == [UHC]
    assert clinic["patient"].mrn not in provider.calls
    assert str(clinic["patient"].id) not in provider.calls
    assert [s.study_uid for s in lookup.studies] == [CT.study_uid, MR.study_uid]


# =============================================================================
# Scope, and the viewer handoff
# =============================================================================


@pytest.mark.asyncio
async def test_another_departments_patient_is_refused(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    other_department = f.make_department(clinic["hospital"])
    session.add(other_department)
    await session.flush()
    outsider_user = f.make_user(clinic["hospital"])
    session.add(outsider_user)
    await session.flush()
    outsider = f.make_doctor(outsider_user, other_department)
    session.add(outsider)
    await session.flush()

    with pytest.raises(im.ImagingError, match="another department"):
        await im.studies_for_visit(
            session,
            visit_id=visit.id,
            doctor=outsider,
            provider=FakePacsProvider({UHC: [CT]}),
            config=_config(),
        )


@pytest.mark.asyncio
async def test_no_such_visit_is_refused(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    with pytest.raises(im.ImagingError, match="no such visit"):
        await im.studies_for_visit(
            session,
            visit_id=uuid.uuid4(),
            doctor=clinic["doctor"],
            provider=FakePacsProvider(),
            config=_config(),
        )


def test_the_viewer_url_carries_a_study_uid_and_nothing_else() -> None:
    """No token, no patient id, no return URL. The viewer authenticates the
    doctor itself; anything more here is a credential in a URL that outlives the
    tab it was opened from."""
    url = im.viewer_url(_config(), CT.study_uid)
    assert url == f"https://viewer.example.org/viewer?StudyInstanceUIDs={CT.study_uid}"
    assert "token" not in url and "patient" not in url

    # A viewer URL that already has a query string keeps it.
    with_query = im.viewer_url(_config(viewer_url="https://v.example/?theme=dark"), "1.2.3")
    assert with_query == "https://v.example/?theme=dark&StudyInstanceUIDs=1.2.3"

    # No viewer configured is an empty string, not a broken href.
    assert im.viewer_url(_config(viewer_url=""), "1.2.3") == ""


# =============================================================================
# Reports
# =============================================================================


@pytest.mark.asyncio
async def test_a_study_from_another_patient_cannot_be_streamed(session: AsyncSession) -> None:
    """A StudyInstanceUID is not a secret — it is in the viewer URL and in this
    console's own HTML. A route that streamed any UID to any authenticated
    doctor would be an enumeration hole dressed as a convenience."""
    clinic, visit = await _clinic_with_visit(session)
    provider = FakePacsProvider(
        {UHC: [CT]},
        reports={"9.9.9.somebody-elses": Report(b"%PDF-1.4", "application/pdf", "r.pdf")},
    )

    with pytest.raises(im.ImagingError, match="does not belong"):
        await im.report_for_study(
            session,
            visit_id=visit.id,
            study_uid="9.9.9.somebody-elses",
            doctor=clinic["doctor"],
            provider=provider,
            config=_config(),
        )


@pytest.mark.asyncio
async def test_an_unreported_study_is_none_rather_than_an_error(session: AsyncSession) -> None:
    """Plenty of studies are acquired before the radiologist has reported them.
    "Not reported yet" is a fact the doctor needs, not a fault."""
    clinic, visit = await _clinic_with_visit(session)
    found = await im.report_for_study(
        session,
        visit_id=visit.id,
        study_uid=CT.study_uid,
        doctor=clinic["doctor"],
        provider=FakePacsProvider({UHC: [CT]}, reports={}),
        config=_config(),
    )
    assert found is None


@pytest.mark.asyncio
async def test_a_report_cannot_be_fetched_while_the_pacs_is_down(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    provider = FakePacsProvider({UHC: [CT]})
    provider.fail_with = PacsUnavailable("timeout")

    with pytest.raises(im.ImagingError, match="unreachable"):
        await im.report_for_study(
            session,
            visit_id=visit.id,
            study_uid=CT.study_uid,
            doctor=clinic["doctor"],
            provider=provider,
            config=_config(),
        )


# =============================================================================
# The DICOMweb provider, against a fake Orthanc
# =============================================================================


def _qido(objects: list[dict], status: int = 200) -> DicomWebPacsProvider:
    """A DICOMweb provider wired to a transport that answers like Orthanc."""

    def handle(request: httpx.Request) -> httpx.Response:
        if status == 204:
            return httpx.Response(204)
        return httpx.Response(
            status,
            content=json.dumps(objects),
            headers={"content-type": "application/dicom+json"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return DicomWebPacsProvider("http://pacs.test/dicom-web", client=client)


def _attrs(**tags) -> dict:
    """DICOM JSON wraps every value: {"vr": ..., "Value": [...]}."""
    return {tag: {"Value": [value]} for tag, value in tags.items()}


@pytest.mark.asyncio
async def test_qido_attributes_become_studies() -> None:
    provider = _qido(
        [
            _attrs(
                **{
                    STUDY_UID: "1.2.3",
                    STUDY_DATE: "20260730",
                    DESCRIPTION: "CT Thorax",
                    SERIES_COUNT: "4",
                },
                **{MODALITIES: "CT"},
            )
        ]
    )
    found = await provider.studies(patient_id=UHC)

    assert len(found) == 1
    assert found[0].study_uid == "1.2.3"
    assert found[0].study_date == date(2026, 7, 30)
    assert found[0].modality == "CT"
    assert found[0].description == "CT Thorax"
    assert found[0].series_count == 4


@pytest.mark.asyncio
async def test_studies_come_back_newest_first_and_undated_ones_sort_last() -> None:
    """An unknown date is not a recent date."""
    provider = _qido(
        [
            _attrs(**{STUDY_UID: "older", STUDY_DATE: "20250101"}),
            _attrs(**{STUDY_UID: "undated"}),
            _attrs(**{STUDY_UID: "newer", STUDY_DATE: "20260730"}),
        ]
    )
    assert [s.study_uid for s in await provider.studies(patient_id=UHC)] == [
        "newer",
        "older",
        "undated",
    ]


@pytest.mark.asyncio
async def test_a_study_with_no_uid_is_dropped_and_the_rest_survive() -> None:
    """There would be nothing to hand the viewer. Everything else is optional —
    a study with no description is still a study a doctor can open."""
    provider = _qido(
        [
            {"00080020": {"Value": ["20260101"]}},
            _attrs(**{STUDY_UID: "1.2.3"}),
        ]
    )
    found = await provider.studies(patient_id=UHC)
    assert [s.study_uid for s in found] == ["1.2.3"]
    assert found[0].description == ""
    assert found[0].series_count is None


@pytest.mark.asyncio
async def test_no_content_means_no_studies_not_an_error() -> None:
    """QIDO answers 204 for "no match". That is the PACS working."""
    assert await _qido([], status=204).studies(patient_id=UHC) == []


@pytest.mark.asyncio
async def test_a_pacs_error_status_is_unavailable() -> None:
    with pytest.raises(PacsUnavailable, match="503"):
        await _qido([], status=503).studies(patient_id=UHC)


@pytest.mark.asyncio
async def test_a_transport_failure_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route to host")

    provider = DicomWebPacsProvider(
        "http://pacs.test/dicom-web",
        client=httpx.AsyncClient(transport=httpx.MockTransport(boom)),
    )
    with pytest.raises(PacsUnavailable):
        await provider.studies(patient_id=UHC)


@pytest.mark.asyncio
async def test_a_person_name_never_leaves_the_parse() -> None:
    """QIDO returns the patient's name and birth date in the same object the
    study attributes come from. They are dropped at the parse rather than
    carried and ignored — the safe direction to fail."""
    raw = {
        STUDY_UID: {"Value": ["1.2.3"]},
        # PatientName, as Orthanc actually sends it.
        "00100010": {"vr": "PN", "Value": [{"Alphabetic": "DEVI^SUNITA"}]},
        "00100030": {"Value": ["19680214"]},
    }
    study = Study.parse(raw)
    assert study is not None
    assert "SUNITA" not in repr(study)
    assert "19680214" not in repr(study)


def test_multi_valued_modalities_are_joined_without_duplicates() -> None:
    study = Study.parse(
        {STUDY_UID: {"Value": ["1.2.3"]}, MODALITIES: {"Value": ["CT", "PT", "CT"]}}
    )
    assert study is not None
    assert study.modality == "CT/PT"


def test_an_unparseable_study_date_is_none_rather_than_a_guess() -> None:
    for bad in ("", "2026-07-30", "notadate", "20261345"):
        study = Study.parse({STUDY_UID: {"Value": ["1.2.3"]}, STUDY_DATE: {"Value": [bad]}})
        assert study is not None
        assert study.study_date is None, bad


@pytest.mark.asyncio
async def test_a_report_that_is_not_there_is_none() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = DicomWebPacsProvider(
        "http://pacs.test/dicom-web",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    assert await provider.report(study_uid="1.2.3") is None


@pytest.mark.asyncio
async def test_a_report_filename_carries_no_patient_identifier() -> None:
    """A PDF called `sunita-devi-ct.pdf` in a doctor's Downloads folder is a
    disclosure waiting for a shared laptop."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-1.4 report", headers={"content-type": "application/pdf"}
        )

    provider = DicomWebPacsProvider(
        "http://pacs.test/dicom-web",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    found = await provider.report(study_uid="1.2.840.113619.2.55.3.1")
    assert found is not None
    assert found.content == b"%PDF-1.4 report"
    assert found.media_type == "application/pdf"
    assert "sunita" not in found.filename.lower()
    assert found.filename == "report-619.2.55.3.1.pdf"


@pytest.mark.asyncio
async def test_the_provider_asks_for_the_tags_it_parses() -> None:
    """Without `includefield` Orthanc returns its default set, which is both
    larger than we need and missing the series count."""
    seen: dict[str, list[str]] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.update({k: request.url.params.get_list(k) for k in request.url.params.keys()})
        return httpx.Response(200, content="[]", headers={"content-type": "application/dicom+json"})

    provider = DicomWebPacsProvider(
        "http://pacs.test/dicom-web",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    await provider.studies(patient_id=UHC)

    assert seen["PatientID"] == [UHC]
    assert set(seen["includefield"]) == {DESCRIPTION, SERIES_COUNT, MODALITIES}


# =============================================================================
# The audit trail
# =============================================================================


@pytest.mark.asyncio
async def test_looking_at_a_patients_imaging_is_audited(session: AsyncSession) -> None:
    """The `before_flush` hook cannot see this: a study never enters this
    database, so there is nothing to flush and nothing to attribute."""
    from app.audit import record_read

    _clinic, visit = await _clinic_with_visit(session)
    record_read(
        session, entity="pacs_studies", entity_id=visit.id, meta={"state": "ok", "count": 2}
    )
    await session.flush()

    rows = list(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity == "pacs_studies", AuditLog.entity_id == visit.id
            )
        )
    )
    assert rows, "no audit row for a PACS study lookup"
    assert rows[0].action is AuditAction.READ
    assert rows[0].meta == {"state": "ok", "count": 2}


def test_the_read_action_fits_the_column_it_is_stored_in() -> None:
    """`audit_log.action` is a plain varchar(11) with no CHECK constraint —
    verified against the live column, which is why this needed no migration.
    A longer value would."""
    assert len(AuditAction.READ.value) <= 11


def test_the_pacs_provider_has_two_verbs_and_no_third() -> None:
    """No series, no instances, no pixel access. The viewer does the viewing
    (plan decision 5), and each method added here is a step towards this repo
    owning a DICOM client it has no reason to own."""
    from app.providers.pacs import PacsProvider

    verbs = {name for name in vars(PacsProvider) if not name.startswith("_")}
    assert verbs == {"studies", "report", "name"}
