"""S-ADAPT.2: intake adaptive_events (per-node interpret telemetry)

One JSONB column on `intakes` holding the adaptive-intake telemetry the V2
tree-improvement report reads (doc 11 §3): a list of per-interpret-event records,
`[{node_id, outcome, enriched, at}]`. The LLM-call turns among them reconcile to
the intake's INTAKE_TURN usage_events — the doc 11 §3 AC.

Additive and nullable with a server-side default of an empty JSON array: no
backfill, and every existing intake (pure-tap, non-adaptive, or pre-S-ADAPT)
stays valid with an empty list. Same shape decision as `answers`/`red_flags`.

Revision ID: a1b2c3d4e5f6
Revises: bc2e83129ac3
Create Date: 2026-07-22 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | Sequence[str] | None = 'bc2e83129ac3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add with a server default so existing rows backfill to an empty array, then
    # drop the default: the model owns the default (Python-side `default=list`,
    # like `answers`/`red_flags`), so the column must not carry a server default or
    # `alembic check` reports drift.
    op.add_column(
        'intakes',
        sa.Column(
            'adaptive_events',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column('intakes', 'adaptive_events', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('intakes', 'adaptive_events')
