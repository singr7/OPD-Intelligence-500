"""Offline token blocks and downtime reconciliation (S7, doc 01 §5).

> "If the server is unreachable, the kiosk keeps issuing tokens from a
> **pre-allocated offline token block** (e.g., server pre-assigns block 500–599
> per kiosk daily) and stores intakes locally. Everything syncs automatically on
> reconnect; conflicts resolve by timestamp, **tokens never collide because
> blocks are pre-allocated**." — doc 01 §5

Two things live here: leasing a block (before the outage — that is the point),
and taking the intakes back afterwards.

## Why the number line is split, rather than checked

The temptation is to let the kiosk pick `max(token_no) + 1` offline and sort out
duplicates at sync. That cannot work: during an outage the kiosk *cannot see*
`max(token_no)`, and two kiosks with the same stale view both issue 41. The
patients are already holding the slips by the time the server finds out, and a
reconciliation that renumbers them is a person being called for a token they are
not holding.

So the number line is partitioned instead, and the partition is enforced on both
sides at the moment of issue:

    1 .. base-1   `allocate_token` — server only, online only, refuses to reach base
    base ..       `lease_blocks`   — carved into disjoint per-kiosk ranges

A collision is then not "unlikely", it is unrepresentable — no offline number is
reachable by the online allocator and no two blocks overlap. That is worth more
than a tidier number line, because it holds while the API is down and nobody is
watching, which is the only time it matters.

## Ranges are sequential across the whole day, not per department

`OfflineTokenBlock` (S2) is keyed `uq(kiosk_id, date, start_no)`, so one kiosk
cannot hold two blocks starting at the same number on the same day — which rules
out giving every department its own range starting at `base`. Blocks are
therefore drawn from one sequence per day, shared by every kiosk and department:
the first is 500–549, the next 550–599, and so on.

The cost is cosmetic (a 9-department kiosk reaches ~950 on day one, and the token
numerals are the biggest thing on the board). The benefit is that ranges are
disjoint by construction rather than by an argument about department scoping, and
`used_up_to` alone reconstructs what an unreachable kiosk has issued.

## What sync does and does not decide

Sync replays a finished offline intake into the same rows an online one produces
(`Patient` + `Visit` + `Intake`), so the doctor screen (S9) cannot tell them
apart — a downtime intake is not a second-class record.

It does **not** trust the kiosk about anything clinical. The kiosk sends its
answers; the red flags are recomputed here from the tree by the same rules that
run online. The kiosk's own flag list is never read. The offline TS evaluator is
proven to agree (see `app/tree_fixtures.py`), and this is what makes that a
belt-and-braces claim rather than the only line of defence: if the port ever did
drift, the server's answer is the one on the record.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.clinical import Intake, Visit
from app.models.enums import Channel, IntakeTier, Lang, VisitStatus
from app.models.org import Department
from app.models.patient import Patient
from app.models.scheduling import OfflineTokenBlock
from app.trees import bank
from app.trees.schema import TreeError
from app.trees.walker import Walk

logger = logging.getLogger(__name__)

#: An offline intake is V3 by definition — there is no model to reach. Same value
#: as `app.kiosk.KIOSK_TIER`, restated rather than imported so this module does
#: not depend on the online channel service.
KIOSK_TIER = IntakeTier.PRERECORDED


class OfflineError(Exception):
    """A block or sync request that cannot be honoured."""


@dataclass(slots=True)
class Block:
    """One leased range, as the kiosk sees it."""

    department_key: str
    department_name: str
    start_no: int
    end_no: int
    used_up_to: int | None

    @property
    def next_free(self) -> int:
        return self.start_no if self.used_up_to is None else self.used_up_to + 1

    @property
    def exhausted(self) -> bool:
        return self.next_free > self.end_no


async def _next_start_no(session: AsyncSession, on: date_type) -> int:
    """The first free number at or above the base, for this day.

    One sequence per day across every kiosk and department (see the module
    docstring), so the ranges cannot overlap each other.
    """
    settings = get_settings()
    highest = await session.scalar(
        select(func.max(OfflineTokenBlock.end_no)).where(OfflineTokenBlock.date == on)
    )
    if highest is None:
        return settings.kiosk_offline_token_base
    return max(int(highest) + 1, settings.kiosk_offline_token_base)


async def lease_blocks(
    session: AsyncSession,
    *,
    kiosk_id: str,
    on: date_type | None = None,
    department_keys: list[str] | None = None,
) -> list[Block]:
    """Give `kiosk_id` a block per department for `on`, creating what is missing.

    Idempotent: a kiosk that re-leases (a reboot, a second tab, an hourly refresh)
    gets the blocks it already holds back, with `used_up_to` as the server last
    heard it. It must not be handed a fresh range — the old one is in patients'
    hands, and the kiosk's own IndexedDB is the authority on how far into it we
    are until it syncs.
    """
    on = on or today()
    result = await session.execute(
        select(Department).where(Department.active.is_(True)).order_by(Department.code)
    )
    departments = list(result.scalars().all())
    if department_keys is not None:
        wanted = set(department_keys)
        departments = [d for d in departments if d.code in wanted]
        missing = wanted - {d.code for d in departments}
        if missing:
            raise OfflineError(f"unknown department(s): {sorted(missing)}")
    if not departments:
        raise OfflineError("no active departments to lease blocks for")

    existing = await _blocks_for(session, kiosk_id=kiosk_id, on=on)
    by_dept = {block.department_id: block for block in existing}

    blocks: list[Block] = []
    for department in departments:
        row = by_dept.get(department.id)
        if row is None:
            row = await _create_block(session, kiosk_id=kiosk_id, department=department, on=on)
        blocks.append(
            Block(
                department_key=department.code,
                department_name=department.name,
                start_no=row.start_no,
                end_no=row.end_no,
                used_up_to=row.used_up_to,
            )
        )
    return blocks


async def _create_block(
    session: AsyncSession,
    *,
    kiosk_id: str,
    department: Department,
    on: date_type,
) -> OfflineTokenBlock:
    settings = get_settings()
    for _ in range(5):
        start = await _next_start_no(session, on)
        block = OfflineTokenBlock(
            kiosk_id=kiosk_id,
            department_id=department.id,
            date=on,
            start_no=start,
            end_no=start + settings.kiosk_offline_block_size - 1,
            used_up_to=None,
        )
        try:
            # A savepoint: two kiosks leasing at once must not tear down each
            # other's transaction, only retry for the next free range.
            async with session.begin_nested():
                session.add(block)
                await session.flush()
            return block
        except IntegrityError:
            session.expunge(block)
    raise OfflineError("could not lease a token block — too many concurrent kiosks")


async def _blocks_for(
    session: AsyncSession, *, kiosk_id: str, on: date_type
) -> list[OfflineTokenBlock]:
    result = await session.execute(
        select(OfflineTokenBlock).where(
            OfflineTokenBlock.kiosk_id == kiosk_id,
            OfflineTokenBlock.date == on,
        )
    )
    return list(result.scalars().all())


def today() -> date_type:
    return datetime.now(UTC).date()


# -- sync ---------------------------------------------------------------------


@dataclass(slots=True)
class SyncResult:
    """What became of one offline intake."""

    client_id: str
    status: str  # "synced" | "duplicate" | "rejected"
    intake_id: uuid.UUID | None = None
    token_no: int | None = None
    red_flags: list[dict[str, Any]] | None = None
    error: str | None = None


async def sync_intake(
    session: AsyncSession,
    *,
    kiosk_id: str,
    client_id: str,
    department_key: str,
    tree_key: str,
    lang: Lang,
    token_no: int,
    answers: dict[str, Any],
    chief_complaint: str | None = None,
    caregiver: bool = False,
    completed_at: datetime | None = None,
) -> SyncResult:
    """Replay one offline intake onto the server.

    `client_id` is the kiosk's own id for the intake and the idempotency key: a
    sync that half-succeeded and is retried (the usual case — the network came
    back mid-batch) must not produce a second visit for one patient. Re-syncing a
    known `client_id` is a no-op that reports the token already issued.
    """
    department = await session.scalar(select(Department).where(Department.code == department_key))
    if department is None:
        return SyncResult(client_id, "rejected", error=f"unknown department {department_key!r}")

    existing = await _find_synced(session, client_id=client_id)
    if existing is not None:
        visit = await session.get(Visit, existing.visit_id)
        return SyncResult(
            client_id,
            "duplicate",
            intake_id=existing.id,
            token_no=visit.token_no if visit else None,
            red_flags=list(existing.red_flags or []),
        )

    block = await _block_covering(
        session, kiosk_id=kiosk_id, department_id=department.id, token_no=token_no
    )
    if block is None:
        # The kiosk issued a number outside any block it holds. Refuse it: the
        # number may already belong to the server's online range or to another
        # kiosk, and honouring it is how two patients end up with one token.
        return SyncResult(
            client_id,
            "rejected",
            error=(
                f"token {token_no} is not inside a block leased to kiosk {kiosk_id!r} "
                f"for {department_key}"
            ),
        )

    try:
        tree = bank.get(tree_key)
    except TreeError as exc:
        # A kiosk cached a tree that has since been renamed or removed. Reject the
        # intake rather than guessing a tree for answers that were given to a
        # different set of questions.
        return SyncResult(client_id, "rejected", error=str(exc))

    # The kiosk's answers, the server's rules. `Walk.from_json` drops answers to
    # nodes the tree no longer has and prunes anything off the live branch, so a
    # kiosk running a stale tree cannot smuggle in an answer nobody was asked.
    walk = Walk.from_json(tree, answers)
    red_flags = [hit.to_json() for hit in walk.red_flags()]

    patient = Patient(
        hospital_id=department.hospital_id,
        mrn=f"WALKIN-{uuid.uuid4().hex[:10].upper()}",
        name="Walk-in patient",
        phone="",
        lang=lang,
        caregiver_name="(caregiver at kiosk)" if caregiver else None,
    )
    session.add(patient)
    await session.flush()

    visit = Visit(
        patient_id=patient.id,
        department_id=department.id,
        date=block.date,
        status=VisitStatus.REGISTERED,
        channel=Channel.KIOSK,
        token_no=token_no,
    )
    session.add(visit)

    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        # The unique constraint caught a number this block should have owned
        # exclusively. That is a real bug in the partition, not a routine race —
        # say so rather than renumbering a patient who is holding the slip.
        logger.exception(
            "offline token %s collided for department %s despite block %s-%s on kiosk %s",
            token_no,
            department_key,
            block.start_no,
            block.end_no,
            kiosk_id,
        )
        return SyncResult(
            client_id,
            "rejected",
            error=f"token {token_no} is already issued for {department_key}",
        )

    intake = Intake(
        visit_id=visit.id,
        tier=KIOSK_TIER,
        lang=lang,
        answers=walk.to_json(),
        red_flags=red_flags,
        chief_complaint=chief_complaint,
        tree_ref=tree.ref,
        client_id=client_id,
        confirmed_by_patient=True,
        # The patient finished and confirmed on the kiosk during the outage; the
        # clock that matters is theirs, not the moment the network returned.
        completed_at=completed_at or datetime.now(UTC),
    )
    session.add(intake)
    await session.flush()

    await _mark_used(session, block, token_no)

    return SyncResult(
        client_id,
        "synced",
        intake_id=intake.id,
        token_no=token_no,
        red_flags=red_flags,
    )


async def _block_covering(
    session: AsyncSession, *, kiosk_id: str, department_id: uuid.UUID, token_no: int
) -> OfflineTokenBlock | None:
    result = await session.execute(
        select(OfflineTokenBlock).where(
            OfflineTokenBlock.kiosk_id == kiosk_id,
            OfflineTokenBlock.department_id == department_id,
            OfflineTokenBlock.start_no <= token_no,
            OfflineTokenBlock.end_no >= token_no,
        )
    )
    return result.scalars().first()


async def _mark_used(session: AsyncSession, block: OfflineTokenBlock, token_no: int) -> None:
    """Advance the block's watermark, never backwards.

    Syncs arrive out of order (the kiosk retries a failed one after later ones
    succeeded), so `used_up_to` is a high-water mark rather than a counter.
    """
    if block.used_up_to is None or token_no > block.used_up_to:
        block.used_up_to = token_no
        await session.flush()


async def _find_synced(session: AsyncSession, *, client_id: str) -> Intake | None:
    return await session.scalar(select(Intake).where(Intake.client_id == client_id))
