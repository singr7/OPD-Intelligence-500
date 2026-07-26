"""Admin console tests (S18) — the editor's invariants and the publish→live path.

`test_publish_makes_the_edit_live_on_the_intake_path` is the S18 headline AC in
one test: edit a tree option, publish, and the very query the kiosk runs
(`store.resolve_tree`) returns the new content — no deploy, no re-seed. The rest
pin the guards: role, validation, audit, and the deferred-panel markers.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app import admin as admin_svc
from app.auth.tokens import create_access_token
from app.config import Settings
from app.models.audit import AuditLog
from app.models.enums import Role, TreeStatus
from app.models.metering import PriceBook
from app.trees import store
from app.trees.bank import TREES_DIR
from tests.factories import make_department, make_hospital, make_user

# `asyncio_mode = "auto"` (pyproject) runs async tests without a per-test mark, so
# no module-level pytestmark here — it would wrongly flag the two sync test-run
# tests below.

TREE_KEY = "general_medicine_routing"
TREE_DEPT = "GENMED"
TREE_ROOT = "gm.problem"


def _raw_tree() -> dict:
    return json.loads((TREES_DIR / f"{TREE_KEY}.json").read_text())


def _set_root_text(raw: dict, text_en: str) -> None:
    for node in raw["nodes"]:
        if node["id"] == TREE_ROOT:
            node["text"]["en"] = text_en
            return
    raise AssertionError("root node not found")


async def _department(session):
    hospital = make_hospital()
    session.add(hospital)
    await session.flush()
    dept = make_department(hospital, code=TREE_DEPT)
    session.add(dept)
    await session.flush()
    return dept


async def _admin_headers(session, settings: Settings) -> dict[str, str]:
    hospital = make_hospital()
    session.add(hospital)
    await session.flush()
    user = make_user(hospital, role=Role.ADMIN)
    session.add(user)
    await session.flush()
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        name=user.name,
        settings=settings,
        hospital_id=user.hospital_id,
    ).token
    return {"Authorization": f"Bearer {token}"}


# -- publish → live -----------------------------------------------------------


async def test_publish_makes_the_edit_live_on_the_intake_path(session) -> None:
    await _department(session)

    # Before anything is published, the intake path falls back to the disk bank.
    fallback = await store.resolve_tree(session, TREE_DEPT)
    assert fallback is not None and fallback.key == TREE_KEY

    # Publish an edited version, then the intake path serves the edit.
    raw = _raw_tree()
    _set_root_text(raw, "EDITED: what brings you in today?")
    v = await admin_svc.save_tree_draft(session, key=TREE_KEY, tree_json=raw)
    await admin_svc.publish_tree(session, key=TREE_KEY, version=v.version)

    live = await store.resolve_tree(session, TREE_DEPT)
    assert live.node(TREE_ROOT).text["en"] == "EDITED: what brings you in today?"

    # A second edit + publish supersedes it — exactly one published version wins.
    raw2 = _raw_tree()
    _set_root_text(raw2, "SECOND EDIT")
    v2 = await admin_svc.save_tree_draft(session, key=TREE_KEY, tree_json=raw2)
    await admin_svc.publish_tree(session, key=TREE_KEY, version=v2.version)

    live2 = await store.resolve_tree(session, TREE_DEPT)
    assert live2.node(TREE_ROOT).text["en"] == "SECOND EDIT"

    # Exactly one published version survives — the ratchet demotes the rest.
    from app.models.content import QuestionTree

    n_published = await session.scalar(
        select(func.count())
        .select_from(QuestionTree)
        .where(QuestionTree.key == TREE_KEY, QuestionTree.status == TreeStatus.PUBLISHED)
    )
    assert n_published == 1


async def test_save_draft_rejects_an_invalid_tree(session) -> None:
    await _department(session)
    raw = _raw_tree()
    del raw["nodes"]  # structurally broken
    with pytest.raises(admin_svc.AdminError):
        await admin_svc.save_tree_draft(session, key=TREE_KEY, tree_json=raw)


async def test_publish_writes_an_audit_row(session) -> None:
    await _department(session)
    raw = _raw_tree()
    v = await admin_svc.save_tree_draft(session, key=TREE_KEY, tree_json=raw)
    await admin_svc.publish_tree(session, key=TREE_KEY, version=v.version)

    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.entity == "question_trees")))
        .scalars()
        .all()
    )
    actions = {r.action.value for r in rows}
    assert "create" in actions and "update" in actions


# -- price book ---------------------------------------------------------------


async def test_add_price_row_audits_and_invalidates_cache(session) -> None:
    from app.models.enums import PriceUnit
    from app.providers.pricing import PriceBookCache, get_price_book, set_price_book

    cache = PriceBookCache(ttl_seconds=999)
    set_price_book(cache)
    try:
        cache._loaded_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        assert cache._loaded_at is not None
        await admin_svc.add_price_row(
            session,
            admin_svc.PriceEdit(
                provider="acme",
                model="m1",
                unit=PriceUnit.TOKEN_IN,
                price_inr=Decimal("1.23"),
                effective_from=date(2026, 6, 1),
            ),
        )
        # Cache was invalidated so the new rate takes effect without a TTL wait.
        assert get_price_book()._loaded_at is None
    finally:
        set_price_book(PriceBookCache())

    audited = await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.entity == "price_book")
    )
    assert audited == 1
    stored = await session.scalar(
        select(func.count()).select_from(PriceBook).where(PriceBook.provider == "acme")
    )
    assert stored == 1


async def test_duplicate_price_row_is_refused(session) -> None:
    from app.models.enums import PriceUnit

    edit = admin_svc.PriceEdit(
        provider="acme",
        model="m1",
        unit=PriceUnit.TOKEN_IN,
        price_inr=Decimal("1.00"),
        effective_from=date(2026, 6, 1),
    )
    await admin_svc.add_price_row(session, edit)
    with pytest.raises(admin_svc.AdminError):
        await admin_svc.add_price_row(session, edit)


# -- test-run -----------------------------------------------------------------


def test_test_run_walks_and_reports() -> None:
    result = admin_svc.test_run(_raw_tree(), {})
    assert result.error is None
    assert result.path[0] == TREE_ROOT
    assert result.complete is False  # nothing answered yet


def test_test_run_reports_a_bad_answer_inline() -> None:
    result = admin_svc.test_run(_raw_tree(), {TREE_ROOT: "not-an-option-value"})
    assert result.error is not None


# -- HTTP: role guard + deferred markers --------------------------------------


async def test_admin_routes_require_admin_role(client: AsyncClient, session, settings) -> None:
    # No token → 401.
    assert (await client.get("/admin/trees")).status_code == 401

    # A non-admin staff token → 403.
    hospital = make_hospital()
    session.add(hospital)
    await session.flush()
    nurse = make_user(hospital, role=Role.NURSE)
    session.add(nurse)
    await session.flush()
    token = create_access_token(
        user_id=nurse.id,
        role=nurse.role,
        name=nurse.name,
        settings=settings,
        hospital_id=nurse.hospital_id,
    ).token
    resp = await client.get("/admin/trees", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_admin_can_list_trees_and_deferred_panels(client: AsyncClient, session, settings):
    headers = await _admin_headers(session, settings)

    assert (await client.get("/admin/trees", headers=headers)).status_code == 200
    assert (await client.get("/admin/templates", headers=headers)).status_code == 200

    # Protocol templates stopped being a placeholder in S17 — the panel now shows
    # the real bank, read-only (the editor is S18-late and wants a table first).
    protocol = (await client.get("/admin/protocol-templates", headers=headers)).json()
    assert protocol["editable"] is False
    assert protocol["source"] == "seeds/protocols.json"
    assert {p["key"] for p in protocol["protocols"]} == {
        "platinum",
        "taxane",
        "anthracycline",
        "radiotherapy",
        "post_op",
        "palliative",
    }
    platinum = next(p for p in protocol["protocols"] if p["key"] == "platinum")
    assert [rung["day_offset"] for rung in platinum["checkins"]] == [2, 7, 14]
    # Every set the console shows can actually escalate something.
    assert all(qset["grading"] for qset in protocol["question_sets"])

    slots = await client.get("/admin/slot-templates", headers=headers)
    assert slots.json()["deferred"] is True and slots.json()["arrives_in"] == "S15"
