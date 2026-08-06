"""What the doctor is about to send, assembled in code from named fields (plan §4.1).

> "**Context assembly is code, not model:** age band, sex, diagnosis from the
> latest *signed* note (the spine's own rule), computed lab flags from Module 1,
> current-visit confirmed note tags from Module 3. Shown to the doctor *before*
> the first call — the doctor can see and trim exactly what leaves the box."
> — SESSION-CLINICAL-INTEL-PLAN §4.1

Exactly those four sources and no fifth. The plan's mock-up also shows "cycle 3
of AC-T", and there is nothing in this record that knows a cycle number — so it
is absent rather than approximated from the nearest available thing.

## Why the client sends ids and never text

Every item has a stable `id`. The panel renders them, the doctor unticks the
ones they do not want sent, and the client posts back **the ids it kept**. The
text is re-derived here on every turn from the database.

The obvious alternative — let the client post the context it wants sent — is one
line shorter and gives away the whole PHI posture. `app.phi.assert_clean` can
only vouch for a payload this module *built*; a string the browser composed is a
string a browser could have composed from anything, including the patient's name
sitting two divs away in the spine. So the trim is subtractive by construction:
a doctor can remove an item, and nobody can add one.

## Unverified readings are labelled, not withheld

M1's lab flags may not have been checked against the pages by a doctor yet, and
doc 21 §1.5's rule is that every surface showing an unverified reading says so.
This one says so twice: in `ContextItem.caveat`, which the panel renders beside
the item, and in the text that actually goes to the model. Withholding it
instead would be worse — the doctor is looking at the same numbers on the
Reports tab, and a research answer that silently ignored them would be answering
a different patient's question.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import (
    ClinicalNote,
    Dictation,
    DocumentExtraction,
    MedicalDocument,
    Visit,
)
from app.models.enums import NoteStatus, ValueFlag
from app.models.patient import Patient
from app.phi import age_band, assert_clean

#: Item ids. Stable strings rather than an enum in the wire contract, because a
#: stored thread records which ones were sent and that record has to stay
#: readable after this list changes.
DEMOGRAPHICS = "demographics"
DIAGNOSIS = "diagnosis"
LABS = "labs"
NOTE_TAGS = "note_tags"

#: How many recent documents' readings can contribute values. Three is roughly
#: "this admission's bloods" at an oncology OPD; beyond that the context stops
#: being a picture of the patient now and starts being a chart review, which is
#: not what a question typed mid-consult is asking about.
MAX_DOCUMENTS = 3

#: Flagged values only, and a ceiling on them. A CBC + LFT + RFT panel with
#: everything deranged is a real thing to be holding a research question about,
#: and it is also a 60-line context nobody reads before tapping send.
MAX_VALUES = 12

#: Values whose flag is one of these are omitted entirely — `normal` because a
#: normal result is not what the question is about, and `unknown` because there
#: was no range to judge it against and an unjudged number in a research prompt
#: reads exactly like a judged one.
_QUIET_FLAGS = frozenset({ValueFlag.NORMAL.value, ValueFlag.UNKNOWN.value})

_ARROW = {
    ValueFlag.LOW.value: "low",
    ValueFlag.CRITICAL_LOW.value: "critically low",
    ValueFlag.HIGH.value: "high",
    ValueFlag.CRITICAL_HIGH.value: "critically high",
}


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One line the doctor can see, drop, and hold us to.

    `text` is what goes to the vendor. `label` and `caveat` are for the panel —
    they never leave the box, so they may say "unverified reading of a scan the
    coordinator took" in words that would be noise in a prompt.
    """

    id: str
    label: str
    text: str
    #: Where this came from, in the doctor's language. Rendered under the item.
    source: str
    #: A reason to look at this twice before sending it, or "".
    caveat: str = ""


@dataclass(frozen=True, slots=True)
class ResearchContext:
    """Everything assembled for one visit, before any trimming."""

    items: tuple[ContextItem, ...] = ()
    #: Sources that exist for this patient but produced nothing to send, with
    #: the reason. The panel renders these: "no labs scanned for this patient"
    #: and "the labs failed to load" must not look the same, and neither should
    #: look like a source this module forgot to build.
    absent: tuple[tuple[str, str], ...] = ()

    def select(self, include: Sequence[str] | None) -> tuple[ContextItem, ...]:
        """The items whose ids the doctor kept, in assembly order.

        `None` means "everything" — the state before the doctor has touched the
        panel. An **empty list means empty**, not everything: a doctor who
        unticks every line is asking a general question with no patient in it,
        and that is a legitimate thing to want.
        """
        if include is None:
            return self.items
        kept = set(include)
        return tuple(item for item in self.items if item.id in kept)

    def prompt_text(self, include: Sequence[str] | None = None) -> str:
        """The context block as the model sees it. Empty when nothing is kept."""
        return "\n".join(f"- {item.text}" for item in self.select(include))


async def assemble(session: AsyncSession, *, visit: Visit) -> ResearchContext:
    """Read the three modules' outputs and minimise them into sendable lines.

    Never raises on a missing source. A patient with no signed note, no scanned
    report and no confirmed note is an ordinary patient on their first visit,
    and the panel has to open for them — with an honest account of why it is
    nearly empty.
    """
    items: list[ContextItem] = []
    absent: list[tuple[str, str]] = []

    patient = await session.get(Patient, visit.patient_id)
    if patient is not None:
        items.append(
            ContextItem(
                id=DEMOGRAPHICS,
                label="Age and sex",
                text=f"Patient: {age_band(patient.age)}, {_sex(patient)}.",
                source="From the registration record. The age is a band, never a date of birth.",
            )
        )
    else:  # pragma: no cover - the FK guarantees a patient
        absent.append(("Age and sex", "no patient record on this visit"))

    diagnosis = await _diagnosis(session, patient_id=visit.patient_id)
    if diagnosis is not None:
        items.append(diagnosis)
    else:
        absent.append(("Working diagnosis", "no signed consult note for this patient yet"))

    labs = await _labs(session, patient_id=visit.patient_id)
    if labs is not None:
        items.append(labs)
    else:
        absent.append(("Flagged lab values", "nothing out of range on file from a scanned report"))

    tags = await _note_tags(session, visit_id=visit.id)
    if tags is not None:
        items.append(tags)
    else:
        absent.append(("Today's note tags", "no confirmed note on this visit yet"))

    context = ResearchContext(items=tuple(items), absent=tuple(absent))
    # The belt to phi.py's braces, run over the thing that actually leaves: a
    # future edit that interpolates a name into one of these lines fails a test
    # rather than a patient.
    assert_clean({"lines": [item.text for item in context.items]})
    return context


def _sex(patient: Patient) -> str:
    value = getattr(patient.sex, "value", patient.sex)
    return str(value) if value else "sex not recorded"


async def _diagnosis(session: AsyncSession, *, patient_id: uuid.UUID) -> ContextItem | None:
    """The latest signed note's diagnosis — the spine's own rule, deliberately.

    Only *signed* notes count, for the reason `app.doctor._diagnosis` gives: a
    draft dictation is a doctor thinking out loud mid-consult, and this is the
    line the model will reason about for the rest of the conversation.

    This is a second query against the same column rather than an import of
    `app.doctor._diagnosis`, because this package imports no clinical writer (see
    the package docstring) and `app.doctor` is one. What keeps them honest is a
    test that asserts this and the patient card agree on the same visit, which
    pins the *behaviour* rather than sharing the code.
    """
    rows = await session.execute(
        select(Dictation.structured, Visit.date)
        .join(Visit, Dictation.visit_id == Visit.id)
        .where(
            Visit.patient_id == patient_id,
            Dictation.signed_at.is_not(None),
            Dictation.deleted_at.is_(None),
            Visit.deleted_at.is_(None),
        )
        .order_by(Dictation.signed_at.desc())
        .limit(5)
    )
    for structured, on in rows.all():
        text = (structured or {}).get("diagnosis") if isinstance(structured, dict) else None
        if not text:
            continue
        return ContextItem(
            id=DIAGNOSIS,
            label="Working diagnosis",
            text=f"Working diagnosis: {str(text).strip()[:300]} (signed {on.isoformat()}).",
            source=f"From the consult note signed on {on.isoformat()}.",
        )
    return None


async def _labs(session: AsyncSession, *, patient_id: uuid.UUID) -> ContextItem | None:
    """Out-of-range values from the most recent scanned reports (Module 1).

    The flags are M1's computed ones, read out of the stored payload. Nothing is
    re-judged here and nothing is judged for the first time: this module has no
    reference table and must not acquire one, or there would be two answers in
    the codebase to "is this value abnormal" and no way to know which one a
    doctor was shown.
    """
    rows = await session.execute(
        select(DocumentExtraction.payload, DocumentExtraction.verified_at, MedicalDocument.id)
        .join(MedicalDocument, DocumentExtraction.document_id == MedicalDocument.id)
        .where(
            MedicalDocument.patient_id == patient_id,
            MedicalDocument.deleted_at.is_(None),
            DocumentExtraction.deleted_at.is_(None),
            DocumentExtraction.outlier_count > 0,
        )
        .order_by(DocumentExtraction.created_at.desc())
        .limit(MAX_DOCUMENTS)
    )

    lines: list[str] = []
    any_unverified = False
    for payload, verified_at, _document_id in rows.all():
        if verified_at is None:
            any_unverified = True
        report_date = (payload or {}).get("report_date") if isinstance(payload, dict) else None
        for test in _tests(payload):
            if len(lines) >= MAX_VALUES:
                break
            rendered = _value_line(test)
            if rendered:
                lines.append(f"{rendered}{f' ({report_date})' if report_date else ''}")

    if not lines:
        return None

    caveat = ""
    if any_unverified:
        caveat = "Includes a machine reading no doctor has checked against the pages yet."
    return ContextItem(
        id=LABS,
        label="Flagged lab values",
        text="Out-of-range values on file: " + "; ".join(lines) + ".",
        source="Read from scanned reports and flagged in code against the printed range.",
        caveat=caveat,
    )


def _tests(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    tests = payload.get("tests")
    return [t for t in tests if isinstance(t, dict)] if isinstance(tests, list) else []


def _value_line(test: dict[str, Any]) -> str:
    """One flagged value, with the direction spelled out rather than arrowed.

    "Hb 8.9 g/dL, low (ref 12-15)" rather than "Hb 8.9 ↓". An arrow is a glyph a
    model has to interpret, and the direction is the part of this line that
    matters most.
    """
    flag = str(test.get("flag") or "")
    if flag in _QUIET_FLAGS or flag not in _ARROW:
        return ""
    name = str(test.get("name") or "").strip()
    if not name:
        return ""
    value = str(test.get("value_text") or test.get("value") or "").strip()
    unit = str(test.get("unit") or "").strip()
    shown = " ".join(part for part in (name, value, unit) if part)
    span = _span(test)
    return f"{shown}, {_ARROW[flag]}{f' (ref {span})' if span else ''}"


def _span(test: dict[str, Any]) -> str:
    low, high = _decimal(test.get("ref_low")), _decimal(test.get("ref_high"))
    if low is not None and high is not None:
        return f"{low}-{high}"
    if low is not None:
        return f"above {low}"
    if high is not None:
        return f"below {high}"
    return ""


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def _note_tags(session: AsyncSession, *, visit_id: uuid.UUID) -> ContextItem | None:
    """Problems, symptoms and follow-ups off this visit's **confirmed** notes.

    Confirmed only, the M4 rule: a draft is a machine reading nobody checked,
    and `analytics.note_tags` already refuses to count one. Grading language is
    carried exactly as M4 stores it — `grade_mentioned` means the doctor said a
    grade out loud, so it goes out as "the doctor mentioned grade 1" and never
    as "grade 1 mucositis", which would read as this system's assessment.
    """
    rows = await session.scalars(
        select(ClinicalNote)
        .where(
            ClinicalNote.visit_id == visit_id,
            ClinicalNote.status == NoteStatus.CONFIRMED,
            ClinicalNote.deleted_at.is_(None),
        )
        .order_by(ClinicalNote.created_at.asc())
    )

    problems: list[str] = []
    symptoms: list[str] = []
    followups: list[str] = []
    for note in rows:
        tags = ((note.structured or {}).get("fields") or {}).get("tags")
        if not isinstance(tags, dict):
            continue
        _extend(problems, tags.get("problems"))
        _extend(followups, tags.get("followups"))
        for symptom in tags.get("symptoms") or []:
            if not isinstance(symptom, dict):
                continue
            name = str(symptom.get("name") or "").strip()
            if not name:
                continue
            grade = str(symptom.get("grade_mentioned") or "").strip()
            rendered = f"{name} (the doctor mentioned grade {grade})" if grade else name
            if rendered not in symptoms:
                symptoms.append(rendered)

    parts: list[str] = []
    if problems:
        parts.append(f"problems noted today: {', '.join(problems)}")
    if symptoms:
        parts.append(f"symptoms noted today: {', '.join(symptoms)}")
    if followups:
        parts.append(f"follow-ups the doctor set: {', '.join(followups)}")
    if not parts:
        return None

    return ContextItem(
        id=NOTE_TAGS,
        label="Today's note tags",
        text="From this consult's confirmed notes — " + "; ".join(parts) + ".",
        source="Tags the doctor confirmed on today's ambient notes.",
    )


def _extend(into: list[str], values: Any) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        text = str(value).strip()
        if text and text not in into:
            into.append(text)


def suggestions(context: ResearchContext) -> tuple[str, ...]:
    """Openers built from the context rather than asked of the model.

    The plan draws two of these under the context block. They are deterministic
    strings assembled from what is on file, because a *model* proposing what to
    ask about a patient is a model steering a clinical enquiry — a much larger
    thing than answering the question a doctor chose to type, and not one this
    session is scoped to argue for.
    """
    """Two or three openers, or none. Never invented from an empty context."""
    by_id = {item.id: item for item in context.items}
    out: list[str] = []
    if DIAGNOSIS in by_id and LABS in by_id:
        out.append("How should these out-of-range values change management for this diagnosis?")
    if LABS in by_id:
        out.append("What is the usual workup for the flagged values above?")
    if DIAGNOSIS in by_id:
        out.append("What does current evidence say about first-line options for this diagnosis?")
    if NOTE_TAGS in by_id:
        out.append("What is the evidence on managing the symptoms noted today?")
    return tuple(out[:3])


#: Every id this module can produce, for validating what a client sends back.
ALL_IDS: frozenset[str] = frozenset({DEMOGRAPHICS, DIAGNOSIS, LABS, NOTE_TAGS})


def unknown_ids(include: Sequence[str] | None) -> list[str]:
    """Ids a client asked for that this module does not build. Rejected rather
    than ignored — a client sending `"free_text"` is a client that thinks it can
    put text in the context, and it should be told plainly that it cannot."""
    if include is None:
        return []
    return sorted({i for i in include if i not in ALL_IDS})


__all__ = [
    "ALL_IDS",
    "DEMOGRAPHICS",
    "DIAGNOSIS",
    "LABS",
    "MAX_DOCUMENTS",
    "MAX_VALUES",
    "NOTE_TAGS",
    "ContextItem",
    "ResearchContext",
    "assemble",
    "suggestions",
    "unknown_ids",
]
