"""S17: what a check-in plan and a check-in have to remember.

`checkin_plans` and `checkins` have existed since S2 and were never written to.
S17 fills them in, and the columns added here are all of one kind: **the things
that must not be re-derived later**.

- `treatment_at` — "D+2" is meaningless once the plan is a row; the anchor is
  stored, so nothing recomputes it from a visit date that later moves.
- `personalisation` — model, prompt ref and what it changed, kept for the same
  reason `Dictation.structured` keeps them.
- `Checkin.asked` — the questions as they were sent. The bank may be re-authored
  next month; a "2" answered last week has to keep meaning what it meant then.
- `state` / `attempts` / `next_attempt_at` / `delivery` — the delivery ladder is
  a row, not a retry decorator, exactly like S15's `outbound_calls`: a message
  sent by the worker is answered by a webhook hours later, and a restart in
  between must not lose that a patient was already messaged twice.
- `grade_reasons` — a nurse queue that shows a colour without the rule that fired
  is a queue nobody can triage.

Every NOT NULL column is added **with** a `server_default` and then has it
dropped in the same migration. The default is there to backfill the rows that
already exist — `scripts/seed_doctor_demo` has been writing check-ins since S9 to
light up the doctor card's trendline, so "the table is empty" was a comfortable
assumption and a wrong one. Dropping it afterwards keeps the models the single
source of truth for defaults, which is what `tests/test_schema.py` checks.

Revision ID: ae3caebf5e9a
Revises: e108276e7d43
Create Date: 2026-07-26 04:45:08.316877
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ae3caebf5e9a"
down_revision: str | Sequence[str] | None = "e108276e7d43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LANG = sa.Enum("en", "hi", "mr", "te", name="lang", native_enum=False)
_STATE = sa.Enum(
    "pending", "sent", "answered", "expired", "cancelled", name="checkin_state", native_enum=False
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("checkin_plans", sa.Column("dictation_id", sa.UUID(), nullable=True))
    op.add_column(
        "checkin_plans", sa.Column("treatment_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("checkin_plans", sa.Column("lang", _LANG, nullable=False, server_default="hi"))
    op.add_column(
        "checkin_plans",
        sa.Column(
            "personalisation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "checkin_plans", sa.Column("next_cycle_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "checkin_plans",
        sa.Column(
            "cycle_reminders",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.create_index(
        op.f("ix_checkin_plans_dictation_id"), "checkin_plans", ["dictation_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_checkin_plans_dictation_id_dictations"),
        "checkin_plans",
        "dictations",
        ["dictation_id"],
        ["id"],
    )

    op.add_column(
        "checkins", sa.Column("day_offset", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "checkins",
        sa.Column("question_set", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "checkins",
        sa.Column(
            "asked", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
    )
    op.add_column("checkins", sa.Column("message", sa.Text(), nullable=False, server_default=""))
    op.add_column("checkins", sa.Column("lang", _LANG, nullable=False, server_default="hi"))
    op.add_column("checkins", sa.Column("state", _STATE, nullable=False, server_default="pending"))
    op.add_column(
        "checkins", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "checkins", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "checkins",
        sa.Column(
            "delivery", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
    )
    op.add_column("checkins", sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "checkins",
        sa.Column(
            "grade_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column("checkins", sa.Column("resolved_by", sa.UUID(), nullable=True))
    op.add_column(
        "checkins", sa.Column("resolution_note", sa.Text(), nullable=False, server_default="")
    )
    op.create_index(
        op.f("ix_checkins_next_attempt_at"), "checkins", ["next_attempt_at"], unique=False
    )
    op.create_index(op.f("ix_checkins_state"), "checkins", ["state"], unique=False)
    op.create_foreign_key(
        op.f("fk_checkins_resolved_by_users"), "checkins", "users", ["resolved_by"], ["id"]
    )

    # Backfilled; from here the models own the defaults.
    op.alter_column("checkin_plans", "lang", server_default=None)
    op.alter_column("checkin_plans", "personalisation", server_default=None)
    op.alter_column("checkin_plans", "cycle_reminders", server_default=None)
    op.alter_column("checkins", "day_offset", server_default=None)
    op.alter_column("checkins", "question_set", server_default=None)
    op.alter_column("checkins", "asked", server_default=None)
    op.alter_column("checkins", "message", server_default=None)
    op.alter_column("checkins", "lang", server_default=None)
    op.alter_column("checkins", "state", server_default=None)
    op.alter_column("checkins", "attempts", server_default=None)
    op.alter_column("checkins", "delivery", server_default=None)
    op.alter_column("checkins", "grade_reasons", server_default=None)
    op.alter_column("checkins", "resolution_note", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("fk_checkins_resolved_by_users"), "checkins", type_="foreignkey")
    op.drop_index(op.f("ix_checkins_state"), table_name="checkins")
    op.drop_index(op.f("ix_checkins_next_attempt_at"), table_name="checkins")
    op.drop_column("checkins", "resolution_note")
    op.drop_column("checkins", "resolved_by")
    op.drop_column("checkins", "grade_reasons")
    op.drop_column("checkins", "answered_at")
    op.drop_column("checkins", "delivery")
    op.drop_column("checkins", "next_attempt_at")
    op.drop_column("checkins", "attempts")
    op.drop_column("checkins", "state")
    op.drop_column("checkins", "lang")
    op.drop_column("checkins", "message")
    op.drop_column("checkins", "asked")
    op.drop_column("checkins", "question_set")
    op.drop_column("checkins", "day_offset")
    op.drop_constraint(
        op.f("fk_checkin_plans_dictation_id_dictations"), "checkin_plans", type_="foreignkey"
    )
    op.drop_index(op.f("ix_checkin_plans_dictation_id"), table_name="checkin_plans")
    op.drop_column("checkin_plans", "cycle_reminders")
    op.drop_column("checkin_plans", "next_cycle_at")
    op.drop_column("checkin_plans", "personalisation")
    op.drop_column("checkin_plans", "lang")
    op.drop_column("checkin_plans", "treatment_at")
    op.drop_column("checkin_plans", "dictation_id")
