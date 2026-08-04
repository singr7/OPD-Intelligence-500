"""Identity matching and doctor assignment (`app.assignment`).

The behaviours worth pinning here are the ones that are quietly destructive:
merging two people's oncology records, retiring a real patient row, offering a
previous anonymous walk-in as though it were a file, or putting a patient on a
worklist filtered by a department they are not queued in.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import tests.factories as f
from app import assignment as a
from app import kiosk as kiosk_svc
from app import queue as q
from app.models.enums import (
    Channel,
    PatientLinkState,
    Priority,
    QueueEntryState,
    Role,
    VisitStatus,
)
from app.models.scheduling import Queue

pytestmark = pytest.mark.asyncio

TODAY = q.today()


@pytest.fixture
async def clinic(session: AsyncSession):
    return await f.build_clinic(session)


async def _walk_in(session: AsyncSession, clinic, **overrides):
    """A kiosk arrival: its own generated patient row plus today's visit."""
    walk_in = f.make_patient(
        clinic["hospital"],
        mrn=f"WALKIN-{f._n():08d}",
        name="Walk-in patient",
        **overrides,
    )
    session.add(walk_in)
    await session.flush()
    visit = f.make_visit(
        walk_in,
        clinic["department"],
        date=TODAY,
        channel=Channel.KIOSK,
        status=VisitStatus.REGISTERED,
    )
    session.add(visit)
    await session.flush()
    return walk_in, visit


# -- finding a prior file -----------------------------------------------------


async def test_a_phone_number_finds_the_registered_patient(session, clinic):
    prior = f.make_patient(clinic["hospital"], phone="+919876543210")
    session.add(prior)
    await session.flush()

    # The same handset, written the three ways the pilot actually receives it.
    for typed in ("+919876543210", "919876543210", "09876543210"):
        found = await a.find_candidate(session, hospital_id=clinic["hospital"].id, phone=typed)
        assert found is not None and found.id == prior.id, typed


async def test_a_uhc_id_beats_a_shared_handset(session, clinic):
    """A phone identifies a household; a health ID identifies a person."""
    husband = f.make_patient(clinic["hospital"], phone="+919876543210", name="A")
    wife = f.make_patient(clinic["hospital"], phone="+919876543210", name="B", external_id="UHC-42")
    session.add_all([husband, wife])
    await session.flush()

    found = await a.find_candidate(
        session,
        hospital_id=clinic["hospital"].id,
        phone="+919876543210",
        external_id="UHC-42",
    )
    assert found is not None and found.id == wife.id


async def test_a_previous_walk_in_is_never_offered_as_a_file(session, clinic):
    """Matching one would hand the coordinator an earlier anonymous arrival."""
    earlier, _ = await _walk_in(session, clinic, phone="+919876543210")
    assert earlier.mrn.startswith("WALKIN-")

    found = await a.find_candidate(
        session, hospital_id=clinic["hospital"].id, phone="+919876543210"
    )
    assert found is None


async def test_a_short_or_missing_number_matches_nobody(session, clinic):
    prior = f.make_patient(clinic["hospital"], phone="+919876543210")
    session.add(prior)
    await session.flush()

    for typed in (None, "", "98765", "not a phone"):
        assert (
            await a.find_candidate(session, hospital_id=clinic["hospital"].id, phone=typed) is None
        ), typed


async def test_a_match_never_crosses_hospitals(session, clinic):
    other = f.make_hospital()
    session.add(other)
    await session.flush()
    theirs = f.make_patient(other, phone="+919876543210")
    session.add(theirs)
    await session.flush()

    assert (
        await a.find_candidate(session, hospital_id=clinic["hospital"].id, phone="+919876543210")
        is None
    )


# -- the candidate is recorded, not acted on ----------------------------------


async def test_noting_a_candidate_does_not_move_the_visit(session, clinic):
    """The kiosk discloses nothing and merges nothing. A human decides."""
    prior = f.make_patient(clinic["hospital"], phone="+919876543210")
    session.add(prior)
    await session.flush()
    walk_in, visit = await _walk_in(session, clinic)

    await a.note_candidate(session, visit=visit, candidate=prior)

    assert visit.patient_link_state is PatientLinkState.CANDIDATE
    assert visit.candidate_patient_id == prior.id
    # Still the walk-in's visit until somebody says otherwise.
    assert visit.patient_id == walk_in.id


async def test_no_candidate_leaves_the_visit_untouched(session, clinic):
    _, visit = await _walk_in(session, clinic)
    await a.note_candidate(session, visit=visit, candidate=None)
    assert visit.patient_link_state is PatientLinkState.NONE
    assert visit.candidate_patient_id is None


# -- confirming and rejecting -------------------------------------------------


async def test_confirming_repoints_the_visit_and_retires_the_walk_in(session, clinic):
    prior = f.make_patient(clinic["hospital"], phone="+919876543210", name="Lakshmi Nair")
    session.add(prior)
    await session.flush()
    walk_in, visit = await _walk_in(session, clinic)
    await a.note_candidate(session, visit=visit, candidate=prior)

    linked = await a.confirm_link(session, visit=visit)

    assert linked.id == prior.id
    assert visit.patient_id == prior.id
    assert visit.patient_link_state is PatientLinkState.CONFIRMED
    # Soft-deleted, with its demographics intact: a wrong link has to be
    # reconstructible without a restore from backup.
    assert walk_in.deleted_at is not None
    assert walk_in.name == "Walk-in patient"


async def test_confirming_is_idempotent(session, clinic):
    prior = f.make_patient(clinic["hospital"])
    session.add(prior)
    await session.flush()
    _, visit = await _walk_in(session, clinic)
    await a.note_candidate(session, visit=visit, candidate=prior)

    first = await a.confirm_link(session, visit=visit)
    again = await a.confirm_link(session, visit=visit)
    assert first.id == again.id == prior.id


async def test_confirming_never_deletes_a_registered_patient(session, clinic):
    """A visit already pointing at a real file must not lose it to a link."""
    registered = f.make_patient(clinic["hospital"], name="Already registered")
    prior = f.make_patient(clinic["hospital"], name="The match")
    session.add_all([registered, prior])
    await session.flush()
    visit = f.make_visit(registered, clinic["department"], date=TODAY, channel=Channel.KIOSK)
    session.add(visit)
    await session.flush()
    await a.note_candidate(session, visit=visit, candidate=prior)

    await a.confirm_link(session, visit=visit)

    assert registered.deleted_at is None


async def test_confirming_without_a_candidate_is_refused(session, clinic):
    _, visit = await _walk_in(session, clinic)
    with pytest.raises(a.AssignmentError):
        await a.confirm_link(session, visit=visit)


async def test_rejecting_clears_the_candidate_for_good(session, clinic):
    prior = f.make_patient(clinic["hospital"])
    session.add(prior)
    await session.flush()
    walk_in, visit = await _walk_in(session, clinic)
    await a.note_candidate(session, visit=visit, candidate=prior)

    await a.reject_link(session, visit=visit)

    assert visit.patient_link_state is PatientLinkState.REJECTED
    assert visit.candidate_patient_id is None
    assert visit.patient_id == walk_in.id
    assert prior.deleted_at is None


# -- who the coordinator may pick ---------------------------------------------


async def _second_doctor(session, clinic, name="Dr Second"):
    user = f.make_user(clinic["hospital"], role=Role.DOCTOR, name=name)
    session.add(user)
    await session.flush()
    doctor = f.make_doctor(user, clinic["department"], name=name)
    session.add(doctor)
    await session.flush()
    return doctor


async def test_the_rostered_doctor_sorts_first_but_nobody_is_hidden(session, clinic):
    """A pilot roster is often incomplete; hiding the consultant standing in the
    room would make the coordinator assign nobody at all."""
    rostered = await _second_doctor(session, clinic, name="A Rostered")
    session.add(f.make_slot_template(rostered, weekday=TODAY.weekday(), active=True))
    await session.flush()

    options = await a.assignable_doctors(session, department_id=clinic["department"].id, on=TODAY)

    assert [o.on_duty for o in options][0] is True
    assert options[0].id == rostered.id
    # The seeded doctor has no template and is still offered.
    assert len(options) == 2
    assert any(o.on_duty is False for o in options)


async def test_a_template_for_another_weekday_is_not_on_duty(session, clinic):
    doctor = await _second_doctor(session, clinic)
    session.add(f.make_slot_template(doctor, weekday=(TODAY.weekday() + 1) % 7, active=True))
    await session.flush()

    options = await a.assignable_doctors(session, department_id=clinic["department"].id, on=TODAY)
    assert all(o.on_duty is False for o in options)


async def test_one_doctor_on_duty_is_the_default_two_is_a_question(session, clinic):
    first = await _second_doctor(session, clinic, name="A First")
    session.add(f.make_slot_template(first, weekday=TODAY.weekday(), active=True))
    await session.flush()

    options = await a.assignable_doctors(session, department_id=clinic["department"].id, on=TODAY)
    assert a.default_doctor(options) is not None
    assert a.default_doctor(options).id == first.id

    second = await _second_doctor(session, clinic, name="B Second")
    session.add(f.make_slot_template(second, weekday=TODAY.weekday(), active=True))
    await session.flush()
    options = await a.assignable_doctors(session, department_id=clinic["department"].id, on=TODAY)
    # Two real candidates: guessing is how a patient lands on the wrong list.
    assert a.default_doctor(options) is None


# -- assignment ---------------------------------------------------------------


async def test_assigning_writes_the_doctor(session, clinic):
    _, visit = await _walk_in(session, clinic)
    await a.assign(session, visit=visit, doctor_id=clinic["doctor"].id)
    assert visit.doctor_id == clinic["doctor"].id


async def test_assigning_nobody_is_a_legal_outcome(session, clinic):
    """`Skip` on the strip, and every offline arrival, land in the pool."""
    _, visit = await _walk_in(session, clinic)
    await a.assign(session, visit=visit, doctor_id=clinic["doctor"].id)
    await a.assign(session, visit=visit, doctor_id=None)
    assert visit.doctor_id is None


async def test_a_doctor_from_another_department_is_refused(session, clinic):
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    user = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add(user)
    await session.flush()
    outsider = f.make_doctor(user, other_dept)
    session.add(outsider)
    await session.flush()
    _, visit = await _walk_in(session, clinic)

    with pytest.raises(a.AssignmentError):
        await a.assign(session, visit=visit, doctor_id=outsider.id)


async def _other_department_with_a_doctor(session, clinic):
    other_dept = f.make_department(clinic["hospital"])
    session.add(other_dept)
    await session.flush()
    user = f.make_user(clinic["hospital"], role=Role.DOCTOR)
    session.add(user)
    await session.flush()
    surgeon = f.make_doctor(user, other_dept)
    session.add(surgeon)
    await session.flush()
    return other_dept, surgeon


async def _queued_walk_in(session, clinic, *, priority=Priority.ROUTINE, reason=None):
    """A walk-in that actually reached the line, which is the only state a
    department correction happens from."""
    walk_in, visit = await _walk_in(session, clinic)
    visit.token_no = await kiosk_svc.allocate_token(session, visit)
    entry = await q.enqueue(session, visit=visit, priority=priority, priority_reason=reason)
    return walk_in, visit, entry


async def test_changing_department_moves_the_visit_and_allows_its_doctors(session, clinic):
    other_dept, surgeon = await _other_department_with_a_doctor(session, clinic)
    _, visit, entry = await _queued_walk_in(session, clinic)

    result = await a.assign(session, visit=visit, doctor_id=surgeon.id, department=other_dept)

    assert visit.department_id == other_dept.id
    assert visit.doctor_id == surgeon.id
    # The queue entry moved with the patient rather than being left behind on a
    # board they are no longer queued in.
    moved_to = await session.get(Queue, entry.queue_id)
    assert moved_to.department_id == other_dept.id


async def test_changing_department_reissues_the_token(session, clinic):
    """The old number belongs to the old department's series and can collide
    with a real one there."""
    other_dept, surgeon = await _other_department_with_a_doctor(session, clinic)
    # Somebody is already holding token 1 in the destination department.
    sitting = f.make_patient(clinic["hospital"])
    session.add(sitting)
    await session.flush()
    theirs = f.make_visit(sitting, other_dept, date=TODAY, channel=Channel.KIOSK)
    session.add(theirs)
    await session.flush()
    theirs.token_no = await kiosk_svc.allocate_token(session, theirs)

    _, visit, _ = await _queued_walk_in(session, clinic)
    old = visit.token_no

    result = await a.assign(session, visit=visit, doctor_id=surgeon.id, department=other_dept)

    assert result.token_reissued
    assert result.old_token_no == old
    assert result.new_token_no == visit.token_no != old
    assert visit.token_no != theirs.token_no


async def test_a_moved_patient_keeps_their_urgency(session, clinic):
    """Re-routing is a clerical correction. A red flag is not undone by it."""
    other_dept, surgeon = await _other_department_with_a_doctor(session, clinic)
    _, visit, entry = await _queued_walk_in(
        session, clinic, priority=Priority.URGENT, reason="Non-healing ulcer"
    )

    await a.assign(session, visit=visit, doctor_id=surgeon.id, department=other_dept)

    assert entry.priority is Priority.URGENT
    assert entry.priority_reason == "Non-healing ulcer"


async def test_a_consultation_already_under_way_cannot_be_re_routed(session, clinic):
    """By then there is a clinical record attached to the department it happened in."""
    other_dept, surgeon = await _other_department_with_a_doctor(session, clinic)
    _, visit, entry = await _queued_walk_in(session, clinic)
    entry.state = QueueEntryState.IN_CONSULT
    await session.flush()

    with pytest.raises(a.AssignmentError):
        await a.assign(session, visit=visit, doctor_id=surgeon.id, department=other_dept)


async def test_assigning_within_the_department_never_touches_the_token(session, clinic):
    _, visit, _ = await _queued_walk_in(session, clinic)
    old = visit.token_no

    result = await a.assign(session, visit=visit, doctor_id=clinic["doctor"].id)

    assert not result.token_reissued
    assert visit.token_no == old


async def test_an_inactive_doctor_is_refused(session, clinic):
    doctor = await _second_doctor(session, clinic)
    doctor.active = False
    await session.flush()
    _, visit = await _walk_in(session, clinic)

    with pytest.raises(a.AssignmentError):
        await a.assign(session, visit=visit, doctor_id=doctor.id)
