"""
tests/conftest.py
-----------------
Shared pytest fixtures for the entire test suite.

Database strategy
-----------------
All tests use an in-memory SQLite database via StaticPool, which forces every
SQLAlchemy session to share the same underlying connection.  This is the only
way to make in-memory SQLite data visible across multiple session instances
(e.g. the db_session fixture seeding rows that the HTTP client then reads).

Each test function gets a fresh database — tables are dropped and re-created
between tests by the test_engine fixture (function scope by default).

API client strategy
-------------------
Background services (MultiChainTracker, PriceAlertChecker, PortfolioTracker)
are patched to no-ops so tests don't attempt real chain RPC connections.
migrate_db / init_db are also patched because they target the production
engine, not the test engine; the test_engine fixture creates schema itself.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from api.models import Base, get_db
from api.main import app

# ── In-memory SQLite URL ───────────────────────────────────────────────────────
_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def test_engine():
    """
    Fresh in-memory SQLite engine per test.

    StaticPool makes every session reuse the same connection, so data written
    by one session is immediately visible to other sessions on the same engine.
    """
    engine = create_async_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    """
    Raw AsyncSession backed by the test engine.

    Use this to seed rows before making HTTP requests so you can verify that
    the API reads back the expected data.
    """
    TestSession = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client(test_engine):
    """
    httpx AsyncClient wired to the FastAPI app with:
      - get_db overridden to use the test engine
      - background services mocked to prevent real RPC connections
    """
    TestSession = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_db():
        async with TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    with (
        patch("api.main.migrate_db", new=AsyncMock()),
        patch("api.main.init_db", new=AsyncMock()),
        patch("api.main.MultiChainTracker") as mock_tracker,
        patch("api.main.PriceAlertChecker") as mock_checker,
        patch("api.main.PortfolioTracker") as mock_portfolio,
    ):
        mock_tracker.return_value.start = AsyncMock()
        mock_checker.return_value.start = AsyncMock()
        mock_portfolio.return_value.start = AsyncMock()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac

    app.dependency_overrides.clear()
