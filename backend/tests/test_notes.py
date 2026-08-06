"""Ambient consult notes (M4, plan §3).

The module's acceptance criterion is a *negative* one — a note cannot produce a
prescription — so the first section of this file is the structural guard for it,
and it is deliberately the first thing a reader hits. The rest drives the record
(start → map → correct → confirm), the degraded mapping state Session C
established, and the tag shape the analytics counts.

No test here calls a vendor.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import notes as notes_svc
from app import queue as q
from app.models.audit import AuditLog
from app.models.clinical import Prescription
from app.models.enums import Channel, NoteStatus
from app.providers.llm import FakeLLMProvider, FakeLLMScript

TODAY = q.today()

NOTES_MODULE = Path(notes_svc.__file__)

#: A mapping a doctor would recognise as their own words come back tidier.
MAPPED: dict[str, Any] = {
    "subjective": "Feels better than after the last cycle. Mouth sore for three days.",
    "objective": "Grade 1 oral mucositis. No pallor.",
    "assessment": "Tolerating AC-T through cycle 3.",
    "plan_narrative": "Salt-water rinses. Repeat CBC before the next cycle.",
    "tags": {
        "problems": ["carcinoma breast"],
        "symptoms": [{"name": "mucositis", "grade_mentioned": "1"}],
        "followups": ["CBC before next cycle"],
    },
}


def _mapper(payload: dict[str, Any] | str) -> notes_svc.NoteMapper:
    """A mapper whose model returns exactly `payload`."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return notes_svc.NoteMapper([FakeLLMProvider(script=[FakeLLMScript(text=text)])])


def _broken_mapper() -> notes_svc.NoteMapper:
    """A mapper whose chain is down."""
    from app.providers import ProviderUnavailable

    provider = FakeLLMProvider()
    provider.fail_with = ProviderUnavailable("gemini http 503")
    return notes_svc.NoteMapper([provider])


async def _clinic_with_visit(session: AsyncSession):
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    return clinic, visit


# =============================================================================
# The acceptance criterion: a note cannot become a prescription
# =============================================================================


def test_the_note_module_cannot_reach_the_prescription_path() -> None:
    """`app.notes` does not import the prescription machinery — at all.

    Read off the source rather than asserted about behaviour, because behaviour
    tests only cover the call paths somebody thought to write. This is the plan's
    decision 6 ("Notes never touch the prescription path") stated as a property
    of the file: if a future session adds `from app import prescription` to write
    "just a small helper", this fails on the import line, before any of it runs.

    `app.formulary` and `app.dictation` are in the list for the same reason.
    Reaching either would mean this module had started to know about drug orders,
    and the only honest place to intervene is before it does.
    """
    tree = ast.parse(NOTES_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)

    forbidden = {"app.prescription", "app.formulary", "app.dictation", "app.checkins"}
    reached = sorted(imported & forbidden)
    assert not reached, f"app/notes.py must not reach the prescription path, but imports {reached}"


def test_the_note_contract_has_no_medication_field() -> None:
    """There is nowhere for a drug order to be parsed into.

    A model that volunteers `meds` gets it dropped by `parse`, which reads five
    named fields and writes five named fields. The same guard the MRD extraction
    contract has against a volunteered `flag`.
    """
    mapping = notes_svc.NoteMapping.parse(
        {
            **MAPPED,
            "meds": [{"name": "Inj Monocef 1 gm", "dose": "1 g", "freq": "BD"}],
            "prescription": ["Tab Dolo 650 SOS"],
        }
    )
    payload = mapping.to_dict()
    assert set(payload) == {"subjective", "objective", "assessment", "plan_narrative", "tags"}
    assert "meds" not in json.dumps(payload)
    assert "Monocef" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_confirming_a_note_creates_no_prescription(session: AsyncSession) -> None:
    """The behavioural half of the same claim.

    `dictation.sign` generates a prescription off the signature. This confirms a
    note carrying a drug in its plan prose and asserts the table stays empty.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(
        session,
        visit_id=visit.id,
        doctor=doctor,
        transcript="continue Tab Tamoxifen 20 OD, repeat CBC before next cycle",
    )
    await notes_svc.map_transcript(
        session,
        note=note,
        doctor=doctor,
        mapper=_mapper(
            {**MAPPED, "plan_narrative": "Continue T. Tamoxifen 20 OD. Repeat CBC before cycle 4."}
        ),
    )
    await notes_svc.confirm(session, note=note, doctor=doctor)

    assert note.status is NoteStatus.CONFIRMED
    # The drug is on the record as the doctor's prose, which is the point — it is
    # kept, it is simply not an order.
    assert "Tamoxifen" in note.structured["fields"]["plan_narrative"]

    scripts = (await session.scalars(select(Prescription))).all()
    assert scripts == []


@pytest.mark.asyncio
async def test_the_demo_note_fixture_carries_no_drug_order(session: AsyncSession) -> None:
    """The fake's canned reply is an input to the contract and is pinned like one.

    `make dev` runs on `FakeLLMProvider`, so this fixture is what every demo of
    this surface shows. If it grew a `meds` list, the screen would be teaching
    the wrong shape even though the parser drops it.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(
        session, visit_id=visit.id, doctor=doctor, transcript="post cycle 3, doing well"
    )
    # No script queued → the fake falls through to its canned `note_map` reply.
    await notes_svc.map_transcript(
        session,
        note=note,
        doctor=doctor,
        mapper=notes_svc.NoteMapper([FakeLLMProvider()]),
    )

    fields = note.structured["fields"]
    assert set(fields) == {"subjective", "objective", "assessment", "plan_narrative", "tags"}
    assert fields["tags"]["symptoms"], "the demo should show at least one symptom tag"
    # One symptom with a spoken grade, one without: the field means "the doctor
    # said a grade", and a demo where every symptom has one reads as though the
    # system grades.
    grades = [s["grade_mentioned"] for s in fields["tags"]["symptoms"]]
    assert None in grades and any(g is not None for g in grades)


# =============================================================================
# The record: start → map → correct → confirm
# =============================================================================


@pytest.mark.asyncio
async def test_a_spoken_observation_becomes_four_fields_and_its_tags(
    session: AsyncSession,
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(
        session,
        visit_id=visit.id,
        doctor=doctor,
        transcript="post-chemo cycle 3, tolerating well, grade 1 mucositis, review CBC next visit",
    )
    assert note.status is NoteStatus.DRAFT
    assert note.structured["fields"] is None

    await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))

    mapping = notes_svc.current_mapping(note)
    assert mapping is not None
    assert mapping.assessment == "Tolerating AC-T through cycle 3."
    assert mapping.tags.symptoms[0].name == "mucositis"
    assert mapping.tags.symptoms[0].grade_mentioned == "1"
    # Provenance, the VOICE1 pattern: what read it, and under which prompt.
    assert note.prompt_refs == ["note_map@v1"]
    assert note.provider_snapshot["map"]["provider"] == "fake-llm"
    # `mapped` is frozen so the review can show what the doctor changed.
    assert note.structured["mapped"] == note.structured["fields"]


@pytest.mark.asyncio
async def test_the_transcript_is_never_overwritten_by_a_mapping(session: AsyncSession) -> None:
    """The Session-C rule. The words are the irreplaceable half."""
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    spoken = "post-chemo cycle 3, tolerating well, grade 1 mucositis"

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript=spoken)
    await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))
    await notes_svc.apply_corrections(
        session, note=note, doctor=doctor, patch={"assessment": "Rewritten by the doctor"}
    )
    await notes_svc.confirm(session, note=note, doctor=doctor)

    assert note.transcript == spoken


@pytest.mark.asyncio
async def test_a_doctors_edit_lands_in_fields_and_leaves_a_trail(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))
    await notes_svc.apply_corrections(
        session,
        note=note,
        doctor=doctor,
        patch={"assessment": "Tolerating AC-T; mucositis settling."},
    )

    assert note.structured["fields"]["assessment"] == "Tolerating AC-T; mucositis settling."
    # The model's version is untouched — that is what makes the review a diff.
    assert note.structured["mapped"]["assessment"] == "Tolerating AC-T through cycle 3."
    trail = note.structured["edits"]
    assert len(trail) == 1
    assert trail[0]["field"] == "assessment"
    assert trail[0]["by"] == str(doctor.id)


@pytest.mark.asyncio
async def test_a_tag_the_doctor_removes_stays_removed(session: AsyncSession) -> None:
    """Whole-field replacement, not a merge.

    A doctor who deletes a problem the model suggested has made a clinical
    judgement about what this consult was about. A merge would put it back.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))
    await notes_svc.apply_corrections(
        session,
        note=note,
        doctor=doctor,
        patch={"tags": {"problems": [], "symptoms": [], "followups": ["CBC before next cycle"]}},
    )

    fields = note.structured["fields"]
    assert fields["tags"]["problems"] == []
    assert fields["tags"]["symptoms"] == []
    assert fields["tags"]["followups"] == ["CBC before next cycle"]


@pytest.mark.asyncio
async def test_only_the_five_fields_are_editable(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))

    with pytest.raises(notes_svc.NoteError, match="not editable"):
        await notes_svc.apply_corrections(
            session, note=note, doctor=doctor, patch={"meds": [{"name": "Tab Dolo 650"}]}
        )


@pytest.mark.asyncio
async def test_a_confirmed_note_does_not_change(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))
    await notes_svc.confirm(session, note=note, doctor=doctor)

    with pytest.raises(notes_svc.NoteLocked):
        await notes_svc.apply_corrections(
            session, note=note, doctor=doctor, patch={"assessment": "second thoughts"}
        )
    with pytest.raises(notes_svc.NoteLocked):
        await notes_svc.confirm(session, note=note, doctor=doctor)


@pytest.mark.asyncio
async def test_an_empty_note_cannot_be_confirmed(session: AsyncSession) -> None:
    """A note saying nothing is indistinguishable from a mapping that failed and
    was tapped through, and only one of those belongs in the tag counts."""
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    await notes_svc.compose(session, note=note, doctor=doctor)

    with pytest.raises(notes_svc.NoteError, match="empty note"):
        await notes_svc.confirm(session, note=note, doctor=doctor)


@pytest.mark.asyncio
async def test_two_observations_in_one_consult_are_two_notes(session: AsyncSession) -> None:
    """Unlike a dictation, `start` does not reopen the previous capture.

    The mic is on the console for the whole consult; something said at minute two
    and something at minute nine are two observations, and merging them means the
    second silently rewriting the first.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    first = await notes_svc.start(
        session, visit_id=visit.id, doctor=doctor, transcript="mucositis, grade 1"
    )
    second = await notes_svc.start(
        session, visit_id=visit.id, doctor=doctor, transcript="counts have recovered"
    )

    assert first.id != second.id
    rows = await notes_svc.list_for_visit(session, visit_id=visit.id, doctor=doctor)
    assert [n.id for n in rows] == [first.id, second.id]
    assert rows[0].transcript == "mucositis, grade 1"


@pytest.mark.asyncio
async def test_a_note_on_another_departments_patient_is_refused(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    other = f.make_department(clinic["hospital"])
    session.add(other)
    await session.flush()
    visit.department_id = other.id
    await session.flush()

    with pytest.raises(notes_svc.NoteError, match="another department"):
        await notes_svc.start(session, visit_id=visit.id, doctor=clinic["doctor"], transcript="…")


@pytest.mark.asyncio
async def test_every_note_write_is_audited(session: AsyncSession) -> None:
    """`ClinicalNote` carries the `Clinical` marker, so this is automatic — which
    is exactly why it is worth a test that the marker is actually on it."""
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))
    await notes_svc.confirm(session, note=note, doctor=doctor)
    await session.flush()

    rows = (await session.scalars(select(AuditLog).where(AuditLog.entity_id == note.id))).all()
    assert rows, "a clinical note write must land in audit_log"


# =============================================================================
# The degraded state
# =============================================================================


@pytest.mark.asyncio
async def test_a_failed_mapping_keeps_the_words_and_opens_the_fields(
    session: AsyncSession,
) -> None:
    """The Session-C degraded state, applied here.

    There is no deterministic floor to fall back to — a template that guessed at
    an assessment would be inventing clinical content — so the honest degrade is
    an open form beside a stated reason, not a fabricated mapping.
    """
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    spoken = "post-chemo cycle 3, tolerating well"

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript=spoken)
    with pytest.raises(notes_svc.MappingUnavailable):
        await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_broken_mapper())

    assert note.transcript == spoken
    assert "503" in note.structured["mapping_error"]
    assert note.structured["mapped"] is None
    # Open, and empty: the doctor types what they meant.
    assert note.structured["fields"] == notes_svc.NoteMapping().to_dict()


@pytest.mark.asyncio
async def test_a_note_can_still_be_confirmed_after_a_failed_mapping(
    session: AsyncSession,
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    with pytest.raises(notes_svc.MappingUnavailable):
        await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_broken_mapper())
    await notes_svc.apply_corrections(
        session,
        note=note,
        doctor=doctor,
        patch={"assessment": "Tolerating cycle 3. Typed by hand — the model was down."},
    )
    await notes_svc.confirm(session, note=note, doctor=doctor)

    assert note.status is NoteStatus.CONFIRMED
    assert note.confirmed_by == doctor.id


@pytest.mark.asyncio
async def test_a_second_failure_does_not_wipe_what_the_doctor_typed(
    session: AsyncSession,
) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    with pytest.raises(notes_svc.MappingUnavailable):
        await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_broken_mapper())
    await notes_svc.apply_corrections(
        session, note=note, doctor=doctor, patch={"objective": "Grade 1 mucositis."}
    )
    with pytest.raises(notes_svc.MappingUnavailable):
        await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_broken_mapper())

    assert note.structured["fields"]["objective"] == "Grade 1 mucositis."


@pytest.mark.asyncio
async def test_a_model_reply_that_is_not_json_fails_loudly(session: AsyncSession) -> None:
    """It must not become a note that reads as though the doctor said nothing."""
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="…")
    with pytest.raises(Exception):  # noqa: B017 - ProviderBadRequest surfaces from .json()
        await notes_svc.map_transcript(
            session, note=note, doctor=doctor, mapper=_mapper("I am afraid I cannot do that")
        )
    assert notes_svc.current_mapping(note) is None


@pytest.mark.asyncio
async def test_an_empty_transcript_is_not_sent_to_the_model(session: AsyncSession) -> None:
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]
    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="")

    with pytest.raises(notes_svc.NoteError, match="empty"):
        await notes_svc.map_transcript(session, note=note, doctor=doctor, mapper=_mapper(MAPPED))


@pytest.mark.asyncio
async def test_compose_opens_the_fields_with_no_model_at_all(session: AsyncSession) -> None:
    """The typed note. Same record, same confirmation, no model in the loop."""
    clinic, visit = await _clinic_with_visit(session)
    doctor = clinic["doctor"]

    note = await notes_svc.start(session, visit_id=visit.id, doctor=doctor, transcript="")
    await notes_svc.compose(session, note=note, doctor=doctor)
    assert note.structured["fields"] == notes_svc.NoteMapping().to_dict()
    assert note.structured["mapped"] is None

    await notes_svc.apply_corrections(
        session, note=note, doctor=doctor, patch={"objective": "Grade 1 mucositis."}
    )
    await notes_svc.compose(session, note=note, doctor=doctor)
    # Idempotent, and never destructive.
    assert note.structured["fields"]["objective"] == "Grade 1 mucositis."


# =============================================================================
# The contract's edges
# =============================================================================


def test_a_grade_the_doctor_did_not_say_stays_null() -> None:
    """`grade_mentioned` records that a grade was spoken, not that one applies."""
    mapping = notes_svc.NoteMapping.parse(
        {"tags": {"symptoms": [{"name": "fatigue"}, {"name": "mucositis", "grade_mentioned": "2"}]}}
    )
    assert mapping.tags.symptoms[0].grade_mentioned is None
    assert mapping.tags.symptoms[1].grade_mentioned == "2"


def test_a_nameless_symptom_is_dropped() -> None:
    mapping = notes_svc.NoteMapping.parse(
        {"tags": {"symptoms": [{"grade_mentioned": "3"}, {"name": "nausea"}]}}
    )
    assert [s.name for s in mapping.tags.symptoms] == ["nausea"]


def test_a_junk_payload_parses_to_an_empty_note_rather_than_crashing() -> None:
    mapping = notes_svc.NoteMapping.parse({"subjective": None, "tags": "not an object"})
    assert mapping.is_empty
    assert mapping.tags.to_dict() == {"problems": [], "symptoms": [], "followups": []}


def test_a_non_object_payload_is_refused() -> None:
    with pytest.raises(notes_svc.NoteError):
        notes_svc.NoteMapping.parse(["subjective"])
