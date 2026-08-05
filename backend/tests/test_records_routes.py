"""The scan and read surfaces (doc 21 §1).

The capture half is a coordinator on a phone at a busy desk; the read half is a
doctor in the room. What is asserted here is mostly the boundary between them,
and the handling of page bytes — the most identifying object this system holds.
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.clinical import MedicalDocument
from app.models.enums import DocumentKind, DocumentStatus, Role, Sex
from app.providers.llm import FakeLLMScript
from app.providers.objectstore import FakeObjectStore

JPEG = b"\xff\xd8\xff\xe0a-photographed-lab-report"

EXTRACT_REPLY = {
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
        }
    ],
    "narrative_findings": [],
    "illegible_regions": [],
}


def _headers(settings: Settings, user) -> dict[str, str]:
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def clinic(session: AsyncSession):
    built = await f.build_clinic(session)
    built["patient"].sex = Sex.FEMALE
    built["patient"].external_id = "UHC-48901"
    coordinator = f.make_user(built["hospital"], role=Role.COORDINATOR)
    session.add(coordinator)
    await session.flush()
    built["coordinator"] = coordinator
    return built


@pytest.fixture
def staff_headers(settings, clinic):
    return _headers(settings, clinic["coordinator"])


@pytest.fixture
def doctor_headers(settings, clinic):
    return _headers(settings, clinic["user"])


async def _scan_one(
    client: AsyncClient, headers, patient_id, *, pages: int = 1, kind: str = "lab"
) -> str:
    """The coordinator's whole interaction, as the phone makes it."""
    created = await client.post(
        "/records/documents",
        json={"patient_id": str(patient_id), "kind": kind},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]

    for _ in range(pages):
        uploaded = await client.post(
            f"/records/documents/{document_id}/pages",
            files={"file": ("page.jpg", JPEG, "image/jpeg")},
            headers=headers,
        )
        assert uploaded.status_code == 200, uploaded.text

    done = await client.post(f"/records/documents/{document_id}/complete", headers=headers)
    assert done.status_code == 200, done.text
    return document_id


# -- capture -------------------------------------------------------------------


async def test_a_coordinator_scans_a_report_in_three_calls(
    client, session, clinic, staff_headers, object_store
):
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id, pages=3)

    document = await session.get(MedicalDocument, uuid.UUID(document_id))
    assert document.status is DocumentStatus.CAPTURED
    assert document.pages == 3
    assert document.kind is DocumentKind.LAB
    assert document.captured_by == clinic["coordinator"].id
    # The bytes are in the store, not in Postgres.
    assert len(object_store.objects) == 3
    assert await object_store.get(document.object_keys[0]) == JPEG


async def test_pages_arrive_one_at_a_time_so_an_interrupted_scan_keeps_what_it_got(
    client, session, clinic, staff_headers, object_store
):
    """A coordinator called away mid-report has the pages so far, not nothing."""
    created = await client.post(
        "/records/documents",
        json={"patient_id": str(clinic["patient"].id), "kind": "histopath"},
        headers=staff_headers,
    )
    document_id = created.json()["id"]
    await client.post(
        f"/records/documents/{document_id}/pages",
        files={"file": ("page.jpg", JPEG, "image/jpeg")},
        headers=staff_headers,
    )

    document = await session.get(MedicalDocument, uuid.UUID(document_id))
    assert document.pages == 1
    assert document.status is DocumentStatus.CAPTURING  # not extractable yet
    assert len(object_store.objects) == 1


async def test_a_page_that_is_not_an_image_is_refused(client, clinic, staff_headers):
    created = await client.post(
        "/records/documents",
        json={"patient_id": str(clinic["patient"].id)},
        headers=staff_headers,
    )
    document_id = created.json()["id"]

    response = await client.post(
        f"/records/documents/{document_id}/pages",
        files={"file": ("report.pdf", b"%PDF-1.4", "application/pdf")},
        headers=staff_headers,
    )

    assert response.status_code == 415


async def test_an_oversized_page_is_refused_with_the_reason(
    client, clinic, staff_headers, settings
):
    """The phone downscales before upload; this is what happens when it did not."""
    created = await client.post(
        "/records/documents",
        json={"patient_id": str(clinic["patient"].id)},
        headers=staff_headers,
    )
    document_id = created.json()["id"]
    huge = b"\xff\xd8" + b"x" * settings.mrd_max_page_bytes

    response = await client.post(
        f"/records/documents/{document_id}/pages",
        files={"file": ("page.jpg", huge, "image/jpeg")},
        headers=staff_headers,
    )

    assert response.status_code == 413
    assert "downscale" in response.json()["detail"]


async def test_a_document_for_someone_elses_visit_is_refused(
    client, session, clinic, staff_headers
):
    """A mis-tapped patient in the picker must not file a report against a
    stranger's visit — the wrong patient's lab values on a doctor's screen is
    the worst outcome this module has."""
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()
    visit = f.make_visit(other, clinic["department"])
    session.add(visit)
    await session.flush()

    response = await client.post(
        "/records/documents",
        json={"patient_id": str(clinic["patient"].id), "visit_id": str(visit.id)},
        headers=staff_headers,
    )

    assert response.status_code == 422


async def test_a_document_for_an_unknown_patient_is_refused(client, staff_headers):
    response = await client.post(
        "/records/documents",
        json={"patient_id": str(uuid.uuid4())},
        headers=staff_headers,
    )

    assert response.status_code == 404


async def test_completing_a_capture_nudges_the_extractor(
    client, clinic, staff_headers, object_store, settings, monkeypatch
):
    """The coordinator's screen does not wait for extraction — they have the
    next patient in front of them — but the work must actually be kicked off,
    not left for the next sweep tick a minute later."""
    from app.config import get_settings as real_get_settings
    from app.routes import records as records_routes

    called: list[int] = []

    async def fake_run(limit: int = 5) -> int:
        called.append(limit)
        return 0

    monkeypatch.setattr(records_routes, "run_pending_extractions", fake_run)
    client._transport.app.dependency_overrides[real_get_settings] = lambda: settings.model_copy(
        update={"mrd_enabled": True}
    )

    await _scan_one(client, staff_headers, clinic["patient"].id)

    assert called == [5]


async def test_extraction_can_be_turned_off_without_losing_the_scans(
    client, session, clinic, staff_headers, object_store, monkeypatch
):
    """`MRD_ENABLED=false` is the operator's switch for "no vision model
    configured yet". Pages must still be captured, stored and viewable — the
    machine reading is what is absent, not the record."""
    from app.routes import records as records_routes

    called: list[int] = []
    monkeypatch.setattr(
        records_routes, "run_pending_extractions", lambda limit=5: called.append(limit)
    )

    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)

    assert called == []
    document = await session.get(MedicalDocument, uuid.UUID(document_id))
    assert document.status is DocumentStatus.CAPTURED
    assert (
        await client.get(f"/records/documents/{document_id}/pages/1", headers=staff_headers)
    ).content == JPEG


async def test_capture_needs_a_staff_login(client, clinic):
    """Unlike the kiosk, nothing here is anonymous."""
    response = await client.post(
        "/records/documents", json={"patient_id": str(clinic["patient"].id)}
    )

    assert response.status_code == 401


# -- the worklist --------------------------------------------------------------


async def test_the_worklist_is_todays_arrivals_in_token_order(
    client, session, clinic, staff_headers
):
    from app import queue as queue_svc

    for token_no in (2, 1):
        patient = f.make_patient(clinic["hospital"])
        session.add(patient)
        await session.flush()
        visit = f.make_visit(
            patient, clinic["department"], date=queue_svc.today(), token_no=token_no
        )
        session.add(visit)
        await session.flush()
        await queue_svc.enqueue(session, visit=visit)
    await session.flush()

    response = await client.get("/records/scan/worklist", headers=staff_headers)

    rows = response.json()
    assert [r["token_no"] for r in rows] == [1, 2]
    assert all(r["patient_id"] for r in rows)


async def test_the_worklist_uses_the_queues_own_definition_of_today(
    client, session, clinic, staff_headers
):
    """The scanner and the coordinator console stand next to each other. When
    this route computed its own "today" from the hospital timezone instead of
    `queue.today()`, the two disagreed between midnight and 05:30 IST and the
    scanner showed an empty list while the console showed a queue."""
    from app import queue as queue_svc

    visit = f.make_visit(
        clinic["patient"], clinic["department"], date=queue_svc.today(), token_no=9
    )
    session.add(visit)
    await session.flush()
    await queue_svc.enqueue(session, visit=visit)
    await session.flush()

    rows = (await client.get("/records/scan/worklist", headers=staff_headers)).json()

    assert [r["token_no"] for r in rows] == [9]


async def test_the_worklist_searches_by_uhc_id_and_phone_but_never_by_name(
    client, session, clinic, staff_headers
):
    """A name search on a staff phone at a public desk turns one shoulder-surfed
    screen into a browsable oncology register."""
    patient = clinic["patient"]
    patient.phone = "+919876543210"
    await session.flush()

    by_uhc = await client.get("/records/scan/worklist?q=UHC-48901", headers=staff_headers)
    by_phone = await client.get("/records/scan/worklist?q=9876543210", headers=staff_headers)
    by_name = await client.get(f"/records/scan/worklist?q={patient.name}", headers=staff_headers)

    assert [r["patient_id"] for r in by_uhc.json()] == [str(patient.id)]
    assert [r["patient_id"] for r in by_phone.json()] == [str(patient.id)]
    assert by_name.json() == []


async def test_the_worklist_shows_how_many_documents_a_patient_already_has(
    client, clinic, staff_headers, object_store
):
    """So a coordinator can see the report is already in, and not scan it twice."""
    await _scan_one(client, staff_headers, clinic["patient"].id)

    rows = (
        await client.get(
            f"/records/scan/worklist?q={clinic['patient'].external_id}", headers=staff_headers
        )
    ).json()

    assert rows[0]["document_count"] == 1


# -- reading -------------------------------------------------------------------


async def test_the_doctor_sees_values_flags_and_summary_after_extraction(
    client, session, clinic, staff_headers, doctor_headers, object_store, llm_fake
):
    llm_fake.queue(
        FakeLLMScript(text=json.dumps(EXTRACT_REPLY)),
        FakeLLMScript(text="Hb 8.9 g/dL (low, range 12-15)."),
    )
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)
    await _extract_now(session, uuid.UUID(document_id), object_store, llm_fake)

    response = await client.get(f"/records/documents/{document_id}", headers=doctor_headers)

    body = response.json()
    assert body["status"] == "summarized"
    assert body["extraction"]["outlier_count"] == 1
    assert body["extraction"]["values"][0]["name"] == "Hemoglobin"
    assert body["extraction"]["values"][0]["flag"] == "low"
    assert body["extraction"]["values"][0]["ref_source"] == "printed"
    assert body["extraction"]["summary_text"].startswith("Hb 8.9")
    # Nobody has vouched for it yet, and the wire says so explicitly.
    assert body["extraction"]["verified"] is False


async def test_a_document_with_no_reading_yet_carries_no_extraction_object(
    client, clinic, staff_headers, doctor_headers, object_store
):
    """Absent, not empty: a client must not be able to mistake "not read yet"
    for "read, and nothing was found"."""
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)

    body = (await client.get(f"/records/documents/{document_id}", headers=doctor_headers)).json()

    assert body["extraction"] is None
    assert body["status"] == "captured"


async def test_a_failed_document_is_still_listed_with_its_reason(
    client, session, clinic, staff_headers, doctor_headers, object_store
):
    """A document is never hidden because a model could not read it — that is
    exactly when the doctor most needs to know the paper exists."""
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)
    document = await session.get(MedicalDocument, uuid.UUID(document_id))
    document.status = DocumentStatus.EXTRACTION_FAILED
    document.failure_reason = "could not be read by the model: gemini http 503"
    await session.flush()

    rows = (
        await client.get(
            f"/records/patients/{clinic['patient'].id}/documents", headers=doctor_headers
        )
    ).json()

    assert len(rows) == 1
    assert rows[0]["status"] == "extraction_failed"
    assert "503" in rows[0]["failure_reason"]
    assert rows[0]["pages"] == 1


async def test_a_document_still_being_captured_is_not_in_the_doctors_list(
    client, session, clinic, staff_headers, doctor_headers, object_store
):
    """Half a report is not a report. It appears when the coordinator says it is
    whole, not while pages are still arriving."""
    created = await client.post(
        "/records/documents",
        json={"patient_id": str(clinic["patient"].id)},
        headers=staff_headers,
    )
    await client.post(
        f"/records/documents/{created.json()['id']}/pages",
        files={"file": ("page.jpg", JPEG, "image/jpeg")},
        headers=staff_headers,
    )

    rows = (
        await client.get(
            f"/records/patients/{clinic['patient'].id}/documents", headers=doctor_headers
        )
    ).json()

    assert rows == []


async def test_a_doctor_can_mark_a_reading_verified(
    client, session, clinic, staff_headers, doctor_headers, object_store, llm_fake
):
    llm_fake.queue(FakeLLMScript(text=json.dumps(EXTRACT_REPLY)), FakeLLMScript(text="Hb 8.9 low."))
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)
    await _extract_now(session, uuid.UUID(document_id), object_store, llm_fake)

    response = await client.post(f"/records/documents/{document_id}/verify", headers=doctor_headers)

    assert response.json()["extraction"]["verified"] is True
    assert response.json()["extraction"]["verified_at"]


async def test_verifying_a_document_with_no_reading_is_refused(
    client, clinic, staff_headers, doctor_headers, object_store
):
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)

    response = await client.post(f"/records/documents/{document_id}/verify", headers=doctor_headers)

    assert response.status_code == 409


# -- page bytes ----------------------------------------------------------------


async def test_an_original_page_is_streamed_under_the_guard(
    client, clinic, staff_headers, object_store
):
    """Deliberately not a signed URL: a link to a patient's lab report that
    keeps working after the session that minted it has a long tail."""
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)

    response = await client.get(f"/records/documents/{document_id}/pages/1", headers=staff_headers)

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"


async def test_page_bytes_need_a_login(client, clinic, staff_headers, object_store):
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)

    assert (await client.get(f"/records/documents/{document_id}/pages/1")).status_code == 401


async def test_a_page_number_outside_the_document_is_a_404(
    client, clinic, staff_headers, object_store
):
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)

    for page in (0, 2, 99):
        response = await client.get(
            f"/records/documents/{document_id}/pages/{page}", headers=staff_headers
        )
        assert response.status_code == 404, page


async def test_a_page_missing_from_the_store_is_gone_not_broken(
    client, clinic, staff_headers, object_store
):
    """A restore that brought back Postgres but not the pages directory. The
    doctor gets a specific answer, not a stack trace or a blank image."""
    document_id = await _scan_one(client, staff_headers, clinic["patient"].id)
    object_store.objects.clear()

    response = await client.get(f"/records/documents/{document_id}/pages/1", headers=staff_headers)

    assert response.status_code == 410
    assert "no longer stored" in response.json()["detail"]


# -- helpers -------------------------------------------------------------------


@pytest.fixture
def llm_fake(providers, settings):
    from app.providers.llm import FakeLLMProvider
    from app.providers.registry import get_llm_provider

    provider = get_llm_provider(settings)
    assert isinstance(provider, FakeLLMProvider)
    return provider


async def _extract_now(
    session: AsyncSession, document_id: uuid.UUID, store: FakeObjectStore, llm
) -> None:
    """Run the pipeline inline. The route schedules this as a background task,
    which ASGITransport runs outside the test's session — so the test drives it
    directly against the session it can see."""
    from app import mrd as mrd_svc
    from app.config import Settings as S

    document = await session.get(MedicalDocument, document_id)
    claimed = await mrd_svc.claim_documents(session, settings=S(env="test"))
    assert [d.id for d in claimed] == [document.id]
    await mrd_svc.process_document(
        session, document, store=store, providers=[llm], settings=S(env="test")
    )
    await session.flush()
