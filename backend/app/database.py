"""Async SQLAlchemy engine + ``WriterLockedAsyncSession``.

# Per-connection PRAGMA hook (SQLite)

Per-connection PRAGMAs applied to every pool checkout:

- ``journal_mode=WAL``     — concurrent readers + single writer; DB-wide state
- ``busy_timeout=30000``   — 30s wait on SQLITE_BUSY (defense-in-depth backstop)
- ``synchronous=NORMAL``   — fsync on checkpoints only (safe with WAL)
- ``cache_size=-64000``    — 64 MB page cache
- ``foreign_keys=ON``      — enforce ForeignKey(..., ondelete=...) cascades

The PRAGMA event hook is required because busy_timeout, foreign_keys,
synchronous, and cache_size are per-connection settings — setting them on a
single throwaway connection does NOT carry over to the pool. Without this
hook those PRAGMAs silently revert to SQLite defaults on every pool checkout.

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
- ``connect_args={"timeout": 30}`` — driver-level (aiosqlite) acquire-connection
  timeout, distinct from PRAGMA ``busy_timeout``.
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
    # v0.4.12 P2: pool_size=1 forces strict connection serialization
    # at the SQLAlchemy pool layer, eliminating multi-connection
    # SQLite WAL writer-slot contention.  Pre-fix: SQLAlchemy's default
    # async pool (size=5, overflow=10, up to 15 concurrent conns) let
    # multiple sessions check out simultaneously, each holding its own
    # SQLite connection.  ``WriterLockedAsyncSession`` correctly
    # serialized FLUSH calls via ``db_writer_lock`` (asyncio.Lock) but
    # the underlying connections still raced for the WAL writer slot at
    # SQLite's libsqlite3 layer -- a connection that just released the
    # asyncio.Lock could still hold lingering WAL state for a brief
    # window, causing the next writer to see "database is locked"
    # despite holding the asyncio.Lock.  pool_size=1 collapses that
    # window: only one connection exists, so transitions between
    # writers happen at the pool checkout boundary which is fully
    # synchronous (queue-and-wait) rather than racing at the SQLite
    # file lock.  Trade-off: read concurrency is also limited to 1,
    # but SQLite is single-threaded internally so concurrent reads
    # were time-sliced anyway -- aggregate throughput is unchanged.
    # The pool checkout queue is async, so sessions await without
    # blocking the event loop.  Diagnostic chain (probes v22-v28):
    # six failed catastrophic runs through every other layer of the
    # writer-coordination stack (verify gate, per-prompt streaming,
    # warm-path Groundhog Day fix, early-abort) confirmed the
    # contention is at the connection/pool layer rather than higher up.
    pool_size=1,
    max_overflow=0,
    connect_args={"timeout": 30},
)


if "sqlite" in str(engine.url):

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):  # noqa: ANN001
        """Apply per-connection PRAGMAs to every pool checkout.

        WAL persists on the DB file itself, but busy_timeout, foreign_keys,
        synchronous, and cache_size are per-connection and would otherwise
        reset to SQLite defaults on each pool connection.

        ``busy_timeout=30s`` is a defense-in-depth backstop. The primary
        write-contention defense is ``WriterLockedAsyncSession`` (see below)
        which holds ``db_writer_lock`` across the entire write transaction.
        With the lock in place, no write should ever wait for the SQLite
        writer slot — the lock funnels writers one at a time.
        """
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
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
    """Close all pooled connections. Called during application shutdown."""
    await engine.dispose()
