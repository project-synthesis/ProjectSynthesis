"""Tests for ProbeRun SQLAlchemy model + idempotent migration (Topic Probe Tier 1).

AC-C2-1 through AC-C2-4 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 2.

Migration-idempotency strategy mirrors ``tests/migrations/test_template_migration.py``
and ``tests/migrations/test_hotpath_indices_migration.py``: a temp ``alembic.ini``
points to an isolated tmp_path SQLite file, we apply ``alembic upgrade head``
twice, and assert via ``inspect(conn).get_table_names()`` per the spec § 4.3
established codebase idiom (NOT ``inspector.has_table()``, which is not used
elsewhere in the migrations).
"""
from __future__ import annotations

import configparser
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProbeRun

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


_PROBE_RUN_FIELDS_EXPECTED = {
    "id", "topic", "scope", "intent_hint", "repo_full_name", "project_id",
    "commit_sha", "started_at", "completed_at", "prompts_generated",
    "prompt_results", "aggregate", "taxonomy_delta", "final_report",
    "status", "suite_id", "error",
}


# ---------------------------------------------------------------------------
# Migration helpers (mirror tests/migrations/test_template_migration.py)
# ---------------------------------------------------------------------------

def _write_temp_ini(tmp_path: Path, db_path: Path) -> Path:
    """Write a temp alembic.ini pointing sqlalchemy.url at db_path.

    Lives in _BACKEND_DIR so ``script_location = %(here)s/alembic`` resolves
    to the real migration folder. UUID-suffixed name avoids xdist collisions.
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
    return subprocess.run(
        ["alembic", "-c", str(ini_path), *args],
        cwd=str(_BACKEND_DIR),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def fresh_db(tmp_path):
    """Bootstrap a new SQLite DB with no migrations applied yet."""
    db_path = tmp_path / "probe_run.db"
    ini_path = _write_temp_ini(tmp_path, db_path)
    try:
        engine = create_engine(f"sqlite:///{db_path}")
        yield engine, ini_path
        engine.dispose()
    finally:
        ini_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProbeRunModel:
    def test_proberun_has_all_17_columns(self):
        """AC-C2-1: ProbeRun declared with all 17 columns + 2 indexes."""
        cols = {c.name for c in ProbeRun.__table__.columns}
        assert cols == _PROBE_RUN_FIELDS_EXPECTED, (
            f"Missing={_PROBE_RUN_FIELDS_EXPECTED - cols}, "
            f"extra={cols - _PROBE_RUN_FIELDS_EXPECTED}"
        )
        # 2 indexes per spec § 4.3
        index_names = {ix.name for ix in ProbeRun.__table__.indexes}
        assert "ix_probe_run_status_started" in index_names
        assert "ix_probe_run_project_id" in index_names

    def test_migration_idempotent(self, fresh_db):
        """AC-C2-2: Re-running migration on already-migrated DB does not raise.

        Per spec § 4.3: uses ``inspector.get_table_names()`` guard — the
        established codebase idiom; ``inspector.has_table()`` is NOT used
        elsewhere in the migrations.

        Pattern (per ``test_template_migration.py:test_migration_is_idempotent``):
        ``alembic upgrade head`` twice with a ``stamp`` rewind between, so the
        second upgrade actually re-enters the migration body and exercises the
        in-function ``inspector.get_table_names()`` guard rather than alembic's
        revision-pointer short-circuit.
        """
        engine, ini_path = fresh_db
        # First upgrade: applies the migration end-to-end.
        _alembic(["upgrade", "head"], ini_path)

        # Rewind the alembic revision pointer to the immediate ancestor without
        # touching schema/data so the next ``upgrade head`` re-enters upgrade().
        # Captured dynamically to remain robust across future migrations stacked
        # on top of the probe_run revision.
        history = subprocess.run(
            ["alembic", "-c", str(ini_path), "history", "--rev-range", "base:head"],
            cwd=str(_BACKEND_DIR),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        # Find the line referencing "add probe_run" — its left-side is the
        # probe_run revision, ``-> <rev>`` direction. Walk one back to get the
        # parent. The format is ``<down_rev> -> <up_rev>, <message>``.
        probe_line = next(
            (ln for ln in history.splitlines() if "probe_run" in ln.lower()),
            None,
        )
        assert probe_line is not None, (
            f"Could not find probe_run migration in alembic history:\n{history}"
        )
        # ``<down> -> <up>, ...``: split on " -> " then on "," to isolate <down>.
        down_rev = probe_line.split("->", 1)[0].strip()

        _alembic(["stamp", down_rev], ini_path)
        # Second upgrade: must converge without raising despite probe_run
        # already existing — the in-function inspector.get_table_names() guard
        # short-circuits create_table.
        _alembic(["upgrade", "head"], ini_path)

        # Assert via inspector.get_table_names() per spec § 4.3.
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert "probe_run" in tables, (
            f"probe_run table missing after two upgrades: {sorted(tables)}"
        )

    @pytest.mark.asyncio
    async def test_proberun_round_trip(self, db_session: AsyncSession):
        """AC-C2-3: Insert, query, JSON fields persist correctly."""
        row = ProbeRun(
            id="test-probe-1",
            topic="embedding cache invalidation",
            scope="**/*",
            intent_hint="audit",
            repo_full_name="owner/repo",
            project_id=None,
            commit_sha="abc123",
            started_at=datetime.now(timezone.utc),
            prompts_generated=12,
            prompt_results=[{"prompt_idx": 0, "overall_score": 7.5}],
            aggregate={"mean_overall": 7.4, "p5_overall": 6.8},
            taxonomy_delta={"domains_created": [], "clusters_created": []},
            final_report="# Probe Run Report\n...",
            status="completed",
        )
        db_session.add(row)
        await db_session.commit()

        result = await db_session.get(ProbeRun, "test-probe-1")
        assert result is not None
        assert result.topic == "embedding cache invalidation"
        assert result.prompt_results[0]["overall_score"] == 7.5
        assert result.aggregate["mean_overall"] == 7.4
        assert result.taxonomy_delta["domains_created"] == []
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_proberun_project_id_fk_enforced(self, enable_sqlite_foreign_keys):
        """AC-C2-4: FK on project_id enforced — non-existent project_id raises IntegrityError.

        Uses the shared ``enable_sqlite_foreign_keys`` fixture (returns the
        same ``db_session`` with ``PRAGMA foreign_keys=ON`` already applied
        — see ``conftest.py``). The FK constraint is declared on the model;
        the fixture just makes SQLite enforce it at the connection level.
        """
        db_session = enable_sqlite_foreign_keys

        row = ProbeRun(
            id="test-probe-2",
            topic="x",
            scope="**/*",
            intent_hint="explore",
            repo_full_name="owner/repo",
            project_id="non-existent-project-id-xyz",  # invalid FK target
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db_session.add(row)
        with pytest.raises(IntegrityError):
            await db_session.commit()
