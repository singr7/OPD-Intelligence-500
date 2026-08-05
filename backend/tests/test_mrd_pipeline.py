"""Capture → extract → flag → summarise, and every way it stops (doc 21 §1.1).

The through-line of this file: **a document that cannot be read is still a
document.** Most of what is asserted here is what survives a failure, because
that is what the coordinator's morning of scanning depends on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.clinical import DocumentExtraction, MedicalDocument
from app.models.enums import DocumentKind, DocumentStatus, Sex, UsagePurpose
from app.mrd.pipeline import (
    CLAIM_TIMEOUT,
    MRDError,
    add_page,
    claim_documents,
    complete_capture,
    page_key,
    process_document,
    retry_document,
    start_document,
)
from app.providers.llm import FakeLLMProvider, FakeLLMScript
from app.providers.objectstore import FakeObjectStore
from app.providers.resilience import ProviderUnavailable, UnsupportedCapability
from tests import factories as f

JPEG = b"\xff\xd8\xff\xe0scanned-page-bytes"


class _ReadsThenFails(FakeLLMProvider):
    """Reads the pages, then goes down before it can write the prose."""

    async def _complete(self, request, call):
        if self.calls:
            raise ProviderUnavailable("summariser down")
        return await super()._complete(request, call)


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
        },
        {
            "name": "Platelet count",
            "value": 210,
            "unit": "10^3/uL",
            "ref_low": 150,
            "ref_high": 410,
            "page": 1,
            "confidence": "high",
        },
    ],
    "narrative_findings": [],
    "illegible_regions": [],
}


@pytest.fixture
def store() -> FakeObjectStore:
    return FakeObjectStore()


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest.fixture
def mrd_settings() -> Settings:
    """The MRD knobs only. Named apart from the session-scoped `settings`
    fixture in conftest, which the engine is built from."""
    return Settings(env="test", object_store="fake")


@pytest.fixture
async def patient(session: AsyncSession):
    clinic = await f.build_clinic(session)
    clinic["patient"].sex = Sex.FEMALE
    await session.flush()
    return clinic


async def _captured(session, clinic, store, *, pages: int = 1, **kwargs) -> MedicalDocument:
    document = await start_document(
        session, patient_id=clinic["patient"].id, kind=DocumentKind.LAB, **kwargs
    )
    for _ in range(pages):
        await add_page(session, document, JPEG, store=store)
    return await complete_capture(session, document)


def _extract_then_summary(reply: dict | None = None) -> list[FakeLLMScript]:
    """The two calls one document makes: the vision reply, then the prose."""
    return [
        FakeLLMScript(text=json.dumps(reply if reply is not None else EXTRACT_REPLY)),
        FakeLLMScript(text="Hb 8.9 g/dL (low, range 12-15). Platelets within range."),
    ]


# -- the happy path ------------------------------------------------------------


async def test_a_captured_report_reaches_the_doctor_as_values_flags_and_prose(
    session, patient, store, llm, mrd_settings
):
    document = await _captured(session, patient, store)
    llm.queue(*_extract_then_summary())

    result = await process_document(
        session, document, store=store, providers=[llm], settings=mrd_settings
    )

    assert result.status is DocumentStatus.SUMMARIZED
    assert result.outlier_count == 1
    assert result.summarized


async def test_the_stored_reading_carries_values_flags_summary_and_provenance(
    session, patient, store, llm, mrd_settings
):
    document = await _captured(session, patient, store)
    llm.queue(*_extract_then_summary())

    await process_document(session, document, store=store, providers=[llm], settings=mrd_settings)
    record = await _extraction(session, document)

    assert document.status is DocumentStatus.SUMMARIZED
    assert record.outlier_count == 1
    assert record.payload["tests"][0]["name"] == "Hemoglobin"
    assert record.payload["tests"][0]["flag"] == "low"
    assert record.summary_text.startswith("Hb 8.9")
    # Both prompts recorded, so "which version wrote this" is answerable later.
    assert record.prompt_refs == ["mrd_extract@v1", "mrd_summarize@v1"]
    assert set(document.provider_snapshot) == {"extract", "summarize"}
    # Nobody has vouched for it yet.
    assert record.verified_by is None


async def test_the_pages_go_to_the_vision_call_and_the_summary_call_gets_none(
    session, patient, store, llm, mrd_settings
):
    """The summary is written from the flagged structure, not from a second look
    at the images: it must be provably about the same numbers the doctor's table
    shows, and a second reading could disagree with nothing to arbitrate."""
    document = await _captured(session, patient, store, pages=3)
    llm.queue(*_extract_then_summary())

    await process_document(session, document, store=store, providers=[llm], settings=mrd_settings)

    extract_call, summary_call = llm.calls
    assert len(extract_call.images) == 3
    assert extract_call.images[0].data == JPEG
    assert summary_call.images == ()
    assert "Hemoglobin: 8.9 g/dL [LOW" in summary_call.prompt


async def test_both_calls_are_metered_as_document_work(
    session, patient, store, llm, mrd_settings, meter
):
    """Its own usage purpose: a document is priced per page of image and an
    intake summary is not, so averaging them makes cost-per-intake a fiction."""
    document = await _captured(session, patient, store)
    llm.queue(*_extract_then_summary())

    await process_document(session, document, store=store, providers=[llm], settings=mrd_settings)
    await meter.flush()

    rows = await _usage_rows(session)
    assert len(rows) == 2
    assert {r.purpose for r in rows} == {UsagePurpose.DOCUMENT}


async def test_the_patients_sex_reaches_the_flagging(session, patient, store, llm, mrd_settings):
    """12.5 g/dL is normal for a woman and low for a man. The fallback table can
    only get that right if the patient's sex is looked up, not assumed."""
    document = await _captured(session, patient, store)
    reply = {"tests": [{"name": "Hemoglobin", "value": 12.5, "unit": "g/dL"}]}
    llm.queue(*_extract_then_summary(reply))

    await process_document(session, document, store=store, providers=[llm], settings=mrd_settings)
    record = await _extraction(session, document)

    assert record.payload["tests"][0]["flag"] == "normal"  # female
    assert record.outlier_count == 0


# -- failure leaves the document intact ----------------------------------------


async def test_a_vendor_outage_leaves_a_readable_failure_and_the_pages(
    session, patient, store, llm, mrd_settings
):
    """The whole point of the module's shape. The coordinator's capture is not
    lost because a vendor was down; the doctor sees the photographs and a
    sentence saying the machine could not read them."""
    document = await _captured(session, patient, store, pages=2)
    llm.fail_with = ProviderUnavailable("gemini http 503")

    result = await process_document(
        session, document, store=store, providers=[llm], settings=mrd_settings
    )

    assert result.status is DocumentStatus.EXTRACTION_FAILED
    assert "could not be read by the model" in document.failure_reason
    assert document.pages == 2
    assert len(document.object_keys) == 2
    assert await store.get(document.object_keys[0]) == JPEG
    assert await _extraction(session, document) is None


async def test_a_chain_with_no_vision_model_fails_visibly_rather_than_summarising_nothing(
    session, patient, store, mrd_settings
):
    """The failure this module exists to prevent. A text-only chain must not
    produce a confident summary of pages no model ever saw."""
    document = await _captured(session, patient, store)
    text_only = FakeLLMProvider()
    text_only.fail_with = UnsupportedCapability("sarvam cannot read images")

    result = await process_document(
        session, document, store=store, providers=[text_only], settings=mrd_settings
    )

    assert result.status is DocumentStatus.EXTRACTION_FAILED
    assert await _extraction(session, document) is None


async def test_an_unusable_model_reply_fails_rather_than_storing_half_a_report(
    session, patient, store, llm, mrd_settings
):
    document = await _captured(session, patient, store)
    llm.queue(FakeLLMScript(text="I'm sorry, I can't read these images."))

    result = await process_document(
        session, document, store=store, providers=[llm], settings=mrd_settings
    )

    assert result.status is DocumentStatus.EXTRACTION_FAILED
    assert await _extraction(session, document) is None


async def test_a_missing_page_object_fails_the_document_not_the_worker(
    session, patient, store, llm, mrd_settings
):
    """A restore that brought back Postgres but not the pages directory. The
    sweep must survive it and say so, one document at a time."""
    document = await _captured(session, patient, store)
    store.objects.clear()

    result = await process_document(
        session, document, store=store, providers=[llm], settings=mrd_settings
    )

    assert result.status is DocumentStatus.EXTRACTION_FAILED
    assert "pages could not be read" in document.failure_reason


async def test_a_failed_summary_is_not_a_failed_document(
    session, patient, store, llm, mrd_settings
):
    """Values, flags and pages are already stored and useful. Losing the prose
    costs a paragraph, not a lab report — the tab shows the table with no
    summary above it."""
    document = await _captured(session, patient, store)
    reader = _ReadsThenFails()
    reader.queue(FakeLLMScript(text=json.dumps(EXTRACT_REPLY)))

    result = await process_document(
        session, document, store=store, providers=[reader], settings=mrd_settings
    )

    assert result.status is DocumentStatus.EXTRACTED
    record = await _extraction(session, document)
    assert record is not None
    assert record.outlier_count == 1
    assert record.summary_text is None


async def test_too_many_pages_is_refused_before_a_vendor_is_paid(
    session, patient, store, llm, mrd_settings
):
    document = await _captured(session, patient, store, pages=4)
    tight = mrd_settings.model_copy(update={"mrd_max_extract_pages": 3})

    result = await process_document(session, document, store=store, providers=[llm], settings=tight)

    assert result.status is DocumentStatus.EXTRACTION_FAILED
    assert "extraction limit" in document.failure_reason
    assert llm.calls == []


# -- capture rules -------------------------------------------------------------


async def test_pages_are_keyed_by_patient_and_document_in_order(session, patient, store):
    document = await _captured(session, patient, store, pages=3)

    assert document.object_keys == [
        page_key(document.patient_id, document.id, i) for i in (1, 2, 3)
    ]
    assert document.pages == 3


async def test_a_page_is_refused_after_capture_is_complete(session, patient, store):
    """`captured` is a claim that the document is whole. A page arriving after
    it would be extracted from or not depending on a race."""
    document = await _captured(session, patient, store)

    with pytest.raises(MRDError, match="not accepting pages"):
        await add_page(session, document, JPEG, store=store)


async def test_an_oversized_or_empty_page_is_refused(session, patient, store, mrd_settings):
    document = await start_document(
        session, patient_id=patient["patient"].id, kind=DocumentKind.LAB
    )
    small = mrd_settings.model_copy(update={"mrd_max_page_bytes": 10})

    with pytest.raises(MRDError, match="over the"):
        await add_page(session, document, b"x" * 11, store=store, settings=small)
    with pytest.raises(MRDError, match="empty"):
        await add_page(session, document, b"", store=store)
    with pytest.raises(MRDError, match="unsupported page type"):
        await add_page(session, document, JPEG, store=store, media_type="application/pdf")


async def test_a_document_with_no_pages_cannot_be_completed(session, patient):
    document = await start_document(
        session, patient_id=patient["patient"].id, kind=DocumentKind.LAB
    )

    with pytest.raises(MRDError, match="no pages"):
        await complete_capture(session, document)


# -- claiming ------------------------------------------------------------------


async def test_a_document_is_claimed_once(session, patient, store, mrd_settings):
    """The API's post-upload nudge and the worker's sweep both run. The loser of
    the race must claim nothing rather than pay a vendor twice for one report."""
    document = await _captured(session, patient, store)

    first = await claim_documents(session, settings=mrd_settings)
    second = await claim_documents(session, settings=mrd_settings)

    assert [d.id for d in first] == [document.id]
    assert second == []
    assert document.status is DocumentStatus.EXTRACTING
    assert document.attempts == 1


async def test_a_document_still_being_captured_is_never_claimed(
    session, patient, store, mrd_settings
):
    document = await start_document(
        session, patient_id=patient["patient"].id, kind=DocumentKind.LAB
    )
    await add_page(session, document, JPEG, store=store)

    assert await claim_documents(session, settings=mrd_settings) == []


async def test_a_document_held_by_a_dead_worker_is_reclaimed(session, patient, store, mrd_settings):
    """Otherwise one killed container loses a patient's report until someone
    notices a row stuck in `extracting`, which nobody is watching for."""
    document = await _captured(session, patient, store)
    await claim_documents(session, settings=mrd_settings)
    assert await claim_documents(session, settings=mrd_settings) == []

    document.claimed_at = datetime.now(UTC) - CLAIM_TIMEOUT - timedelta(seconds=1)
    await session.flush()

    assert [d.id for d in await claim_documents(session, settings=mrd_settings)] == [document.id]
    assert document.attempts == 2


async def test_the_sweep_gives_up_after_the_attempt_budget(session, patient, store, mrd_settings):
    """A vendor outage must not re-bill the same document all day."""
    document = await _captured(session, patient, store)
    budget = mrd_settings.mrd_max_extract_attempts

    for _ in range(budget):
        claimed = await claim_documents(session, settings=mrd_settings)
        assert claimed, "should still be retrying"
        document.status = DocumentStatus.EXTRACTION_FAILED
        await session.flush()

    assert await claim_documents(session, settings=mrd_settings) == []
    assert document.attempts == budget


async def test_a_human_retry_restores_the_budget(session, patient, store, mrd_settings):
    document = await _captured(session, patient, store)
    document.status = DocumentStatus.EXTRACTION_FAILED
    document.attempts = mrd_settings.mrd_max_extract_attempts
    document.failure_reason = "gemini http 503"
    await session.flush()
    assert await claim_documents(session, settings=mrd_settings) == []

    await retry_document(session, document)

    assert document.attempts == 0
    assert document.failure_reason is None
    assert [d.id for d in await claim_documents(session, settings=mrd_settings)] == [document.id]


# -- re-extraction -------------------------------------------------------------


async def test_re_extracting_clears_a_previous_doctors_verification(
    session, patient, store, llm, mrd_settings
):
    """A re-run is a new reading. Carrying the old verification onto it would
    put a doctor's name on numbers they never saw."""
    document = await _captured(session, patient, store)
    llm.queue(*_extract_then_summary())
    await process_document(session, document, store=store, providers=[llm], settings=mrd_settings)

    record = await _extraction(session, document)
    record.verified_by = patient["doctor"].id
    record.verified_at = datetime.now(UTC)
    await session.flush()

    llm.queue(*_extract_then_summary())
    await process_document(session, document, store=store, providers=[llm], settings=mrd_settings)

    record = await _extraction(session, document)
    assert record.verified_by is None
    assert record.verified_at is None


async def test_one_reading_per_document(session, patient, store, llm, mrd_settings):
    document = await _captured(session, patient, store)
    for _ in range(2):
        llm.queue(*_extract_then_summary())
        await process_document(
            session, document, store=store, providers=[llm], settings=mrd_settings
        )

    from sqlalchemy import func, select

    count = await session.scalar(
        select(func.count())
        .select_from(DocumentExtraction)
        .where(DocumentExtraction.document_id == document.id)
    )
    assert count == 1


# -- helpers -------------------------------------------------------------------


async def _extraction(session: AsyncSession, document: MedicalDocument):
    from sqlalchemy import select

    result = await session.execute(
        select(DocumentExtraction).where(DocumentExtraction.document_id == document.id)
    )
    return result.scalar_one_or_none()


async def _usage_rows(session: AsyncSession):
    from sqlalchemy import select

    from app.models.metering import UsageEvent

    result = await session.execute(select(UsageEvent))
    return list(result.scalars())
