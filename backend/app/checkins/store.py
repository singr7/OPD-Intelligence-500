"""Runtime protocol-bank source: the published row, the seed file as floor.

The mirror of `app.trees.store` for check-ins (S18-late). Until now the bank was
a file read once at boot (`protocols.get_bank`, `@cache`), which made the admin
console's protocol panel read-only by construction: there was no table for an
editor to write. This module is the seam that closes it, and it holds the same
two lines the tree seam holds.

**`parse()` stays the only constructor.** A row reaches a `ProtocolBank` through
`app.checkins.protocols.parse` and nowhere else, so every guarantee S17 argued
for — no green rule, no rule over `free_voice`, no orphan question set, no tied
precedence, no unbounded number question — is enforced on DB content exactly as
it is on the file. A row that no longer parses is **skipped, not fatal**: a bad
publish must never be the reason a signed note drafts no follow-up, and the floor
below it is content an oncologist will have reviewed.

**The file is the floor.** A database with nothing published behaves precisely as
it did before this table existed, which is what makes the migration safe to land
mid-pilot.

Note what this does *not* change: `Checkin.asked` is still a snapshot frozen when
the row is created. Publishing a new bank changes what the *next* plan asks. It
cannot change what a patient was asked last week, or what her "2" meant.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.checkins.protocols import ProtocolBank, ProtocolError, get_bank, parse
from app.models.content import ProtocolBankVersion
from app.models.enums import ContentStatus


async def published_bank(session: AsyncSession) -> ProtocolBank | None:
    """The newest published bank that parses, or `None`.

    Newest-first so that publishing a *later* version supersedes an earlier one
    without anybody having to unpublish it, and so a rollback (publish an older
    version, which demotes its siblings) takes effect immediately.
    """
    rows = (
        (
            await session.execute(
                select(ProtocolBankVersion)
                .where(
                    ProtocolBankVersion.status == ContentStatus.PUBLISHED,
                    ProtocolBankVersion.deleted_at.is_(None),
                )
                .order_by(ProtocolBankVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        try:
            return parse(row.bank)
        except ProtocolError:
            continue
    return None


async def resolve_bank(session: AsyncSession) -> ProtocolBank:
    """The bank a drafting plan or an arriving answer should be graded against.

    DB-published content wins; `seeds/protocols.json` is the floor. Every
    check-in entry point calls this instead of `get_bank()`, so an approved edit
    in the console is live on the next plan with no deploy — the same property
    S18-early gave the trees.

    No cache: one indexed query per signed note (or per answered check-in) is
    nothing next to the class of bug a stale cache invites when the thing cached
    decides whether a fever rings a phone.
    """
    return await published_bank(session) or get_bank()
