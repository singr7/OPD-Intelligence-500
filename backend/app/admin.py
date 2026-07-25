"""Admin-console service (doc 03 §10, S18) — the editable side of authored content.

The HTTP surface (`app/routes/admin.py`) is thin over this; the logic — validate,
version, publish, audit — lives here so it is unit-testable without a request. Two
invariants hold across every function:

- **Nothing publishes without validating.** A tree only reaches `question_trees`
  after `app.trees.schema.parse` accepts it, so "it published" means "the walker
  can run it" (the same guarantee the seed and CI give the authored bank).
- **Every edit is audited.** These tables are content, not `Clinical` records, so
  the `before_flush` hook does not cover them; each mutation writes its own
  `audit_log` row via `record_admin_action`. A publish, a price change and a
  cost-guard clear are exactly the acts an operator must be able to point at.

Scope note (S18-early): this covers the panels whose backing models exist —
trees, red-flag rules (they live *inside* the tree JSON, so the tree editor is
their editor), the price book, and the cost guard. The protocol-template and
slot-template editors are deferred with their models (S17, S15); the routes
return an explicit "deferred" marker rather than a half-built schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_admin_action
from app.models.content import QuestionTree
from app.models.enums import AuditAction, Lang, PriceUnit, TreeStatus
from app.models.metering import PriceBook
from app.models.org import Department
from app.providers.pricing import get_price_book
from app.trees import bank
from app.trees.schema import Tree, TreeError, parse
from app.trees.walker import AnswerError, Walk


class AdminError(Exception):
    """A rejected admin edit — a bad tree, an unknown key, a duplicate price row."""


# -- trees --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TreeVersion:
    id: Any
    key: str
    version: int
    status: str
    department_code: str | None
    published_at: datetime | None
    node_count: int


async def list_trees(session: AsyncSession) -> list[TreeVersion]:
    """Every tree version in the table, newest version first within a key.

    Draft *and* published — the editor needs to see a draft it is working on and
    the published version it will replace side by side.
    """
    rows = (
        await session.execute(
            select(QuestionTree, Department.code)
            .outerjoin(Department, Department.id == QuestionTree.department_id)
            .where(QuestionTree.deleted_at.is_(None))
            .order_by(QuestionTree.key, QuestionTree.version.desc())
        )
    ).all()
    return [_version(row, code) for row, code in rows]


async def get_tree(session: AsyncSession, key: str, version: int | None = None) -> dict[str, Any]:
    """One tree's JSON. `version=None` returns the highest version for the key."""
    row = await _load_row(session, key, version)
    return row.tree


async def save_tree_draft(
    session: AsyncSession, *, key: str, tree_json: dict[str, Any]
) -> TreeVersion:
    """Validate an edited tree and store it as a **new draft version**.

    Never edits a published row in place: a version is immutable content that an
    offline kiosk may have cached and an `Intake.tree_ref` may point at (doc 02
    §4). Editing therefore forks — `version = max(version)+1`, status DRAFT — and
    publishing is the separate, deliberate second step. The key must match the
    body, and the department it names must exist, or a walk-in routes to a desk
    with nobody at it.
    """
    try:
        tree = parse(tree_json)
    except TreeError as exc:
        raise AdminError(f"invalid tree: {exc}") from exc
    if tree.key != key:
        raise AdminError(f"tree body key {tree.key!r} does not match {key!r}")

    department = await _department_for(session, tree)

    next_version = (
        await session.scalar(
            select(func.coalesce(func.max(QuestionTree.version), 0)).where(QuestionTree.key == key)
        )
    ) + 1

    row = QuestionTree(
        key=key,
        version=next_version,
        tree=tree_json,
        department_id=department.id if department else None,
        status=TreeStatus.DRAFT,
    )
    session.add(row)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=QuestionTree.__tablename__,
        entity_id=row.id,
        meta={"key": key, "version": next_version, "status": "draft"},
    )
    return _version(row, department.code if department else None)


async def publish_tree(session: AsyncSession, *, key: str, version: int) -> TreeVersion:
    """Make one version the live one (doc 03 §10; the S18 headline AC).

    Exactly one published version per key: the target becomes PUBLISHED and every
    other version of the key is demoted to DRAFT, so `store.resolve_tree` — which
    the intake path now reads — has one unambiguous answer and a rollback (publish
    an older version) actually takes effect. Publishing is validated again from
    the stored JSON, so a row that somehow became unparseable cannot go live.
    """
    row = await _load_row(session, key, version)
    try:
        parse(row.tree)
    except TreeError as exc:  # pragma: no cover - save_tree_draft already gates this
        raise AdminError(f"refusing to publish an invalid tree: {exc}") from exc

    siblings = (
        (
            await session.execute(
                select(QuestionTree).where(
                    QuestionTree.key == key, QuestionTree.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    for sibling in siblings:
        sibling.status = TreeStatus.DRAFT
        sibling.published_at = None

    row.status = TreeStatus.PUBLISHED
    row.published_at = datetime.now(UTC)
    await session.flush()

    department_code = await session.scalar(
        select(Department.code).where(Department.id == row.department_id)
    )
    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=QuestionTree.__tablename__,
        entity_id=row.id,
        meta={"key": key, "version": version, "status": "published"},
    )
    return _version(row, department_code)


@dataclass(frozen=True, slots=True)
class TestRun:
    """The outcome of walking a tree with a set of answers — the editor's
    'test-run' (doc 03 §10). Deterministic and read-only: it builds no rows."""

    path: list[str]
    complete: bool
    red_flags: list[dict[str, str]]
    error: str | None = None


def test_run(tree_json: dict[str, Any], answers: dict[str, Any]) -> TestRun:
    """Dry-walk a tree with `{node_id: value}` answers, in ask order.

    Runs entirely in memory against the *submitted* JSON (draft or edited, not yet
    saved), so an author sees the branch their answers take and the red flags they
    raise before committing anything. Answering out of order, or a value the node
    rejects, comes back as `error` rather than an exception — the console renders
    it inline.
    """
    try:
        tree = parse(tree_json)
    except TreeError as exc:
        return TestRun(path=[], complete=False, red_flags=[], error=f"invalid tree: {exc}")

    walk = Walk(tree)
    try:
        while (node := walk.current) is not None and node.id in answers:
            walk.save(node.id, answers[node.id])
    except AnswerError as exc:
        return TestRun(
            path=list(walk.path()), complete=walk.is_complete, red_flags=[], error=str(exc)
        )

    flags = [
        {"id": hit.id, "severity": str(hit.severity), "label": hit.name()}
        for hit in walk.red_flags()
    ]
    return TestRun(path=list(walk.path()), complete=walk.is_complete, red_flags=flags)


# -- price book ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceEdit:
    provider: str
    model: str
    unit: PriceUnit
    price_inr: Decimal
    effective_from: date
    notes: str | None = None


async def add_price_row(session: AsyncSession, edit: PriceEdit) -> PriceBook:
    """Add a price-book row and make it take effect immediately.

    A price change is a new *versioned* row (natural key provider·model·unit·
    effective_from), never an in-place edit: history has to re-price at the rate
    that was in force, which is the whole reason `unit_cost_ref` exists. After
    the write the in-memory `PriceBookCache` is invalidated so the next provider
    call and the cost guard price against the new rate without waiting out the
    TTL.
    """
    exists = await session.scalar(
        select(func.count())
        .select_from(PriceBook)
        .where(
            PriceBook.provider == edit.provider,
            PriceBook.model == edit.model,
            PriceBook.unit == edit.unit,
            PriceBook.effective_from == edit.effective_from,
        )
    )
    if exists:
        raise AdminError(
            f"a {edit.provider}/{edit.model} {edit.unit.value} price effective "
            f"{edit.effective_from} already exists — use a later date to supersede it"
        )

    row = PriceBook(
        provider=edit.provider,
        model=edit.model,
        unit=edit.unit,
        price_inr=edit.price_inr,
        effective_from=edit.effective_from,
        notes=edit.notes,
    )
    session.add(row)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=PriceBook.__tablename__,
        entity_id=row.id,
        meta={
            "provider": edit.provider,
            "model": edit.model,
            "unit": edit.unit.value,
            "price_inr": str(edit.price_inr),
            "effective_from": edit.effective_from.isoformat(),
        },
    )
    get_price_book().invalidate()
    return row


# -- voice-pack coverage ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VoicePackClip:
    """One clip a tree expects, and whether a recording exists for it."""

    tree_key: str
    node_id: str
    lang: str
    clip_name: str | None
    recorded: bool


def voice_pack_coverage() -> list[VoicePackClip]:
    """What the V3 voice packs *should* contain, from the authored trees.

    The pilot's pack is empty — every clip falls through to TTS (S7/S21 record the
    real human voice). The manager panel is therefore a coverage view: each node ×
    language that needs audio, and `recorded=False` everywhere until a pack is
    uploaded. A read view now; upload/re-record is S7's pack format landing on the
    box (backlog), so this does not invent a storage layout.
    """
    langs = [lang.value for lang in Lang]
    out: list[VoicePackClip] = []
    for tree in sorted(bank.load_bank().values(), key=lambda t: t.key):
        for node in tree.nodes.values():
            for lang in langs:
                out.append(
                    VoicePackClip(
                        tree_key=tree.key,
                        node_id=node.id,
                        lang=lang,
                        clip_name=node.audio_clip(lang),
                        recorded=False,
                    )
                )
    return out


# -- helpers ------------------------------------------------------------------


def _version(row: QuestionTree, department_code: str | None) -> TreeVersion:
    node_count = len(row.tree.get("nodes", {})) if isinstance(row.tree, dict) else 0
    return TreeVersion(
        id=row.id,
        key=row.key,
        version=row.version,
        status=row.status.value,
        department_code=department_code,
        published_at=row.published_at,
        node_count=node_count,
    )


async def _load_row(session: AsyncSession, key: str, version: int | None) -> QuestionTree:
    stmt = select(QuestionTree).where(QuestionTree.key == key, QuestionTree.deleted_at.is_(None))
    if version is not None:
        stmt = stmt.where(QuestionTree.version == version)
    stmt = stmt.order_by(QuestionTree.version.desc())
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        what = f"{key}@v{version}" if version is not None else key
        raise AdminError(f"no tree {what}")
    return row


async def _department_for(session: AsyncSession, tree: Tree) -> Department | None:
    if not tree.department:
        return None
    department = (
        (await session.execute(select(Department).where(Department.code == tree.department)))
        .scalars()
        .first()
    )
    if department is None:
        raise AdminError(f"tree names department {tree.department!r}, which does not exist")
    return department
