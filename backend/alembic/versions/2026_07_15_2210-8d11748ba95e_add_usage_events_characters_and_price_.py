"""add usage_events.characters (S3 provider metering)

Both TTS vendors (Sarvam Bulbul, Google) bill per *character*, not per second of
audio produced. Metering TTS by output duration would have been an estimate that
never reconciles against an invoice, so S3 added `PriceUnit.CHAR` and this column
to hold the quantity behind it. Without the quantity stored, TTS history could
not be re-priced when a vendor changes rates — which is the whole promise
`usage_events.unit_cost_ref` makes (doc 02 §8).

`PriceUnit.CHAR` itself needs no DDL: `price_book.unit` is a plain VARCHAR(9)
with no CHECK constraint (`enum_type` sets `native_enum=False`, and SQLAlchemy
2.0 defaults `create_constraint=False`), and "char" fits inside 9. Worth knowing
because `app/models/enums.py` claims a CHECK is there — it is not, on any enum
column. Flagged in HANDOFF for a session that owns the schema.

Safe against a populated table despite NOT NULL: the column arrives with a
server default of 0, existing rows backfill, then the default is dropped.

Revision ID: 8d11748ba95e
Revises: 75dc12335238
Create Date: 2026-07-15 22:10:11.822817

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d11748ba95e"
down_revision: str | Sequence[str] | None = "75dc12335238"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "usage_events",
        sa.Column("characters", sa.Integer(), nullable=False, server_default="0"),
    )
    # Drop the server default: the model declares a Python-side default, and a
    # lingering server default makes `alembic check` report drift.
    op.alter_column("usage_events", "characters", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("usage_events", "characters")
