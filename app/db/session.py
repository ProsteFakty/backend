from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

SessionFactory = async_sessionmaker[AsyncSession]


# Lazy: building the engine at import time would fail whenever DATABASE_URL is not set (e.g. in tests).
@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().async_database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> SessionFactory:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with get_session_factory()() as session:
        yield session


async def ensure_schema() -> None:
    """No migrations yet: create missing tables, then add the columns that older databases lack.

    Safe to run on every start. Replace with alembic once the schema settles.
    """
    from app.db.models import ARTICLE_SEARCH_VECTOR, Base  # here to keep this module importable without models

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Look before altering: ALTER TABLE queues for an exclusive lock even when it has nothing to do, and
        # everything else on the table then waits behind it (e.g. while a long script holds a transaction).
        columns = set(
            (
                await conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = 'articles'")
                )
            ).scalars()
        )
        if "title" not in columns:
            await conn.execute(text("ALTER TABLE articles ADD COLUMN title text"))
        if "search_vector" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE articles ADD COLUMN search_vector tsvector "
                    f"GENERATED ALWAYS AS ({ARTICLE_SEARCH_VECTOR}) STORED"
                )
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_articles_search_vector ON articles USING gin (search_vector)")
            )
