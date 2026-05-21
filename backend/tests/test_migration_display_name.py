"""v0.4.32 — migration tests for display_name column add.

Verifies:
- Upgrade adds the ``display_name`` column to ``run_row``
- ``ix_run_row_suite_id`` is recreated with DESC ordering preserved
  (T3.3 lesson — batch_alter_table loses per-column ASC/DESC, see
  ``backend/alembic/versions/1fa50d7f82b7_add_display_name_to_run_row.py``).

Strategy mirrors ``backend/tests/test_validation_suite_migration.py``: a temp
``alembic.ini`` points at an isolated ``tmp_path`` SQLite file,
``alembic upgrade head`` applies every migration in tree, then
``sqlalchemy.inspect()`` reflects the resulting schema for assertions.
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
    """Invoke ``alembic`` via the active Python interpreter so the venv's
    site-packages resolves the entry point even when ``alembic`` is not on
    PATH (canonical test env). Mirrors the python-launcher trick used by
    other migration test suites in this repo.
    """
    import shutil
    import sys
    alembic_bin = shutil.which("alembic")
    if alembic_bin is None:
        # Fall back to ``python -m alembic`` so the venv's installed alembic
        # is used regardless of PATH state.
        cmd = [sys.executable, "-m", "alembic", "-c", str(ini_path), *args]
    else:
        cmd = [alembic_bin, "-c", str(ini_path), *args]
    return subprocess.run(
        cmd,
        cwd=str(_BACKEND_DIR),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def migrated_engine(tmp_path):
    """Yield a synchronous Engine bound to a fresh DB upgraded to alembic head."""
    db_path = tmp_path / "display_name_migration.db"
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

    SQLite preserves per-column ``ASC``/``DESC`` ordering inside
    ``sqlite_master.sql``; SQLAlchemy's Inspector does NOT, so we read the
    DDL directly.
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


def test_run_row_has_display_name_column_post_upgrade(migrated_engine):
    """1. ``alembic upgrade head`` adds ``display_name`` column to ``run_row``."""
    inspector = inspect(migrated_engine)
    cols = inspector.get_columns("run_row")
    col_names = {c["name"] for c in cols}
    assert "display_name" in col_names, (
        f"display_name column not added by migration "
        f"1fa50d7f82b7_add_display_name_to_run_row; "
        f"got columns={sorted(col_names)}"
    )
    # Must be nullable
    display_col = next(c for c in cols if c["name"] == "display_name")
    assert display_col["nullable"] is True, (
        "display_name must be nullable=True per spec §3.1; "
        f"got nullable={display_col['nullable']!r}"
    )


def test_ix_run_row_suite_id_preserves_started_at_desc_after_batch_alter(migrated_engine):
    """2. The DESC-index preservation contract from T3.3 still holds after the
    batch_alter_table rebuild in migration 1fa50d7f82b7.

    The Tier-2 substrate created ``ix_run_row_suite_id`` with
    ``(suite_id, started_at DESC)`` to back the regression-alarm "latest
    replay per suite_id" query. ``batch_alter_table`` rebuilds the table by
    copy + re-creates indexes via SQLAlchemy reflection, which does NOT
    preserve per-column ASC/DESC. The migration drops + recreates the index
    AFTER batch_alter to restore DESC.
    """
    inspector = inspect(migrated_engine)
    indexes = inspector.get_indexes("run_row")
    matching = [idx for idx in indexes if idx["name"] == "ix_run_row_suite_id"]
    assert matching, (
        f"ix_run_row_suite_id not found on run_row after upgrade; "
        f"got indexes={[idx['name'] for idx in indexes]}; "
        f"expected migration 1fa50d7f82b7 to restore it after batch_alter."
    )
    idx = matching[0]
    assert idx["column_names"] == ["suite_id", "started_at"], (
        f"ix_run_row_suite_id should cover (suite_id, started_at) in that order; "
        f"got {idx['column_names']!r}"
    )

    # DESC verification — Inspector flattens DESC but sqlite_master.sql
    # preserves the per-column DESC token verbatim.
    ddl = _index_ddl(migrated_engine, "run_row", "ix_run_row_suite_id")
    assert ddl is not None, (
        "ix_run_row_suite_id DDL not found in sqlite_master after upgrade."
    )
    ddl_upper = ddl.upper()
    has_desc = (
        "STARTED_AT DESC" in ddl_upper
        or '"STARTED_AT" DESC' in ddl_upper
    )
    assert has_desc, (
        f"ix_run_row_suite_id should order started_at DESC after migration "
        f"1fa50d7f82b7 (T3.3 lesson — batch_alter loses ASC/DESC); "
        f"actual DDL: {ddl!r}"
    )
