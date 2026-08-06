"""Ambient consult notes — the doctor's working memory (plan §3).

> "A floating mic button on the doctor console … for capturing observations
> *while browsing* — 'post-chemo cycle 3, tolerating well, grade 1 mucositis,
> review CBC next visit.'" — SESSION-CLINICAL-INTEL-PLAN §3.1

Session C built a whole dictation stack — recording, STT over snapshotted
profiles, prompt-driven mapping, editable review, a signature boundary, audit —
and every line of it exists to produce **a prescription**. This module is the
second, lighter use of the same shape: speech in, a small readable structure
out, a doctor's confirmation on top. What it deliberately does not have is
everything downstream of that signature.

## The one rule this module is built around

**A note cannot prescribe.** Not "does not currently", not "should not" — cannot.
The enforcement is structural rather than a check somebody could forget:

1. `NoteMapping` has **no medication field**. There is nowhere for a drug order
   to be parsed *into*, so there is nothing for a later reader to mistake for
   one. A dictated drug lands in `plan_narrative` as the doctor's own prose,
   which is exactly what it is: a note to themselves.
2. `confirm` calls nothing. `dictation.sign` generates a prescription and drafts
   a check-in plan; the equivalent verb here stamps two columns and stops.
3. **This module does not import `app.prescription`, `app.formulary` or
   `app.dictation`.** Pinned by `test_notes.py::test_the_note_module_cannot_
   reach_the_prescription_path`, which reads this file's imports. It is why
   `assert_visit_scope` below is a local copy of a check `app.dictation` also
   has rather than an import of it — eight duplicated lines are a cheaper way to
   keep the two paths genuinely separate than a shared helper that quietly
   couples them. (`app.doctor` already carries four copies of the same check.)

Decision 6 of the plan states the rule; this docstring is where a future session
finds out that it was built in rather than written down.

## Several notes per visit, and why they are never merged

`Dictation` is one per visit — `start` reopens the existing draft, because there
is one prescription. The mic here is on the console for the whole consult, so a
doctor who observes something at minute two and something else at minute nine
has made two observations. Merging them would mean the second capture rewriting
the first, and the first is the one nobody can recreate.

## The degraded state is the one Session C already established

Mapping fails loudly: the transcript stays on the row, `mapping_error` is
recorded, and the editable fields open **empty beside it** so the doctor can type
what they meant and confirm anyway. There is no deterministic floor to degrade
to — a template that guessed at an assessment would be inventing clinical
content — so the honest degrade is an open form, not a fabricated one.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical import ClinicalNote, Intake, Visit
from app.models.enums import NoteStatus, UsagePurpose
from app.models.org import Doctor
from app.models.patient import Patient
from app.prompts import load
from app.providers import LLMProvider, LLMRequest, ProviderError, with_fallback

logger = logging.getLogger(__name__)

#: Bump when the shape of `ClinicalNote.structured` changes.
STRUCTURED_VERSION = 1

#: Pinned, not "latest" — a prompt edit must not quietly change how live notes
#: map (see `app.prompts.loader`).
PROMPT_VERSION = 1

_EDITABLE_TOP_LEVEL = {"subjective", "objective", "assessment", "plan_narrative", "tags"}

#: A note is a sentence or two of thinking, not a discharge summary. Long enough
#: for a doctor who talks while they type, short enough that a stuck recorder
#: cannot post an essay at the model.
MAX_TRANSCRIPT = 20_000


class NoteError(Exception):
    """The caller may not do this to this note."""


class NoteLocked(NoteError):
    """It is confirmed. A confirmed note does not change."""


class MappingUnavailable(NoteError):
    """The LLM chain is down. The transcript is kept; nothing is invented."""


# -- the structured contract --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Symptom:
    """One symptom the doctor mentioned, and the grade **they said out loud**.

    `grade_mentioned` is named for what it is. It records that a grade was
    spoken, not that this system has graded anything: CTCAE grading is a
    clinical judgement, and "a model may interpret or summarize; it may not
    decide clinical urgency" (CODEBASE_MEMORY invariants) covers it. A null here
    means the doctor did not say a grade — never that the symptom is mild.
    """

    name: str
    grade_mentioned: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "grade_mentioned": self.grade_mentioned}


@dataclass(frozen=True, slots=True)
class Tags:
    """The countable part of a note (plan §3.2).

    Model-suggested and doctor-visible at confirm time, which is the whole basis
    on which `app.analytics.note_tags` is allowed to count them — and why it
    counts confirmed notes only.
    """

    problems: tuple[str, ...] = ()
    symptoms: tuple[Symptom, ...] = ()
    followups: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "problems": list(self.problems),
            "symptoms": [s.to_dict() for s in self.symptoms],
            "followups": list(self.followups),
        }

    @classmethod
    def parse(cls, payload: Any) -> Tags:
        if not isinstance(payload, Mapping):
            return cls()
        symptoms = tuple(
            Symptom(name=name, grade_mentioned=_text(row.get("grade_mentioned")) or None)
            for row in _rows(payload.get("symptoms"))
            if (name := _text(row.get("name")))
        )
        return cls(
            problems=_str_tuple(payload.get("problems")),
            symptoms=symptoms,
            followups=_str_tuple(payload.get("followups")),
        )


@dataclass(frozen=True, slots=True)
class NoteMapping:
    """The plan §3.1 shape, and nothing else.

    Four prose fields and three tag lists. **There is no `meds` field and adding
    one is not a small change** — it is the difference between a note and a
    prescription, and this system already has exactly one path that writes drug
    orders. See the module docstring.
    """

    subjective: str = ""
    objective: str = ""
    assessment: str = ""
    plan_narrative: str = ""
    tags: Tags = field(default_factory=Tags)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subjective": self.subjective,
            "objective": self.objective,
            "assessment": self.assessment,
            "plan_narrative": self.plan_narrative,
            "tags": self.tags.to_dict(),
        }

    @property
    def is_empty(self) -> bool:
        """Nothing worth keeping. `confirm` refuses this — a note that says
        nothing is indistinguishable from one that failed silently."""
        return not any(
            (
                self.subjective.strip(),
                self.objective.strip(),
                self.assessment.strip(),
                self.plan_narrative.strip(),
                self.tags.problems,
                self.tags.symptoms,
                self.tags.followups,
            )
        )

    @classmethod
    def parse(cls, payload: Any) -> NoteMapping:
        """Model JSON (or a stored dict) → the contract.

        Tolerant of shape and, unlike the dictation mapper, it has nothing
        strict to be: there is no drug name here whose exactness a patient's
        safety depends on. **Unknown keys are dropped rather than carried** — a
        model that decides to volunteer `"meds": [...]` gets it discarded here,
        silently and by construction, because `to_dict` only ever writes the
        five fields above.
        """
        if not isinstance(payload, Mapping):
            raise NoteError("mapping payload must be an object")
        return cls(
            subjective=_text(payload.get("subjective")),
            objective=_text(payload.get("objective")),
            assessment=_text(payload.get("assessment")),
            plan_narrative=_text(payload.get("plan_narrative")),
            tags=Tags.parse(payload.get("tags")),
        )


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(text for item in value if (text := str(item).strip()))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


# -- the mapper (the provider-chain adapter) ----------------------------------


@dataclass(frozen=True, slots=True)
class MapResult:
    mapping: NoteMapping
    model: str
    provider: str
    prompt_ref: str


class NoteMapper:
    """`note_map` on the configured LLM chain.

    Same adapter seam as `DictationMapper`: it takes a provider chain and nothing
    else, so `LLM_PROVIDER=local_vllm` runs the whole thing on the box. An
    ambient note is the most private text this system holds — it is the doctor
    thinking aloud — and being able to keep it on the premises by changing one
    setting is the point.
    """

    def __init__(
        self, providers: Sequence[LLMProvider], *, prompt_version: int | None = PROMPT_VERSION
    ):
        self._providers = list(providers)
        self._prompt = load("note_map", prompt_version)

    async def map(self, transcript: str, *, patient: str, context: str) -> MapResult:
        if not transcript.strip():
            raise NoteError("nothing to map: the transcript is empty")

        rendered = self._prompt.render(transcript=transcript, patient=patient, context=context)
        request = LLMRequest(
            prompt=rendered,
            system=self._prompt.system,
            prompt_ref=self._prompt.ref,
            json_output=True,
            # Near-zero, for the same reason the dictation mapper is: this is
            # transcription-shaped work. A creative temperature writes an
            # assessment the doctor never gave.
            temperature=0.0,
            max_tokens=900,
        )
        try:
            result = await with_fallback(
                self._providers,
                lambda provider: provider.complete(request, purpose=UsagePurpose.NOTE),
            )
        except ProviderError as exc:
            raise MappingUnavailable(str(exc)) from exc

        return MapResult(
            mapping=NoteMapping.parse(result.json()),
            model=result.model,
            provider=_provider_name(self._providers, result),
            prompt_ref=self._prompt.ref,
        )


def _provider_name(providers: Sequence[LLMProvider], result: Any) -> str:
    """Which provider in the chain actually answered, by the model it returned.

    The same identification `app.mrd.pipeline` uses — `LLMResult` carries the
    model but not the provider, and a fallback chain means the first link is not
    necessarily the one that replied.
    """
    for provider in providers:
        if provider.model == result.model:
            return provider.name
    return providers[0].name if providers else "unknown"


# -- the record ---------------------------------------------------------------


def empty_structured() -> dict[str, Any]:
    return {
        "version": STRUCTURED_VERSION,
        "mapped": None,
        "fields": None,
        "edits": [],
        "model": None,
        "prompt_ref": None,
        "mapping_error": None,
        "mapped_at": None,
    }


def current_mapping(note: ClinicalNote) -> NoteMapping | None:
    """What the note currently says — the doctor's version if they edited it."""
    fields = (note.structured or {}).get("fields")
    return NoteMapping.parse(fields) if isinstance(fields, Mapping) else None


async def list_for_visit(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> list[ClinicalNote]:
    """Every note on this visit, oldest first — the order they were observed in."""
    await assert_visit_scope(session, visit_id=visit_id, doctor=doctor)
    rows = await session.scalars(
        select(ClinicalNote)
        .where(ClinicalNote.visit_id == visit_id, ClinicalNote.deleted_at.is_(None))
        .order_by(ClinicalNote.created_at.asc())
    )
    return list(rows)


async def start(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor, transcript: str = ""
) -> ClinicalNote:
    """Open a new note on this visit and store what was said.

    Deliberately **not** idempotent per visit, unlike `dictation.start`. Each
    capture is its own observation; see the module docstring.
    """
    await assert_visit_scope(session, visit_id=visit_id, doctor=doctor)
    note = ClinicalNote(
        visit_id=visit_id,
        doctor_id=doctor.id,
        transcript=transcript.strip() or None,
        structured=empty_structured(),
        status=NoteStatus.DRAFT,
    )
    session.add(note)
    await session.flush()
    return note


async def compose(session: AsyncSession, *, note: ClinicalNote, doctor: Doctor) -> ClinicalNote:
    """Open the editable fields with no model in the loop.

    The typed note, and also the escape hatch from a mapping the doctor does not
    want to wait for. Idempotent, and it never clears fields that already exist.
    """
    _assert_unconfirmed(note)
    structured = dict(note.structured or empty_structured())
    if isinstance(structured.get("fields"), Mapping):
        return note
    structured["fields"] = NoteMapping().to_dict()
    structured["edits"] = list(structured.get("edits") or [])
    note.structured = structured
    await session.flush()
    return note


async def map_transcript(
    session: AsyncSession, *, note: ClinicalNote, doctor: Doctor, mapper: NoteMapper
) -> ClinicalNote:
    """Run the mapping and store both the model's version and the working copy.

    `mapped` is frozen and is what the review screen shows the doctor's edits
    against; `fields` is where those edits land.
    """
    _assert_unconfirmed(note)
    if not (note.transcript or "").strip():
        raise NoteError("nothing to map: the transcript is empty")

    patient_line, context_line = await _prompt_context(session, note=note)
    structured = dict(note.structured or empty_structured())
    try:
        result = await mapper.map(note.transcript or "", patient=patient_line, context=context_line)
    except MappingUnavailable as exc:
        # The Session-C degraded state, verbatim: record the failure, keep the
        # words, and open the fields so the doctor can still finish. A model
        # being down is not a reason an observation is lost.
        structured["mapping_error"] = str(exc)
        if not isinstance(structured.get("fields"), Mapping):
            structured["fields"] = NoteMapping().to_dict()
        note.structured = structured
        await session.flush()
        raise

    payload = result.mapping.to_dict()
    structured.update(
        {
            "version": STRUCTURED_VERSION,
            "mapped": payload,
            "fields": payload,
            "edits": [],
            "model": result.model,
            "prompt_ref": result.prompt_ref,
            "mapping_error": None,
            "mapped_at": _now().isoformat(),
        }
    )
    note.structured = structured
    note.prompt_refs = [result.prompt_ref]
    note.provider_snapshot = {"map": {"provider": result.provider, "model": result.model}}
    await session.flush()
    return note


async def apply_corrections(
    session: AsyncSession,
    *,
    note: ClinicalNote,
    doctor: Doctor,
    patch: Mapping[str, Any],
) -> ClinicalNote:
    """The doctor's edits, with an append-only trail.

    Whole-field replacement rather than a JSON merge, the `apply_corrections`
    rule from `app.dictation`: these fields are small, and merging into a tag
    list the doctor just pruned is a silent restoration of something they
    removed.
    """
    _assert_unconfirmed(note)
    structured = dict(note.structured or empty_structured())
    before = structured.get("fields")
    if not isinstance(before, Mapping):
        raise NoteError("nothing to correct: this note has not been mapped yet")

    unknown = set(patch) - _EDITABLE_TOP_LEVEL
    if unknown:
        raise NoteError(f"not editable: {sorted(unknown)}")

    after = NoteMapping.parse({**before, **patch}).to_dict()

    edits = list(structured.get("edits") or [])
    for key in sorted(patch):
        if before.get(key) != after.get(key):
            edits.append(
                {
                    "at": _now().isoformat(),
                    "by": str(doctor.id),
                    "field": key,
                    "from": before.get(key),
                    "to": after.get(key),
                }
            )
    structured["fields"] = after
    structured["edits"] = edits
    note.structured = structured
    await session.flush()
    return note


async def confirm(session: AsyncSession, *, note: ClinicalNote, doctor: Doctor) -> ClinicalNote:
    """ "I have read this back and it is what I meant."

    Compare `dictation.sign`, which generates a prescription and drafts a
    check-in plan off the same moment. This stamps two columns and returns. That
    difference is the module — see the docstring at the top of this file — and
    the test that guards it reads this file's import list.

    It refuses an empty mapping. A note carrying nothing is not a record of a
    quiet consult; it is indistinguishable from a mapping that failed and was
    confirmed by a doctor tapping through, and only one of those should end up
    in the tag counts.
    """
    _assert_unconfirmed(note)
    mapping = current_mapping(note)
    if mapping is None:
        raise NoteError("cannot confirm a note that has not been mapped or opened")
    if mapping.is_empty:
        raise NoteError("cannot confirm an empty note")

    note.status = NoteStatus.CONFIRMED
    note.confirmed_at = _now()
    note.confirmed_by = doctor.id
    await session.flush()
    logger.info("clinical note %s confirmed by doctor %s", note.id, doctor.id)
    return note


# -- helpers ------------------------------------------------------------------


def _assert_unconfirmed(note: ClinicalNote) -> None:
    if note.status is NoteStatus.CONFIRMED:
        raise NoteLocked("this note is confirmed; confirmed notes do not change")


async def assert_visit_scope(
    session: AsyncSession, *, visit_id: uuid.UUID, doctor: Doctor
) -> Visit:
    """Your department, or an error that says so — the S9 card's boundary.

    A local copy of the check `app.dictation` also carries, on purpose: see the
    module docstring. `app.doctor` already has four copies of it, so this is the
    house pattern rather than an exception made for this module.
    """
    visit = await session.get(Visit, visit_id)
    if visit is None or visit.deleted_at is not None:
        raise NoteError(f"no such visit {visit_id}")
    if visit.department_id != doctor.department_id:
        raise NoteError("that patient is in another department")
    return visit


async def _prompt_context(session: AsyncSession, *, note: ClinicalNote) -> tuple[str, str]:
    """The two context lines the mapping prompt takes.

    Thin, for the reason `app.dictation._prompt_context` is thin: the model's job
    is to structure what the doctor said, and handing it the patient's full story
    invites it to fill in gaps the doctor left deliberately. The chief concern is
    included where the dictation prompt omits it — a note is *about* how the
    patient is doing, so "vomiting since Tuesday" is what "she says it is better"
    is better *than*.
    """
    visit = await session.get(Visit, note.visit_id)
    if visit is None:  # pragma: no cover - FK guarantees it
        return "(unknown patient)", "(no visit on file)"
    patient = await session.get(Patient, visit.patient_id)
    intake = await session.scalar(
        select(Intake)
        .where(Intake.visit_id == visit.id, Intake.deleted_at.is_(None))
        .order_by(Intake.created_at.desc())
        .limit(1)
    )

    if patient is None:  # pragma: no cover - FK guarantees it
        patient_line = "(unknown patient)"
    else:
        bits = [patient.name, f"{patient.age}y" if patient.age else "", str(patient.sex or "")]
        patient_line = ", ".join(b for b in bits if b)

    context = [f"visit date: {visit.date.isoformat()}"]
    if intake and intake.chief_complaint_en:
        context.append(f"chief concern at intake: {intake.chief_complaint_en}")
    return patient_line, "; ".join(context)


def _now() -> datetime:
    return datetime.now(UTC)
