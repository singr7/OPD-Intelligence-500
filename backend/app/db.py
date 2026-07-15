"""Async SQLAlchemy engine, session factory, and the request-scoped session dependency.

One engine per process, created lazily so importing `app.db` never touches the
network (tests and Alembic build their own engines against their own URLs).

Sessions handed out by `get_session` are audit-instrumented: see `app.audit`.
Every clinical write inside a request therefore produces an `audit_log` row
without feature code having to remember to write one.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(
        settings.database_url,
        # Pilot sizing (doc 02 §1): a few thousand req/min on one box. Modest pool,
        # pre-ping so a Postgres restart doesn't poison pooled connections.
        pool_size=10,
        max_overflow=10,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    return build_engine()


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return build_sessionmaker(get_engine())


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session.

    Commits on success, rolls back on any exception, always closes.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
