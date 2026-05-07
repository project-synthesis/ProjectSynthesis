"""Tests for the RunRow model + migration (Foundation P3, v0.4.18).

Covers spec section 9 category 1 (RunRow model + migration) — 8 tests.

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 4
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, ProbeRun, RunRow

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


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


async def test_run_row_table_has_all_18_columns(db: AsyncSession) -> None:
    """RunRow table has all expected columns from spec section 4.1."""
    bind = (await db.connection()).engine
    inspector = inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("run_row")}
    expected = {
        "id", "mode", "status", "started_at", "completed_at", "error",
        "project_id", "repo_full_name", "topic", "intent_hint",
        "prompts_generated", "prompt_results", "aggregate", "taxonomy_delta",
        "final_report", "suite_id", "topic_probe_meta", "seed_agent_meta",
    }
    assert cols == expected, f"Column mismatch: extra={cols - expected}, missing={expected - cols}"


async def test_run_row_has_4_indexes(db: AsyncSession) -> None:
    """RunRow has the 4 indexes from spec section 4.1."""
    bind = (await db.connection()).engine
    inspector = inspect(bind)
    idx_names = {ix["name"] for ix in inspector.get_indexes("run_row")}
    expected = {
        "ix_run_row_mode_started",
        "ix_run_row_status_started",
        "ix_run_row_project_id",
        "ix_run_row_topic",
    }
    assert expected.issubset(idx_names), f"Missing indexes: {expected - idx_names}"


async def test_run_row_status_accepts_all_4_values(db: AsyncSession) -> None:
    """RunRow.status takes 4 values: running, completed, failed, partial."""
    from datetime import datetime

    from sqlalchemy import select

    for status in ("running", "completed", "failed", "partial"):
        row = RunRow(
            id=f"test-{status}", mode="topic_probe", status=status,
            started_at=datetime.utcnow(),
        )
        db.add(row)
    await db.commit()
    rows = (await db.execute(select(RunRow.id, RunRow.status).order_by(RunRow.id))).all()
    statuses = {r.status for r in rows}
    assert statuses == {"running", "completed", "failed", "partial"}


async def test_run_row_topic_probe_meta_roundtrips(db: AsyncSession) -> None:
    """JSON metadata stores and retrieves intact."""
    from datetime import datetime
    row = RunRow(
        id="meta-1", mode="topic_probe", status="running",
        started_at=datetime.utcnow(),
        topic_probe_meta={"scope": "**/*.py", "commit_sha": "abc123"},
    )
    db.add(row)
    await db.commit()
    fetched = await db.get(RunRow, "meta-1")
    assert fetched.topic_probe_meta == {"scope": "**/*.py", "commit_sha": "abc123"}


async def test_run_row_seed_agent_meta_roundtrips(db: AsyncSession) -> None:
    """seed_agent_meta accepts the spec-defined shape."""
    from datetime import datetime
    seed_meta = {
        "project_description": "test desc",
        "workspace_path": "/tmp/x",
        "agents": ["a1", "a2"],
        "prompt_count": 30,
        "prompts_provided": False,
        "batch_id": "batch-uuid",
        "tier": "internal",
        "estimated_cost_usd": 1.23,
    }
    row = RunRow(
        id="seed-meta-1", mode="seed_agent", status="running",
        started_at=datetime.utcnow(), seed_agent_meta=seed_meta,
    )
    db.add(row)
    await db.commit()
    fetched = await db.get(RunRow, "seed-meta-1")
    assert fetched.seed_agent_meta == seed_meta


async def test_probe_run_alias_default_mode_is_topic_probe(db: AsyncSession) -> None:
    """ProbeRun(...) sets mode='topic_probe' by default (legacy-compat).

    Per spec section 10.1 option (b): ProbeRun is a Python subclass of RunRow
    that defaults mode='topic_probe' in __init__. PR1 has zero seed_agent
    rows (the seed dispatch doesn't go through RunOrchestrator until PR2),
    so the lack of select-time filter is safe transient.
    """
    from datetime import datetime

    row = ProbeRun(id="probe-default", started_at=datetime.utcnow(),
                   topic_probe_meta={"scope": "**/*", "commit_sha": None})
    assert row.mode == "topic_probe"


async def test_probe_run_property_accessors_read_topic_probe_meta(db: AsyncSession) -> None:
    """Legacy .scope / .commit_sha access paths work via property accessors."""
    from datetime import datetime

    row = ProbeRun(
        id="probe-props", started_at=datetime.utcnow(),
        topic_probe_meta={"scope": "src/**/*.py", "commit_sha": "abc123"},
    )
    assert row.scope == "src/**/*.py"
    assert row.commit_sha == "abc123"

    # Defaults when topic_probe_meta is empty
    bare = ProbeRun(id="probe-bare", started_at=datetime.utcnow())
    assert bare.scope == "**/*"  # default fallback
    assert bare.commit_sha is None


async def test_migration_aborts_on_partial_state(db: AsyncSession) -> None:
    """Upgrade raises RuntimeError if both run_row and probe_run exist."""
    bind = (await db.connection()).engine
    # Recreate a probe_run table to simulate partial state
    async with bind.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE probe_run (id TEXT PRIMARY KEY, topic TEXT NOT NULL)"
        ))
    # Re-run upgrade — should raise
    from alembic.config import Config

    from alembic import command
    cfg = Config(str(_ALEMBIC_INI))
    with pytest.raises(RuntimeError, match="partial migration detected"):
        command.upgrade(cfg, "head")
    # Cleanup
    async with bind.begin() as conn:
        await conn.execute(text("DROP TABLE probe_run"))
