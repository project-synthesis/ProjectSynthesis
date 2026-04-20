"""Verify the hotpath-indices migration creates every expected index.

Strategy mirrors ``test_template_migration.py``: a temp alembic.ini
points to an isolated tmp_path SQLite file, we bootstrap to the prior
revision (``d9e0f1a2b3c4``) and then run ``alembic upgrade head`` to
apply the hotpath-indices migration.
"""
from __future__ import annotations

import configparser
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

_PRE_HOTPATH_HEAD = "d9e0f1a2b3c4"
_HOTPATH_REVISION = "cc9c44e78f78"
_BACKEND_DIR = Path(__file__).resolve().parents[2]  # …/backend
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"

_EXPECTED_OPT_INDICES = {
    "ix_optimizations_created_at",
    "ix_optimizations_overall_score",
    "ix_optimizations_task_type",
    "ix_optimizations_status",
    "ix_optimizations_strategy_used",
    "ix_optimizations_intent_label",
    "ix_optimizations_domain",
    "ix_optimizations_project_created",
}
_EXPECTED_FEEDBACK_INDICES = {"ix_feedbacks_optimization_id"}


def _write_temp_ini(tmp_path: Path, db_path: Path) -> Path:
    cfg = configparser.ConfigParser()
    cfg.read(str(_ALEMBIC_INI))
    cfg["alembic"]["sqlalchemy.url"] = f"sqlite+aiosqlite:///{db_path}"
    ini_name = f"alembic_test_{uuid.uuid4().hex[:8]}.ini"
    ini_path = _BACKEND_DIR / ini_name
    with ini_path.open("w") as fh:
        cfg.write(fh)
    return ini_path


def _alembic(args: list[str], ini_path: Path, *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["alembic", "-c", str(ini_path), *args],
        cwd=str(_BACKEND_DIR),
        check=True,
        capture_output=capture,
        text=True,
    )


@pytest.fixture
def fresh_db(tmp_path):
    """Bootstrap a new SQLite DB at the pre-hotpath revision."""
    db_path = tmp_path / "hotpath.db"
    ini_path = _write_temp_ini(tmp_path, db_path)
    try:
        _alembic(["upgrade", _PRE_HOTPATH_HEAD], ini_path)
        engine = create_engine(f"sqlite:///{db_path}")
        yield engine, ini_path
        engine.dispose()
    finally:
        ini_path.unlink(missing_ok=True)


def _indices_on(engine, table: str) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t"),
            {"t": table},
        ).fetchall()
    return {r[0] for r in rows}


def test_upgrade_creates_all_optimization_indices(fresh_db):
    engine, ini_path = fresh_db
    _alembic(["upgrade", "head"], ini_path)

    opt_indices = _indices_on(engine, "optimizations")
    fb_indices = _indices_on(engine, "feedbacks")

    missing_opt = _EXPECTED_OPT_INDICES - opt_indices
    missing_fb = _EXPECTED_FEEDBACK_INDICES - fb_indices
    assert not missing_opt, f"missing optimization indices: {missing_opt}"
    assert not missing_fb, f"missing feedback indices: {missing_fb}"


def test_upgrade_is_idempotent(fresh_db):
    """Running upgrade twice must converge — indices are not duplicated."""
    engine, ini_path = fresh_db
    _alembic(["upgrade", "head"], ini_path)

    # Rewind the alembic pointer to the pre-hotpath revision, then re-run.
    # Because each DDL is guarded with `_has_index()`, the second run is a no-op.
    _alembic(["stamp", _PRE_HOTPATH_HEAD], ini_path)
    _alembic(["upgrade", "head"], ini_path)

    opt_indices = _indices_on(engine, "optimizations")
    assert _EXPECTED_OPT_INDICES <= opt_indices, "indices lost on re-entry"


def test_downgrade_drops_new_indices(fresh_db):
    engine, ini_path = fresh_db
    _alembic(["upgrade", "head"], ini_path)
    _alembic(["downgrade", _PRE_HOTPATH_HEAD], ini_path)

    opt_indices = _indices_on(engine, "optimizations")
    fb_indices = _indices_on(engine, "feedbacks")
    assert not (_EXPECTED_OPT_INDICES & opt_indices), (
        f"downgrade left behind: {_EXPECTED_OPT_INDICES & opt_indices}"
    )
    assert not (_EXPECTED_FEEDBACK_INDICES & fb_indices), (
        f"downgrade left behind: {_EXPECTED_FEEDBACK_INDICES & fb_indices}"
    )
