"""Test fixtures.

Two decisions worth knowing about:

**Real Postgres, not SQLite.** The schema leans on JSONB, UUID, and PL/pgSQL
triggers (the append-only audit log). A SQLite stand-in would test a different
database than the one we ship, and would report green on exactly the append-only
guarantee it cannot enforce.

**Schema built by `alembic upgrade head`, not `metadata.create_all`.** The
triggers live in the migration, so create_all would produce a schema without
them — and the append-only tests would pass vacuously. Running the real
migration also means every test run exercises the path production deploys use,
and `test_schema.py` asserts the result still matches the models.

The database comes from `TEST_DATABASE_URL`, defaulting to the compose Postgres
on host port 5433 (see docker-compose.yml for why not 5432). CI provides its own.

Isolation: each test runs inside a transaction that is rolled back at teardown,
so tests neither see nor clobber each other's rows — including audit rows, which
cannot be deleted after the fact.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.audit import AuditedSession
from app.config import Settings, get_settings
from app.db import get_session
from app.main import create_app
from app.providers.metering import UsageMeter, set_meter
from app.providers.pricing import PriceBookCache, set_price_book
from app.providers.registry import reset_providers
from app.providers.sms import FakeSMSProvider

BACKEND_DIR = Path(__file__).resolve().parents[1]

DEFAULT_TEST_DB = "postgresql+asyncpg://opd:opd_local_dev@localhost:5433/opd_test"


def test_database_url() -> str:
    return os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DB)


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


async def _ensure_database(url: str) -> None:
    """CREATE DATABASE the test DB if it's missing, connecting via `postgres`."""
    import asyncpg

    target = urlsplit(url).path.lstrip("/")
    admin_dsn = _with_database(url, "postgres").replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", target)
        if not exists:
            # No parameter binding for DDL; the name is ours, not user input.
            await conn.execute(f'CREATE DATABASE "{target}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        env="test",
        database_url=test_database_url(),
        # ≥32 chars: shorter keys weaken HS256 and make pyjwt warn on every decode.
        jwt_secret="test-secret-not-a-real-one-padded-to-32+",
        sms_provider="fake",
        otp_debug_echo=True,
        # Cooldown off by default: most tests request several OTPs in a row and
        # aren't testing the rate limiter. The test that is sets its own value.
        otp_resend_cooldown_seconds=0,
    )


@pytest.fixture(scope="session")
def _schema(settings: Settings) -> None:
    """Create the test database and migrate it to head, once per test session."""
    asyncio.run(_ensure_database(settings.database_url))

    # Subprocess rather than alembic's Python API: env.py calls asyncio.run(),
    # which cannot run inside pytest-asyncio's event loop.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env={**os.environ, "ALEMBIC_DATABASE_URL": settings.database_url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest_asyncio.fixture(scope="session")
async def engine(settings: Settings, _schema: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose writes are always rolled back.

    The session joins an outer transaction on one connection with
    `join_transaction_mode="create_savepoint"`, so a `commit()` inside a test
    (the OTP attempt-cap path does one) only releases a savepoint. The outer
    rollback at teardown discards everything.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = sessionmaker(
            bind=connection,
            class_=AsyncSession,
            # Same audited session class the app uses — tests must not get a
            # quieter session than production.
            sync_session_class=AuditedSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as db_session:
            yield db_session
        await transaction.rollback()


@pytest.fixture
def sms(providers: None, settings: Settings) -> FakeSMSProvider:
    """The fake SMS provider this test's app is wired to.

    Depends on `providers` so the registry is freshly reset, then takes the
    instance *from the registry* rather than building a second one — the test
    must assert on the same object the route actually used.
    """
    from app.providers.registry import get_sms_provider

    provider = get_sms_provider(settings)
    assert isinstance(provider, FakeSMSProvider)
    return provider


@pytest.fixture
def providers(settings: Settings) -> Iterator[None]:
    """Reset the provider registry around every test.

    Providers are process-wide singletons that accumulate state — the fakes keep
    their `sent` lists, and every provider carries a circuit breaker and health
    counters. Leaking those across tests makes failures depend on test order,
    which is the kind of bug that costs an afternoon.
    """
    reset_providers()
    yield
    reset_providers()


@pytest.fixture
def prices() -> Iterator[PriceBookCache]:
    """A price book cache scoped to one test, with no TTL caching surprises.

    Installed as *the* process cache, because that is how production is wired:
    the meter prices against `get_price_book()` and `/providers/health` reports
    the same object's `unpriced` set. Two caches would let the endpoint report
    all-clear while the meter was failing to price anything.
    """
    cache = PriceBookCache(ttl_seconds=0)
    set_price_book(cache)
    yield cache
    set_price_book(PriceBookCache())


@pytest_asyncio.fixture
async def meter(session: AsyncSession, prices: PriceBookCache) -> AsyncIterator[UsageMeter]:
    """A usage meter writing into the test's rolled-back transaction.

    The real meter owns its own sessionmaker and drains on a background task.
    Here it gets a factory that hands back *this test's* session and does not
    close it, so:
      - flushed usage rows are visible to the test, and
      - they vanish with the outer rollback at teardown.

    No drain task is started: `await meter.flush()` is deterministic, and a
    background task racing the assertions is how you get a flaky suite.
    """

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    meter = UsageMeter(factory, prices, flush_interval_seconds=0.05)
    set_meter(meter)
    yield meter
    set_meter(None)


@pytest_asyncio.fixture
async def seeded_prices(session: AsyncSession) -> None:
    """Load `seeds/price_book.json` into the test transaction.

    Metering tests need real prices: the S3 AC is that every fake call produces a
    *priced* usage_event, and pricing against invented rates would test the
    arithmetic while missing a missing seed row.
    """
    from app.seed import SeedReport, _load, _upsert_price_book

    await _upsert_price_book(session, _load("price_book.json")["entries"], SeedReport.empty())


@pytest_asyncio.fixture
async def client(
    session: AsyncSession, settings: Settings, sms: FakeSMSProvider
) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the same rolled-back session the test sees."""
    app = create_app(settings)

    # ASGITransport does not run the lifespan, so the engine the lifespan builds
    # is absent. Set an in-memory one here — it persists across the requests of a
    # single kiosk flow within one test (the same guarantee one api process gives).
    from app.intake import InMemorySessionStore, IntakeEngine

    app.state.intake_engine = IntakeEngine(InMemorySessionStore())

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
