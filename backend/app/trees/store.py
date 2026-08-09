"""Runtime tree source: published rows in `question_trees`, disk bank as floor.

Until S18 the intake path read trees straight off disk (`app.trees.bank`). That
made "edit a tree in the console and see it live on the kiosk without a deploy"
(the S18 headline AC) impossible: the kiosk never looked at the table the editor
writes. This module is the seam that closes it.

`resolve_tree` prefers the **latest published** row for a department and falls
back to the on-disk bank when the table has none — so a fresh database, a test
without a seed, or a department whose tree was never published still gets the
authored content. The publish endpoint (`app/routes/admin.py`) writes a new
published version; the very next intake resolves it. No cache to invalidate: at
pilot scale one indexed query per intake start is cheaper than the class of bug a
stale cache invites, and "live immediately" is the feature.

Selection (which of a department's trees a walk-in gets) is shared with the disk
path via `pick`, so DB-served and file-served trees choose identically.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import QuestionTree
from app.models.enums import TreeStatus
from app.models.org import Department
from app.trees import bank, visibility
from app.trees.schema import Tree, TreeError, parse


def pick(trees: list[Tree]) -> Tree | None:
    """Choose the intake tree for a department from its candidates.

    Most departments have one tree; med-onc has three and a walk-in defaults to
    the new-patient intake (sub-tree disambiguation wants visit history — backlog,
    S9/S18). Routing trees are the next preference, then any. Deterministic:
    sorted by key so the choice never depends on row/file order.
    """
    if not trees:
        return None
    ordered = sorted(trees, key=lambda t: t.key)
    for tree in ordered:
        if tree.key.endswith("_new_patient"):
            return tree
    for tree in ordered:
        if tree.key.endswith("_routing"):
            return tree
    return ordered[0]


async def published_for_department(session: AsyncSession, dept_key: str) -> list[Tree]:
    """Every published tree for a department, latest version per key.

    A key can have several published rows over time (each publish is a new
    version); the newest wins. Rows that no longer parse are skipped rather than
    fatal — a bad publish must not take down intake, and `resolve_tree` will fall
    through to disk if nothing parses.
    """
    stmt = (
        select(QuestionTree)
        .join(Department, Department.id == QuestionTree.department_id)
        .where(
            Department.code == dept_key,
            QuestionTree.status == TreeStatus.PUBLISHED,
            QuestionTree.deleted_at.is_(None),
        )
        .order_by(QuestionTree.key, QuestionTree.version.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    latest: dict[str, Tree] = {}
    for row in rows:
        if row.key in latest:  # a lower version of a key we already took
            continue
        try:
            latest[row.key] = parse(row.tree)
        except TreeError:
            continue
    return list(latest.values())


async def active_department_codes(session: AsyncSession) -> set[str]:
    """Every open department's code — what a tree's offers are checked against."""
    rows = await session.execute(select(Department.code).where(Department.active.is_(True)))
    return set(rows.scalars().all())


async def resolve_tree(session: AsyncSession, dept_key: str) -> Tree | None:
    """The tree a kiosk/telephony walk-in for `dept_key` should run.

    DB-published content wins; the disk bank is the floor. This is the call the
    intake path makes instead of `bank.for_department` so an admin publish is
    live on the next intake.

    It is also where a tree stops offering a department the hospital has closed
    (doc 24 §5, `app.trees.visibility`). Doing it here rather than in each
    channel's renderer is what gives kiosk, WhatsApp and telephony the same
    answer, and what keeps the question out of the offline pack entirely.
    """
    published = await published_for_department(session, dept_key)
    chosen = pick(published) or pick(bank.for_department(dept_key))
    if chosen is None:
        return None
    return visibility.for_active(chosen, await active_department_codes(session))
