"""voice profile usage metadata

Revision ID: a4d5e6f7b801
Revises: 2c978d44c900
Create Date: 2026-07-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4d5e6f7b801"
down_revision: str | Sequence[str] | None = "2c978d44c900"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usage_events", sa.Column("voice_profile", sa.String(length=32), nullable=True))
    op.create_index(
        op.f("ix_usage_events_voice_profile"),
        "usage_events",
        ["voice_profile"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_events_voice_profile"), table_name="usage_events")
    op.drop_column("usage_events", "voice_profile")
