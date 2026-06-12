"""Async SQLAlchemy engines (read + writer) for Project Synthesis.

# Per-connection PRAGMA hook (SQLite)

Per-connection PRAGMAs applied upon physical DBAPI connection creation:

- ``journal_mode=WAL``     — concurrent readers + single writer; DB-wide state
- ``busy_timeout``         — wait on SQLITE_BUSY (backstop), value from
  ``Settings.DB_LOCK_TIMEOUT_SECONDS``
- ``synchronous=NORMAL``   — fsync on checkpoints only (safe with WAL)
- ``cache_size``           — page cache sourced from ``Settings.DB_CACHE_SIZE_KB``
- ``foreign_keys=ON``      — enforce ForeignKey(..., ondelete=...) cascades

The PRAGMA event hook uses the ``connect`` event because busy_timeout, foreign_keys,
synchronous, and cache_size are per-connection settings. By applying them when the
underlying SQLite connection is first opened, the PRAGMAs persist for the lifetime
of the connection, remaining active across all pool checkouts and post-commit queries.

# Write architecture (v0.4.13+, class retired v0.4.36)

ALL writes route through the ``WriteQueue`` worker against the dedicated
``writer_engine`` (pool_size=1) — see ``services/write_queue.py``. The
read engine serves SELECT-only sessions; WAL allows unlimited concurrent
readers. The read-engine audit hook (below) RAISEs on any drift write
outside the ``migration_mode`` / ``cold_path_mode`` allow-list
(``WRITE_QUEUE_AUDIT_HOOK_RAISE``, default True since v0.4.22).

The legacy v0.4.13 defense-in-depth flush-serializer session class was
retired in v0.4.36 after its time gate passed (RAISE-in-prod since
2026-05-16 with zero audit events and zero "database is locked" errors —
it was dead code serializing every read-engine flush for no benefit; see
CHANGELOG v0.4.36).

# Pool hardening

- ``pool_pre_ping=True`` validates connections before checkout (catches stale
  connections after ``./init.sh restart`` without raising to callers).
- ``pool_recycle=3600`` recycles connections older than 1h.
- ``connect_args={"timeout": ...}`` — driver-level (aiosqlite) acquire-connection
  timeout, distinct from PRAGMA ``busy_timeout`` but synced to ``Settings.DB_LOCK_TIMEOUT_SECONDS``.
"""

import logging
import re
import traceback
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass

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
        write-contention defense is the single-writer ``WriteQueue`` (see
        ``services/write_queue.py``) — within this process no two writers
        ever compete for the SQLite writer slot.
        """
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.DB_LOCK_TIMEOUT_SECONDS * 1000}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA cache_size={settings.DB_CACHE_SIZE_KB}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


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

        async with engine.begin() as conn:
            # TRUNCATE ensures the WAL file is truncated to zero bytes
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            logger.info("Executed explicit SQLite WAL checkpoint on shutdown")
    except Exception as exc:
        # If the DB is completely locked by an orphaned connection, we catch
        # the error and proceed to dispose anyway.
        logger.warning("Explicit WAL checkpoint failed (likely orphaned active connections): %s", exc)

    await engine.dispose()


# ---------------------------------------------------------------------------
# Writer engine + audit hook (v0.4.13)
# ---------------------------------------------------------------------------
# See docs/specs/sqlite-writer-queue-2026-05-02.md for full design rationale.
#
# Architecture: a SECOND engine (`writer_engine`) with `pool_size=1,
# max_overflow=0` is used exclusively by the WriteQueue worker. The main
# `engine` above stays unchanged for read paths. This eliminates within-
# backend WAL writer-slot races that defeated the v0.4.12 stack
# (busy_timeout + the legacy flush-serializer session class + per-callsite
# mutex + retries; that class was retired in v0.4.36).
#
# Read paths continue to use `async_session_factory()` against the main
# engine. WAL allows unlimited concurrent readers — read-side concurrency
# is preserved.


writer_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=1,
    max_overflow=0,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"timeout": settings.DB_LOCK_TIMEOUT_SECONDS},
)


if "sqlite" in str(writer_engine.url):

    @event.listens_for(writer_engine.sync_engine, "connect")
    def _set_writer_pragmas(dbapi_conn, _connection_record):  # noqa: ANN001
        """Mirror the read engine's PRAGMA setup for the writer connection."""
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.DB_LOCK_TIMEOUT_SECONDS * 1000}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA cache_size={settings.DB_CACHE_SIZE_KB}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


writer_session_factory = async_sessionmaker(
    writer_engine, class_=AsyncSession, expire_on_commit=False,
)


@dataclass
class _ReadEngineMeta:
    """Allow-list flags for read-engine writes that ARE expected.

    Invariant: at most ONE flag may be True at a time. Set/cleared in
    try/finally blocks at their respective entry points (lifespan
    migrations + cold-path full-refit).
    """
    migration_mode: bool = False
    cold_path_mode: bool = False


read_engine_meta = _ReadEngineMeta()


class WriteOnReadEngineError(RuntimeError):
    """Raised by audit hook when a write is detected on the read engine
    outside of the migration_mode/cold_path_mode allow-list.
    """


def _is_write_statement(statement: str) -> bool:
    """Detect SQL writes including REPLACE, prefixed CTEs, block AND line comments.

    Catches: INSERT, UPDATE, DELETE, REPLACE, WITH ... INSERT/UPDATE/DELETE/REPLACE.
    Does NOT match: SELECT, PRAGMA, BEGIN, COMMIT, ROLLBACK, SAVEPOINT, RELEASE.
    """
    s = statement
    while True:
        s2 = re.sub(
            r"^\s*(?:/\*.*?\*/|--[^\n]*\n?)\s*",
            "",
            s,
            flags=re.DOTALL,
        )
        if s2 == s:
            break
        s = s2
    upper = s.upper().lstrip()
    if upper.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
        return True
    if upper.startswith("WITH"):
        return bool(re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", upper))
    return False


# Module-level so uninstall can reach the listener; idempotency guard.
_audit_listener: Callable | None = None
_audit_installed_engine = None


def install_read_engine_audit_hook(target_engine) -> None:  # noqa: ANN001
    """Register a before_cursor_execute hook on the read engine that catches
    writes outside of the allow-list flags.

    Idempotent: raises RuntimeError if already installed.

    Bypass conditions: `migration_mode=True` OR `cold_path_mode=True`.
    Asserts only ONE flag is set at a time (dual-flag invariant always raises,
    REGARDLESS of WRITE_QUEUE_AUDIT_HOOK_RAISE — programmer error, not write).

    Behavior on detected write:
      WRITE_QUEUE_AUDIT_HOOK_RAISE=True  (CI): raises WriteOnReadEngineError
      WRITE_QUEUE_AUDIT_HOOK_RAISE=False (dev/prod): logs WARNING
    """
    global _audit_listener, _audit_installed_engine
    if _audit_listener is not None:
        raise RuntimeError(
            "read engine audit hook already installed; "
            "call uninstall_read_engine_audit_hook() first"
        )

    def _audit(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if read_engine_meta.migration_mode and read_engine_meta.cold_path_mode:
            raise RuntimeError(
                "read_engine_meta: both migration_mode and cold_path_mode True; "
                "this is a programmer error, not a write-detection event"
            )
        if read_engine_meta.migration_mode or read_engine_meta.cold_path_mode:
            return
        if not _is_write_statement(statement):
            return

        # Cycle 9.6 diagnostic: capture call site so each WARN line tells you
        # the exact source path. We retain BOTH a wider framework-filtered
        # tail (for the "expected" stack — the application code that called
        # SQLAlchemy) AND the raw deepest 5 frames (for cases where the
        # write originates inside SQLAlchemy code generation, e.g. the
        # ``<string>:2 commit`` autoflush path that has no application
        # frame above the ORM internals). Single-block warning (one log
        # call) keeps grep -A 12 'read-engine audit:' parsing trivial.
        stack = traceback.extract_stack(limit=64)
        framework_markers = (
            "sqlalchemy/",
            "_aexit_",
            "/aiosqlite/",
            "alembic/",
        )
        app_frames = [
            f for f in stack[:-1]  # skip our own _audit frame
            if not any(m in f.filename for m in framework_markers)
        ]
        # Take the deepest 8 application frames so the chain shows
        # router → service → ORM call site.
        site_frames = app_frames[-8:] if len(app_frames) > 8 else app_frames
        site_app = "\n".join(
            f"  {f.filename}:{f.lineno} {f.name}" for f in site_frames
        )
        # Also include raw deepest 5 frames so we never lose the ORM
        # call-site info. Useful for diagnosing autoflush leaks where
        # the application frame is several layers above SQLAlchemy.
        raw_frames = stack[-6:-1] if len(stack) > 6 else stack[:-1]
        site_raw = "\n".join(
            f"  RAW {f.filename}:{f.lineno} {f.name}" for f in raw_frames
        )
        err_msg = (
            f"write statement on read engine outside allow-list: "
            f"{statement[:120]}...\n{site_app}\n{site_raw}"
        )
        err = WriteOnReadEngineError(err_msg)
        if settings.WRITE_QUEUE_AUDIT_HOOK_RAISE:
            raise err
        logger.warning("read-engine audit:\n%s", err_msg)

    event.listen(target_engine.sync_engine, "before_cursor_execute", _audit)
    _audit_listener = _audit
    _audit_installed_engine = target_engine


def uninstall_read_engine_audit_hook() -> None:
    """Remove the hook on lifespan shutdown / test fixture teardown.
    Idempotent on already-uninstalled.
    """
    global _audit_listener, _audit_installed_engine
    if _audit_listener is None or _audit_installed_engine is None:
        return
    event.remove(
        _audit_installed_engine.sync_engine,
        "before_cursor_execute",
        _audit_listener,
    )
    _audit_listener = None
    _audit_installed_engine = None


async def dispose_writer() -> None:
    """Close writer engine pool. Called during application shutdown
    AFTER the WriteQueue worker has fully drained.
    """
    await writer_engine.dispose()
