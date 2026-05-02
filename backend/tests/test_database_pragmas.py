"""Verify SQLite PRAGMA event hook fires on every pool checkout.

Regression test for the PR #1 audit finding: the previous implementation
opened a single throwaway aiosqlite connection at startup, set WAL +
busy_timeout, and closed it. busy_timeout, foreign_keys, synchronous,
and cache_size are per-connection PRAGMAs — they did NOT carry over to
the pool. Tests here assert that every pool checkout sees the PRAGMAs
set, which is the contract the event listener in ``app/database.py``
establishes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _register_hook(engine) -> None:
    """Mirror the hook from ``app/database.py`` against a test engine."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.DB_LOCK_TIMEOUT_SECONDS * 1000}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA cache_size={settings.DB_CACHE_SIZE_KB}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest.mark.asyncio
async def test_pragma_hook_applies_to_every_pool_checkout(tmp_path) -> None:
    """Two independent sessions both see busy_timeout + foreign_keys set."""
    db_file = tmp_path / "pragma_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={"timeout": settings.DB_LOCK_TIMEOUT_SECONDS},
    )
    _register_hook(engine)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        for _ in range(2):
            async with factory() as session:
                busy = (await session.execute(text("PRAGMA busy_timeout"))).scalar()
                fk = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
                sync = (await session.execute(text("PRAGMA synchronous"))).scalar()
                expected_busy = settings.DB_LOCK_TIMEOUT_SECONDS * 1000
                assert busy == expected_busy, f"busy_timeout not propagated to pool: {busy}"
                assert fk == 1, f"foreign_keys not enabled on pool connection: {fk}"
                # SQLite returns 1 for NORMAL in PRAGMA synchronous
                assert sync == 1, f"synchronous not NORMAL: {sync}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_journal_mode_is_wal(tmp_path) -> None:
    """journal_mode=WAL is set DB-wide and surfaces on every connection."""
    db_file = tmp_path / "wal_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
        pool_pre_ping=True,
        connect_args={"timeout": settings.DB_LOCK_TIMEOUT_SECONDS},
    )
    _register_hook(engine)
    try:
        async with engine.connect() as conn:
            mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
            assert str(mode).lower() == "wal", f"journal_mode not WAL: {mode}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_foreign_keys_enforced(tmp_path) -> None:
    """PRAGMA foreign_keys=ON actually makes the FK constraint raise."""
    db_file = tmp_path / "fk_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
        pool_pre_ping=True,
        connect_args={"timeout": settings.DB_LOCK_TIMEOUT_SECONDS},
    )
    _register_hook(engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE parent (id INTEGER PRIMARY KEY)"
            ))
            await conn.execute(text(
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY, "
                "  parent_id INTEGER NOT NULL, "
                "  FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE CASCADE"
                ")"
            ))

        async with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                await conn.execute(text(
                    "INSERT INTO child (id, parent_id) VALUES (1, 999)"
                ))
    finally:
        await engine.dispose()


def test_app_database_engine_has_pool_hardening() -> None:
    """``app.database.engine`` uses pool_pre_ping + pool_recycle.

    Regression guard for the PR #1 audit finding: those two pool options
    were dropped during the v2 rebuild, letting stale connections reach
    callers without validation. Revealed as a crash after ``./init.sh
    restart`` while holding an existing session.
    """
    from app.database import engine

    pool = engine.pool
    # SQLAlchemy's pool.pre_ping attribute is set from pool_pre_ping=True
    assert getattr(pool, "_pre_ping", False) is True, (
        "pool_pre_ping is not enabled on app.database.engine"
    )
    # recycle is stored in seconds; we configured 3600s
    recycle = getattr(pool, "_recycle", None)
    assert recycle == 3600, f"pool_recycle not set to 3600: {recycle}"


@pytest.mark.asyncio
async def test_app_database_engine_applies_pragmas_on_checkout() -> None:
    """The live ``app.database.engine`` fires the PRAGMA hook on each checkout."""
    from app.database import async_session_factory

    async with async_session_factory() as session:
        fk = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
        busy = (await session.execute(text("PRAGMA busy_timeout"))).scalar()
        expected_busy = settings.DB_LOCK_TIMEOUT_SECONDS * 1000
        assert fk == 1, f"foreign_keys not enabled on live engine: {fk}"
        assert busy == expected_busy, f"busy_timeout not {expected_busy} on live engine: {busy}"

@pytest.mark.asyncio
async def test_pragmas_persist_across_post_commit_query_pattern(tmp_path) -> None:
    """A session retains its PRAGMAs for follow-up queries after a commit().

    Verifies the prompt's concern: when a transaction commits and the DBAPI
    connection is returned to the pool, the subsequent auto-begun transaction
    (which checks out a connection again) still has all PRAGMAs active,
    whether it receives the same physical connection or a new one.
    """
    db_file = tmp_path / "post_commit_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        echo=False,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        connect_args={"timeout": settings.DB_LOCK_TIMEOUT_SECONDS},
    )
    _register_hook(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            # First transaction
            busy1 = (await session.execute(text("PRAGMA busy_timeout"))).scalar()
            expected_busy = settings.DB_LOCK_TIMEOUT_SECONDS * 1000
            assert busy1 == expected_busy

            await session.commit()

            # Post-commit query pattern (triggers new checkout)
            busy2 = (await session.execute(text("PRAGMA busy_timeout"))).scalar()
            assert busy2 == expected_busy
    finally:
        await engine.dispose()
