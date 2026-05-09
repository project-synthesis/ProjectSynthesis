"""Tests for _gc_orphan_runs (Foundation P3, v0.4.18) — 4 tests, cat 10.

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 5
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.6

Pins the contract for the unified ``_gc_orphan_runs`` sweep that
supersedes ``_gc_orphan_probe_runs`` — sweeps both ``topic_probe`` and
``seed_agent`` mode rows whose ``status='running'`` predates
``RUN_ORPHAN_TTL_HOURS`` ago.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, RunRow

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Per-test in-memory SQLite session with full schema applied."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


async def test_gc_orphan_runs_marks_stale_running_rows_failed(db: AsyncSession) -> None:
    """Rows in status='running' for >TTL hours are marked failed."""
    from app.services.gc import RUN_ORPHAN_TTL_HOURS, _gc_orphan_runs
    cutoff = datetime.utcnow() - timedelta(hours=RUN_ORPHAN_TTL_HOURS + 1)

    db.add(RunRow(id="orphan-1", mode="topic_probe", status="running", started_at=cutoff))
    db.add(RunRow(id="fresh-1", mode="topic_probe", status="running", started_at=datetime.utcnow()))
    await db.commit()

    n = await _gc_orphan_runs(db)
    await db.commit()

    assert n == 1
    orphan = await db.get(RunRow, "orphan-1")
    fresh = await db.get(RunRow, "fresh-1")
    assert orphan is not None
    assert fresh is not None
    assert orphan.status == "failed"
    assert orphan.error == "orphaned (ttl exceeded)"
    assert fresh.status == "running"


async def test_gc_orphan_runs_includes_seed_mode(db: AsyncSession) -> None:
    """Both topic_probe and seed_agent rows are swept."""
    from app.services.gc import RUN_ORPHAN_TTL_HOURS, _gc_orphan_runs
    cutoff = datetime.utcnow() - timedelta(hours=RUN_ORPHAN_TTL_HOURS + 1)

    db.add(RunRow(id="probe-orphan", mode="topic_probe", status="running", started_at=cutoff))
    db.add(RunRow(id="seed-orphan", mode="seed_agent", status="running", started_at=cutoff))
    await db.commit()

    n = await _gc_orphan_runs(db)
    await db.commit()

    assert n == 2


async def test_gc_orphan_runs_returns_zero_when_no_orphans(db: AsyncSession) -> None:
    from app.services.gc import _gc_orphan_runs
    n = await _gc_orphan_runs(db)
    assert n == 0


async def test_probe_orphan_ttl_hours_is_alias_of_run_orphan_ttl_hours(db: AsyncSession) -> None:
    """Backward-compat alias for the constant rename."""
    from app.services.gc import PROBE_ORPHAN_TTL_HOURS, RUN_ORPHAN_TTL_HOURS
    assert PROBE_ORPHAN_TTL_HOURS is RUN_ORPHAN_TTL_HOURS or \
           PROBE_ORPHAN_TTL_HOURS == RUN_ORPHAN_TTL_HOURS
