"""Reflection-based RED tests for the v0.4.22 Topic Probe Tier 2 schema substrate.

Asserts the eventual shape of the ``validation_suite`` table + the FK/index
extension on ``run_row`` per spec section 3 (``docs/superpowers/specs/
2026-05-11-topic-probe-tier-2-design.md``) and plan Cycle 1 Task 1.1
(``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md``).

Strategy mirrors ``tests/migrations/test_hotpath_indices_migration.py`` and
``tests/migrations/test_template_migration.py``: a temp ``alembic.ini``
points at an isolated ``tmp_path`` SQLite file, ``alembic upgrade head``
applies every migration in tree, then ``sqlalchemy.inspect()`` reflects
the resulting schema for assertions.

All 7 tests are expected to FAIL until Cycle 1 GREEN lands:
  * NEW migration ``<rev>_validation_suite_topic_probe_t2.py``
    creating ``validation_suite`` + 3 indexes + the ``fk_run_row_suite_id``
    FK + ``ix_run_row_suite_id`` index.
  * NEW ``ValidationSuite`` ORM class in ``app/models.py``.
  * ``RunRow.suite_id`` declaration extended with
    ``ForeignKey("validation_suite.id", ondelete="SET NULL")``.
  * ``RunRow.__table_args__`` extended with
    ``Index("ix_run_row_suite_id", "suite_id", started_at.desc())``.
"""
from __future__ import annotations

import configparser
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"

# Expected schema constants — single source of truth for the assertions.

_EXPECTED_VALIDATION_SUITE_COLUMNS = {
    "id",
    "source_run_id",
    "prompts_snapshot",
    "baseline_scores",
    "tolerance_abs",
    "label",
    "project_id",
    "repo_full_name",
    "created_at",
    "retired_at",
    "retired_reason",
    "is_release_gate",  # T3.1 release-gate (rev 4d16718c337c)
}

_EXPECTED_VALIDATION_SUITE_INDEX_NAMES = {
    "ix_validation_suite_project_id",
    "ix_validation_suite_source_run_id",
    "ix_validation_suite_active",
}


# ---------------------------------------------------------------------------
# Infrastructure helpers — temp ini + subprocess alembic upgrade
# ---------------------------------------------------------------------------


def _write_temp_ini(tmp_path: Path, db_path: Path) -> Path:
    """Write a temp alembic.ini pointing at ``db_path``.

    The ini lives in ``_BACKEND_DIR`` so the ``script_location =
    %(here)s/alembic`` setting resolves correctly. The filename embeds
    a uuid so parallel pytest-xdist workers don't collide.
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
def migrated_engine(tmp_path):
    """Yield a synchronous Engine bound to a fresh DB upgraded to alembic head.

    Uses the canonical ``alembic upgrade head`` path so the resulting schema
    is exactly what production will see post-Cycle-1-GREEN.
    """
    db_path = tmp_path / "validation_suite_migration.db"
    ini_path = _write_temp_ini(tmp_path, db_path)
    try:
        _alembic(["upgrade", "head"], ini_path)
        engine = create_engine(f"sqlite:///{db_path}")
        yield engine
        engine.dispose()
    finally:
        ini_path.unlink(missing_ok=True)


def _index_ddl(engine, table: str, index_name: str) -> str | None:
    """Return the raw ``CREATE INDEX`` SQL for ``index_name`` on ``table``.

    SQLite preserves per-column ``ASC``/``DESC`` ordering and partial
    ``WHERE`` predicates inside ``sqlite_master.sql``; SQLAlchemy's
    Inspector does NOT surface either, so we read the DDL directly.
    """
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name=:t AND name=:n"
            ),
            {"t": table, "n": index_name},
        ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Tests — 1..7 per plan Cycle 1 Task 1.1
# ---------------------------------------------------------------------------


def test_validation_suite_table_exists_post_upgrade(migrated_engine):
    """1. The ``validation_suite`` table is created by alembic upgrade head."""
    inspector = inspect(migrated_engine)
    assert inspector.has_table("validation_suite"), (
        "validation_suite table not created by alembic upgrade head; "
        "expected migration <rev>_validation_suite_topic_probe_t2 to create it."
    )


def test_validation_suite_has_12_columns(migrated_engine):
    """2. The table has exactly 12 columns (11 from spec §3 + is_release_gate from T3.1)."""
    inspector = inspect(migrated_engine)
    columns = inspector.get_columns("validation_suite")
    column_names = {col["name"] for col in columns}
    assert column_names == _EXPECTED_VALIDATION_SUITE_COLUMNS, (
        f"validation_suite column set mismatch.\n"
        f"  expected: {sorted(_EXPECTED_VALIDATION_SUITE_COLUMNS)}\n"
        f"  actual:   {sorted(column_names)}\n"
        f"  missing:  {sorted(_EXPECTED_VALIDATION_SUITE_COLUMNS - column_names)}\n"
        f"  extra:    {sorted(column_names - _EXPECTED_VALIDATION_SUITE_COLUMNS)}"
    )


def test_validation_suite_source_run_id_fk_set_null(migrated_engine):
    """3. ``source_run_id`` FK references ``run_row.id`` with SET NULL, nullable."""
    inspector = inspect(migrated_engine)

    # FK shape — reference + ondelete.
    fks = inspector.get_foreign_keys("validation_suite")
    matching_fks = [
        fk for fk in fks
        if fk.get("constrained_columns") == ["source_run_id"]
    ]
    assert matching_fks, (
        f"no FK found on validation_suite.source_run_id; got fks={fks!r}"
    )
    fk = matching_fks[0]
    assert fk["referred_table"] == "run_row", (
        f"source_run_id FK should reference run_row, got {fk['referred_table']!r}"
    )
    assert fk["referred_columns"] == ["id"], (
        f"source_run_id FK should reference run_row.id, "
        f"got {fk['referred_columns']!r}"
    )
    ondelete = (fk.get("options") or {}).get("ondelete")
    assert ondelete == "SET NULL", (
        f"source_run_id FK ondelete should be SET NULL "
        f"(SET NULL semantics require nullable per spec § 3 row 2 note), "
        f"got {ondelete!r}"
    )

    # Column nullability — must be True so SET NULL is semantically coherent.
    cols_by_name = {c["name"]: c for c in inspector.get_columns("validation_suite")}
    assert cols_by_name["source_run_id"]["nullable"] is True, (
        "source_run_id must be nullable=True; "
        "NOT NULL would contradict ondelete=SET NULL per spec § 3."
    )


def test_validation_suite_3_indexes_exist(migrated_engine):
    """4. All 3 ``validation_suite`` indexes named in spec § 3 are created."""
    inspector = inspect(migrated_engine)
    indexes = inspector.get_indexes("validation_suite")
    index_names = {idx["name"] for idx in indexes}
    missing = _EXPECTED_VALIDATION_SUITE_INDEX_NAMES - index_names
    assert not missing, (
        f"validation_suite is missing {len(missing)} expected indexes: "
        f"{sorted(missing)}; got index_names={sorted(index_names)}"
    )


def test_validation_suite_active_index_partial_predicate(migrated_engine):
    """5. ``ix_validation_suite_active`` is a partial index on ``retired_at IS NULL``."""
    ddl = _index_ddl(migrated_engine, "validation_suite", "ix_validation_suite_active")
    assert ddl is not None, (
        "ix_validation_suite_active not found in sqlite_master; "
        "expected migration to create a partial index per spec § 3 row 3."
    )
    # SQLite preserves the WHERE clause verbatim in sqlite_master.sql. Case
    # may vary slightly (SQLite normalizes some keywords), so do an
    # uppercase contains rather than a literal substring search.
    assert "WHERE retired_at IS NULL" in ddl.upper().replace("RETIRED_AT", "retired_at"), (
        f"ix_validation_suite_active should be a partial index with "
        f"WHERE retired_at IS NULL predicate per spec § 3; "
        f"actual DDL: {ddl!r}"
    )


def test_run_row_suite_id_fk_exists(migrated_engine):
    """6. ``run_row`` has FK ``fk_run_row_suite_id`` on suite_id → validation_suite.id."""
    inspector = inspect(migrated_engine)
    fks = inspector.get_foreign_keys("run_row")
    matching_fks = [
        fk for fk in fks
        if fk.get("constrained_columns") == ["suite_id"]
    ]
    assert matching_fks, (
        f"no FK found on run_row.suite_id; got fks={fks!r}; "
        f"expected fk_run_row_suite_id per spec § 3 + plan Cycle 1."
    )
    fk = matching_fks[0]
    assert fk.get("name") == "fk_run_row_suite_id", (
        f"FK constraint name should be 'fk_run_row_suite_id' per spec § 3, "
        f"got {fk.get('name')!r}"
    )
    assert fk["referred_table"] == "validation_suite", (
        f"suite_id FK should reference validation_suite, "
        f"got {fk['referred_table']!r}"
    )
    assert fk["referred_columns"] == ["id"], (
        f"suite_id FK should reference validation_suite.id, "
        f"got {fk['referred_columns']!r}"
    )
    ondelete = (fk.get("options") or {}).get("ondelete")
    assert ondelete == "SET NULL", (
        f"suite_id FK ondelete should be SET NULL per spec § 3, "
        f"got {ondelete!r}"
    )


def test_ix_run_row_suite_id_exists_with_descending_started_at(migrated_engine):
    """7. ``ix_run_row_suite_id`` covers ``(suite_id, started_at DESC)``."""
    inspector = inspect(migrated_engine)
    indexes = inspector.get_indexes("run_row")
    matching = [idx for idx in indexes if idx["name"] == "ix_run_row_suite_id"]
    assert matching, (
        f"ix_run_row_suite_id not found on run_row; "
        f"got indexes={[idx['name'] for idx in indexes]}; "
        f"expected per spec § 3 / RunRow.__table_args__ update."
    )
    idx = matching[0]
    assert idx["column_names"] == ["suite_id", "started_at"], (
        f"ix_run_row_suite_id should cover (suite_id, started_at) in that order; "
        f"got {idx['column_names']!r}"
    )

    # DESC verification — Inspector flattens DESC into the column list but does
    # not surface ordering; sqlite_master.sql preserves the per-column DESC
    # token verbatim.
    ddl = _index_ddl(migrated_engine, "run_row", "ix_run_row_suite_id")
    assert ddl is not None, (
        "ix_run_row_suite_id DDL not found in sqlite_master after creation."
    )
    # SQLite emits the column as `started_at DESC` (or "started_at" DESC with
    # quotes depending on the dialect rendering). Accept both forms.
    ddl_upper = ddl.upper()
    has_desc = (
        "STARTED_AT DESC" in ddl_upper
        or '"STARTED_AT" DESC' in ddl_upper
    )
    assert has_desc, (
        f"ix_run_row_suite_id should order started_at DESC per spec § 3 "
        f"('latest replay per suite' regression-alarm query pattern); "
        f"actual DDL: {ddl!r}"
    )
