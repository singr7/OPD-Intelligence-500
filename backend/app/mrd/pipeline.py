"""Capture → extract → flag → summarise, and every way it can stop (doc 21 §1.1).

The shape of this module is one idea: **a document that cannot be read is still
a document.** Every failure below leaves the pages stored, listed and viewable,
and leaves a named status the doctor's screen can render as a sentence. There is
no path that discards a capture, and no path that reports a summary of pages a
model did not see.

## Claiming, not queuing

There is no separate queue table. A document is claimed by moving it to
`extracting` in one atomic `UPDATE ... FOR UPDATE SKIP LOCKED`, which is what
makes it safe for the API's post-upload nudge and the worker's sweep to run at
once: the second one to arrive claims nothing and does nothing. `claimed_at`
doubles as the staleness clock, so a worker killed mid-call leaves a document
that is reclaimed a few minutes later rather than one stuck in `extracting`
forever.

## Two calls, and only the first sees the images

Extraction is a vision call over the pages. Summarisation is a *text* call over
the flagged structure the first call produced (`Extraction.summary_input`). It
is not a second look at the pages, for a reason beyond cost: the summary must be
provably about the same numbers the doctor's table shows, and a second reading
could disagree with the first with nothing to say which one the prose described.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.clinical import DocumentExtraction, MedicalDocument
from app.models.enums import DocumentKind, DocumentStatus, UsagePurpose
from app.models.patient import Patient
from app.mrd.contract import Extraction, ExtractionFormatError
from app.phi import scrub_text
from app.prompts.loader import load
from app.providers import (
    ImagePart,
    LLMProvider,
    LLMRequest,
    ObjectStore,
    ProviderError,
    with_fallback,
)
from app.providers.objectstore import ObjectStoreError

logger = logging.getLogger(__name__)

#: A document sitting in `extracting` longer than this was claimed by a worker
#: that died. Generous next to a vision call over a dozen pages, short enough
#: that a doctor's report is not lost for a session.
CLAIM_TIMEOUT = timedelta(minutes=5)

#: Extraction is transcription. Any creativity here invents a platelet count.
EXTRACT_TEMPERATURE = 0.0
EXTRACT_MAX_TOKENS = 4000

#: The summary is prose, but prose about numbers. Still low.
SUMMARIZE_TEMPERATURE = 0.2
SUMMARIZE_MAX_TOKENS = 500

_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp"}


class MRDError(RuntimeError):
    """A document could not be processed. Always leaves the pages intact."""


def page_key(patient_id: uuid.UUID, document_id: uuid.UUID, index: int) -> str:
    """The only place an object key is built. Lowercase hex, no user input."""
    return f"records/{patient_id.hex}/{document_id.hex}/page-{index:02d}.jpg"


# -- capture -------------------------------------------------------------------


async def start_document(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    kind: DocumentKind,
    visit_id: uuid.UUID | None = None,
    captured_by: uuid.UUID | None = None,
) -> MedicalDocument:
    """Open a document. It holds no pages yet and is not extractable."""
    document = MedicalDocument(
        patient_id=patient_id,
        visit_id=visit_id,
        kind=kind,
        captured_by=captured_by,
        status=DocumentStatus.CAPTURING,
        object_keys=[],
        pages=0,
    )
    session.add(document)
    await session.flush()
    return document


async def add_page(
    session: AsyncSession,
    document: MedicalDocument,
    data: bytes,
    *,
    store: ObjectStore,
    media_type: str = "image/jpeg",
    settings: Settings | None = None,
) -> str:
    """Store one page and record its key. Refuses after capture is complete.

    The store write happens before the row is updated, so a crash between them
    leaves an orphan object rather than a key pointing at nothing — one wastes a
    few hundred kilobytes, the other shows the doctor a broken page.
    """
    settings = settings or get_settings()
    if document.status is not DocumentStatus.CAPTURING:
        raise MRDError(f"document is {document.status.value}, not accepting pages")
    if len(data) > settings.mrd_max_page_bytes:
        raise MRDError(f"page is {len(data)} bytes, over the {settings.mrd_max_page_bytes} limit")
    if not data:
        raise MRDError("page is empty")
    if media_type not in _MEDIA_TYPES:
        raise MRDError(f"unsupported page type {media_type!r}")
    if document.pages >= settings.mrd_max_pages:
        raise MRDError(f"document already has {document.pages} pages")

    key = page_key(document.patient_id, document.id, document.pages + 1)
    await store.put(key, data, media_type=media_type)
    # Reassigned, not appended: SQLAlchemy does not track in-place list mutation
    # on a plain JSONB column, and an appended key would be silently dropped.
    document.object_keys = [*document.object_keys, key]
    document.pages = len(document.object_keys)
    await session.flush()
    return key


async def complete_capture(session: AsyncSession, document: MedicalDocument) -> MedicalDocument:
    """Close capture and make the document extractable."""
    if document.status is not DocumentStatus.CAPTURING:
        return document
    if not document.object_keys:
        raise MRDError("cannot complete a document with no pages")
    document.status = DocumentStatus.CAPTURED
    await session.flush()
    return document


async def retry_document(session: AsyncSession, document: MedicalDocument) -> MedicalDocument:
    """A human asking for another go. Resets the attempt budget, which is the
    difference between this and the sweep — the sweep must not resurrect a
    document a vendor has already refused three times."""
    if document.status is not DocumentStatus.EXTRACTION_FAILED:
        return document
    document.status = DocumentStatus.CAPTURED
    document.attempts = 0
    document.failure_reason = None
    await session.flush()
    return document


# -- claiming ------------------------------------------------------------------


async def claim_documents(
    session: AsyncSession, *, limit: int = 5, settings: Settings | None = None
) -> list[MedicalDocument]:
    """Atomically take up to `limit` documents for extraction.

    `FOR UPDATE SKIP LOCKED` is what lets the API's post-upload nudge and the
    worker's sweep both run without either paying a vendor twice for one
    document: the loser of the race selects nothing.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    stale = now - CLAIM_TIMEOUT

    eligible = (
        select(MedicalDocument.id)
        .where(
            MedicalDocument.deleted_at.is_(None),
            MedicalDocument.attempts < settings.mrd_max_extract_attempts,
            (MedicalDocument.status == DocumentStatus.CAPTURED)
            | (MedicalDocument.status == DocumentStatus.EXTRACTION_FAILED)
            # Reclaim what a dead worker was holding.
            | (
                (MedicalDocument.status == DocumentStatus.EXTRACTING)
                & (MedicalDocument.claimed_at < stale)
            ),
        )
        .order_by(MedicalDocument.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )

    claimed = await session.execute(
        update(MedicalDocument)
        .where(MedicalDocument.id.in_(eligible.scalar_subquery()))
        .values(
            status=DocumentStatus.EXTRACTING,
            claimed_at=now,
            attempts=MedicalDocument.attempts + 1,
        )
        .returning(MedicalDocument.id)
    )
    ids = list(claimed.scalars())
    if not ids:
        return []

    # Re-read as ORM objects so the audit hook sees ordinary instrumented writes
    # from here on, rather than another bulk UPDATE it cannot describe.
    result = await session.execute(select(MedicalDocument).where(MedicalDocument.id.in_(ids)))
    return list(result.scalars())


# -- extraction ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcessResult:
    document_id: uuid.UUID
    status: DocumentStatus
    outlier_count: int = 0
    summarized: bool = False
    reason: str = ""


async def process_document(
    session: AsyncSession,
    document: MedicalDocument,
    *,
    store: ObjectStore,
    providers: Sequence[LLMProvider],
    settings: Settings | None = None,
) -> ProcessResult:
    """One document, end to end. Never raises for an expected failure.

    Failure lands as `extraction_failed` with a reason a human can read, because
    every caller of this — a worker sweep, a background nudge — has nowhere
    useful to put an exception, and the doctor's screen needs the sentence.
    """
    settings = settings or get_settings()
    try:
        images = await _load_pages(document, store=store, settings=settings)
    except (ObjectStoreError, MRDError) as exc:
        return await _fail(session, document, f"pages could not be read: {exc}")

    prompt = load("mrd_extract")
    request = LLMRequest(
        prompt=prompt.render(kind_hint=document.kind.value, page_count=str(document.pages)),
        system=prompt.system,
        prompt_ref=prompt.ref,
        json_output=True,
        temperature=EXTRACT_TEMPERATURE,
        max_tokens=EXTRACT_MAX_TOKENS,
        images=images,
    )

    try:
        result = await with_fallback(
            list(providers),
            lambda provider: provider.complete(request, purpose=UsagePurpose.DOCUMENT),
        )
    except ProviderError as exc:
        # Includes UnsupportedCapability: a chain with no vision model in it
        # says so here rather than summarising pages nobody read.
        return await _fail(session, document, f"could not be read by the model: {exc}")

    try:
        extraction = Extraction.parse(result.json())
    except (ExtractionFormatError, ProviderError) as exc:
        return await _fail(session, document, f"the model's reply was not usable: {exc}")

    patient = await session.get(Patient, document.patient_id)
    extraction.flag_all(sex=patient.sex if patient else None)
    extraction.narrative_findings = [scrub_text(text) for text in extraction.narrative_findings]

    record = await _store_extraction(
        session,
        document,
        extraction,
        prompt_refs=[prompt.ref],
        provider_snapshot={
            "extract": {"provider": _provider_name(providers, result), **_model(result)}
        },
    )
    document.status = DocumentStatus.EXTRACTED
    document.failure_reason = None
    await session.flush()

    summarized = await _summarize(
        session, document, record, extraction, providers=providers, settings=settings
    )
    return ProcessResult(
        document_id=document.id,
        status=document.status,
        outlier_count=record.outlier_count,
        summarized=summarized,
        reason=document.failure_reason or "",
    )


async def _summarize(
    session: AsyncSession,
    document: MedicalDocument,
    record: DocumentExtraction,
    extraction: Extraction,
    *,
    providers: Sequence[LLMProvider],
    settings: Settings,
) -> bool:
    """Write the prose. A failure here is *not* a failed document.

    The values, the flags and the pages are all already stored and useful. If
    the summariser is down the doctor loses a paragraph, not a lab report, so
    the document stays `extracted` and the Reports tab shows the table with no
    summary above it.
    """
    findings = extraction.summary_input()
    if not findings.strip():
        return False

    patient = await session.get(Patient, document.patient_id)
    from app.phi import patient_context

    context = patient_context(patient) if patient else {"age_band": "unknown", "sex": "unknown"}

    prompt = load("mrd_summarize")
    request = LLMRequest(
        prompt=prompt.render(
            kind=document.kind.value,
            report_date=extraction.report_date or "not printed",
            findings=findings,
            context=", ".join(f"{k}: {v}" for k, v in context.items()),
        ),
        system=prompt.system,
        prompt_ref=prompt.ref,
        temperature=SUMMARIZE_TEMPERATURE,
        max_tokens=SUMMARIZE_MAX_TOKENS,
    )

    try:
        result = await with_fallback(
            list(providers),
            lambda provider: provider.complete(request, purpose=UsagePurpose.DOCUMENT),
        )
    except ProviderError as exc:
        logger.info("mrd summary unavailable for %s: %s", document.id, exc)
        return False

    text = scrub_text(result.text.strip())
    if not text:
        return False

    record.summary_text = text
    record.prompt_refs = [*record.prompt_refs, prompt.ref]
    document.provider_snapshot = {
        **document.provider_snapshot,
        "summarize": {"provider": _provider_name(providers, result), **_model(result)},
    }
    document.status = DocumentStatus.SUMMARIZED
    await session.flush()
    return True


async def _load_pages(
    document: MedicalDocument, *, store: ObjectStore, settings: Settings
) -> list[ImagePart]:
    keys = list(document.object_keys)
    if not keys:
        raise MRDError("document has no pages")
    if len(keys) > settings.mrd_max_extract_pages:
        raise MRDError(
            f"{len(keys)} pages is over the {settings.mrd_max_extract_pages}-page extraction limit"
        )
    pages = await asyncio.gather(*(store.get(key) for key in keys))
    return [ImagePart(data=data) for data in pages]


async def _store_extraction(
    session: AsyncSession,
    document: MedicalDocument,
    extraction: Extraction,
    *,
    prompt_refs: list[str],
    provider_snapshot: dict,
) -> DocumentExtraction:
    """Replace this document's reading, in place. One reading per document."""
    existing = await session.execute(
        select(DocumentExtraction).where(DocumentExtraction.document_id == document.id)
    )
    record = existing.scalar_one_or_none()
    if record is None:
        record = DocumentExtraction(document_id=document.id)
        session.add(record)

    record.payload = extraction.as_payload()
    record.outlier_count = extraction.outlier_count
    record.prompt_refs = prompt_refs
    record.summary_text = None
    # A re-run is a new reading: whoever verified the old one verified different
    # numbers, and carrying their name onto these would be putting a doctor's
    # signature on text they never saw.
    record.verified_by = None
    record.verified_at = None
    document.provider_snapshot = {**document.provider_snapshot, **provider_snapshot}
    await session.flush()
    return record


async def _fail(session: AsyncSession, document: MedicalDocument, reason: str) -> ProcessResult:
    logger.warning("mrd document %s failed: %s", document.id, reason)
    document.status = DocumentStatus.EXTRACTION_FAILED
    document.failure_reason = reason[:500]
    await session.flush()
    return ProcessResult(
        document_id=document.id, status=document.status, reason=document.failure_reason
    )


def _model(result) -> dict[str, str]:
    return {"model": result.model}


def _provider_name(providers: Sequence[LLMProvider], result) -> str:
    """Which provider in the chain actually answered, by the model it returned."""
    for provider in providers:
        if provider.model == result.model:
            return provider.name
    return providers[0].name if providers else "unknown"
