"""Runtime channel-document source: the published row, `config/tiers.yaml` as floor.

The third instance of the seam `app.trees.store` and `app.checkins.store` already
hold, and deliberately identical to them: newest published version that parses
wins, the file is the floor, `parse_tier_config` is the only constructor, and a
row that no longer parses is skipped rather than fatal.

The last of those matters more here than it does for a tree. A tree that fails to
parse falls back to authored content and a patient answers slightly older
questions. A channel document that fails to parse decides whether *anything*
answers at all — so an unparseable published row must land on the file's ladders
(every channel open, on its authored tier order), never on "closed". A bad
publish that shut the OPD would be a worse outage than the one it was trying to
prevent.

No cache: one indexed query per intake start, next to the class of bug a stale
cache invites when the thing cached is "is this channel open".
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ChannelConfigVersion
from app.models.enums import ContentStatus
from app.tiers import TierConfig, TierConfigError, get_tier_config, parse_tier_config

logger = logging.getLogger(__name__)


async def published_config(session: AsyncSession) -> TierConfig | None:
    """The newest published channel document that parses, or `None`.

    Newest-first so publishing a later version supersedes an earlier one without
    anybody unpublishing it, and so a rollback (publish an older version, which
    demotes its siblings) takes effect on the next intake.
    """
    rows = (
        (
            await session.execute(
                select(ChannelConfigVersion)
                .where(
                    ChannelConfigVersion.status == ContentStatus.PUBLISHED,
                    ChannelConfigVersion.deleted_at.is_(None),
                )
                .order_by(ChannelConfigVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        try:
            return parse_tier_config(row.config)
        except TierConfigError:
            logger.exception(
                "published channel document v%s does not parse — falling through to the file",
                row.version,
            )
            continue
    return None


async def resolve_config(session: AsyncSession) -> TierConfig:
    """The channel document in force: DB-published wins, `config/tiers.yaml` is the floor.

    Every gate, every ladder lookup and the campaign planner call this rather than
    `get_tier_config()`, so a switch thrown in the console is live on the next
    request with no deploy and no restart — the property S18-early gave the trees.
    """
    return await published_config(session) or get_tier_config()
