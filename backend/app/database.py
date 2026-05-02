"""Async SQLAlchemy engine + ``WriterLockedAsyncSession``.

# Per-connection PRAGMA hook (SQLite)

Per-connection PRAGMAs applied upon physical DBAPI connection creation:

- ``journal_mode=WAL``     — concurrent readers + single writer; DB-wide state
- ``busy_timeout``         — wait on SQLITE_BUSY (defense-in-depth backstop) sourced from ``Settings.DB_LOCK_TIMEOUT_SECONDS``
- ``synchronous=NORMAL``   — fsync on checkpoints only (safe with WAL)
- ``cache_size``           — page cache sourced from ``Settings.DB_CACHE_SIZE_KB``
- ``foreign_keys=ON``      — enforce ForeignKey(..., ondelete=...) cascades

The PRAGMA event hook uses the ``connect`` event because busy_timeout, foreign_keys,
synchronous, and cache_size are per-connection settings. By applying them when the
underlying SQLite connection is first opened, the PRAGMAs persist for the lifetime
of the connection, remaining active across all pool checkouts and post-commit queries.

# Writer-lock architecture

Every backend `AsyncSession` is a ``WriterLockedAsyncSession`` — a subclass that
**automatically** holds the process-wide ``db_writer_lock`` for the entire
write-transaction span (from first ``flush()`` until ``commit()`` /
``rollback()`` / ``close()``).

This eliminates SQLite "database is locked" lock storms at the architectural
layer rather than via per-call-site mutex wrapping. The lock is opaque to
business logic — every existing ``async with async_session_factory() as db:
... await db.commit()`` call site is now serialized correctly without code
changes. ``busy_timeout`` and any application-level retry loops become
defense-in-depth backstops, not load-bearing primitives.

Read-only sessions (SELECT-only, no flush) never acquire the lock — WAL
allows unlimited concurrent readers.

Reentrancy: ``flush()`` is called internally from ``commit()``. The session
tracks lock-held state via an instance flag so the inner ``flush()`` skips
re-acquisition (asyncio.Lock is not reentrant; without this guard the
session would deadlock on its own commit).

Cross-process contention (e.g. MCP server) is NOT covered — those processes
write via HTTP POST events, not direct DB writes, so they don't compete here.

# Pool hardening

- ``pool_pre_ping=True`` validates connections before checkout (catches stale
  connections after ``./init.sh restart`` without raising to callers).
- ``pool_recycle=3600`` recycles connections older than 1h.
- ``connect_args={"timeout": ...}`` — driver-level (aiosqlite) acquire-connection
  timeout, distinct from PRAGMA ``busy_timeout`` but synced to ``Settings.DB_LOCK_TIMEOUT_SECONDS``.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"timeout": settings.DB_LOCK_TIMEOUT_SECONDS},
)


if "sqlite" in str(engine.url):

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):  # noqa: ANN001
        """Apply per-connection PRAGMAs upon physical DBAPI connection creation.

        WAL persists on the DB file itself, but busy_timeout, foreign_keys,
        synchronous, and cache_size are per-connection. By applying them on the
        ``connect`` event, they persist for the lifetime of the physical DBAPI
        handle, remaining active across all subsequent pool checkouts and
        post-commit transactions.

        ``busy_timeout`` is a defense-in-depth backstop. The primary
        write-contention defense is ``WriterLockedAsyncSession`` (see below)
        which holds ``db_writer_lock`` across the entire write transaction.
        With the lock in place, no write should ever wait for the SQLite
        writer slot — the lock funnels writers one at a time.
        """
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.DB_LOCK_TIMEOUT_SECONDS * 1000}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA cache_size={settings.DB_CACHE_SIZE_KB}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Process-wide writer mutex.  See ``WriterLockedAsyncSession`` below for the
# automatic-acquisition contract; manual ``async with db_writer_lock:`` wrapping
# is no longer needed for routine write paths (it's redundant — the session
# subclass handles it).
db_writer_lock = asyncio.Lock()


class WriterLockedAsyncSession(AsyncSession):
    """``AsyncSession`` that auto-acquires ``db_writer_lock`` for the
    entire write-transaction span.

    Lock lifecycle:

    1. First ``flush()`` (explicit OR autoflush triggered by a query) acquires
       the lock if not already held.
    2. ``commit()`` / ``rollback()`` releases the lock after the underlying
       SQLAlchemy operation completes.
    3. ``close()`` is a safety net: if a session closes without an explicit
       commit/rollback (e.g. async-context-manager exit on an exception), the
       lock is released here.

    The lock-held state is tracked on the session instance so reentrant calls
    (``commit()`` internally calls ``flush()``) skip re-acquisition. This is
    necessary because ``asyncio.Lock`` is not reentrant — a naive subclass that
    locked both ``flush`` and ``commit`` independently would deadlock on its
    own ``commit()`` call.

    Read-only sessions (no flush ever fires) never acquire the lock —
    SQLite WAL allows unlimited concurrent readers.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._writer_lock_held: bool = False

    async def _acquire_writer_lock(self) -> None:
        if not self._writer_lock_held:
            await db_writer_lock.acquire()
            self._writer_lock_held = True

    def _release_writer_lock(self) -> None:
        if self._writer_lock_held:
            try:
                db_writer_lock.release()
            except RuntimeError:
                # Lock was already released (e.g. by close() after rollback).
                # Benign — the goal is "lock is not held"; we don't care which
                # path released it.
                logger.debug("db_writer_lock already released")
            finally:
                self._writer_lock_held = False

    async def flush(self, objects: Any = None) -> Any:  # type: ignore[override]
        # Only acquire the writer lock when there are actually pending
        # changes. SQLAlchemy issues an autoflush before SELECT queries
        # on every session -- including read-only ones. Without this
        # gate, a session that does SELECTs only would acquire the lock
        # at the first autoflush and HOLD it until close() (we only
        # release on commit/rollback/close). That starves any other
        # writer in the process for the read-session's full lifetime.
        # Read-only paths must not acquire the writer lock; only paths
        # that actually mutate state.
        has_pending = bool(self.new or self.dirty or self.deleted)
        if has_pending:
            await self._acquire_writer_lock()
        return await super().flush(objects)

    async def commit(self) -> None:  # type: ignore[override]
        try:
            return await super().commit()
        finally:
            self._release_writer_lock()

    async def rollback(self) -> None:  # type: ignore[override]
        try:
            return await super().rollback()
        finally:
            self._release_writer_lock()

    async def close(self) -> None:  # type: ignore[override]
        # Safety net: a session entered a write transaction but exited without
        # explicit commit/rollback (e.g. async-context-manager `__aexit__` on
        # exception). Release the lock to prevent process-wide writer
        # starvation.
        try:
            return await super().close()
        finally:
            self._release_writer_lock()


async_session_factory = async_sessionmaker(
    engine, class_=WriterLockedAsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session, auto-closes on exit."""
    async with async_session_factory() as session:
        yield session


async def dispose() -> None:
    """Close all pooled connections. Called during application shutdown.
    
    Performs an explicit WAL checkpoint to ensure the WAL file is merged
    and truncated. This prevents silent WAL checkpoint loss if the process
    is terminated abruptly after disposal but before SQLite can perform
    its auto-checkpoint-on-close.
    """
    try:
        from sqlalchemy import text
        import sqlalchemy.exc
        
        async with engine.begin() as conn:
            # TRUNCATE ensures the WAL file is truncated to zero bytes
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            logger.info("Executed explicit SQLite WAL checkpoint on shutdown")
    except Exception as exc:
        # If the DB is completely locked by an orphaned connection, we catch
        # the error and proceed to dispose anyway.
        logger.warning("Explicit WAL checkpoint failed (likely orphaned active connections): %s", exc)
        
    await engine.dispose()
