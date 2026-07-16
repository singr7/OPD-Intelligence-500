"""drop question_trees.lang; key trees by (key, version) (S4 tree engine)

Doc 02 §4 sketched `question_trees(id, dept_id, version, lang, tree JSONB, status)`
— one row per language, keyed (key, lang, version). Doc 03 §3's node schema then
specified the JSONB itself as `text:{"en":…,"hi":…,"mr":…,"te":…}` — every
language *inside* the tree. Both cannot be true. S4 kept doc 03 §3 and dropped the
column:

- **Language is switchable mid-intake** (doc 03 §1: "switchable any time",
  `Intake.lang` is per-intake). With text in the node that is a re-render of the
  same node id. With per-language rows it is a swap onto a different tree row
  mid-session, which is only safe if the rows happen to share node ids and
  branching — an invariant nothing enforces.
- **Structure stays single-sourced.** Four rows per tree means four copies of the
  branching and the red-flag rules, free to drift, under one clinical sign-off
  (S21) that only ever covered the copy the reviewer opened. The validator
  (`app.trees.schema`) instead requires every declared language to be complete,
  which turns a missing translation into a failed publish rather than a patient
  facing English on a kiosk in Alwar.
- **S13 (mr/te) becomes additive** — fill in text keys, touch no structure.

Ratification: the human ratified `PriceUnit.CHAR` the same way in S3. Doc 02 §4
should lose `lang` from the `question_trees` line. Flagged in HANDOFF.

Safe on a populated table in both directions, though today it is trivially safe in
one: `question_trees` has no writers until this session's seed. The downgrade
re-adds `lang` with a server default of 'en' and then drops the default, because a
NOT NULL column cannot arrive on rows that exist without one — and a downgrade
that only works on an empty table is a downgrade nobody can run.

Note the downgrade is lossy in the way that matters: it restores the *column*, not
one row per language. A multilingual tree collapsed back would claim to be English.

Revision ID: fbcaee31fa43
Revises: 8d11748ba95e
Create Date: 2026-07-16 08:21:37.613002

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fbcaee31fa43"
down_revision: str | Sequence[str] | None = "8d11748ba95e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        op.f("uq_question_trees_key_lang_version"), "question_trees", type_="unique"
    )
    op.create_unique_constraint(
        "uq_question_trees_key_version", "question_trees", ["key", "version"]
    )
    op.drop_column("question_trees", "lang")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "question_trees",
        sa.Column("lang", sa.VARCHAR(length=2), nullable=False, server_default="en"),
    )
    op.alter_column("question_trees", "lang", server_default=None)
    op.drop_constraint("uq_question_trees_key_version", "question_trees", type_="unique")
    op.create_unique_constraint(
        op.f("uq_question_trees_key_lang_version"),
        "question_trees",
        ["key", "lang", "version"],
        postgresql_nulls_not_distinct=False,
    )
