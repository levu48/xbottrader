"""Async SQLAlchemy engine + session factory.

DATABASE_URL drives the driver:
    sqlite+aiosqlite:///./dev.db        (tests / local)
    postgresql+asyncpg://user:pw@h/db   (production — DO Managed Postgres)

Use `create_engine_from_env()` once at boot; pass the returned session_factory
into the API / launcher / router. ``async with session_factory() as s:`` per
request.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine_from_url(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def create_engine_from_env(*, env_var: str = "DATABASE_URL") -> AsyncEngine:
    url = os.environ.get(env_var)
    if not url:
        raise RuntimeError(f"{env_var} env var must be set")
    return create_engine_from_url(url)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def transactional(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Run a unit of work inside a single transaction; commit/rollback as needed."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
