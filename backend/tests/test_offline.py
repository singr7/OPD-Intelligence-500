"""Offline token blocks and downtime sync (S7, doc 01 §5).

The AC these serve is a demo script: *kill the API for 10 minutes → the kiosk
completes three offline intakes with valid tokens → restart → all sync, zero
collisions*. The interesting half is "zero collisions", so most of what is below
tries to **cause** one rather than confirm the happy path:

- the online allocator reaching into the offline range,
- an offline number the server also issues online,
- two kiosks whose blocks overlap,
- a synced offline token dragging the online sequence up behind it,
- a kiosk claiming a token from a block it does not hold,
- the same intake synced twice (the ordinary case — the network returns mid-batch).

The other thing pinned here: sync does not trust the kiosk about anything
clinical. It sends answers; the server recomputes the red flags.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import kiosk as kiosk_svc
from app import offline as offline_svc
from app.config import Settings
from app.models.clinical import Intake, Visit
from app.models.enums import Channel, Lang, VisitStatus
from app.models.org import Department, Hospital
from app.models.patient import Patient
from app.models.scheduling import OfflineTokenBlock
from app.trees import bank
from app.trees.walker import Walk
from tests import factories as f

pytestmark = pytest.mark.asyncio


async def _seed_departments(session: AsyncSession) -> Hospital:
    hospital = f.make_hospital()
    session.add(hospital)
    await session.flush()
    for code, name in [("MEDONC", "Medical Oncology"), ("DERM", "Dermatology")]:
        session.add(f.make_department(hospital, code=code, name=name))
    await session.flush()
    return hospital


async def _department(session: AsyncSession, code: str) -> Department:
    return await session.scalar(select(Department).where(Department.code == code))


def _tree_key(dept: str = "MEDONC") -> str:
    tree = kiosk_svc.select_tree(dept)
    assert tree is not None
    return tree.key


def _answers_for(tree_key: str, *, lang: Lang = Lang.HI) -> dict[str, Any]:
    """A complete offline walk's answers, produced by the real walker — the same
    shape the TS walker writes into IndexedDB."""
    tree = bank.get(tree_key)
    assert tree is not None
    walk = Walk(tree)
    while (node := walk.current) is not None:
        if node.type.value == "single":
            value: Any = node.options[0].id
        elif node.type.value in ("multi", "body_map"):
            value = [node.options[0].id]
        elif node.type.value in ("scale", "number"):
            value = node.min if node.min is not None else 1
        else:
            value = "mujhe pet mein dard hai"
        walk.save(node.id, value, lang=lang)
    return walk.to_json()


# -- leasing -------------------------------------------------------------------


async def test_a_kiosk_leases_one_block_per_department(
    session: AsyncSession, settings: Settings
) -> None:
    await _seed_departments(session)

    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")

    assert {b.department_key for b in blocks} == {"MEDONC", "DERM"}
    for block in blocks:
        # Never below the base: everything under it belongs to the online
        # allocator, which cannot see this block.
        assert block.start_no >= settings.kiosk_offline_token_base
        assert block.end_no - block.start_no + 1 == settings.kiosk_offline_block_size
        assert block.next_free == block.start_no
        assert not block.exhausted


async def test_leasing_is_idempotent_because_the_old_block_is_on_paper_slips(
    session: AsyncSession,
) -> None:
    """A kiosk reboot must not hand out a fresh range — patients hold the old one."""
    await _seed_departments(session)

    first = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    again = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")

    assert [(b.department_key, b.start_no, b.end_no) for b in first] == [
        (b.department_key, b.start_no, b.end_no) for b in again
    ]
    rows = (await session.execute(select(OfflineTokenBlock))).scalars().all()
    assert len(rows) == 2  # not four


async def test_two_kiosks_never_share_a_number(session: AsyncSession) -> None:
    await _seed_departments(session)

    a = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    b = await offline_svc.lease_blocks(session, kiosk_id="kiosk-b")

    ranges = [(block.start_no, block.end_no) for block in [*a, *b]]
    covered: set[int] = set()
    for start, end in ranges:
        numbers = set(range(start, end + 1))
        assert not (numbers & covered), f"block {start}-{end} overlaps an earlier block"
        covered |= numbers


async def test_every_leased_number_is_out_of_the_online_allocators_reach(
    session: AsyncSession, settings: Settings
) -> None:
    """The partition, stated as one property: no leased number is one the online
    allocator can ever produce."""
    await _seed_departments(session)
    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")

    for block in blocks:
        assert block.start_no >= settings.kiosk_offline_token_base
        assert block.end_no >= settings.kiosk_offline_token_base


# -- the online allocator stays on its side ------------------------------------


async def test_the_online_allocator_refuses_to_enter_the_offline_range(
    session: AsyncSession, settings: Settings
) -> None:
    hospital = await _seed_departments(session)
    department = await _department(session, "MEDONC")
    base = settings.kiosk_offline_token_base

    # A day that has already run right up to the boundary.
    patient = f.make_patient(hospital)
    session.add(patient)
    await session.flush()
    visit = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=offline_svc.today(),
        status=VisitStatus.REGISTERED,
        channel=Channel.KIOSK,
        token_no=base - 1,
    )
    session.add(visit)
    await session.flush()

    nxt = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=offline_svc.today(),
        status=VisitStatus.REGISTERED,
        channel=Channel.KIOSK,
    )
    session.add(nxt)
    await session.flush()

    # Refusing loudly is the point: wrapping into the offline blocks would hand a
    # second patient a number a kiosk is already giving out.
    with pytest.raises(kiosk_svc.KioskError, match="online token range is exhausted"):
        await kiosk_svc.allocate_token(session, nxt)


async def test_a_synced_offline_token_does_not_drag_the_online_sequence_up(
    session: AsyncSession, settings: Settings
) -> None:
    """The regression the S6 allocator would have had: `max(token_no) + 1` over
    *all* visits sees the offline 500 and issues 501 — inside another kiosk's
    block."""
    hospital = await _seed_departments(session)
    department = await _department(session, "MEDONC")
    patient = f.make_patient(hospital)
    session.add(patient)
    await session.flush()

    # An offline intake has synced at the base.
    session.add(
        Visit(
            patient_id=patient.id,
            department_id=department.id,
            date=offline_svc.today(),
            status=VisitStatus.REGISTERED,
            channel=Channel.KIOSK,
            token_no=settings.kiosk_offline_token_base,
        )
    )
    await session.flush()

    fresh = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=offline_svc.today(),
        status=VisitStatus.REGISTERED,
        channel=Channel.KIOSK,
    )
    session.add(fresh)
    await session.flush()

    token = await kiosk_svc.allocate_token(session, fresh)

    assert token == 1, "the online sequence must ignore tokens in the offline range"


# -- sync ----------------------------------------------------------------------


async def test_an_offline_intake_syncs_into_the_same_rows_an_online_one_makes(
    session: AsyncSession,
) -> None:
    await _seed_departments(session)
    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    block = next(b for b in blocks if b.department_key == "MEDONC")
    tree_key = _tree_key()

    result = await offline_svc.sync_intake(
        session,
        kiosk_id="kiosk-a",
        client_id="c-0000000000000001",
        department_key="MEDONC",
        tree_key=tree_key,
        lang=Lang.HI,
        token_no=block.start_no,
        answers=_answers_for(tree_key),
        chief_complaint="pet mein dard",
        completed_at=datetime(2026, 7, 17, 9, 30, tzinfo=UTC),
    )

    assert result.status == "synced"
    assert result.token_no == block.start_no

    intake = await session.get(Intake, result.intake_id)
    assert intake is not None
    assert intake.client_id == "c-0000000000000001"
    assert intake.tree_ref.startswith(tree_key)
    assert intake.confirmed_by_patient
    # The patient's clock, not the moment the network returned.
    assert intake.completed_at == datetime(2026, 7, 17, 9, 30, tzinfo=UTC)

    visit = await session.get(Visit, intake.visit_id)
    assert visit.token_no == block.start_no
    assert visit.channel is Channel.KIOSK
    # A downtime intake is a first-class record, not an annotation.
    patient = await session.get(Patient, visit.patient_id)
    assert patient is not None


async def test_syncing_the_same_intake_twice_does_not_make_a_second_patient(
    session: AsyncSession,
) -> None:
    """The ordinary case: the network returns mid-batch and the kiosk retries."""
    await _seed_departments(session)
    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    block = next(b for b in blocks if b.department_key == "MEDONC")
    tree_key = _tree_key()
    payload = dict(
        kiosk_id="kiosk-a",
        client_id="c-0000000000000002",
        department_key="MEDONC",
        tree_key=tree_key,
        lang=Lang.HI,
        token_no=block.start_no,
        answers=_answers_for(tree_key),
    )

    first = await offline_svc.sync_intake(session, **payload)
    second = await offline_svc.sync_intake(session, **payload)

    assert first.status == "synced"
    assert second.status == "duplicate"
    assert second.intake_id == first.intake_id
    assert second.token_no == first.token_no

    visits = (await session.execute(select(Visit))).scalars().all()
    assert len(visits) == 1


async def test_a_token_outside_the_kiosks_block_is_refused(session: AsyncSession) -> None:
    """A kiosk may only claim numbers it was actually leased — anything else may
    belong to the online range or to another kiosk."""
    await _seed_departments(session)
    await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    tree_key = _tree_key()

    result = await offline_svc.sync_intake(
        session,
        kiosk_id="kiosk-a",
        client_id="c-0000000000000003",
        department_key="MEDONC",
        tree_key=tree_key,
        lang=Lang.HI,
        token_no=7,  # an online number
        answers=_answers_for(tree_key),
    )

    assert result.status == "rejected"
    assert "not inside a block" in result.error
    assert (await session.execute(select(Visit))).scalars().first() is None


async def test_a_kiosk_cannot_claim_another_kiosks_block(session: AsyncSession) -> None:
    await _seed_departments(session)
    await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    b_blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-b")
    b_medonc = next(b for b in b_blocks if b.department_key == "MEDONC")
    tree_key = _tree_key()

    result = await offline_svc.sync_intake(
        session,
        kiosk_id="kiosk-a",
        client_id="c-0000000000000004",
        department_key="MEDONC",
        tree_key=tree_key,
        lang=Lang.HI,
        token_no=b_medonc.start_no,  # kiosk-b's number
        answers=_answers_for(tree_key),
    )

    assert result.status == "rejected"


async def test_sync_recomputes_red_flags_and_ignores_what_the_kiosk_claims(
    session: AsyncSession,
) -> None:
    """The kiosk sends answers, not verdicts. Nothing it says about red flags is
    read — the server re-walks the tree with the same rules that run online."""
    await _seed_departments(session)
    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    block = next(b for b in blocks if b.department_key == "MEDONC")
    tree_key = _tree_key()
    answers = _answers_for(tree_key)

    result = await offline_svc.sync_intake(
        session,
        kiosk_id="kiosk-a",
        client_id="c-0000000000000005",
        department_key="MEDONC",
        tree_key=tree_key,
        lang=Lang.HI,
        token_no=block.start_no,
        answers=answers,
    )

    intake = await session.get(Intake, result.intake_id)
    tree = bank.get(tree_key)
    expected = [hit.to_json() for hit in Walk.from_json(tree, answers).red_flags()]
    assert intake.red_flags == expected


async def test_sync_drops_answers_to_nodes_the_tree_does_not_have(
    session: AsyncSession,
) -> None:
    """A kiosk running a stale cached tree cannot smuggle in an answer to a
    question this tree never asks."""
    await _seed_departments(session)
    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    block = next(b for b in blocks if b.department_key == "MEDONC")
    tree_key = _tree_key()
    answers = _answers_for(tree_key)
    answers["ghost.node.from.an.old.tree"] = {
        "value": "boo",
        "text": None,
        "text_en": None,
        "lang": "hi",
        "at": "2026-07-17T09:00:00+00:00",
    }

    result = await offline_svc.sync_intake(
        session,
        kiosk_id="kiosk-a",
        client_id="c-0000000000000006",
        department_key="MEDONC",
        tree_key=tree_key,
        lang=Lang.HI,
        token_no=block.start_no,
        answers=answers,
    )

    intake = await session.get(Intake, result.intake_id)
    assert "ghost.node.from.an.old.tree" not in intake.answers


async def test_an_unknown_tree_or_department_is_rejected_not_guessed(
    session: AsyncSession,
) -> None:
    await _seed_departments(session)
    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    block = next(b for b in blocks if b.department_key == "MEDONC")
    tree_key = _tree_key()

    bad_dept = await offline_svc.sync_intake(
        session,
        kiosk_id="kiosk-a",
        client_id="c-0000000000000007",
        department_key="NOSUCH",
        tree_key=tree_key,
        lang=Lang.HI,
        token_no=block.start_no,
        answers=_answers_for(tree_key),
    )
    assert bad_dept.status == "rejected"

    bad_tree = await offline_svc.sync_intake(
        session,
        kiosk_id="kiosk-a",
        client_id="c-0000000000000008",
        department_key="MEDONC",
        tree_key="no_such_tree",
        lang=Lang.HI,
        token_no=block.start_no,
        answers={},
    )
    assert bad_tree.status == "rejected"


# -- the routes ----------------------------------------------------------------


async def test_the_lease_route_hands_the_kiosk_its_blocks(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_departments(session)

    resp = await client.post("/kiosk/blocks/lease", params={"kiosk_id": "kiosk-a"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kiosk_id"] == "kiosk-a"
    assert {b["department"]["key"] for b in body["blocks"]} == {"MEDONC", "DERM"}
    for block in body["blocks"]:
        assert block["next_free"] == block["start_no"]


async def test_the_demo_script_three_offline_intakes_sync_with_zero_collisions(
    client: AsyncClient, session: AsyncSession
) -> None:
    """The S7 AC, end to end at the service boundary.

    The kiosk leased its blocks, the API went away, three patients completed an
    intake, and the API came back. Every token must survive, be distinct, and be
    the number the patient is holding.
    """
    await _seed_departments(session)
    lease = await client.post("/kiosk/blocks/lease", params={"kiosk_id": "kiosk-a"})
    block = next(b for b in lease.json()["blocks"] if b["department"]["key"] == "MEDONC")
    tree_key = _tree_key()

    # ... the API is unreachable here; the kiosk issues from its block ...
    offline_tokens = [block["start_no"], block["start_no"] + 1, block["start_no"] + 2]

    resp = await client.post(
        "/kiosk/sync",
        json={
            "kiosk_id": "kiosk-a",
            "intakes": [
                {
                    "client_id": f"c-offline-{index:012d}",
                    "department_key": "MEDONC",
                    "tree_key": tree_key,
                    "lang": "hi",
                    "token_no": token,
                    "answers": _answers_for(tree_key),
                    "chief_complaint": "pet mein dard",
                }
                for index, token in enumerate(offline_tokens)
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["synced"] == 3
    assert body["rejected"] == 0

    visits = (await session.execute(select(Visit))).scalars().all()
    tokens = sorted(v.token_no for v in visits)
    assert tokens == offline_tokens, "every offline patient keeps the number they hold"
    assert len(set(tokens)) == 3, "zero collisions"

    # And the block's watermark now reflects what the kiosk actually issued, so a
    # kiosk that reboots after syncing resumes past it rather than re-issuing.
    row = await session.scalar(
        select(OfflineTokenBlock).where(
            OfflineTokenBlock.kiosk_id == "kiosk-a",
            OfflineTokenBlock.start_no == block["start_no"],
        )
    )
    assert row.used_up_to == offline_tokens[-1]


async def test_a_bad_intake_in_a_batch_does_not_strand_the_good_ones(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Per-intake results: one rejection must not cost the kiosk the others."""
    await _seed_departments(session)
    lease = await client.post("/kiosk/blocks/lease", params={"kiosk_id": "kiosk-a"})
    block = next(b for b in lease.json()["blocks"] if b["department"]["key"] == "MEDONC")
    tree_key = _tree_key()

    resp = await client.post(
        "/kiosk/sync",
        json={
            "kiosk_id": "kiosk-a",
            "intakes": [
                {
                    "client_id": "c-good-000000000001",
                    "department_key": "MEDONC",
                    "tree_key": tree_key,
                    "lang": "hi",
                    "token_no": block["start_no"],
                    "answers": _answers_for(tree_key),
                },
                {
                    "client_id": "c-bad-0000000000001",
                    "department_key": "MEDONC",
                    "tree_key": tree_key,
                    "lang": "hi",
                    "token_no": 3,  # not in any block this kiosk holds
                    "answers": _answers_for(tree_key),
                },
                {
                    "client_id": "c-good-000000000002",
                    "department_key": "MEDONC",
                    "tree_key": tree_key,
                    "lang": "hi",
                    "token_no": block["start_no"] + 1,
                    "answers": _answers_for(tree_key),
                },
            ],
        },
    )

    body = resp.json()
    assert body["synced"] == 2
    assert body["rejected"] == 1
    by_id = {r["client_id"]: r for r in body["results"]}
    assert by_id["c-bad-0000000000001"]["error"]
    assert by_id["c-good-000000000002"]["status"] == "synced"


async def test_a_used_up_to_watermark_only_moves_forward(session: AsyncSession) -> None:
    """Syncs arrive out of order when the kiosk retries a failed one, so the
    watermark is a high-water mark, not a counter."""
    await _seed_departments(session)
    blocks = await offline_svc.lease_blocks(session, kiosk_id="kiosk-a")
    block = next(b for b in blocks if b.department_key == "MEDONC")
    tree_key = _tree_key()

    for index, token in enumerate([block.start_no + 5, block.start_no]):
        await offline_svc.sync_intake(
            session,
            kiosk_id="kiosk-a",
            client_id=f"c-order-{index:011d}",
            department_key="MEDONC",
            tree_key=tree_key,
            lang=Lang.HI,
            token_no=token,
            answers=_answers_for(tree_key),
        )

    row = await session.scalar(
        select(OfflineTokenBlock).where(
            OfflineTokenBlock.kiosk_id == "kiosk-a",
            OfflineTokenBlock.start_no == block.start_no,
        )
    )
    assert row.used_up_to == block.start_no + 5


# -- the bundle ----------------------------------------------------------------


async def test_the_bundle_carries_canonical_trees_the_kiosk_can_walk(
    client: AsyncClient, session: AsyncSession
) -> None:
    """What the kiosk caches before the outage: every tree, already validated and
    desugared, plus the chooser it needs when there is no classifier."""
    await _seed_departments(session)

    resp = await client.get("/kiosk/bundle")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {d["key"] for d in body["departments"]} == {"MEDONC", "DERM"}
    assert len(body["trees"]) == 11

    tree = next(t["tree"] for t in body["trees"] if t["tree"]["key"] == _tree_key())
    # The canonical shape the TS walker expects: nodes as a list, flags desugared
    # out of the options, every rule already type-checked by parse().
    assert isinstance(tree["nodes"], list)
    assert tree["root"]
    assert all("flag" not in option for node in tree["nodes"] for option in node["options"])


async def test_the_bundle_etag_lets_a_kiosk_skip_an_unchanged_download(
    client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_departments(session)

    first = await client.get("/kiosk/bundle")
    again = await client.get("/kiosk/bundle")

    assert first.headers["etag"] == again.headers["etag"]
    assert first.json()["etag"] == again.json()["etag"]
    # A stale tree is a stale clinical question: never served from cache without
    # asking.
    assert first.headers["cache-control"] == "no-cache"


async def test_the_bundle_etag_changes_when_the_content_does(
    client: AsyncClient, session: AsyncSession
) -> None:
    hospital = await _seed_departments(session)
    before = (await client.get("/kiosk/bundle")).json()["etag"]

    session.add(f.make_department(hospital, code="CARDIO", name="Cardiology"))
    await session.flush()

    after = (await client.get("/kiosk/bundle")).json()["etag"]
    assert before != after
