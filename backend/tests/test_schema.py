"""The migration is the schema: it must not drift from the models.

Everything downstream (deploys, restores, the S18 analytics that reconcile to
usage_events) assumes `alembic upgrade head` produces exactly what the models
describe. Models edited without a migration is the classic way that stops being
true — and it stays invisible until a deploy, because tests would have run
against a create_all schema. Here they don't: conftest migrates the test DB, so
this asserts the real artefact.
"""

from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.models import Base
from tests.conftest import BACKEND_DIR


def test_migration_matches_the_models(settings: Settings, _schema: None) -> None:
    """`alembic check` — fails if a model change has no migration behind it."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=BACKEND_DIR,
        env={**os.environ, "ALEMBIC_DATABASE_URL": settings.database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "models and migrations have drifted — generate a revision:\n"
        f"{result.stdout}\n{result.stderr}"
    )


async def test_every_model_has_a_table(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    missing = set(Base.metadata.tables) - tables
    assert not missing, f"models without tables in the migrated database: {missing}"


async def test_the_domain_schema_is_complete(engine: AsyncEngine) -> None:
    """Doc 02 §4 lists these tables; a pilot missing one is a pilot that stalls."""
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    expected = {
        "hospitals",
        "departments",
        "users",
        "doctors",
        "patients",
        "visits",
        "intakes",
        "dictations",
        "prescriptions",
        "appointments",
        "queues",
        "queue_entries",
        "offline_token_blocks",
        "question_trees",
        "checkin_plans",
        "checkins",
        "usage_events",
        "price_book",
        "otp_codes",
        "refresh_tokens",
        "audit_log",
    }
    assert expected <= tables, f"missing from the schema: {expected - tables}"


async def test_append_only_triggers_exist_in_the_migrated_schema(engine: AsyncEngine) -> None:
    """The immutability tests would pass vacuously if these were missing."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT tgname FROM pg_trigger WHERE tgrelid = 'audit_log'::regclass")
        )
        triggers = {row[0] for row in result}

    assert {"audit_log_no_update", "audit_log_no_delete", "audit_log_no_truncate"} <= triggers


async def test_money_is_never_stored_as_float(engine: AsyncEngine) -> None:
    """Costs get summed into invoices that must reconcile exactly (doc 02 §8)."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (column_name LIKE '%_inr' OR column_name LIKE 'price%')
                """
            )
        )
        columns = list(result)

    assert columns, "expected money columns to exist"
    for table, column, data_type in columns:
        assert data_type == "numeric", f"{table}.{column} is {data_type}, not numeric"
