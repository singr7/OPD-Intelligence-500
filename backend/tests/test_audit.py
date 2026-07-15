"""Audit trail: coverage, correctness, immutability, and PII discipline.

AC for S2: "audit row on every clinical write". `test_every_clinical_write_is_audited`
is the direct proof — it writes to *every* Clinical model found on the mapper
registry and asserts a row appears, so a table added in a later session is
covered without anyone remembering to extend this test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import Actor, acting_as, audited_models
from app.models.audit import AuditLog
from app.models.base import Clinical
from app.models.enums import AuditAction, Role, VisitStatus
from app.models.org import Department, Doctor, Hospital, User
from tests import factories as f


async def _audit_rows(session: AsyncSession, entity: str) -> list[AuditLog]:
    result = await session.execute(
        select(AuditLog).where(AuditLog.entity == entity).order_by(AuditLog.at)
    )
    return list(result.scalars())


async def test_create_of_clinical_row_is_audited(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)

    rows = await _audit_rows(session, "patients")
    assert len(rows) == 1
    assert rows[0].action is AuditAction.CREATE
    assert rows[0].entity_id == clinic["patient"].id


async def test_update_records_changed_fields(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()

    visit.status = VisitStatus.IN_CONSULT
    await session.flush()

    rows = await _audit_rows(session, "visits")
    assert [r.action for r in rows] == [AuditAction.CREATE, AuditAction.UPDATE]
    assert rows[1].meta["changed"]["status"] == {"from": "registered", "to": "in_consult"}


async def test_soft_delete_is_recorded_as_a_deletion(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)

    clinic["patient"].deleted_at = datetime.now(UTC)
    await session.flush()

    rows = await _audit_rows(session, "patients")
    assert [r.action for r in rows] == [AuditAction.CREATE, AuditAction.SOFT_DELETE]


async def test_untouched_object_produces_no_audit_row(session: AsyncSession) -> None:
    """A no-op write must not log — otherwise re-running the seed spams the log."""
    clinic = await f.build_clinic(session)
    before = len(await _audit_rows(session, "patients"))

    clinic["patient"].village = clinic["patient"].village  # same value
    await session.flush()

    assert len(await _audit_rows(session, "patients")) == before


async def test_non_clinical_tables_are_not_audited(session: AsyncSession) -> None:
    """Departments and users are configuration, not the clinical record."""
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    session.add(f.make_department(hospital))
    await session.flush()

    assert await _audit_rows(session, "departments") == []
    assert await _audit_rows(session, "hospitals") == []


async def test_actor_is_attributed_from_context(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    actor_id = clinic["user"].id

    with acting_as(
        Actor(
            id=actor_id,
            role=Role.DOCTOR,
            label="Dr. Anil Gupta",
            request_id="req-123",
            ip="10.0.0.5",
        )
    ):
        visit = f.make_visit(clinic["patient"], clinic["department"])
        session.add(visit)
        await session.flush()

    row = (await _audit_rows(session, "visits"))[0]
    assert row.actor_id == actor_id
    assert row.actor_role is Role.DOCTOR
    assert row.actor_label == "Dr. Anil Gupta"
    assert row.request_id == "req-123"
    assert row.ip == "10.0.0.5"


async def test_writes_without_an_actor_are_attributed_to_system(session: AsyncSession) -> None:
    """Celery, webhooks and seeds have no user — they must not look like one."""
    await f.build_clinic(session)

    row = (await _audit_rows(session, "patients"))[0]
    assert row.actor_id is None
    assert row.actor_label == "system"


async def test_pii_is_redacted_in_the_change_log(session: AsyncSession) -> None:
    """The log records *that* a field changed, never the PII itself (doc 02 §7)."""
    clinic = await f.build_clinic(session)
    patient = clinic["patient"]

    patient.name = "Sunita Devi"
    patient.phone = "+915551900999"
    patient.village = "Behror"
    await session.flush()

    row = (await _audit_rows(session, "patients"))[-1]
    changed = row.meta["changed"]

    assert set(changed) == {"name", "phone", "village"}
    for field in ("name", "phone", "village"):
        assert changed[field] == {"from": "<redacted>", "to": "<redacted>"}

    serialized = str(row.meta)
    assert "Sunita Devi" not in serialized
    assert "+915551900999" not in serialized
    assert "Behror" not in serialized


async def test_non_pii_fields_keep_their_values(session: AsyncSession) -> None:
    """Redaction must not blind the log to the things it exists to explain."""
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()

    visit.status = VisitStatus.NO_SHOW
    await session.flush()

    row = (await _audit_rows(session, "visits"))[-1]
    assert row.meta["changed"]["status"]["to"] == "no_show"


async def test_clinical_free_text_is_redacted(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()

    intake = f.make_intake(visit)
    session.add(intake)
    await session.flush()

    intake.summary_md = "## Patient reports severe abdominal pain, 8/10"
    intake.answers = {"onc.pain.severity": {"value": 8}}
    await session.flush()

    row = (await _audit_rows(session, "intakes"))[-1]
    assert row.meta["changed"]["summary_md"] == {"from": None, "to": "<redacted>"}
    assert "abdominal pain" not in str(row.meta)


async def test_every_clinical_write_is_audited(session: AsyncSession) -> None:
    """AC: an audit row on every clinical write, enforced over the whole registry.

    Rather than listing tables by hand (which goes stale the moment S5 adds one),
    this walks every model marked `Clinical` and writes one. A new clinical table
    that somehow escapes the hook fails here.
    """
    clinic = await f.build_clinic(session)
    visit = f.make_visit(clinic["patient"], clinic["department"])
    session.add(visit)
    await session.flush()

    dictation = f.make_dictation(visit, clinic["doctor"])
    session.add(dictation)
    await session.flush()

    # One representative instance per Clinical model.
    from app.models.clinical import Prescription
    from app.models.content import Checkin, CheckinPlan
    from app.models.enums import Channel
    from app.models.scheduling import Appointment, Queue, QueueEntry

    queue = Queue(department_id=clinic["department"].id, date=visit.date)
    session.add(queue)
    await session.flush()

    plan = CheckinPlan(patient_id=clinic["patient"].id, visit_id=visit.id, protocol_key="platinum")
    session.add(plan)
    await session.flush()

    instances: dict[str, object] = {
        "patients": clinic["patient"],
        "visits": visit,
        "intakes": f.make_intake(visit),
        "dictations": dictation,
        "prescriptions": Prescription(visit_id=visit.id, dictation_id=dictation.id),
        "appointments": Appointment(
            patient_id=clinic["patient"].id,
            department_id=clinic["department"].id,
            slot_at=datetime.now(UTC),
            source=Channel.PHONE,
        ),
        "queue_entries": QueueEntry(queue_id=queue.id, visit_id=visit.id, token_no=1),
        "checkin_plans": plan,
        "checkins": Checkin(plan_id=plan.id, due_at=datetime.now(UTC), channel=Channel.WHATSAPP),
    }

    expected = {m.__tablename__ for m in audited_models()}
    assert set(instances) == expected, (
        "a Clinical model is missing from this test — add an instance for it: "
        f"{expected.symmetric_difference(instances)}"
    )

    for obj in instances.values():
        session.add(obj)
    await session.flush()

    for table in expected:
        rows = await _audit_rows(session, table)
        assert rows, f"no audit row written for a write to {table}"
        assert any(r.action is AuditAction.CREATE for r in rows)


async def test_clinical_marker_covers_the_expected_tables() -> None:
    """Guards the policy itself: these tables are the clinical record.

    If a table legitimately joins or leaves this set, that is a deliberate
    decision and this list should change with it.
    """
    assert {m.__tablename__ for m in audited_models()} == {
        "patients",
        "visits",
        "intakes",
        "dictations",
        "prescriptions",
        "appointments",
        "queue_entries",
        "checkin_plans",
        "checkins",
    }


async def test_config_tables_are_deliberately_not_clinical() -> None:
    for model in (Hospital, Department, User, Doctor):
        assert not issubclass(model, Clinical)


async def test_audit_row_shares_the_writers_transaction(session: AsyncSession) -> None:
    """A write and its audit row commit or roll back together."""
    clinic = await f.build_clinic(session)

    savepoint = await session.begin_nested()
    patient = f.make_patient(clinic["hospital"])
    session.add(patient)
    await session.flush()
    assert any(r.entity_id == patient.id for r in await _audit_rows(session, "patients"))

    await savepoint.rollback()

    rows = await _audit_rows(session, "patients")
    assert not any(r.entity_id == patient.id for r in rows), (
        "audit row survived a rolled-back write — the log would claim something that never happened"
    )


async def test_audit_log_rejects_update(session: AsyncSession) -> None:
    """Append-only, enforced by the database (doc 02 §7)."""
    await f.build_clinic(session)
    row = (await _audit_rows(session, "patients"))[0]

    with pytest.raises(DBAPIError) as exc:
        await session.execute(
            text("UPDATE audit_log SET actor_label = 'tampered' WHERE id = :id"), {"id": row.id}
        )
    assert "append-only" in str(exc.value)


async def test_audit_log_rejects_delete(session: AsyncSession) -> None:
    await f.build_clinic(session)
    row = (await _audit_rows(session, "patients"))[0]

    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": row.id})
    assert "append-only" in str(exc.value)


async def test_audit_log_rejects_truncate(session: AsyncSession) -> None:
    with pytest.raises(DBAPIError) as exc:
        await session.execute(text("TRUNCATE audit_log"))
    assert "append-only" in str(exc.value)


async def test_audit_entry_identifies_the_row_it_describes(session: AsyncSession) -> None:
    clinic = await f.build_clinic(session)
    row = (await _audit_rows(session, "patients"))[0]

    assert row.entity == "patients"
    assert row.entity_id == clinic["patient"].id
    assert isinstance(row.entity_id, uuid.UUID)
    assert row.at is not None
