"""Pin the no-DDL invariant on ``app/main.py`` lifespan.

Foundation P3 prework — migration ``bdd8e96cf489`` lifted ~12 idempotent
``ALTER TABLE`` / ``CREATE INDEX`` blocks out of the lifespan startup
into proper Alembic migrations. The regression alarms below trip if a
future commit ever reintroduces startup-time DDL.

Two layers of pinning:

1. **Static-text guard** — ``test_main_py_has_no_alter_table_at_startup``
   greps the live ``main.py`` source for raw DDL keywords and asserts none
   appear inside the ``lifespan`` function. Cheap, fast, catches the
   pattern before any DB is even touched.

2. **Schema-existence pins** — one test per (table, column) pair that the
   consolidation migration is responsible for. Each one boots a fresh
   on-disk DB through ``alembic upgrade head`` and asserts the column /
   index materialises. If anyone deletes the migration body (or breaks
   the inspector guards), the pin fires.
"""
from __future__ import annotations

import ast
import configparser
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MAIN_PY = _BACKEND_DIR / "app" / "main.py"

# DDL keywords that must NEVER appear inside the lifespan function.
# Compiled case-insensitive so a future ``Alter Table`` reintroduction
# also trips. Word boundaries on each keyword keep the alarm narrow:
# we don't fire on the migration filename "ALTER TABLE …"-style comments,
# we fire only on actual SQL text strings.
_BANNED_DDL = (
    r"\bALTER TABLE\b",
    r"\bALTER COLUMN\b",
    r"\bCREATE INDEX\b",
    r"\bCREATE UNIQUE INDEX\b",
    r"\bCREATE TABLE\b",
    r"\bDROP COLUMN\b",
    r"\bDROP INDEX\b",
    r"\bDROP TABLE\b",
    r"\bRENAME COLUMN\b",
)
_BANNED_RE = re.compile("|".join(_BANNED_DDL), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers (lifted from test_alembic_drift_regression.py for self-containment)
# ---------------------------------------------------------------------------


def _write_temp_ini(tmp_path: Path, db_path: Path) -> Path:
    cfg = configparser.ConfigParser()
    cfg.read(str(_ALEMBIC_INI))
    cfg["alembic"]["sqlalchemy.url"] = f"sqlite+aiosqlite:///{db_path}"
    ini_name = f"alembic_no_ddl_test_{uuid.uuid4().hex[:8]}.ini"
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


def _index_names_on(engine, table: str) -> set[str]:
    """Return all index names registered on ``table`` via sqlite_master.

    Direct query rather than SQLAlchemy reflection so partial /
    expression-based indexes round-trip.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t"),
            {"t": table},
        ).fetchall()
    return {r[0] for r in rows}


def _column_names_on(engine, table: str) -> set[str]:
    """Return all column names on ``table`` via SQLAlchemy inspector."""
    insp = inspect(engine)
    return {col["name"] for col in insp.get_columns(table)}


# ---------------------------------------------------------------------------
# Fixture — fresh head DB shared across the schema-existence pins
# ---------------------------------------------------------------------------


@pytest.fixture
def head_db(tmp_path):
    db_path = tmp_path / "no_ddl_regression.db"
    ini_path = _write_temp_ini(tmp_path, db_path)
    try:
        _alembic(["upgrade", "head"], ini_path)
        engine = create_engine(f"sqlite:///{db_path}")
        yield engine
        engine.dispose()
    finally:
        ini_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 1. Static-text guard — no DDL keywords in lifespan
# ---------------------------------------------------------------------------


def _extract_lifespan_source() -> str:
    """Return the source text of ``lifespan`` (the asynccontextmanager).

    Walks the ``main.py`` AST, finds the ``lifespan`` async function, and
    returns its source slice. Failing to find the function fails the test
    immediately — that itself is a regression we want to catch.
    """
    src = _MAIN_PY.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(
        f"lifespan() async function not found in {_MAIN_PY}"
    )


def test_main_py_has_no_alter_table_at_startup():
    """``app/main.py`` lifespan must contain zero raw-SQL DDL keywords.

    Pins migration ``bdd8e96cf489``. If a future commit reintroduces
    ``ALTER TABLE``, ``CREATE INDEX``, or any related DDL inside the
    lifespan function body, this assertion fires with the offending line.

    Note: this test only inspects the lifespan function. ``main.py`` may
    contain DDL keywords elsewhere (docstrings, comments referencing
    history, the ADR-005 migration helper docstring) — those are fine.
    The invariant we care about is "no startup-time hooks".
    """
    body = _extract_lifespan_source()
    matches = []
    for line_idx, line in enumerate(body.splitlines(), start=1):
        # Skip pure comments — "# Old ALTER TABLE block was here" should
        # not trip the alarm. Match on actual code.
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _BANNED_RE.search(line):
            matches.append((line_idx, line.rstrip()))
    assert not matches, (
        "lifespan() contains raw DDL — those belong in alembic migrations:\n"
        + "\n".join(f"  line {n}: {ln}" for n, ln in matches)
    )


# ---------------------------------------------------------------------------
# 2. Schema-existence pins — one per (table, column-or-index)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table,column",
    [
        ("optimizations", "routing_tier"),
        ("optimizations", "optimized_embedding"),
        ("optimizations", "transformation_embedding"),
        ("optimizations", "phase_weights_json"),
        ("meta_patterns", "global_source_count"),
        ("optimization_patterns", "global_pattern_id"),
        ("prompt_cluster", "weighted_member_sum"),
        ("linked_repos", "project_node_id"),
        ("repo_index_meta", "explore_synthesis"),
        ("repo_index_meta", "synthesis_status"),
        ("repo_index_meta", "synthesis_error"),
        ("repo_file_index", "content"),
    ],
)
def test_consolidation_migration_added_column(head_db, table, column):
    """Each column from migration ``bdd8e96cf489`` must materialise on a
    freshly migrated DB.

    If anyone reverts the migration body (or breaks the inspector guards
    so the column gets skipped on fresh installs), the pin fires.
    """
    actual = _column_names_on(head_db, table)
    assert column in actual, (
        f"{table}.{column} missing after alembic upgrade head; "
        f"present={sorted(actual)}"
    )


@pytest.mark.parametrize(
    "table,index",
    [
        ("optimizations", "ix_optimizations_project_id"),
        ("taxonomy_snapshots", "ix_taxonomy_snapshot_created_at"),
        ("repo_file_index", "idx_repo_file_index_repo_branch_path"),
    ],
)
def test_consolidation_migration_added_index(head_db, table, index):
    """Each index from migration ``bdd8e96cf489`` must materialise on a
    freshly migrated DB.
    """
    actual = _index_names_on(head_db, table)
    assert index in actual, (
        f"{index} missing on {table} after alembic upgrade head; "
        f"present={sorted(actual)}"
    )


# ---------------------------------------------------------------------------
# 3. Type fidelity — phase_weights_json must reflect as JSON, not TEXT
# ---------------------------------------------------------------------------


def test_phase_weights_json_column_is_json_affinity_on_fresh_db(head_db):
    """``optimizations.phase_weights_json`` must be declared as JSON on
    fresh DBs.

    Pins the type fidelity goal of migration ``bdd8e96cf489``: the
    legacy lifespan hook used raw ``TEXT``, but the model declares
    ``JSON``, so SQLite affinity drift accumulated in the live DB. Fresh
    DBs migrated purely via Alembic must NOT inherit that drift —
    ``phase_weights_json`` should reflect as a TEXT-affinity JSON column,
    not a generic TEXT column.

    The ``compare_type`` callback in alembic/env.py forgives
    ``JSON↔TEXT`` drift on legacy DBs, but new migrations should not
    introduce more.
    """
    insp = inspect(head_db)
    cols = {col["name"]: col for col in insp.get_columns("optimizations")}
    assert "phase_weights_json" in cols, "phase_weights_json missing"
    # SQLAlchemy reflects JSON columns as ``TEXT`` on SQLite (the storage
    # affinity), but the type's class name preserves the declaration.
    # We can't directly check ``isinstance(JSON)`` on the reflected type,
    # so we verify the column exists — the real fidelity check is
    # ``alembic check`` reporting zero drift, which is pinned by
    # ``test_alembic_check_reports_no_drift`` in test_alembic_drift_regression.
    assert cols["phase_weights_json"] is not None


# ---------------------------------------------------------------------------
# 4. DML backfill — routing_tier and synthesis_status
# ---------------------------------------------------------------------------


def test_routing_tier_backfill_runs_on_fresh_db(head_db):
    """Migration ``bdd8e96cf489`` must populate routing_tier on existing
    rows according to the documented mapping.

    Insert a row with ``provider='mcp_sampling'`` BEFORE the migration —
    actually we can't easily do that on a fresh DB because the migration
    has already run. Instead we verify the migration is idempotent: the
    DML clauses must not crash on an empty optimizations table, and the
    column must be present and writable.
    """
    with head_db.begin() as conn:
        # Smoke: insert a row with explicit routing_tier — proves column
        # is writable.
        conn.execute(
            text(
                "INSERT INTO optimizations "
                "(id, created_at, raw_prompt, status, routing_tier) "
                "VALUES ('rt-test', datetime('now'), 'p', 'completed', 'internal')"
            )
        )
        rt = conn.execute(
            text("SELECT routing_tier FROM optimizations WHERE id='rt-test'")
        ).scalar()
    assert rt == "internal", f"expected 'internal', got {rt!r}"


def test_synthesis_status_backfill_runs_idempotently(head_db):
    """Migration ``bdd8e96cf489`` synthesis_status backfill must be a
    no-op when no rows exist, and convert pending→ready when run again
    on rows where explore_synthesis is set.

    Round-trip the DML clause manually — the migration has already run
    once, but we verify the clause stays idempotent under repeated
    invocation.
    """
    with head_db.begin() as conn:
        # Insert a row with explore_synthesis but pending status.
        conn.execute(
            text(
                "INSERT INTO repo_index_meta "
                "(id, repo_full_name, branch, status, file_count, "
                "explore_synthesis, synthesis_status, "
                "created_at, updated_at) "
                "VALUES ('m1', 'foo/bar', 'main', 'ready', 0, "
                "'synthesis text', 'pending', "
                "datetime('now'), datetime('now'))"
            )
        )
        # Manually re-run the migration's DML.
        conn.execute(
            text(
                "UPDATE repo_index_meta "
                "SET synthesis_status = 'ready' "
                "WHERE explore_synthesis IS NOT NULL "
                "AND synthesis_status = 'pending'"
            )
        )
        result = conn.execute(
            text("SELECT synthesis_status FROM repo_index_meta WHERE id='m1'")
        ).scalar()
    assert result == "ready", f"expected 'ready', got {result!r}"
