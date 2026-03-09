"""Tests for database engine configuration."""
import os
import tempfile
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def mem_engine():
    """In-memory engine with pragmas registered (for foreign_keys test)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from app.database import _register_sqlite_pragmas
    _register_sqlite_pragmas(engine)
    yield engine
    await engine.dispose()


@pytest.fixture
async def file_engine(tmp_path):
    """File-based engine — required for WAL mode (not supported on :memory:)."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    from app.database import _register_sqlite_pragmas
    _register_sqlite_pragmas(engine)
    yield engine
    await engine.dispose()
    if db_path.exists():
        os.unlink(db_path)


async def test_wal_mode_enabled(file_engine):
    """Engine must set journal_mode=WAL on connect (file DB required for WAL)."""
    async with file_engine.connect() as conn:
        result = await conn.execute(sa.text("PRAGMA journal_mode"))
        mode = result.scalar()
    assert mode == "wal"


async def test_foreign_keys_enabled(mem_engine):
    """Engine must enable foreign key enforcement."""
    async with mem_engine.connect() as conn:
        result = await conn.execute(sa.text("PRAGMA foreign_keys"))
        fk = result.scalar()
    assert fk == 1


async def test_pool_pre_ping():
    """Engine must be created with pool_pre_ping=True."""
    from app.database import engine
    assert engine.pool._pre_ping  # SQLAlchemy stores this on the pool


async def test_missing_indices_created(tmp_path):
    """_migrate_add_missing_indexes must create all 5 new indices on optimizations."""
    import app.models.optimization  # noqa: ensure model registered
    import app.models.github  # noqa
    import app.models.auth  # noqa

    db_path = tmp_path / "idx_test.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    from app.database import Base, _migrate_add_missing_indexes

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _migrate_add_missing_indexes(eng)

    async with eng.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='optimizations'")
        )
        index_names = {row[0] for row in result.fetchall()}

    expected = {
        "idx_optimizations_status",
        "idx_optimizations_overall_score",
        "idx_optimizations_primary_framework",
        "idx_optimizations_is_improvement",
        "idx_optimizations_linked_repo",
    }
    assert expected.issubset(index_names), f"Missing indices: {expected - index_names}"

    await eng.dispose()
