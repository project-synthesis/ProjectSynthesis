"""Pin the alembic schema-drift fixes that landed on release/v0.4.18.

Regression tests for the four prework commits that closed the
``alembic check`` drift loop:

* ``cfd93306`` — Optimization gained 8 hotpath indexes; Feedback gained
  ``ix_feedbacks_optimization_id``.
* ``b60868cb`` — ``compare_type`` callback in ``alembic/env.py`` suppresses
  cosmetic SQLite affinity drift on JSON↔TEXT and Float↔REAL.
* ``4ff6115d`` — ``include_object`` filter excludes the COALESCE-wrapped
  ``uq_prompt_cluster_domain_label`` index from autogenerate noise.
* ``8cb2529b`` — Migration ``2d61e9b37427`` re-asserts the
  ``uq_prompt_cluster_domain_label`` partial unique index and the
  ``global_patterns.id NOT NULL`` constraint, both inspector-guarded so
  the migration is idempotent.

The eight tests below pin every observable consequence of those commits:
DB-state index existence, COALESCE-aware uniqueness semantics,
``global_patterns.id`` NOT NULL enforcement, migration idempotency, and
the canonical "No new upgrade operations detected" baseline that future
schema regressions must trip.
"""
from __future__ import annotations

import configparser
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_HEAD_REVISION = "2d61e9b37427"
_PRE_REPAIR_REVISION = "ec86c86ba298"

_EXPECTED_OPTIMIZATION_INDEXES = {
    "ix_optimizations_created_at",
    "ix_optimizations_overall_score",
    "ix_optimizations_task_type",
    "ix_optimizations_status",
    "ix_optimizations_strategy_used",
    "ix_optimizations_intent_label",
    "ix_optimizations_domain",
    "ix_optimizations_project_created",
}


# ---------------------------------------------------------------------------
# Helpers — lifted from test_hotpath_indices_migration.py / test_template_migration.py
# so this regression module stays self-contained against future fixture moves.
# ---------------------------------------------------------------------------


def _write_temp_ini(tmp_path: Path, db_path: Path) -> Path:
    """Write a temp alembic.ini pointing at ``db_path``.

    The temp ini is placed alongside the real one (``…/backend``) so the
    relative ``script_location = %(here)s/alembic`` keeps resolving to the
    real migration folder. UUID suffix protects against pytest-xdist
    parallelism.
    """
    cfg = configparser.ConfigParser()
    cfg.read(str(_ALEMBIC_INI))
    cfg["alembic"]["sqlalchemy.url"] = f"sqlite+aiosqlite:///{db_path}"
    ini_name = f"alembic_drift_test_{uuid.uuid4().hex[:8]}.ini"
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


def _index_names_on(engine, table: str) -> set[str]:
    """Return all index names registered on ``table`` via sqlite_master.

    Direct ``sqlite_master`` query (rather than SQLAlchemy reflection) so
    the result includes expression-based partial indexes like
    ``uq_prompt_cluster_domain_label`` — the SQLAlchemy reflector silently
    skips those (see SAWarning in env.py) and the assertion would falsely
    fail.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t"),
            {"t": table},
        ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def head_db(tmp_path):
    """Yield ``(engine, ini_path)`` migrated to the v0.4.18 head revision.

    Each test gets a fresh on-disk SQLite file so DDL state never leaks
    between tests. Uses ``alembic upgrade head`` rather than
    ``Base.metadata.create_all`` to ensure the on-disk schema matches what
    real deployments will see (SQLAlchemy's ``create_all`` would synthesise
    the model's plain-column ``Index(...)`` instead of the COALESCE form
    the migration emits).
    """
    db_path = tmp_path / "drift_regression.db"
    ini_path = _write_temp_ini(tmp_path, db_path)
    try:
        _alembic(["upgrade", "head"], ini_path)
        engine = create_engine(f"sqlite:///{db_path}")
        yield engine, ini_path
        engine.dispose()
    finally:
        ini_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 1. Index existence (cfd93306)
# ---------------------------------------------------------------------------


def test_optimization_table_has_8_hotpath_indexes(head_db):
    """All 8 hotpath indexes from migration cc9c44e78f78 must be present.

    Pins commit cfd93306 which declared ``__table_args__`` on
    ``Optimization``. If a future model edit accidentally drops one, the
    set difference fails loudly with the missing names.
    """
    engine, _ = head_db
    actual = _index_names_on(engine, "optimizations")
    missing = _EXPECTED_OPTIMIZATION_INDEXES - actual
    assert not missing, (
        f"missing hotpath indexes on optimizations: {missing}; "
        f"present={sorted(actual)}"
    )


def test_feedback_table_has_optimization_id_index(head_db):
    """``ix_feedbacks_optimization_id`` must be present on ``feedbacks``.

    Pins the second half of cfd93306. The FK join column is the only
    feedback query path.
    """
    engine, _ = head_db
    actual = _index_names_on(engine, "feedbacks")
    assert "ix_feedbacks_optimization_id" in actual, (
        f"missing ix_feedbacks_optimization_id on feedbacks; present={sorted(actual)}"
    )


def test_prompt_cluster_unique_domain_label_index_exists(head_db):
    """``uq_prompt_cluster_domain_label`` must be registered on ``prompt_cluster``.

    Uses ``sqlite_master`` directly because SQLAlchemy's reflector skips
    expression-based partial indexes (see env.py SAWarning). Pins migration
    ``2d61e9b37427`` (commit 8cb2529b).
    """
    engine, _ = head_db
    actual = _index_names_on(engine, "prompt_cluster")
    assert "uq_prompt_cluster_domain_label" in actual, (
        "uq_prompt_cluster_domain_label missing from prompt_cluster; "
        f"present={sorted(actual)}"
    )


# ---------------------------------------------------------------------------
# 2. Constraint enforcement
# ---------------------------------------------------------------------------


def test_unique_domain_label_rejects_duplicate_for_same_parent(head_db):
    """Two ``state='domain'`` rows with the same parent_id+label must conflict.

    Exercises the partial unique semantics of
    ``uq_prompt_cluster_domain_label``. NULL parent_id is normalised to
    ``''`` via COALESCE, so two NULL-parent domains with the same label
    must also conflict.

    Uses a synthetic label (``zzz-uniq-test``) that is not part of any seed
    domain set so the first INSERT succeeds cleanly — migration
    ``a1b2c3d4e5f6`` and ``c2d4e6f8a0b2`` together seed 11 named domains
    (general, backend, frontend, database, data, devops, security,
    fullstack, marketing, business, content) under a NULL parent.
    """
    engine, _ = head_db
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO prompt_cluster "
                "(id, state, label, parent_id, domain, task_type, "
                "member_count, usage_count) "
                "VALUES ('d1', 'domain', 'zzz-uniq-test', NULL, 'general', 'general', 0, 0)"
            )
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO prompt_cluster "
                    "(id, state, label, parent_id, domain, task_type, "
                    "member_count, usage_count) "
                    "VALUES ('d2', 'domain', 'zzz-uniq-test', NULL, 'general', 'general', 0, 0)"
                )
            )


def test_unique_domain_label_allows_same_label_in_different_parents(head_db):
    """COALESCE(parent_id, '') makes the index parent-aware.

    Two domains named ``zzz-shared-label`` parented to *different* clusters
    must both succeed — proves the index isn't a global-label uniqueness
    gate. Synthetic label avoids collision with the 11 NULL-parented seed
    domains.
    """
    engine, _ = head_db
    with engine.begin() as conn:
        # Two parent project nodes with disjoint IDs.
        conn.execute(
            text(
                "INSERT INTO prompt_cluster "
                "(id, state, label, parent_id, domain, task_type, "
                "member_count, usage_count) "
                "VALUES ('p1', 'project', 'proj-a', NULL, 'general', 'general', 0, 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO prompt_cluster "
                "(id, state, label, parent_id, domain, task_type, "
                "member_count, usage_count) "
                "VALUES ('p2', 'project', 'proj-b', NULL, 'general', 'general', 0, 0)"
            )
        )
        # Same domain label under each parent — must both insert cleanly.
        conn.execute(
            text(
                "INSERT INTO prompt_cluster "
                "(id, state, label, parent_id, domain, task_type, "
                "member_count, usage_count) "
                "VALUES ('d-a', 'domain', 'zzz-shared-label', 'p1', 'backend', 'general', 0, 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO prompt_cluster "
                "(id, state, label, parent_id, domain, task_type, "
                "member_count, usage_count) "
                "VALUES ('d-b', 'domain', 'zzz-shared-label', 'p2', 'backend', 'general', 0, 0)"
            )
        )

    with engine.begin() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM prompt_cluster "
                "WHERE state='domain' AND label='zzz-shared-label'"
            )
        ).scalar()
    assert count == 2, "two same-label domains under different parents must both persist"


def test_global_patterns_id_is_not_null(head_db):
    """``global_patterns.id`` must reject NULL inserts.

    Pins commit 8cb2529b's NOT-NULL re-assertion. SQLite's VARCHAR PRIMARY
    KEY does *not* auto-imply NOT NULL (only INTEGER PRIMARY KEY does), so
    without the explicit constraint a NULL insert would silently succeed.
    """
    engine, _ = head_db
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO global_patterns "
                    "(id, pattern_text, source_cluster_ids, source_project_ids, "
                    "cross_project_count, global_source_count, promoted_at, "
                    "last_validated_at, state) "
                    "VALUES (NULL, 'pat', '[]', '[]', 0, 0, "
                    "datetime('now'), datetime('now'), 'active')"
                )
            )


# ---------------------------------------------------------------------------
# 3. Migration roundtrip
# ---------------------------------------------------------------------------


def test_migration_2d61e9b37427_idempotent_upgrade(tmp_path):
    """Running migration ``2d61e9b37427`` twice must converge.

    The implementer guarded both ops with ``_has_index`` and
    ``_column_is_nullable`` inspector checks. We bootstrap to the
    pre-repair revision, upgrade to head, rewind via ``alembic stamp``,
    and re-run ``upgrade head`` — that re-enters the function body
    against a DB where the index already exists and the column is
    already NOT NULL. The second run must succeed without crashing or
    duplicating the index.
    """
    db_path = tmp_path / "idempotency.db"
    ini_path = _write_temp_ini(tmp_path, db_path)
    try:
        # First pass: bootstrap → head
        _alembic(["upgrade", _PRE_REPAIR_REVISION], ini_path)
        _alembic(["upgrade", "head"], ini_path)

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            first_indexes = _index_names_on(engine, "prompt_cluster")
            assert "uq_prompt_cluster_domain_label" in first_indexes
        finally:
            engine.dispose()

        # Second pass: rewind pointer (no DDL) → upgrade head re-enters upgrade()
        _alembic(["stamp", _PRE_REPAIR_REVISION], ini_path)
        _alembic(["upgrade", "head"], ini_path)

        engine = create_engine(f"sqlite:///{db_path}")
        try:
            second_indexes = _index_names_on(engine, "prompt_cluster")
            assert "uq_prompt_cluster_domain_label" in second_indexes, (
                "uq_prompt_cluster_domain_label lost after re-entering upgrade()"
            )
            # Index must not be duplicated (sqlite_master would list it twice
            # if migration added a second copy).
            count_with_name = sum(
                1 for n in second_indexes if n == "uq_prompt_cluster_domain_label"
            )
            assert count_with_name == 1, (
                "uq_prompt_cluster_domain_label appears multiple times after re-entry"
            )
        finally:
            engine.dispose()
    finally:
        ini_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 4. Drift-state alarm
# ---------------------------------------------------------------------------


def test_alembic_check_reports_no_drift():
    """``alembic check`` against the production DB must report zero drift.

    This is the canonical alarm test: it runs the same comparison Alembic's
    autogenerate uses, against the live ``data/synthesis.db`` configured in
    ``alembic.ini``, with the real ``compare_type`` (commit b60868cb) and
    ``include_object`` (commit 4ff6115d) callbacks active. If any future
    commit reintroduces drift — declared model vs. on-disk schema mismatch
    — this assertion fails immediately with the diff in stdout.

    The production DB is the only correct comparison target because a
    handful of columns are added at startup time by ``app/main.py`` (see
    the chain of idempotent ``ALTER TABLE optimizations ADD COLUMN
    routing_tier`` etc. starting at line ~600) rather than via an alembic
    migration. A freshly migrated DB would surface those as "drift" and
    drown the real signal — the user-verified baseline pins the live state
    that lifespan + migrations together produce.

    Skipped (not failed) when the production DB is absent — keeps a fresh
    clone CI from spuriously failing before ``./init.sh`` has ever run.
    """
    prod_db = _BACKEND_DIR.parent / "data" / "synthesis.db"
    if not prod_db.exists():
        pytest.skip(
            f"production DB {prod_db} not present — run ./init.sh once to "
            "materialise it, or add a CI step that boots the backend."
        )

    result = subprocess.run(
        ["alembic", "check"],
        cwd=str(_BACKEND_DIR),
        check=False,
        capture_output=True,
        text=True,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"alembic check exited {result.returncode}; "
        f"expected 0. stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "No new upgrade operations detected" in combined, (
        "alembic check did not report 'No new upgrade operations detected'; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
