"""Tests for the RunRow model + migration (Foundation P3, v0.4.18).

Covers spec section 9 category 1 (RunRow model + migration) — 8 tests.

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 4
"""
from __future__ import annotations

import configparser
import subprocess
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, inspect, text
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
    conn = await db.connection()
    cols = await conn.run_sync(
        lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("run_row")}
    )
    expected = {
        "id", "mode", "status", "started_at", "completed_at", "error",
        "project_id", "repo_full_name", "topic", "intent_hint",
        "prompts_generated", "prompt_results", "aggregate", "taxonomy_delta",
        "final_report", "suite_id", "topic_probe_meta", "seed_agent_meta",
    }
    assert cols == expected, f"Column mismatch: extra={cols - expected}, missing={expected - cols}"


async def test_run_row_has_4_indexes(db: AsyncSession) -> None:
    """RunRow has the 4 indexes from spec section 4.1."""
    conn = await db.connection()
    idx_names = await conn.run_sync(
        lambda sync_conn: {ix["name"] for ix in inspect(sync_conn).get_indexes("run_row")}
    )
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


def _write_temp_ini(tmp_path: Path, db_path: Path) -> Path:
    """Write a temp alembic.ini pointing sqlalchemy.url at db_path.

    Mirrors ``tests/test_probe_run_model.py``: lives in _BACKEND_DIR so
    ``script_location = %(here)s/alembic`` resolves to the real migration
    folder. UUID-suffixed name avoids xdist collisions.
    """
    cfg = configparser.ConfigParser()
    cfg.read(str(_ALEMBIC_INI))
    cfg["alembic"]["sqlalchemy.url"] = f"sqlite+aiosqlite:///{db_path}"
    ini_name = f"alembic_test_{uuid.uuid4().hex[:8]}.ini"
    ini_path = _BACKEND_DIR / ini_name
    with ini_path.open("w") as fh:
        cfg.write(fh)
    return ini_path


def _alembic(args: list[str], ini_path: Path) -> subprocess.CompletedProcess:
    """Run alembic in subprocess to avoid asyncio.run-in-running-loop conflict.

    The alembic env uses ``asyncio.run(run_async_migrations())`` internally,
    which collides with the pytest-asyncio event loop. Subprocess invocation
    sidesteps the conflict entirely.
    """
    return subprocess.run(
        ["alembic", "-c", str(ini_path), *args],
        cwd=str(_BACKEND_DIR),
        check=False,  # caller inspects returncode
        capture_output=True,
        text=True,
    )


async def test_migration_aborts_on_partial_state(tmp_path: Path) -> None:
    """Upgrade raises RuntimeError if both run_row and probe_run exist.

    Spec section 4.3 partial-state guard: the run_row migration aborts when
    it detects ``run_row`` AND ``probe_run`` both present (indicates a prior
    upgrade failed mid-flight). Strategy:

    1. Bootstrap a fresh DB and run ``alembic upgrade head`` so run_row exists.
    2. Manually CREATE TABLE probe_run to simulate the partial state.
    3. ``alembic stamp`` back to the parent revision so the next upgrade
       re-enters the migration body (rather than alembic's
       revision-pointer short-circuit).
    4. Re-run ``alembic upgrade head`` — must fail with the partial-state
       error from the migration's matched-state guard.
    """
    db_path = tmp_path / "run_row_partial.db"
    ini_path = _write_temp_ini(tmp_path, db_path)
    try:
        # Step 1: fresh upgrade end-to-end.
        first = _alembic(["upgrade", "head"], ini_path)
        assert first.returncode == 0, (
            f"Initial upgrade failed: stdout={first.stdout}\nstderr={first.stderr}"
        )

        # Step 2: simulate partial state by recreating probe_run alongside run_row.
        sync_engine = create_engine(f"sqlite:///{db_path}")
        with sync_engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE probe_run (id TEXT PRIMARY KEY, topic TEXT NOT NULL)"
            ))
        sync_engine.dispose()

        # Step 3: rewind alembic pointer to the parent revision so upgrade
        # re-enters the run_row migration body (where the matched-state
        # guard raises).
        parent_rev = "bdd8e96cf489"  # down_revision of 58510d3f6b81
        stamp_result = _alembic(["stamp", parent_rev], ini_path)
        assert stamp_result.returncode == 0, (
            f"Stamp failed: stdout={stamp_result.stdout}\nstderr={stamp_result.stderr}"
        )

        # Step 4: re-run upgrade — should fail with the partial-state guard.
        result = _alembic(["upgrade", "head"], ini_path)
        assert result.returncode != 0, (
            f"Expected alembic to fail; stdout={result.stdout}\nstderr={result.stderr}"
        )
        combined = (result.stdout + result.stderr).lower()
        assert "partial migration detected" in combined, (
            f"Expected partial-migration error; "
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    finally:
        ini_path.unlink(missing_ok=True)
