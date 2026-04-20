"""Verify the template-entity migration handles all documented cases.

Strategy notes
--------------
* alembic/env.py sources the DB URL exclusively from `sqlalchemy.url` in
  alembic.ini — there is no env-var override.  Each fixture writes a
  temporary alembic.ini that overrides only that one key so every test
  runs against an isolated in-memory-file SQLite database.
* downgrade() raises NotImplementedError (forward-only migration), so we
  cannot use `alembic downgrade base` to bootstrap.  Instead we run
  `alembic upgrade bad4ceeb3451` (the pre-template revision) directly.
* optimizations.project_id is nullable — no extra column needed in seeds.
* prompt_cluster.task_type and domain are NOT NULL with no server_default
  in the DDL, so seed INSERTs supply explicit values.
"""
from __future__ import annotations

import configparser
import json
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

_PRE_TEMPLATE_HEAD = "bad4ceeb3451"
_BACKEND_DIR = Path(__file__).resolve().parents[2]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_temp_ini(tmp_path: Path, db_path: Path) -> Path:
    """Return path to a temp alembic.ini with sqlalchemy.url pointing at db_path.

    alembic uses %(here)s (the ini file's directory) to resolve script_location.
    We write the temp ini into the backend directory so that the relative
    ``script_location = %(here)s/alembic`` resolves to the real migration folder.
    The file is named with a uuid suffix to avoid collisions between parallel
    pytest-xdist workers.
    """
    cfg = configparser.ConfigParser()
    cfg.read(str(_ALEMBIC_INI))
    # aiosqlite is required by the async env.py path (async_engine_from_config).
    cfg["alembic"]["sqlalchemy.url"] = f"sqlite+aiosqlite:///{db_path}"
    ini_name = f"alembic_test_{uuid.uuid4().hex[:8]}.ini"
    ini_path = _BACKEND_DIR / ini_name
    with ini_path.open("w") as fh:
        cfg.write(fh)
    return ini_path


def _alembic(
    args: list[str],
    ini_path: Path,
    *,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["alembic", "-c", str(ini_path), *args],
        cwd=str(_BACKEND_DIR),
        check=check,
        capture_output=capture,
        text=True,
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def migrated_db(tmp_path):
    """Yield (engine, ini_path) bootstrapped to bad4ceeb3451.

    The temp ini is written to the backend directory (so that
    ``script_location = %(here)s/alembic`` resolves correctly) and cleaned
    up after the test.
    """
    db_path = tmp_path / "synthesis.db"
    ini_path = _write_temp_ini(tmp_path, db_path)

    try:
        # Bootstrap to the revision just before the template migration.
        _alembic(["upgrade", _PRE_TEMPLATE_HEAD], ini_path)

        engine = create_engine(f"sqlite:///{db_path}")
        yield engine, ini_path
        engine.dispose()
    finally:
        ini_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_template_cluster(
    engine,
    *,
    cluster_id: str,
    has_opt: bool,
    parent_id: str | None,
    seed_pattern: bool = False,
) -> None:
    """Insert a minimal template-state cluster and optionally one optimization.

    NOT NULL columns that have no server_default in the alembic DDL (as of
    bad4ceeb3451) must be supplied explicitly: member_count, usage_count.
    The optimizations table requires: id, created_at, raw_prompt, status.

    When ``seed_pattern=True`` (requires ``has_opt=True``), also inserts a
    meta_patterns row (id='mp1') plus an optimization_patterns join row so
    the migration's ``meta_pattern_id IS NOT NULL`` filter has something
    to pick up and round-trip into ``prompt_templates.pattern_ids``.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO prompt_cluster "
                "(id, state, label, parent_id, domain, task_type, "
                "member_count, usage_count) "
                "VALUES (:id, 'template', :label, :pid, 'general', 'general', 0, 0)"
            ),
            {"id": cluster_id, "label": f"tpl-{cluster_id}", "pid": parent_id},
        )
        if has_opt:
            opt_id = uuid.uuid4().hex
            if seed_pattern:
                # meta_patterns at revision bad4ceeb3451: id, cluster_id,
                # pattern_text, embedding (nullable), source_count, created_at,
                # updated_at. global_source_count is added in a later migration
                # and isn't present at this bootstrap point.
                conn.execute(
                    text(
                        "INSERT INTO meta_patterns "
                        "(id, cluster_id, pattern_text, source_count, "
                        "created_at, updated_at) "
                        "VALUES ('mp1', :cid, 'example pattern', 1, "
                        "datetime('now'), datetime('now'))"
                    ),
                    {"cid": cluster_id},
                )
            conn.execute(
                text(
                    "INSERT INTO optimizations "
                    "(id, cluster_id, raw_prompt, optimized_prompt, "
                    "strategy_used, overall_score, created_at, status) "
                    "VALUES (:oid, :cid, 'raw', 'optimized', 'auto', 7.5, "
                    "datetime('now'), 'completed')"
                ),
                {"oid": opt_id, "cid": cluster_id},
            )
            if seed_pattern:
                # optimization_patterns: optimization_id, cluster_id,
                # relationship, created_at are NOT NULL; meta_pattern_id
                # is nullable but the migration filter only picks up rows
                # where it's set.
                conn.execute(
                    text(
                        "INSERT INTO optimization_patterns "
                        "(optimization_id, cluster_id, meta_pattern_id, "
                        "relationship, created_at) "
                        "VALUES (:oid, :cid, 'mp1', 'source', datetime('now'))"
                    ),
                    {"oid": opt_id, "cid": cluster_id},
                )


def _run_template_migration(engine, ini_path: Path) -> None:
    _alembic(["upgrade", "head"], ini_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path_migrates_template_cluster(migrated_db):
    engine, ini_path = migrated_db
    _seed_template_cluster(
        engine,
        cluster_id="c1",
        has_opt=True,
        parent_id=None,
        seed_pattern=True,
    )
    _run_template_migration(engine, ini_path)

    with engine.begin() as conn:
        templates = conn.execute(
            text("SELECT COUNT(*) FROM prompt_templates")
        ).scalar()
        state = conn.execute(
            text("SELECT state FROM prompt_cluster WHERE id='c1'")
        ).scalar()
        count = conn.execute(
            text("SELECT template_count FROM prompt_cluster WHERE id='c1'")
        ).scalar()
        pattern_ids_raw = conn.execute(
            text(
                "SELECT pattern_ids FROM prompt_templates "
                "WHERE source_cluster_id='c1'"
            )
        ).scalar()

    assert templates == 1
    assert state == "mature"
    assert count == 1

    pattern_ids = json.loads(pattern_ids_raw)
    assert pattern_ids == ["mp1"], (
        f"expected pattern_ids to round-trip as ['mp1'], got {pattern_ids!r}"
    )


def test_migration_is_idempotent(migrated_db):
    """Re-enter ``upgrade()`` a second time against an already-migrated DB.

    A plain ``alembic upgrade head`` twice is a no-op the second time because
    alembic's revision pointer already sits at head — the function body never
    executes. To actually exercise the in-function ``_table_exists`` /
    ``_column_exists`` / ``_index_exists`` / insert pre-check guards, we
    ``alembic stamp bad4ceeb3451`` to rewind the revision pointer without
    touching schema or data, then re-run ``alembic upgrade head``. The second
    ``upgrade()`` now runs against a DB where every DDL object already exists
    and the prompt_templates row is already present — and must converge
    without crashing, duplicating rows, or re-flipping cluster state.
    """
    engine, ini_path = migrated_db
    _seed_template_cluster(engine, cluster_id="c1", has_opt=True, parent_id=None)
    _run_template_migration(engine, ini_path)

    # Rewind the alembic revision pointer without touching schema/data so the
    # next ``upgrade head`` actually re-enters ``upgrade()``.
    _alembic(["stamp", _PRE_TEMPLATE_HEAD], ini_path)
    _run_template_migration(engine, ini_path)

    with engine.begin() as conn:
        templates = conn.execute(
            text("SELECT COUNT(*) FROM prompt_templates")
        ).scalar()
        state = conn.execute(
            text("SELECT state FROM prompt_cluster WHERE id='c1'")
        ).scalar()
        count = conn.execute(
            text("SELECT template_count FROM prompt_cluster WHERE id='c1'")
        ).scalar()

    assert templates == 1, "second upgrade() must not duplicate prompt_templates row"
    assert state == "mature", "cluster state must not be re-flipped"
    assert count == 1, "template_count must remain 1 after re-entry"


def test_template_cluster_without_optimization_reverts_state_only(migrated_db):
    engine, ini_path = migrated_db
    _seed_template_cluster(engine, cluster_id="c2", has_opt=False, parent_id=None)
    _run_template_migration(engine, ini_path)

    with engine.begin() as conn:
        state = conn.execute(
            text("SELECT state FROM prompt_cluster WHERE id='c2'")
        ).scalar()
        templates = conn.execute(
            text("SELECT COUNT(*) FROM prompt_templates")
        ).scalar()

    assert state == "mature"
    assert templates == 0


def test_orphan_parent_sets_domain_label_general(migrated_db):
    """A cluster whose parent_id references a non-existent cluster gets domain_label='general'."""
    engine, ini_path = migrated_db
    _seed_template_cluster(engine, cluster_id="c3", has_opt=True, parent_id="missing_parent")
    _run_template_migration(engine, ini_path)

    with engine.begin() as conn:
        dlabel = conn.execute(
            text(
                "SELECT domain_label FROM prompt_templates "
                "WHERE source_cluster_id='c3'"
            )
        ).scalar()

    assert dlabel == "general", (
        f"expected domain_label='general' for orphan-parent cluster, got {dlabel!r}"
    )


def test_downgrade_refuses(migrated_db):
    """Downgrading *across* the template migration must exit non-zero and
    emit 'forward-only' in output.

    We target ``_PRE_TEMPLATE_HEAD`` explicitly so the test survives new
    migrations stacked on top of the template revision — ``downgrade -1``
    alone would just peel the newest migration off instead of crossing
    the forward-only boundary.
    """
    engine, ini_path = migrated_db
    _run_template_migration(engine, ini_path)

    result = _alembic(
        ["downgrade", _PRE_TEMPLATE_HEAD],
        ini_path,
        check=False,
        capture=True,
    )

    assert result.returncode != 0, (
        f"expected non-zero exit from downgrade to {_PRE_TEMPLATE_HEAD}, "
        f"got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "forward-only" in result.stderr.lower(), (
        f"expected 'forward-only' in stderr; stderr={result.stderr!r}"
    )
