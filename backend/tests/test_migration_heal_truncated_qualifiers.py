"""RED test for AC-10 — heal_truncated_generated_qualifiers migration.

Spec: docs/superpowers/specs/2026-06-12-v0.4.38-sub-domain-telemetry-design.md §3.5.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _make_cfg(db_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


@pytest.fixture()
def migrated_db(tmp_path):
    db_path = tmp_path / "synthesis.db"
    # alembic env.py uses async driver; sync sqlalchemy.create_engine uses
    # the plain sqlite scheme — match the pattern in tests/test_main_lifespan_no_ddl.py.
    async_url = f"sqlite+aiosqlite:///{db_path}"
    sync_url = f"sqlite:///{db_path}"
    cfg = _make_cfg(async_url)
    # Run up to the PRIOR head so we can seed legacy data, then upgrade.
    command.upgrade(cfg, "1fa50d7f82b7")
    eng = create_engine(sync_url)
    yield eng, cfg
    eng.dispose()


def _insert_domain(eng, cluster_id: str, meta_dict: dict | None) -> None:
    # Table name confirmed against app/models.py:177 (__tablename__ = "prompt_cluster" — singular).
    # NOT NULL columns (id, label, state, domain, task_type, member_count, usage_count,
    # prune_flag_count, scored_count, template_count, weighted_member_sum) populated
    # explicitly because raw SQL bypasses ORM Mapped defaults.
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO prompt_cluster ("
                "id, label, state, domain, task_type, member_count, usage_count, "
                "prune_flag_count, scored_count, template_count, weighted_member_sum, "
                "cluster_metadata"
                ") VALUES ("
                ":id, :label, 'domain', 'general', 'general', 0, 0, "
                "0, 0, 0, 0.0, "
                ":meta"
                ")"
            ),
            {
                "id": cluster_id,
                "label": cluster_id,
                "meta": json.dumps(meta_dict) if meta_dict is not None else None,
            },
        )


def _read_meta(eng, cluster_id: str) -> dict | None:
    with eng.begin() as conn:
        row = conn.execute(
            text("SELECT cluster_metadata FROM prompt_cluster WHERE id=:id"),
            {"id": cluster_id},
        ).first()
    if row is None or row[0] is None:
        return None
    raw = row[0]
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def test_ac10_strips_only_len20_keys(migrated_db):
    eng, cfg = migrated_db
    _insert_domain(eng, "dom-a", {
        "generated_qualifiers": {
            "pipeline-observabili": ["a", "b"],      # len 20 — drop
            "pipeline-observability": ["c", "d"],     # len 22 — keep
            "embeddings": ["e", "f"],                 # len 10 — keep
        },
        "generated_qualifiers_cluster_count": 7,
    })

    # Upgrade past head — our new migration runs.
    command.upgrade(cfg, "head")

    meta = _read_meta(eng, "dom-a")
    gq = meta["generated_qualifiers"]
    assert "pipeline-observabili" not in gq
    assert "pipeline-observability" in gq
    assert "embeddings" in gq
    assert meta["generated_qualifiers_cluster_count"] == -1


def test_ac10_count_unchanged_on_clean_domain(migrated_db):
    eng, cfg = migrated_db
    _insert_domain(eng, "dom-clean", {
        "generated_qualifiers": {"embeddings": ["e"]},
        "generated_qualifiers_cluster_count": 5,
    })
    command.upgrade(cfg, "head")
    meta = _read_meta(eng, "dom-clean")
    # No len-20 keys → count untouched.
    assert meta["generated_qualifiers_cluster_count"] == 5


def test_ac10_idempotent_on_rerun(migrated_db):
    eng, cfg = migrated_db
    _insert_domain(eng, "dom-idem", {
        "generated_qualifiers": {"pipeline-observabili": ["a"]},
        "generated_qualifiers_cluster_count": 4,
    })
    command.upgrade(cfg, "head")
    # Downgrade is a no-op; re-upgrade must not change state again.
    meta_after_first = _read_meta(eng, "dom-idem")
    command.downgrade(cfg, "1fa50d7f82b7")
    command.upgrade(cfg, "head")
    meta_after_second = _read_meta(eng, "dom-idem")
    assert meta_after_first == meta_after_second


def test_ac10_handles_null_metadata(migrated_db):
    eng, cfg = migrated_db
    _insert_domain(eng, "dom-null", None)
    command.upgrade(cfg, "head")
    assert _read_meta(eng, "dom-null") is None


def test_ac10_handles_missing_generated_qualifiers_key(migrated_db):
    eng, cfg = migrated_db
    _insert_domain(eng, "dom-noseed", {"other_key": "ok"})
    command.upgrade(cfg, "head")
    meta = _read_meta(eng, "dom-noseed")
    assert "generated_qualifiers" not in meta or meta["generated_qualifiers"] == {}
    assert "other_key" in meta


def test_ac10_alembic_upgrade_head_clean(migrated_db):
    eng, cfg = migrated_db
    # After upgrade-head, alembic check returns 0 on a clean DB.
    # Invoke via `sys.executable -m alembic` so the test runs both locally
    # (where .venv/bin/alembic exists) and in CI (where alembic is on PATH
    # but not at that exact relative path — the system site-packages
    # installation).
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_DIR),
        env={"DATABASE_URL": cfg.get_main_option("sqlalchemy.url"), "PATH": os.environ.get("PATH", "")},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
