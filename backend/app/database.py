"""Async database session factory and FastAPI dependency."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# busy_timeout=10000 (10s) prevents "database is locked" errors when
# the MCP server and backend warm path write concurrently to SQLite.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"timeout": 10},
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session, auto-closes on exit."""
    async with async_session_factory() as session:
        yield session


async def dispose() -> None:
    """Close all pooled connections. Called during application shutdown."""
    await engine.dispose()
