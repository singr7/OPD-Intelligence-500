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

Red-flag rules need no editor of their own: they live *inside* the tree JSON, so
the tree editor is their editor and `parse` validates them in place.

Two content types are versioned here, and they are versioned at different
granularities on purpose. A **tree** is versioned per key — it is self-contained,
and one department's tree going live has nothing to do with another's. The
**protocol bank** is versioned as one document, because `protocols.parse`
cross-checks the whole thing (no orphaned question set, no tied precedence); a
protocol-at-a-time editor would let a half-edit pass and fail those checks later,
on a box, at the moment a doctor signs a note.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_admin_action
from app.checkins import protocols
from app.config import Settings, get_settings
from app.models.content import (
    ChannelConfigVersion,
    ProtocolBankVersion,
    ProviderSecret,
    QuestionTree,
)
from app.models.enums import AuditAction, Channel, ContentStatus, Lang, PriceUnit, TreeStatus
from app.models.metering import PriceBook
from app.models.org import Department
from app.providers import runtime
from app.providers.pricing import get_price_book
from app.providers.probe import probe, probe_voice_component
from app.providers.profiles import VoiceProfileName, snapshot_profile
from app.providers.secrets import SecretUnreadable, decrypt, encrypt, using_a_derived_key
from app.tiers import SWITCHABLE, TierConfigError, get_tier_config, parse_tier_config
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


# -- protocol bank ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BankVersion:
    id: Any
    version: int
    status: str
    published_at: datetime | None
    notes: str | None
    protocol_count: int
    question_set_count: int


async def list_protocol_banks(session: AsyncSession) -> list[BankVersion]:
    """Every stored version of the check-in protocol bank, newest first."""
    rows = (
        (
            await session.execute(
                select(ProtocolBankVersion)
                .where(ProtocolBankVersion.deleted_at.is_(None))
                .order_by(ProtocolBankVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_bank_version(row) for row in rows]


async def get_protocol_bank(session: AsyncSession, version: int | None = None) -> dict[str, Any]:
    """One stored bank's JSON — the document the editor loads and posts back."""
    return (await _load_bank_row(session, version)).bank


async def save_protocol_bank_draft(
    session: AsyncSession, *, bank_json: dict[str, Any], notes: str | None = None
) -> BankVersion:
    """Validate an edited protocol bank and store it as a new **draft** version.

    The version number is assigned here, not taken from the body: the document's
    own `version` field is rewritten to match the row so the two can never
    disagree, and an editor cannot overwrite a version some `CheckinPlan` was
    drafted against by posting the same number twice.

    Validation is `app.checkins.protocols.parse` — the whole document, including
    the cross-checks a per-protocol editor could not make: no orphaned question
    set, no tied precedence, no rung naming a set that does not exist. A bank
    that would grade nothing, or grade `free_voice` text, or carry a `green`
    rule, is refused here rather than discovered on a patient.
    """
    next_version = (
        await session.scalar(select(func.coalesce(func.max(ProtocolBankVersion.version), 0)))
    ) + 1
    payload = {**bank_json, "version": next_version}
    try:
        parsed = protocols.parse(payload)
    except protocols.ProtocolError as exc:
        raise AdminError(f"invalid protocol bank: {exc}") from exc

    row = ProtocolBankVersion(
        version=next_version,
        bank=payload,
        status=ContentStatus.DRAFT,
        notes=notes,
    )
    session.add(row)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=ProtocolBankVersion.__tablename__,
        entity_id=row.id,
        meta={
            "version": next_version,
            "status": "draft",
            "protocols": len(parsed.protocols),
            "question_sets": len(parsed.question_sets),
        },
    )
    return _bank_version(row)


async def publish_protocol_bank(session: AsyncSession, *, version: int) -> BankVersion:
    """Make one version of the bank the live one.

    Exactly one published version, like the trees: publishing demotes every
    sibling, so `store.resolve_bank` has an unambiguous answer and publishing an
    older version is a working rollback. Re-validated from the stored JSON, so a
    row that somehow became unparseable cannot go live.

    What this does **not** do is touch a check-in already created: those carry
    their own frozen questions and grading rules. Publishing changes the next
    plan a doctor signs.
    """
    row = await _load_bank_row(session, version)
    try:
        protocols.parse(row.bank)
    except protocols.ProtocolError as exc:  # pragma: no cover - the draft save gates this
        raise AdminError(f"refusing to publish an invalid protocol bank: {exc}") from exc

    siblings = (
        (
            await session.execute(
                select(ProtocolBankVersion).where(ProtocolBankVersion.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    for sibling in siblings:
        sibling.status = ContentStatus.DRAFT
        sibling.published_at = None

    row.status = ContentStatus.PUBLISHED
    row.published_at = datetime.now(UTC)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=ProtocolBankVersion.__tablename__,
        entity_id=row.id,
        meta={"version": version, "status": "published"},
    )
    return _bank_version(row)


# -- the channel document (S-GL.1) --------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelConfigVersionOut:
    id: Any
    version: int
    status: str
    published_at: datetime | None
    notes: str | None
    #: `{channel: enabled}` — enough for the version list to read as history
    #: ("v3 is the one that closed WhatsApp") without loading each document.
    enabled: dict[str, bool]


async def list_channel_configs(session: AsyncSession) -> list[ChannelConfigVersionOut]:
    rows = (
        (
            await session.execute(
                select(ChannelConfigVersion)
                .where(ChannelConfigVersion.deleted_at.is_(None))
                .order_by(ChannelConfigVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_channel_version(row) for row in rows]


async def get_channel_config(session: AsyncSession, version: int | None = None) -> dict[str, Any]:
    """One stored document, or — with nothing stored — the file (S-GL.1).

    Falling back to the file matters for the editor's first use: an admin opening
    the Channels tab on a box that has never published sees the ladders actually
    in force, edits those, and publishes. The alternative is an empty form that
    silently discards `config/tiers.yaml`'s content on first save.
    """
    if version is None:
        row = await _latest_channel_row(session)
        if row is None:
            return get_tier_config().to_json()
        return row.config
    return (await _load_channel_row(session, version)).config


async def save_channel_config_draft(
    session: AsyncSession, *, config: dict[str, Any], notes: str | None = None
) -> ChannelConfigVersionOut:
    """Validate an edited channel document and store it as a new **draft**.

    `parse_tier_config` is the same validator the file goes through, so a document
    typed into a console cannot express a ladder, a seat share or a campaign mix
    the file could not — and the checks that only make sense document-wide (a
    share larger than the box, a mix that does not sum to 100) are made here,
    before anybody can publish them.
    """
    try:
        parse_tier_config(config)
    except TierConfigError as exc:
        raise AdminError(f"invalid channel document: {exc}") from exc

    next_version = (
        await session.scalar(select(func.coalesce(func.max(ChannelConfigVersion.version), 0)))
    ) + 1
    row = ChannelConfigVersion(
        version=next_version, config=config, status=ContentStatus.DRAFT, notes=notes
    )
    session.add(row)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.CREATE,
        entity=ChannelConfigVersion.__tablename__,
        entity_id=row.id,
        meta={"version": next_version, "status": "draft"},
    )
    return _channel_version(row)


async def publish_channel_config(
    session: AsyncSession, *, version: int, settings: Settings | None = None
) -> ChannelConfigVersionOut:
    """Make one version the live one — the act that opens or closes a channel.

    Exactly one published version, like the trees and the banks, so `resolve_config`
    has an unambiguous answer and publishing an older version is a working
    rollback. Re-validated from the stored JSON: a row that somehow became
    unparseable must not go live, because the thing it decides is whether anything
    answers at all.

    The audit row names which channels this publish opens and closes. "Who turned
    WhatsApp off on Tuesday" is exactly the question an operator asks afterwards,
    and a version number alone does not answer it.
    """
    row = await _load_channel_row(session, version)
    try:
        config = parse_tier_config(row.config)
    except TierConfigError as exc:  # pragma: no cover - the draft save gates this
        raise AdminError(f"refusing to publish an invalid channel document: {exc}") from exc

    settings = settings or get_settings()
    profile_status = next(
        status
        for status in await voice_profile_statuses(
            session, active=config.kiosk_voice_profile, settings=settings
        )
        if status.name == config.kiosk_voice_profile.value
    )
    if config.is_enabled(Channel.KIOSK) and not profile_status.ready:
        raise AdminError(
            f"refusing to activate kiosk profile {profile_status.name}: {profile_status.reason}"
        )

    siblings = (
        (
            await session.execute(
                select(ChannelConfigVersion).where(ChannelConfigVersion.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    previous_profile: str | None = None
    for sibling in siblings:
        if sibling.status == ContentStatus.PUBLISHED:
            try:
                previous_profile = parse_tier_config(sibling.config).kiosk_voice_profile.value
            except TierConfigError:
                previous_profile = None
            break

    for sibling in siblings:
        sibling.status = ContentStatus.DRAFT
        sibling.published_at = None

    row.status = ContentStatus.PUBLISHED
    row.published_at = datetime.now(UTC)
    await session.flush()
    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=ChannelConfigVersion.__tablename__,
        entity_id=row.id,
        meta={
            "version": version,
            "status": "published",
            "open": sorted(c.value for c in SWITCHABLE if config.is_enabled(c)),
            "closed": sorted(c.value for c in SWITCHABLE if not config.is_enabled(c)),
            "kiosk_voice_profile": config.kiosk_voice_profile.value,
            "previous_kiosk_voice_profile": previous_profile,
        },
    )
    return _channel_version(row)


@dataclass(frozen=True, slots=True)
class VoiceComponentStatus:
    component: str
    provider: str
    model: str
    configured: bool
    tested: bool
    healthy: bool
    detail: str


@dataclass(frozen=True, slots=True)
class VoiceProfileStatus:
    name: str
    active: bool
    ready: bool
    reason: str
    components: tuple[VoiceComponentStatus, ...]


async def voice_profile_statuses(
    session: AsyncSession,
    *,
    active: VoiceProfileName,
    settings: Settings | None = None,
) -> list[VoiceProfileStatus]:
    """Readiness for every selectable profile, without returning any secret."""
    settings = settings or get_settings()
    credential_status = {row.provider: row for row in await provider_credentials(session, settings)}
    rows = {
        row.provider: row
        for row in (
            (
                await session.execute(
                    select(ProviderSecret).where(ProviderSecret.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
    }
    statuses: list[VoiceProfileStatus] = []
    for name in VoiceProfileName:
        snapshot = snapshot_profile(name, settings)
        if name is VoiceProfileName.LOCAL_OSS:
            components = tuple(
                VoiceComponentStatus(
                    component=component,
                    provider=value.provider,
                    model=value.model,
                    configured=True,
                    tested=False,
                    healthy=True,
                    detail="local profile; runtime health is reported by provider health",
                )
                for component, value in (
                    ("stt", snapshot.stt),
                    ("llm", snapshot.llm),
                    ("tts", snapshot.tts),
                )
            )
            ready, reason = True, "local components configured"
        else:
            vendor = snapshot.stt.provider
            credential_name = f"vendor:{vendor}"
            credential = credential_status[credential_name]
            tests = dict(rows.get(credential_name).last_test if rows.get(credential_name) else {})
            tested_components = dict(tests.get("components") or {})
            component_rows: list[VoiceComponentStatus] = []
            for component, value in (
                ("stt", snapshot.stt),
                ("llm", snapshot.llm),
                ("tts", snapshot.tts),
            ):
                result = dict(tested_components.get(component) or {})
                component_rows.append(
                    VoiceComponentStatus(
                        component=component,
                        provider=value.provider,
                        model=value.model,
                        configured=credential.configured,
                        tested=bool(result),
                        healthy=bool(result.get("ok")),
                        detail=str(result.get("detail") or "not tested"),
                    )
                )
            components = tuple(component_rows)
            failed = [
                row.component
                for row in components
                if not row.configured or not row.tested or not row.healthy
            ]
            ready = not failed
            reason = (
                "all components passed their latest test"
                if ready
                else "components not ready: " + ", ".join(failed)
            )
        statuses.append(
            VoiceProfileStatus(
                name=name.value,
                active=name is active,
                ready=ready,
                reason=reason,
                components=components,
            )
        )
    return statuses


async def _latest_channel_row(session: AsyncSession) -> ChannelConfigVersion | None:
    return (
        await session.execute(
            select(ChannelConfigVersion)
            .where(ChannelConfigVersion.deleted_at.is_(None))
            .order_by(ChannelConfigVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _load_channel_row(session: AsyncSession, version: int) -> ChannelConfigVersion:
    row = (
        await session.execute(
            select(ChannelConfigVersion).where(
                ChannelConfigVersion.version == version,
                ChannelConfigVersion.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AdminError(f"no channel document version {version}")
    return row


def _channel_version(row: ChannelConfigVersion) -> ChannelConfigVersionOut:
    try:
        config = parse_tier_config(row.config)
        enabled = {c.value: config.is_enabled(c) for c in SWITCHABLE}
    except TierConfigError:
        # A stored draft that no longer parses still has to list, or an admin
        # cannot see the version they need to fix.
        enabled = {}
    return ChannelConfigVersionOut(
        id=row.id,
        version=row.version,
        status=str(row.status),
        published_at=row.published_at,
        notes=row.notes,
        enabled=enabled,
    )


# -- provider credentials (S-GL.1) --------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderCredentialStatus:
    """Everything the console is ever told about a credential set.

    Note what is not here: the credentials. There is no field for them and no
    function that returns them to a route — the console shows whether a vendor is
    configured, when it was last set, and what the vendor itself said the last
    time we tested it. `configured` is computed from the required fields, so a set
    missing its phone number id reads as incomplete rather than as ready.
    """

    provider: str
    configured: bool
    #: Which required fields are still missing — the actionable half of "not
    #: configured", and safe to show: field *names*, never values.
    missing: list[str]
    source: str  # "console" | "env" | "unset"
    updated_at: datetime | None
    last_test: dict[str, Any]
    #: True when the encrypting key is derived from JWT_SECRET rather than set
    #: explicitly, which couples the two secrets (see app/providers/secrets.py).
    derived_key: bool
    #: Set when a stored row will not decrypt — a rotated key. Distinguished from
    #: "never entered", which has a different fix.
    unreadable: bool = False


async def provider_credentials(
    session: AsyncSession, settings: Settings | None = None
) -> list[ProviderCredentialStatus]:
    """The status of every credential set the console can manage."""
    settings = settings or get_settings()
    rows = {
        row.provider: row
        for row in (
            (
                await session.execute(
                    select(ProviderSecret).where(ProviderSecret.deleted_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
    }

    out: list[ProviderCredentialStatus] = []
    for name in sorted(runtime.CREDENTIAL_FIELDS):
        row = rows.get(name)
        stored: dict[str, Any] = {}
        unreadable = False
        if row is not None:
            try:
                stored = runtime.sanitise(name, decrypt(row.secret, row.key_id, settings))
            except SecretUnreadable:
                unreadable = True

        # `.env` is the floor: a vendor credentialed there is configured even with
        # no row, and the console says so rather than offering to "set it up".
        from_env = {
            field: str(getattr(settings, field, "") or "")
            for field in runtime.CREDENTIAL_FIELDS[name]
        }
        effective = {**{k: v for k, v in from_env.items() if v}, **stored}
        missing = runtime.missing_fields(name, effective)
        source = "console" if stored else ("env" if not missing else "unset")

        out.append(
            ProviderCredentialStatus(
                provider=name,
                configured=not missing and not unreadable,
                missing=missing,
                source=source,
                updated_at=row.updated_at if row is not None else None,
                last_test=dict(row.last_test) if row is not None else {},
                derived_key=using_a_derived_key(settings),
                unreadable=unreadable,
            )
        )
    return out


async def save_provider_credentials(
    session: AsyncSession,
    *,
    provider: str,
    values: dict[str, Any],
    actor_id: Any = None,
    settings: Settings | None = None,
) -> ProviderCredentialStatus:
    """Store (or replace) one vendor's credentials, encrypted.

    Merged over what is already stored rather than replacing it wholesale, so an
    admin who re-enters one field does not silently blank the other three — a real
    hazard on a form that cannot show what it currently holds.

    Nothing is logged, nothing is echoed, and the audit row records the field
    *names* that changed and never their values.
    """
    settings = settings or get_settings()
    incoming = runtime.sanitise(runtime.known(provider), values)
    if not incoming:
        raise AdminError(
            f"no recognised credential fields for {provider}; "
            f"expected some of {list(runtime.CREDENTIAL_FIELDS[provider])}"
        )

    row = (
        await session.execute(
            select(ProviderSecret).where(
                ProviderSecret.provider == provider, ProviderSecret.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()

    existing: dict[str, Any] = {}
    if row is not None:
        try:
            existing = runtime.sanitise(provider, decrypt(row.secret, row.key_id, settings))
        except SecretUnreadable:
            # Unreadable under the current key: this save replaces it outright,
            # which is the only way out of a rotation anyway.
            existing = {}

    merged = {**existing, **incoming}
    ciphertext, kid = encrypt(merged, settings)

    if row is None:
        row = ProviderSecret(provider=provider, secret=ciphertext, key_id=kid)
        session.add(row)
    else:
        row.secret = ciphertext
        row.key_id = kid
        row.last_test = {}  # a new credential has not been tested yet
    row.updated_by = actor_id
    await session.flush()

    record_admin_action(
        session,
        action=AuditAction.UPDATE,
        entity=ProviderSecret.__tablename__,
        entity_id=row.id,
        meta={"provider": provider, "fields": sorted(incoming)},
    )
    # The admin who just typed these will press "test" next; that must not read a
    # ten-second-old overlay from before the save.
    runtime.invalidate()
    return next(
        status
        for status in await provider_credentials(session, settings)
        if status.provider == provider
    )


async def clear_provider_credentials(
    session: AsyncSession, *, provider: str, actor_id: Any = None
) -> None:
    """Drop a stored credential set — the box falls back to `.env`.

    A hard delete, not a soft one. A soft-deleted secret is still a live vendor
    credential sitting in the database after somebody decided it should not be,
    which is the opposite of what "remove" means here.
    """
    row = (
        await session.execute(
            select(ProviderSecret).where(
                ProviderSecret.provider == runtime.known(provider),
                ProviderSecret.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AdminError(f"no stored credentials for {provider}")
    record_admin_action(
        session,
        action=AuditAction.DELETE,
        entity=ProviderSecret.__tablename__,
        entity_id=row.id,
        meta={"provider": provider},
    )
    await session.delete(row)
    await session.flush()
    runtime.invalidate()


async def test_provider_credentials(
    session: AsyncSession,
    *,
    provider: str,
    component: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """One real round-trip against the vendor, recorded on the row.

    **The vendor's own error is kept verbatim.** "The access token has expired" is
    the entire value of a test button; paraphrasing it into "connection failed"
    throws away the only part an admin can act on.

    A test that cannot even be attempted (nothing configured) says that rather
    than reporting a failure, because they are different problems.
    """
    settings = settings or get_settings()
    runtime.known(provider)
    effective = await runtime.effective_settings(session, settings)

    status = next(
        s for s in await provider_credentials(session, effective) if s.provider == provider
    )
    if not status.configured:
        result = {
            "ok": False,
            "at": datetime.now(UTC).isoformat(),
            "detail": (
                "not configured — missing " + ", ".join(status.missing)
                if status.missing
                else "credentials are unreadable under the current key; enter them again"
            ),
        }
        await _record_test(session, provider, result, component=component, settings=settings)
        return result

    kind, vendor = provider.split(":", 1)
    try:
        if kind == "vendor":
            if component not in {"stt", "llm", "tts"}:
                raise AdminError("voice vendor tests require component=stt|llm|tts")
            checked = await probe_voice_component(component, vendor, effective)
        else:
            checked = await probe(kind, vendor, effective)
        result = {
            "ok": True,
            "at": datetime.now(UTC).isoformat(),
            "detail": runtime.redact_credentials(checked, effective),
        }
    except Exception as exc:  # noqa: BLE001 — a vendor's own failure, whatever shape
        result = {
            "ok": False,
            "at": datetime.now(UTC).isoformat(),
            "detail": runtime.redact_credentials(str(exc), effective),
        }
    await _record_test(session, provider, result, component=component, settings=settings)
    return result


async def _record_test(
    session: AsyncSession,
    provider: str,
    result: dict[str, Any],
    *,
    component: str | None = None,
    settings: Settings,
) -> None:
    row = (
        await session.execute(
            select(ProviderSecret).where(
                ProviderSecret.provider == provider, ProviderSecret.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Keep test evidence even when the credential floor is `.env`. The row
        # contains an encrypted empty object—not a duplicate credential—and the
        # runtime overlay therefore continues to read the key from `.env`.
        ciphertext, kid = encrypt({}, settings)
        row = ProviderSecret(provider=provider, secret=ciphertext, key_id=kid)
        session.add(row)
    if component is None:
        row.last_test = result
    else:
        previous = dict(row.last_test)
        components = dict(previous.get("components") or {})
        components[component] = result
        row.last_test = {"components": components}
    await session.flush()


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


def _bank_version(row: ProtocolBankVersion) -> BankVersion:
    bank = row.bank if isinstance(row.bank, dict) else {}
    return BankVersion(
        id=row.id,
        version=row.version,
        status=row.status.value,
        published_at=row.published_at,
        notes=row.notes,
        protocol_count=len(bank.get("protocols", {})),
        question_set_count=len(bank.get("question_sets", {})),
    )


async def _load_bank_row(session: AsyncSession, version: int | None) -> ProtocolBankVersion:
    stmt = select(ProtocolBankVersion).where(ProtocolBankVersion.deleted_at.is_(None))
    if version is not None:
        stmt = stmt.where(ProtocolBankVersion.version == version)
    stmt = stmt.order_by(ProtocolBankVersion.version.desc())
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        what = f"v{version}" if version is not None else "any version"
        raise AdminError(f"no protocol bank ({what}) — run `make seed`")
    return row


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
