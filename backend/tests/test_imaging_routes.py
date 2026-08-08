"""The `/imaging` HTTP surface (M3, plan §2).

Two routes, and the interesting assertions are about what they refuse and what
they never say. The four empty states have to survive serialisation as four
distinct answers — a payload that renders them all as `studies: []` would let a
console tell a doctor "no imaging on file" when the PACS was simply unreachable.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import queue as q
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.audit import AuditLog
from app.models.enums import AuditAction, Channel, Role
from app.providers.pacs import FakePacsProvider, PacsUnavailable, Report, Study

TODAY = q.today()
UHC = "UHC-77002"

pytestmark = pytest.mark.asyncio

CT = Study(
    study_uid="1.2.840.113619.2.55.3.1",
    study_date=date(2026, 7, 30),
    modality="CT",
    description="CT Thorax with contrast",
    series_count=4,
    has_report=False,
)


def _headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


async def _clinic_with_visit(session: AsyncSession, *, uhc: str | None = UHC):
    clinic = await f.build_clinic(session)
    clinic["patient"].external_id = uhc
    await session.flush()
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    return clinic, visit


def _use_pacs(monkeypatch, settings: Settings, provider: FakePacsProvider) -> FakePacsProvider:
    monkeypatch.setattr("app.routes.imaging.pacs_provider", lambda s=None: provider)
    monkeypatch.setattr(settings, "pacs_enabled", True)
    monkeypatch.setattr(settings, "pacs_viewer_url", "https://viewer.example.org/v")
    return provider


# =============================================================================
# The flow
# =============================================================================


async def test_the_study_list_carries_a_ready_made_viewer_url(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """Built server-side so the console never composes a viewer URL of its own,
    and never learns the viewer's shape."""
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(monkeypatch, settings, FakePacsProvider({UHC: [CT]}))

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["state"] == "ok"
    assert body["aet"] == "RAD-RENVA-PACS"
    assert len(body["studies"]) == 1
    study = body["studies"][0]
    assert study["study_uid"] == CT.study_uid
    assert study["study_date"] == "2026-07-30"
    assert study["modality"] == "CT"
    assert study["series_count"] == 4
    assert study["viewer_url"] == f"https://viewer.example.org/v?StudyInstanceUIDs={CT.study_uid}"


async def test_no_patient_identifier_reaches_the_payload(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The UHC ID is what we send *to* the PACS; it has no business coming back
    to the browser, and neither does the name or the MRN."""
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(monkeypatch, settings, FakePacsProvider({UHC: [CT]}))

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies", headers=_headers(settings, clinic["user"])
    )
    assert clinic["patient"].name not in resp.text
    assert clinic["patient"].mrn not in resp.text
    assert UHC not in resp.text


# =============================================================================
# The four empty states, still four after serialisation
# =============================================================================


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("answered_empty", "ok"),
        ("unreachable", "unreachable"),
        ("no_uhc", "no_uhc_id"),
        ("disabled", "disabled"),
    ],
)
async def test_every_empty_list_says_why_it_is_empty(
    client: AsyncClient,
    session: AsyncSession,
    settings: Settings,
    monkeypatch,
    setup: str,
    expected: str,
) -> None:
    """The module's whole point, held at the wire. If these ever collapse into
    one shape, a console can tell a doctor something false about their patient."""
    clinic, visit = await _clinic_with_visit(session, uhc=None if setup == "no_uhc" else UHC)
    provider = FakePacsProvider({UHC: []})
    if setup == "unreachable":
        provider.fail_with = PacsUnavailable("pacs http 502")
    _use_pacs(monkeypatch, settings, provider)
    if setup == "disabled":
        monkeypatch.setattr(settings, "pacs_enabled", False)

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == expected
    assert resp.json()["studies"] == []


async def test_the_vendors_complaint_is_not_in_the_payload(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """The M5 finding, applied here from the start: a doctor reading "pacs http
    502" learns nothing they can act on. The state is the contract; the detail
    is in the log."""
    clinic, visit = await _clinic_with_visit(session)
    provider = FakePacsProvider({UHC: []})
    provider.fail_with = PacsUnavailable("pacs http 502 from orthanc-prod-1")
    _use_pacs(monkeypatch, settings, provider)

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies", headers=_headers(settings, clinic["user"])
    )
    assert resp.json()["state"] == "unreachable"
    assert "502" not in resp.text
    assert "orthanc-prod-1" not in resp.text


# =============================================================================
# Reports
# =============================================================================


async def test_a_report_streams_inline_with_no_identifier_in_the_filename(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(
        monkeypatch,
        settings,
        FakePacsProvider(
            {UHC: [CT]},
            reports={CT.study_uid: Report(b"%PDF-1.4 x", "application/pdf", "report-3.1.pdf")},
        ),
    )

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies/{CT.study_uid}/report",
        headers=_headers(settings, clinic["user"]),
    )
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 x"
    assert resp.headers["content-type"] == "application/pdf"
    # `inline`, not `attachment`: a PDF in Downloads is a patient's report left
    # on a shared laptop.
    assert resp.headers["content-disposition"].startswith("inline;")
    assert clinic["patient"].name.split()[0].lower() not in resp.headers["content-disposition"]


async def test_an_unreported_study_is_a_404_that_says_so(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(monkeypatch, settings, FakePacsProvider({UHC: [CT]}, reports={}))

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies/{CT.study_uid}/report",
        headers=_headers(settings, clinic["user"]),
    )
    assert resp.status_code == 404
    assert "not been reported yet" in resp.json()["detail"]


async def test_another_patients_study_uid_cannot_be_streamed(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """A StudyInstanceUID is not a secret — it is in the viewer URL and in this
    console's own HTML. Enumeration must not work."""
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(
        monkeypatch,
        settings,
        FakePacsProvider(
            {UHC: [CT]},
            reports={"9.9.9.elsewhere": Report(b"%PDF secret", "application/pdf", "x.pdf")},
        ),
    )

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies/9.9.9.elsewhere/report",
        headers=_headers(settings, clinic["user"]),
    )
    assert resp.status_code == 404
    assert b"secret" not in resp.content


# =============================================================================
# Who may look, and the trail they leave
# =============================================================================


async def test_a_coordinator_cannot_see_a_patients_imaging(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(monkeypatch, settings, FakePacsProvider({UHC: [CT]}))
    coordinator = f.make_user(clinic["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies", headers=_headers(settings, coordinator)
    )
    assert resp.status_code == 403


async def test_another_departments_patient_is_refused(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(monkeypatch, settings, FakePacsProvider({UHC: [CT]}))
    other_department = f.make_department(clinic["hospital"])
    session.add(other_department)
    await session.flush()
    outsider_user = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add(outsider_user)
    await session.flush()
    session.add(f.make_doctor(outsider_user, other_department))
    await session.flush()

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies", headers=_headers(settings, outsider_user)
    )
    assert resp.status_code == 403


async def test_looking_is_audited_even_when_there_is_nothing_to_see(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    """ "This doctor asked what scans this patient has" is the event, and it
    happened whether or not there were any."""
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(monkeypatch, settings, FakePacsProvider({UHC: []}))

    resp = await client.get(
        f"/imaging/visits/{visit.id}/studies", headers=_headers(settings, clinic["user"])
    )
    assert resp.status_code == 200

    rows = list(
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity == "pacs_studies", AuditLog.entity_id == visit.id
            )
        )
    )
    assert rows, "no audit row for a PACS lookup"
    assert rows[-1].action is AuditAction.READ
    assert rows[-1].meta["state"] == "ok"
    assert rows[-1].meta["count"] == 0


async def test_viewing_a_report_is_audited(
    client: AsyncClient, session: AsyncSession, settings: Settings, monkeypatch
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    _use_pacs(
        monkeypatch,
        settings,
        FakePacsProvider(
            {UHC: [CT]},
            reports={CT.study_uid: Report(b"%PDF-1.4 x", "application/pdf", "r.pdf")},
        ),
    )

    await client.get(
        f"/imaging/visits/{visit.id}/studies/{CT.study_uid}/report",
        headers=_headers(settings, clinic["user"]),
    )

    rows = list(await session.scalars(select(AuditLog).where(AuditLog.entity == "pacs_report")))
    assert rows, "no audit row for a report view"
    assert rows[-1].meta["study_uid"] == CT.study_uid
    assert rows[-1].action is AuditAction.READ


async def test_the_imaging_surface_has_no_verb_but_get(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    """Nothing here writes, orders or annotates. The viewer does the viewing and
    the radiologist does the reporting; this is a window, and a window with a
    POST on it is a different thing."""
    from app.routes.imaging import router

    methods = {m for route in router.routes for m in getattr(route, "methods", set())}
    assert methods == {"GET"}, f"the imaging surface has grown a verb: {methods}"
