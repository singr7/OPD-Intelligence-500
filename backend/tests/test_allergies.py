"""Allergy capture (SESSION-ALLERGY) — the spine's third slot, finally holding a fact.

The module's acceptance criterion is a *distinction*, not a feature: three states
that must never collapse into each other, on any surface, ever. So the first and
largest section here is the derivation, and specifically the ways a careless
implementation would quietly turn "nobody asked" into "no known allergies" —
which is the sentence this product has refused to print since Session B, because
a doctor reads it and prescribes.

No test here calls a vendor.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import allergies as svc
from app.models.audit import AuditLog
from app.models.enums import AllergySeverity, AllergySource
from app.models.patient import PatientAllergy


async def _clinic(session: AsyncSession) -> dict:
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()
    clinic["visit"] = visit
    return clinic


# -- the three states ---------------------------------------------------------


async def test_a_patient_nobody_asked_is_never_asked(session: AsyncSession) -> None:
    """AC: no rows means nobody asked — not "no known allergies".

    This is the state every patient in the database is in the moment this
    migration lands, and the one the console has been describing accurately in
    words for six sessions. It stays a distinct state forever.
    """
    clinic = await _clinic(session)
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert view.state == svc.NEVER_ASKED
    assert view.entries == []
    assert view.none_statement is None


async def test_the_patient_saying_none_is_its_own_state_with_provenance(
    session: AsyncSession,
) -> None:
    """AC: "asked and told none" is distinguishable from "never asked", and it
    carries who said it and when — the console may not render it bare."""
    clinic = await _clinic(session)
    await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=True,
    )
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)

    assert view.state == svc.NONE_STATED
    assert view.none_statement is not None
    assert view.none_statement.source == AllergySource.PATIENT_KIOSK
    assert view.none_statement.stated_at is not None
    assert view.none_statement.substance is None


async def test_a_named_substance_is_known(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "पेनिसिलिन", "substance_en": "penicillin"}],
    )
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)

    assert view.state == svc.KNOWN
    assert [e.substance for e in view.entries] == ["पेनिसिलिन"]
    assert view.entries[0].substance_en == "penicillin"
    # Nobody asked what it did to her, so the record says so rather than
    # inventing the reassuring half.
    assert view.entries[0].severity == AllergySeverity.UNKNOWN
    assert view.unconfirmed_count == 1


async def test_a_later_none_never_suppresses_a_named_substance(
    session: AsyncSession,
) -> None:
    """AC: the states are ordered and `known` wins.

    The dangerous version of this module: the patient names penicillin in March,
    a rushed second intake in August taps "no", and the spine goes quiet on an
    anaphylaxis. Un-saying an allergy takes a clinician and a reason.
    """
    clinic = await _clinic(session)
    await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin", "substance_en": "penicillin"}],
    )
    later = f.make_visit(clinic["patient"], clinic["department"])
    session.add(later)
    await session.flush()
    await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=later.id,
        caregiver=False,
        none_known=True,
    )

    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert view.state == svc.KNOWN
    assert [e.substance for e in view.entries] == ["penicillin"]


async def test_everything_retracted_reads_as_never_asked_not_as_none(
    session: AsyncSession,
) -> None:
    """A doctor withdrew the only thing on file. That is not reassurance — the
    next reader should be asking again, so the state goes back to `never_asked`
    rather than to `none_stated`."""
    clinic = await _clinic(session)
    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    await svc.retract(
        session,
        allergy_id=row.id,
        patient_id=clinic["patient"].id,
        doctor=clinic["doctor"],
        reason="patient meant her mother",
    )

    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert view.state == svc.NEVER_ASKED
    assert view.entries == []
    # …but the withdrawal itself is still readable.
    assert len(view.retracted) == 1
    assert view.retracted[0].retracted_reason == "patient meant her mother"
    assert view.retracted[0].retracted_by_name == clinic["doctor"].name


async def test_severe_sorts_above_the_rest(session: AsyncSession) -> None:
    """The spine is read in two seconds; the anaphylaxis goes first."""
    clinic = await _clinic(session)
    await svc.record_by_doctor(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        doctor=clinic["doctor"],
        substance="dust",
    )
    await svc.record_by_doctor(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        doctor=clinic["doctor"],
        substance="penicillin",
        reaction="throat closed",
        severity=AllergySeverity.SEVERE,
    )
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert [e.substance for e in view.entries] == ["penicillin", "dust"]
    assert view.has_severe is True


async def test_a_doctors_none_outranks_a_kiosk_none(session: AsyncSession) -> None:
    """Both say "none". The one the doctor asked in the room is the one shown."""
    clinic = await _clinic(session)
    await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=True,
    )
    await svc.record_by_doctor(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        doctor=clinic["doctor"],
        substance=None,
        none_known=True,
    )
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert view.state == svc.NONE_STATED
    assert view.none_statement is not None
    assert view.none_statement.source == AllergySource.DOCTOR
    assert view.none_statement.confirmed_by_name == clinic["doctor"].name


# -- what the kiosk writes ----------------------------------------------------


async def test_a_resynced_intake_does_not_write_the_allergy_twice(
    session: AsyncSession,
) -> None:
    """AC: idempotent per visit.

    The offline kiosk re-sends an intake whenever the network returns mid-batch.
    A retry that appended a second "penicillin" would show the doctor the same
    allergy twice with two timestamps, which reads as two separate reports.
    """
    clinic = await _clinic(session)
    payload = dict(
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    await svc.from_intake(session, **payload)
    second = await svc.from_intake(session, **payload)

    assert second == []
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert len(view.entries) == 1


async def test_a_resynced_none_does_not_write_twice(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    payload = dict(
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=True,
    )
    await svc.from_intake(session, **payload)
    assert await svc.from_intake(session, **payload) == []


async def test_tapping_yes_and_naming_nothing_writes_nothing(
    session: AsyncSession,
) -> None:
    """An empty `known` state would render as an allergy warning with no
    substance in it, which is an alarm a doctor cannot act on."""
    clinic = await _clinic(session)
    written = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "   "}],
    )
    assert written == []
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert view.state == svc.NEVER_ASKED


async def test_a_caregiver_answering_is_recorded_as_a_caregiver(
    session: AsyncSession,
) -> None:
    """ "Her son said she is allergic to sulfa" is weaker evidence than the
    patient saying it, and the doctor should be able to see which one they have."""
    clinic = await _clinic(session)
    await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=True,
        none_known=False,
        substances=[{"substance": "sulfa"}],
    )
    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    assert view.entries[0].source == AllergySource.CAREGIVER_KIOSK


async def test_a_patient_naming_twenty_things_is_capped(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    written = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": f"drug {i}"} for i in range(20)],
    )
    assert len(written) == svc.MAX_FROM_INTAKE


async def test_a_runaway_transcript_cannot_fill_the_spine(session: AsyncSession) -> None:
    """A stuck STT stream must not write a paragraph into the top of the screen."""
    clinic = await _clinic(session)
    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin " * 400}],
    )
    assert row.substance is not None
    assert len(row.substance) <= svc.MAX_SUBSTANCE


# -- what a doctor does with it -----------------------------------------------


async def test_a_doctor_confirming_keeps_the_patients_words(
    session: AsyncSession,
) -> None:
    """AC: confirming does not require re-typing the substance.

    The commonest act on this surface: the patient named it at a tablet, the
    doctor asked about it in the room. The statement stays the patient's; what
    changes is that a clinician has now heard it.
    """
    clinic = await _clinic(session)
    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    await svc.confirm(
        session, allergy_id=row.id, patient_id=clinic["patient"].id, doctor=clinic["doctor"]
    )

    view = await svc.for_patient(session, patient_id=clinic["patient"].id)
    entry = view.entries[0]
    assert entry.source == AllergySource.PATIENT_KIOSK  # still hers
    assert entry.confirmed_by_name == clinic["doctor"].name
    assert view.unconfirmed_count == 0


async def test_confirming_twice_leaves_the_first_doctors_name(
    session: AsyncSession,
) -> None:
    """Two doctors confirming the same allergy is agreement, not a conflict."""
    clinic = await _clinic(session)
    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    await svc.confirm(
        session, allergy_id=row.id, patient_id=clinic["patient"].id, doctor=clinic["doctor"]
    )
    first_at = row.confirmed_at
    await svc.confirm(
        session, allergy_id=row.id, patient_id=clinic["patient"].id, doctor=clinic["doctor"]
    )
    assert row.confirmed_at == first_at


async def test_a_doctors_own_statement_needs_no_second_doctor(
    session: AsyncSession,
) -> None:
    clinic = await _clinic(session)
    row = await svc.record_by_doctor(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        doctor=clinic["doctor"],
        substance="penicillin",
        reaction="throat closed",
        severity=AllergySeverity.SEVERE,
    )
    assert row.confirmed_at is not None
    assert row.confirmed_by_doctor_id == clinic["doctor"].id
    assert row.recorded_by_doctor_id == clinic["doctor"].id


async def test_a_doctor_cannot_record_an_allergy_with_no_substance(
    session: AsyncSession,
) -> None:
    clinic = await _clinic(session)
    with pytest.raises(svc.AllergyError):
        await svc.record_by_doctor(
            session,
            patient_id=clinic["patient"].id,
            visit_id=clinic["visit"].id,
            doctor=clinic["doctor"],
            substance="  ",
        )


async def test_retracting_is_never_a_delete(session: AsyncSession) -> None:
    """AC: the row survives, struck out. A record that silently loses a withdrawn
    allergy cannot answer what it told the doctor who prescribed last month."""
    clinic = await _clinic(session)
    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    await svc.retract(
        session,
        allergy_id=row.id,
        patient_id=clinic["patient"].id,
        doctor=clinic["doctor"],
        reason="wrong patient",
    )

    stored = (await session.execute(select(PatientAllergy))).scalars().all()
    assert len(stored) == 1
    assert stored[0].deleted_at is None
    assert stored[0].retracted_at is not None


async def test_a_retracted_statement_cannot_be_confirmed(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    await svc.retract(
        session, allergy_id=row.id, patient_id=clinic["patient"].id, doctor=clinic["doctor"]
    )
    with pytest.raises(svc.AllergyError):
        await svc.confirm(
            session, allergy_id=row.id, patient_id=clinic["patient"].id, doctor=clinic["doctor"]
        )


async def test_a_statement_cannot_be_written_onto_another_patients_chart(
    session: AsyncSession,
) -> None:
    """A console bug that passed the wrong patient id must fail loudly rather
    than quietly striking out somebody else's penicillin allergy."""
    clinic = await _clinic(session)
    other = f.make_patient(clinic["hospital"])
    session.add(other)
    await session.flush()

    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    with pytest.raises(svc.AllergyError):
        await svc.retract(session, allergy_id=row.id, patient_id=other.id, doctor=clinic["doctor"])
    with pytest.raises(svc.AllergyError):
        await svc.confirm(session, allergy_id=row.id, patient_id=other.id, doctor=clinic["doctor"])


async def test_an_unknown_allergy_id_is_refused(session: AsyncSession) -> None:
    clinic = await _clinic(session)
    with pytest.raises(svc.AllergyError):
        await svc.confirm(
            session,
            allergy_id=uuid.uuid4(),
            patient_id=clinic["patient"].id,
            doctor=clinic["doctor"],
        )


# -- the record of the record -------------------------------------------------


async def test_every_statement_is_audited(session: AsyncSession) -> None:
    """Both halves: stating it and withdrawing it."""
    clinic = await _clinic(session)
    [row] = await svc.from_intake(
        session,
        patient_id=clinic["patient"].id,
        visit_id=clinic["visit"].id,
        caregiver=False,
        none_known=False,
        substances=[{"substance": "penicillin"}],
    )
    await svc.retract(
        session, allergy_id=row.id, patient_id=clinic["patient"].id, doctor=clinic["doctor"]
    )
    await session.flush()

    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.entity == "patient_allergies")))
        .scalars()
        .all()
    )
    actions = {str(r.action) for r in rows}
    assert "create" in actions
    assert "update" in actions  # the retraction


async def test_nothing_in_this_module_reads_the_formulary() -> None:
    """AC (negative): no drug matching, here or anywhere downstream of here.

    An interaction checker fed free text a patient typed at a kiosk would be a
    safety feature made of guesses, and the failure mode of a *missed* match is
    a doctor who trusted a green tick. The spine puts the words in front of the
    doctor; the doctor decides. Pinned as source, like `app/notes.py`'s refusal
    to reach the prescription path, so that adding one is a deliberate act
    against a failing test rather than an import somebody did not notice.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(svc.__file__).read_text())
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden = {"app.formulary", "app.prescription", "app.rx_sheets"}
    assert not (imported & forbidden), f"allergies must not reach {imported & forbidden}"
