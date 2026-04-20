"""Async SQLAlchemy engine with per-connection PRAGMA hook for SQLite.

Per-connection PRAGMAs applied to every pool checkout:

- journal_mode=WAL       — concurrent readers + single writer; DB-wide state
- busy_timeout=30000     — 30s wait on SQLITE_BUSY (warm path can hold the lock 10-20s)
- synchronous=NORMAL     — fsync on checkpoints only (safe with WAL)
- cache_size=-64000      — 64 MB page cache
- foreign_keys=ON        — enforce ForeignKey(..., ondelete=...) cascade rules

The PRAGMA event hook is required because busy_timeout, foreign_keys, synchronous,
and cache_size are per-connection settings in SQLite — setting them on a single
throwaway connection does NOT carry over to the pool. Without this hook those
PRAGMAs silently revert to SQLite defaults on every pool checkout.

Connection args:

- connect_args={"timeout": 30} — driver-level (aiosqlite) acquire-connection timeout.
  Distinct from PRAGMA busy_timeout; both are wanted.

Pool hardening:

- pool_pre_ping=True — validates connections before checkout (catches stale
  connections after ``./init.sh restart`` without raising to callers).
- pool_recycle=3600   — recycle connections older than 1h as a defense against
  long-lived stale handles.
"""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"timeout": 30},
)


if "sqlite" in str(engine.url):

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):  # noqa: ANN001
        """Apply per-connection PRAGMAs to every pool checkout.

        WAL persists on the DB file itself, but busy_timeout, foreign_keys,
        synchronous, and cache_size are per-connection and would otherwise
        reset to SQLite defaults on each pool connection.
        """
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


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
