"""Tests for the unified RunRow substrate at the topic_probe call site.

Originally AC-C2-1 through AC-C2-4 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 2.

After Foundation P3 (v0.4.18), the legacy ``probe_run`` table was replaced by
the unified ``run_row`` substrate (spec
``docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md``).
Cycle 14 retired the ``ProbeRun`` Python-alias entirely; all in-tree callers
use ``RunRow`` directly with ``mode='topic_probe'`` filtering.

What this file pins:

  - ``RunRow.__tablename__ == "run_row"`` for topic_probe rows
  - ``RunRow(...)`` accepts the topic_probe payload shape (``mode='topic_probe'``,
    ``topic_probe_meta={...}``)
  - ``RunRow.scope`` / ``RunRow.commit_sha`` accessed via JSON metadata, NOT
    first-class columns (they moved into ``topic_probe_meta`` JSON)
  - Migration ``58510d3f6b81_add_run_row_table_foundation_p3`` is idempotent

Full RunRow column/index coverage lives in ``tests/test_run_row_model.py``;
partial-state abort coverage in
``tests/test_run_row_model.py::test_migration_aborts_on_partial_state``.
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

from app.models import RunRow

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


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


class TestRunRowAtTopicProbeCallSite:
    def test_topic_probe_runrow_columns_match_p3_substrate(self):
        """RunRow with topic_probe payload — column set + JSON-routing contract.

        Replaces the legacy 17-column probe_run assertion (Foundation P3,
        v0.4.18). Cycle 14 retired the ``ProbeRun`` Python alias; the
        substrate-level guarantees are now asserted directly on ``RunRow``:

        a) Underlying table is ``run_row``.
        b) ``scope`` and ``commit_sha`` live in ``topic_probe_meta`` JSON,
           NOT as first-class columns (they were on the legacy probe_run).
        c) Substrate has the new mode-discriminator + ``seed_agent_meta``
           columns absent from the old probe_run table.

        Full RunRow column/index coverage lives in
        ``tests/test_run_row_model.py``.
        """
        # (a) Underlying table.
        assert RunRow.__tablename__ == "run_row"

        # (b) Construct a topic_probe RunRow exercising the JSON metadata
        # routing path.
        row = RunRow(
            id="probe-1",
            mode="topic_probe",
            started_at=datetime.now(timezone.utc),
            topic_probe_meta={"scope": "src/**/*.py", "commit_sha": "abc123"},
        )
        assert row.mode == "topic_probe"
        assert row.topic_probe_meta == {
            "scope": "src/**/*.py", "commit_sha": "abc123",
        }
        # JSON extraction contract — no .scope / .commit_sha column accessor.
        meta = row.topic_probe_meta or {}
        assert meta.get("scope") == "src/**/*.py"
        assert meta.get("commit_sha") == "abc123"

        # JSON metadata absent → JSON .get() returns None / default fallback.
        bare = RunRow(
            id="probe-bare",
            mode="topic_probe",
            started_at=datetime.now(timezone.utc),
        )
        assert (bare.topic_probe_meta or {}).get("scope") is None
        assert (bare.topic_probe_meta or {}).get("commit_sha") is None

        # (c) Substrate columns introduced by P3.
        cols = {c.name for c in RunRow.__table__.columns}
        for required in ("mode", "topic_probe_meta", "seed_agent_meta"):
            assert required in cols, f"Missing P3 column: {required}"

        # Legacy probe_run-only columns must NOT be first-class on run_row;
        # they're now JSON metadata fields.
        for legacy in ("scope", "commit_sha"):
            assert legacy not in cols, (
                f"{legacy} should be in topic_probe_meta JSON, not a column"
            )

    def test_run_row_migration_is_idempotent(self, fresh_db):
        """The new ``58510d3f6b81_add_run_row_table_foundation_p3`` migration
        is idempotent on the already-migrated state.

        Replaces the legacy ``test_migration_idempotent`` (which tested the
        old probe_run migration; that table no longer exists post-P3).
        Strategy mirrors ``tests/test_run_row_model.py::test_migration_aborts_on_partial_state``:
        bootstrap a fresh DB, run upgrade head once, then run upgrade head
        again — the second invocation must be a no-op (alembic's revision
        pointer short-circuits the migration entirely). The matched-state
        guard inside the migration also short-circuits when ``run_row`` is
        present and ``probe_run`` is absent (the "fully migrated" branch),
        which we confirm post-second-upgrade.

        Partial-state abort coverage lives in
        ``tests/test_run_row_model.py::test_migration_aborts_on_partial_state``.
        """
        engine, ini_path = fresh_db
        # First upgrade — applies the migration end-to-end.
        first = _alembic(["upgrade", "head"], ini_path)
        assert first.returncode == 0, (
            f"First upgrade failed: stdout={first.stdout}\nstderr={first.stderr}"
        )

        # Second upgrade — must converge without raising.
        second = _alembic(["upgrade", "head"], ini_path)
        assert second.returncode == 0, (
            f"Second upgrade should be idempotent, got: "
            f"stdout={second.stdout}\nstderr={second.stderr}"
        )

        # Verify run_row exists and probe_run does NOT — spec § 4.3 final-state
        # contract (single substrate post-migration, no leftover legacy table).
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert "run_row" in tables, (
            f"run_row missing after upgrade: {sorted(tables)}"
        )
        assert "probe_run" not in tables, (
            f"probe_run should have been dropped: {sorted(tables)}"
        )

    @pytest.mark.asyncio
    async def test_topic_probe_runrow_round_trip(self, db_session: AsyncSession):
        """AC-C2-3: Insert, query, JSON fields persist correctly."""
        row = RunRow(
            id="test-probe-1",
            mode="topic_probe",
            topic="embedding cache invalidation",
            intent_hint="audit",
            repo_full_name="owner/repo",
            project_id=None,
            topic_probe_meta={"scope": "**/*", "commit_sha": "abc123"},
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

        result = await db_session.get(RunRow, "test-probe-1")
        assert result is not None
        assert result.topic == "embedding cache invalidation"
        assert result.mode == "topic_probe"
        assert (result.topic_probe_meta or {}).get("scope") == "**/*"
        assert (result.topic_probe_meta or {}).get("commit_sha") == "abc123"
        assert result.prompt_results[0]["overall_score"] == 7.5
        assert result.aggregate["mean_overall"] == 7.4
        assert result.taxonomy_delta["domains_created"] == []
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_topic_probe_runrow_project_id_fk_enforced(
        self, enable_sqlite_foreign_keys,
    ):
        """AC-C2-4: FK on project_id enforced — non-existent project_id raises IntegrityError.

        Uses the shared ``enable_sqlite_foreign_keys`` fixture (returns the
        same ``db_session`` with ``PRAGMA foreign_keys=ON`` already applied
        — see ``conftest.py``). The FK constraint is declared on the model;
        the fixture just makes SQLite enforce it at the connection level.
        """
        db_session = enable_sqlite_foreign_keys

        row = RunRow(
            id="test-probe-2",
            mode="topic_probe",
            topic="x",
            intent_hint="explore",
            repo_full_name="owner/repo",
            project_id="non-existent-project-id-xyz",  # invalid FK target
            topic_probe_meta={"scope": "**/*", "commit_sha": None},
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db_session.add(row)
        with pytest.raises(IntegrityError):
            await db_session.commit()
