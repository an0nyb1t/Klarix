from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Called on app startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def migrate_v12() -> None:
    """Add V1.2 columns to conversations table if they don't already exist."""
    new_columns = [
        ("llm_provider", "ALTER TABLE conversations ADD COLUMN llm_provider TEXT"),
        ("llm_model", "ALTER TABLE conversations ADD COLUMN llm_model TEXT"),
        ("summary", "ALTER TABLE conversations ADD COLUMN summary TEXT"),
        (
            "summarized_message_count",
            "ALTER TABLE conversations ADD COLUMN summarized_message_count INTEGER DEFAULT 0",
        ),
    ]

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(conversations)"))
        existing = {row[1] for row in result.fetchall()}
        for col_name, stmt in new_columns:
            if col_name not in existing:
                await conn.execute(text(stmt))
