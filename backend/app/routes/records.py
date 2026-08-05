"""Scanning a patient's paper records, and reading them back (doc 21 §1).

Two audiences, one resource:

    coordinator, at the desk, on a phone
      GET  /records/scan/worklist          who is here to scan for
      POST /records/documents              open a document
      POST /records/documents/{id}/pages   one photograph
      POST /records/documents/{id}/complete  close it; extraction starts
      POST /records/documents/{id}/retry   after a failure

    doctor, in the room
      GET  /records/patients/{id}/documents  everything on file, newest first
      GET  /records/documents/{id}           one document with its reading
      POST /records/documents/{id}/verify    "I have read this against the pages"

    both
      GET  /records/documents/{id}/pages/{n}  the original photograph

Every route is authenticated: unlike `/kiosk`, nothing here is anonymous. A
scanned oncology report is the most identifying object in this system — it
carries the patient's name, their diagnosis and often their phone number in a
lab header — so page bytes are streamed by the backend under a guard, never
handed out as a signed URL that outlives the session that minted it.

Capture is `require_staff`; reading a document's *reading* is `require_clinical`.
Coordinators upload pages they cannot then browse as a clinical record, which is
the same line the S9 patient card draws.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as date_type
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import mrd as mrd_svc
from app.auth.rbac import Principal, require_clinical, require_staff
from app.config import Settings, get_settings
from app.db import get_session
from app.models.clinical import DocumentExtraction, MedicalDocument, Visit
from app.models.enums import DocumentKind, DocumentStatus, QueueEntryState
from app.models.patient import Patient
from app.models.scheduling import Queue, QueueEntry
from app.providers import ObjectNotFound, ObjectStore
from app.providers.registry import get_object_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/records", tags=["records"])

#: Page bytes are pushed through the app, so the ceiling is enforced twice: here,
#: before the body is read into memory, and again in `mrd.add_page` against the
#: configured limit. This one is the cheap one.
_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


def get_store(settings: Settings = Depends(get_settings)) -> ObjectStore:
    return get_object_store(settings)


# -- wire models ---------------------------------------------------------------


class WorklistRowOut(BaseModel):
    patient_id: uuid.UUID
    visit_id: uuid.UUID | None = None
    token_no: int | None = None
    patient_name: str
    department_name: str = ""
    state: str = ""
    document_count: int = 0


class DocumentOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    visit_id: uuid.UUID | None = None
    kind: str
    status: str
    pages: int
    created_at: str
    failure_reason: str | None = None
    #: Present once a reading exists. Absent — not empty — while it does not,
    #: so a client cannot mistake "not read yet" for "read, nothing found".
    extraction: ExtractionOut | None = None


class FlaggedValueOut(BaseModel):
    name: str
    value_text: str
    unit: str = ""
    ref_low: str | None = None
    ref_high: str | None = None
    flag: str
    ref_source: str
    page: int | None = None
    confidence: str = "low"
    canonical_value: str | None = None
    canonical_unit: str | None = None


class ExtractionOut(BaseModel):
    summary_text: str | None = None
    outlier_count: int = 0
    report_date: str | None = None
    values: list[FlaggedValueOut] = []
    narrative_findings: list[str] = []
    illegible_regions: list[str] = []
    dropped_rows: int = 0
    #: False until a doctor has read it against the original pages. Every
    #: surface that shows this must say so — an unverified machine reading of a
    #: lab report is a draft (doc 21 §1.5).
    verified: bool = False
    verified_at: str | None = None
    prompt_refs: list[str] = []
    #: True when any flag came from our own fallback table rather than a range
    #: printed on the report. The table is unreviewed, so those flags are shown
    #: as the weaker signal they are.
    uses_fallback_ranges: bool = False


class StartDocumentIn(BaseModel):
    patient_id: uuid.UUID
    visit_id: uuid.UUID | None = None
    kind: DocumentKind = DocumentKind.OTHER


class PageOut(BaseModel):
    document_id: uuid.UUID
    page: int
    pages: int


# -- capture (coordinator) -----------------------------------------------------


@router.get("/scan/worklist", response_model=list[WorklistRowOut])
async def scan_worklist(
    q: str = "",
    on: date_type | None = None,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_staff),
) -> list[WorklistRowOut]:
    """Who to scan for: today's arrivals, or a search.

    Today's queue is the default because that is the coordinator's actual
    situation — the patient is standing there with a folder. Search exists for
    the other case: papers handed over after the visit, or before it.
    """
    query = q.strip()
    if query:
        return await _search_patients(session, query)
    return await _todays_arrivals(session, on=on)


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def start_document(
    payload: StartDocumentIn,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_staff),
) -> DocumentOut:
    patient = await session.get(Patient, payload.patient_id)
    if patient is None or patient.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such patient")
    if payload.visit_id is not None:
        visit = await session.get(Visit, payload.visit_id)
        if visit is None or visit.patient_id != payload.patient_id:
            raise HTTPException(status_code=422, detail="that visit is not this patient's")

    document = await mrd_svc.start_document(
        session,
        patient_id=payload.patient_id,
        visit_id=payload.visit_id,
        kind=payload.kind,
        captured_by=principal.id,
    )
    await session.commit()
    await session.refresh(document)
    return _document_out(document, None)


@router.post("/documents/{document_id}/pages", response_model=PageOut)
async def upload_page(
    document_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    session: AsyncSession = Depends(get_session),
    store: ObjectStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(require_staff),
) -> PageOut:
    """One photograph. Uploaded as it is taken, so a coordinator interrupted
    halfway through a report has the pages so far rather than nothing."""
    document = await _load(session, document_id)
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in _ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail=f"unsupported page type {content_type!r}")

    data = await file.read(settings.mrd_max_page_bytes + 1)
    if len(data) > settings.mrd_max_page_bytes:
        raise HTTPException(status_code=413, detail="page is too large — downscale before upload")

    try:
        await mrd_svc.add_page(
            session, document, data, store=store, media_type=content_type, settings=settings
        )
    except mrd_svc.MRDError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return PageOut(document_id=document.id, page=document.pages, pages=document.pages)


@router.post("/documents/{document_id}/complete", response_model=DocumentOut)
async def complete_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(require_staff),
) -> DocumentOut:
    """Close capture. Extraction starts now if it can, and the coordinator's
    screen does not wait for it — they have the next patient in front of them."""
    document = await _load(session, document_id)
    try:
        await mrd_svc.complete_capture(session, document)
    except mrd_svc.MRDError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()

    if settings.mrd_enabled:
        # A nudge, not the only path: the worker sweep picks up anything this
        # misses, and the claim makes running both safe.
        background.add_task(run_pending_extractions)
    return _document_out(document, None)


@router.post("/documents/{document_id}/retry", response_model=DocumentOut)
async def retry_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _: Principal = Depends(require_staff),
) -> DocumentOut:
    """Another go at a document the model refused. Restores the attempt budget,
    which the automatic sweep deliberately cannot do for itself."""
    document = await _load(session, document_id)
    await mrd_svc.retry_document(session, document)
    await session.commit()
    if settings.mrd_enabled:
        background.add_task(run_pending_extractions)
    return _document_out(document, None)


async def run_pending_extractions(limit: int = 5) -> int:
    """Claim and process whatever is waiting, on this process's own session.

    A background task outlives the request, so it must not borrow the request's
    session — that one is closed the moment the response is written.

    Nothing here propagates. This runs after the response has already gone to a
    coordinator who has moved on to the next patient, so there is nobody to tell:
    an exception escaping a BackgroundTask only lands as a traceback in the api
    log with no document attached to it. Per-document failures are already
    recorded on the document by `process_document`; what this catches is the
    layer below — a database that went away, a store that will not open. The
    worker sweep retries either way, which is why this can afford to be quiet.
    """
    from app.db import build_engine, build_sessionmaker
    from app.providers.registry import llm_chain

    settings = get_settings()
    processed = 0
    try:
        engine = build_engine()
        try:
            async with build_sessionmaker(engine)() as session:
                documents = await mrd_svc.claim_documents(session, limit=limit, settings=settings)
                await session.commit()
                for document in documents:
                    await mrd_svc.process_document(
                        session,
                        document,
                        store=get_object_store(settings),
                        providers=llm_chain(settings),
                        settings=settings,
                    )
                    await session.commit()
                    processed += 1
        finally:
            await engine.dispose()
    except Exception:  # noqa: BLE001 — see the docstring: there is nobody to raise to
        logger.exception("mrd background extraction failed")
    return processed


# -- reading (doctor) ----------------------------------------------------------


@router.get("/patients/{patient_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    patient_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_clinical),
) -> list[DocumentOut]:
    """Everything on file for one patient, newest first — including the ones
    that failed to read. A document is never hidden because a model could not
    read it; that is exactly when the doctor most needs to know it exists."""
    result = await session.execute(
        select(MedicalDocument, DocumentExtraction)
        .outerjoin(
            DocumentExtraction,
            (DocumentExtraction.document_id == MedicalDocument.id)
            & (DocumentExtraction.deleted_at.is_(None)),
        )
        .where(
            MedicalDocument.patient_id == patient_id,
            MedicalDocument.deleted_at.is_(None),
            MedicalDocument.status != DocumentStatus.CAPTURING,
        )
        .order_by(MedicalDocument.created_at.desc())
    )
    return [_document_out(document, extraction) for document, extraction in result.all()]


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_clinical),
) -> DocumentOut:
    document = await _load(session, document_id)
    return _document_out(document, await _extraction(session, document_id))


@router.post("/documents/{document_id}/verify", response_model=DocumentOut)
async def verify_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_clinical),
) -> DocumentOut:
    """A doctor saying they have read this against the original pages.

    Recorded as who and when, on the reading rather than the document, because
    it is the *reading* that was vouched for: a re-extraction produces different
    numbers and clears this (`pipeline._store_extraction`).
    """
    from app import doctor as doctor_svc

    document = await _load(session, document_id)
    record = await _extraction(session, document_id)
    if record is None:
        raise HTTPException(status_code=409, detail="there is no reading to verify yet")

    try:
        doctor = await doctor_svc.resolve_doctor(session, user_id=principal.id)
    except doctor_svc.DoctorError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    from datetime import UTC, datetime

    record.verified_by = doctor.id
    record.verified_at = datetime.now(UTC)
    await session.commit()
    return _document_out(document, record)


@router.get("/documents/{document_id}/pages/{page}")
async def get_page(
    document_id: uuid.UUID,
    page: int,
    session: AsyncSession = Depends(get_session),
    store: ObjectStore = Depends(get_store),
    _: Principal = Depends(require_staff),
) -> Response:
    """One original photograph, streamed under the guard.

    Deliberately not a signed URL. A link to a patient's lab report that keeps
    working after the session that minted it — in a browser history, in a chat,
    in a screenshot — is a disclosure with a long tail, and the pages are small
    enough that proxying them costs nothing worth having.
    """
    document = await _load(session, document_id)
    if not 1 <= page <= len(document.object_keys):
        raise HTTPException(status_code=404, detail="no such page")
    try:
        data = await store.get(document.object_keys[page - 1])
    except ObjectNotFound as exc:
        # The pages directory was not restored with the database (doc 21 §1.3).
        raise HTTPException(status_code=410, detail="this page is no longer stored") from exc

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            # Private, and not written to disk by an intermediary. The browser
            # may keep it for the length of the consult and no longer.
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="page-{page}.jpg"',
        },
    )


# -- helpers -------------------------------------------------------------------


async def _load(session: AsyncSession, document_id: uuid.UUID) -> MedicalDocument:
    document = await session.get(MedicalDocument, document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(status_code=404, detail="no such document")
    return document


async def _extraction(session: AsyncSession, document_id: uuid.UUID) -> DocumentExtraction | None:
    result = await session.execute(
        select(DocumentExtraction).where(
            DocumentExtraction.document_id == document_id,
            DocumentExtraction.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


def _document_out(document: MedicalDocument, extraction: DocumentExtraction | None) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        patient_id=document.patient_id,
        visit_id=document.visit_id,
        kind=document.kind.value,
        status=document.status.value,
        pages=document.pages,
        created_at=document.created_at.isoformat(),
        failure_reason=document.failure_reason,
        extraction=_extraction_out(extraction) if extraction is not None else None,
    )


def _extraction_out(record: DocumentExtraction) -> ExtractionOut:
    payload: dict[str, Any] = record.payload or {}
    values = [
        FlaggedValueOut(
            name=row.get("name", ""),
            value_text=row.get("value_text", ""),
            unit=row.get("unit", ""),
            ref_low=row.get("ref_low"),
            ref_high=row.get("ref_high"),
            flag=row.get("flag", "unknown"),
            ref_source=row.get("ref_source", "none"),
            page=row.get("page"),
            confidence=row.get("confidence", "low"),
            canonical_value=row.get("canonical_value"),
            canonical_unit=row.get("canonical_unit"),
        )
        for row in payload.get("tests", [])
    ]
    return ExtractionOut(
        summary_text=record.summary_text,
        outlier_count=record.outlier_count,
        report_date=payload.get("report_date"),
        values=values,
        narrative_findings=payload.get("narrative_findings", []),
        illegible_regions=payload.get("illegible_regions", []),
        dropped_rows=payload.get("dropped_rows", 0),
        verified=record.verified_by is not None,
        verified_at=record.verified_at.isoformat() if record.verified_at else None,
        prompt_refs=list(record.prompt_refs or []),
        uses_fallback_ranges=any(v.ref_source == "default" for v in values),
    )


async def _todays_arrivals(session: AsyncSession, *, on: date_type | None) -> list[WorklistRowOut]:
    # `queue.today()` and not a fresh `datetime.now(...)`: the queue's definition
    # of the operating day is the one the coordinator console and the board use,
    # and the scanner has to agree with the screen standing next to it. Computing
    # it independently here meant that between midnight and 05:30 IST the scanner
    # looked at a different day than the queue and showed nobody — which is how
    # this line got written twice.
    from app.queue import today as queue_today

    day = on or queue_today()
    result = await session.execute(
        select(QueueEntry, Visit, Patient)
        .join(Queue, Queue.id == QueueEntry.queue_id)
        .join(Visit, Visit.id == QueueEntry.visit_id)
        .join(Patient, Patient.id == Visit.patient_id)
        .where(
            Queue.date == day,
            QueueEntry.deleted_at.is_(None),
            QueueEntry.state != QueueEntryState.NO_SHOW,
        )
        .order_by(QueueEntry.token_no)
    )
    rows = result.all()
    counts = await _document_counts(session, [patient.id for _, _, patient in rows])
    return [
        WorklistRowOut(
            patient_id=patient.id,
            visit_id=visit.id,
            token_no=entry.token_no,
            patient_name=patient.name,
            state=entry.state.value,
            document_count=counts.get(patient.id, 0),
        )
        for entry, visit, patient in rows
    ]


async def _search_patients(session: AsyncSession, query: str) -> list[WorklistRowOut]:
    """Token, phone (last 10 digits) or UHC ID. Never a name.

    Name search on a staff phone at a public desk turns one shoulder-surfed
    screen into a browsable oncology register. A coordinator scanning a report
    is holding the patient's own paperwork, which carries all three of these.
    """
    digits = "".join(ch for ch in query if ch.isdigit())
    conditions = [Patient.external_id == query]
    if len(digits) >= 10:
        conditions.append(Patient.phone.like(f"%{digits[-10:]}"))

    result = await session.execute(
        select(Patient).where(Patient.deleted_at.is_(None), *[_or(conditions)]).limit(10)
    )
    patients = list(result.scalars())
    counts = await _document_counts(session, [p.id for p in patients])
    return [
        WorklistRowOut(
            patient_id=p.id,
            patient_name=p.name,
            document_count=counts.get(p.id, 0),
        )
        for p in patients
    ]


def _or(conditions: list):
    from sqlalchemy import or_

    return or_(*conditions)


async def _document_counts(
    session: AsyncSession, patient_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """How many documents each patient already has — so a coordinator can see at
    a glance that today's report is already in, and not scan it twice."""
    if not patient_ids:
        return {}
    from sqlalchemy import func

    result = await session.execute(
        select(MedicalDocument.patient_id, func.count())
        .where(
            MedicalDocument.patient_id.in_(patient_ids),
            MedicalDocument.deleted_at.is_(None),
            MedicalDocument.status != DocumentStatus.CAPTURING,
        )
        .group_by(MedicalDocument.patient_id)
    )
    return {patient_id: count for patient_id, count in result.all()}
