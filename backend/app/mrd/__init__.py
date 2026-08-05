"""Medical record digitisation (doc 21 §1).

A coordinator photographs a patient's lab report at the desk; the doctor opens
the consult already knowing what it says.

    contract   what a model may say about a document, and the parse of it
    ranges     the deterministic verdict on every value — the flags live here
    pipeline   capture → claim → extract → flag → summarise, and every failure

The division of labour is the point: a model reads pages, Python decides what
the numbers mean, and every state in between is one the doctor's screen can
render as a sentence.
"""

from app.mrd.contract import (
    PAYLOAD_VERSION,
    ExtractedTest,
    Extraction,
    ExtractionFormatError,
)
from app.mrd.pipeline import (
    CLAIM_TIMEOUT,
    MRDError,
    ProcessResult,
    add_page,
    claim_documents,
    complete_capture,
    page_key,
    process_document,
    retry_document,
    start_document,
)
from app.mrd.ranges import (
    OUTLIER_FLAGS,
    Flagged,
    ReferenceTable,
    flag_value,
    get_reference_table,
)

__all__ = [
    "CLAIM_TIMEOUT",
    "OUTLIER_FLAGS",
    "PAYLOAD_VERSION",
    "ExtractedTest",
    "Extraction",
    "ExtractionFormatError",
    "Flagged",
    "MRDError",
    "ProcessResult",
    "ReferenceTable",
    "add_page",
    "claim_documents",
    "complete_capture",
    "flag_value",
    "get_reference_table",
    "page_key",
    "process_document",
    "retry_document",
    "start_document",
]
