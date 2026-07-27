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
from app.checkins import protocols
from app.checkins import store as checkin_store
from app.config import Settings
from app.models.audit import AuditLog
from app.models.enums import ContentStatus, Role, TreeStatus
from app.models.metering import PriceBook
from app.seed import SEEDS_DIR
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

    # The protocol panel shows the *live* bank — the seed file until something is
    # published, the published row after (S18-late).
    protocol = (await client.get("/admin/protocol-templates", headers=headers)).json()
    assert protocol["editable"] is True
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

    # S-GL.2 built the last deferred panel: this answers with the clinic grid
    # (empty on a fresh transaction), not with a marker. `test_roster.py` walks
    # the panel itself.
    slots = await client.get("/admin/slot-templates", headers=headers)
    assert slots.status_code == 200 and isinstance(slots.json(), list)


# -- protocol bank: publish → live on the check-in path ------------------------


def _raw_bank() -> dict:
    return json.loads((SEEDS_DIR / "protocols.json").read_text())


async def test_publishing_a_protocol_bank_makes_it_live_for_the_next_plan(session) -> None:
    """The S18-late mirror of the tree AC, on the check-in side.

    Edit a protocol, publish, and the call every check-in entry point makes
    (`checkin_store.resolve_bank`) returns the edit — no deploy, no re-seed.
    """
    # Nothing published: the resolver is the seed file, exactly as before S18-late.
    assert await checkin_store.published_bank(session) is None
    floor = await checkin_store.resolve_bank(session)
    assert (
        floor.protocol("platinum").cycle_days
        == protocols.get_bank().protocol("platinum").cycle_days
    )

    raw = _raw_bank()
    raw["protocols"]["platinum"]["checkins"].append(
        {"day_offset": 21, "question_set": "gi_platinum"}
    )
    v = await admin_svc.save_protocol_bank_draft(session, bank_json=raw, notes="added a D+21 rung")

    # A draft changes nothing — publishing is the deliberate second step.
    assert await checkin_store.published_bank(session) is None

    await admin_svc.publish_protocol_bank(session, version=v.version)
    live = await checkin_store.resolve_bank(session)
    assert [rung.day_offset for rung in live.protocol("platinum").checkins] == [2, 7, 14, 21]


async def test_a_protocol_bank_that_could_not_grade_is_refused(session) -> None:
    """Every whole-document check the validator makes still applies to an edit."""
    orphan = _raw_bank()
    orphan["question_sets"]["never_asked"] = orphan["question_sets"]["gi_platinum"]
    with pytest.raises(admin_svc.AdminError, match="no protocol uses"):
        await admin_svc.save_protocol_bank_draft(session, bank_json=orphan)

    ungraded = _raw_bank()
    ungraded["question_sets"]["gi_platinum"]["grading"] = []
    with pytest.raises(admin_svc.AdminError, match="grading"):
        await admin_svc.save_protocol_bank_draft(session, bank_json=ungraded)

    tied = _raw_bank()
    tied["protocols"]["platinum"]["precedence"] = tied["protocols"]["taxane"]["precedence"]
    with pytest.raises(admin_svc.AdminError, match="precedence"):
        await admin_svc.save_protocol_bank_draft(session, bank_json=tied)

    # A green rule is still a load error — green is the absence of a fired rule.
    greened = _raw_bank()
    greened["question_sets"]["gi_platinum"]["grading"][0]["grade"] = "green"
    with pytest.raises(admin_svc.AdminError, match="red' or 'amber"):
        await admin_svc.save_protocol_bank_draft(session, bank_json=greened)

    # None of the four wrote a row.
    assert await admin_svc.list_protocol_banks(session) == []


async def test_saving_a_bank_draft_versions_it_and_audits(session) -> None:
    raw = _raw_bank()
    raw["version"] = 999  # the body does not get to choose

    first = await admin_svc.save_protocol_bank_draft(session, bank_json=raw, notes="one")
    second = await admin_svc.save_protocol_bank_draft(session, bank_json=raw, notes="two")
    assert (first.version, second.version) == (1, 2)
    assert first.status == "draft"

    # The document's own version is rewritten to match the row it lives in, so
    # `parse()` and the table can never disagree about which bank this is.
    assert (await admin_svc.get_protocol_bank(session, 2))["version"] == 2

    rows = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.entity == "protocol_banks", AuditLog.action == "create")
    )
    assert rows == 2


async def test_publishing_an_older_bank_version_rolls_back(session) -> None:
    raw = _raw_bank()
    v1 = await admin_svc.save_protocol_bank_draft(session, bank_json=raw)

    edited = _raw_bank()
    edited["protocols"]["platinum"]["cycle_days"] = 28
    v2 = await admin_svc.save_protocol_bank_draft(session, bank_json=edited)

    await admin_svc.publish_protocol_bank(session, version=v2.version)
    assert (await checkin_store.resolve_bank(session)).protocol("platinum").cycle_days == 28

    await admin_svc.publish_protocol_bank(session, version=v1.version)
    live = await checkin_store.resolve_bank(session)
    assert live.protocol("platinum").cycle_days == raw["protocols"]["platinum"]["cycle_days"]

    published = [b for b in await admin_svc.list_protocol_banks(session) if b.status == "published"]
    assert [b.version for b in published] == [v1.version]


async def test_an_unparseable_published_bank_falls_through_to_the_file(session) -> None:
    """A bad publish must not be the reason a signed note drafts no follow-up."""
    from app.models.content import ProtocolBankVersion

    session.add(
        ProtocolBankVersion(
            version=1,
            bank={"version": 1, "protocols": {}, "question_sets": {}, "option_sets": {}},
            status=ContentStatus.PUBLISHED,
        )
    )
    await session.flush()

    assert await checkin_store.published_bank(session) is None
    assert (await checkin_store.resolve_bank(session)).protocols  # the seed file


# -- the channel document (S-GL.1) --------------------------------------------


def _channel_doc(**enabled: bool) -> dict:
    on = {"kiosk": True, "phone": True, "whatsapp": True, "app": True, **enabled}
    return {
        "channels": {
            name: {"ladder": ["v2", "v3"], "enabled": value} for name, value in on.items()
        },
        "admission": {"max_oss_sessions": 12},
    }


async def test_publishing_a_channel_document_changes_what_is_open(session) -> None:
    """The S-GL.1 headline: a switch thrown in the console is live on the next
    intake, with no deploy — the property S18-early gave the trees."""
    from app import channels as channel_svc
    from app.models.enums import Channel

    assert (await channel_svc.resolve_config(session)).is_enabled(Channel.WHATSAPP)

    v = await admin_svc.save_channel_config_draft(session, config=_channel_doc(whatsapp=False))
    # A draft alone changes nothing — publishing is the deliberate second step.
    assert (await channel_svc.resolve_config(session)).is_enabled(Channel.WHATSAPP)

    await admin_svc.publish_channel_config(session, version=v.version)
    assert not (await channel_svc.resolve_config(session)).is_enabled(Channel.WHATSAPP)


async def test_an_invalid_channel_document_is_refused_at_draft(session) -> None:
    with pytest.raises(admin_svc.AdminError, match="invalid channel document"):
        await admin_svc.save_channel_config_draft(
            session, config={"channels": {"kiosk": {"ladder": ["v9"]}}}
        )


async def test_publishing_a_channel_document_names_what_it_closed(session) -> None:
    """ "Who turned WhatsApp off on Tuesday" is the question asked afterwards, and
    a version number alone does not answer it."""
    v = await admin_svc.save_channel_config_draft(
        session, config=_channel_doc(whatsapp=False, phone=False)
    )
    await admin_svc.publish_channel_config(session, version=v.version)

    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.entity == "channel_configs")))
        .scalars()
        .all()
    )
    published = [r for r in rows if r.meta.get("status") == "published"]
    assert len(published) == 1
    assert published[0].meta["closed"] == ["phone", "whatsapp"]
    assert published[0].meta["open"] == ["app", "kiosk"]


async def test_rolling_back_to_an_earlier_channel_document(session) -> None:
    from app import channels as channel_svc
    from app.models.enums import Channel

    v1 = await admin_svc.save_channel_config_draft(session, config=_channel_doc())
    v2 = await admin_svc.save_channel_config_draft(session, config=_channel_doc(kiosk=False))

    await admin_svc.publish_channel_config(session, version=v2.version)
    assert not (await channel_svc.resolve_config(session)).is_enabled(Channel.KIOSK)

    await admin_svc.publish_channel_config(session, version=v1.version)
    assert (await channel_svc.resolve_config(session)).is_enabled(Channel.KIOSK)

    published = [
        c for c in await admin_svc.list_channel_configs(session) if c.status == "published"
    ]
    assert [c.version for c in published] == [v1.version]


async def test_the_editor_opens_on_the_file_when_nothing_is_stored(session) -> None:
    """Otherwise the first save silently discards config/tiers.yaml's content."""
    from app.tiers import get_tier_config

    assert await admin_svc.get_channel_config(session) == get_tier_config().to_json()
