"""
Test configuration — uses in-memory SQLite so tests don't touch the real DB.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

import database
import rate_limiter
from database import Base


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def test_db_engine(monkeypatch):
    """
    Create a fresh in-memory DB engine for each test.
    Patches the global engine so all modules use the test DB.
    """
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    test_session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Patch the global engine and session factory
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "AsyncSessionLocal", test_session_factory)

    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Clear in-memory rate limit cache so tests don't bleed into each other
    rate_limiter.clear_cache()

    yield test_engine

    # Teardown
    rate_limiter.clear_cache()
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest_asyncio.fixture
async def db(test_db_engine):
    """Provide a test DB session."""
    async with async_sessionmaker(
        test_db_engine, class_=AsyncSession, expire_on_commit=False
    )() as session:
        yield session
