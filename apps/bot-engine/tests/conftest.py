"""Shared fixtures — an in-memory SQLite DB with the live schema applied."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Base
from app.db.session import create_engine_from_url, make_session_factory


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    # ``:memory:`` per-connection isn't shared; the URI form below gives a
    # single shared in-memory DB for the test session via the StaticPool the
    # SQLAlchemy default uses for in-memory SQLite.
    engine = create_engine_from_url("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_session_factory(db_engine)
