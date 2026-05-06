"""Repair residual schema drift: uq_prompt_cluster_domain_label + global_patterns.id NOT NULL.

Pre-existing drift discovered during Foundation P3 Cycle 0. Two real items
that earlier drift-fix migrations failed to fully apply on at least one
deployment:

1. ``uq_prompt_cluster_domain_label`` (partial unique on
   ``COALESCE(parent_id, '') + label) WHERE state = 'domain'``).
   Originally created label-only by ``a1b2c3d4e5f6`` and rewritten to the
   composite form by ``e7f8a9b0c1d2``. Some deployments are at head
   (``ec86c86ba298``) but missing the index — likely from a snapshot
   restore that fast-forwarded ``alembic_version`` past those migrations.
   The index is the only thing preventing duplicate per-project domain
   labels (e.g. each project owning its own ``general``/``backend`` etc.),
   so a missing index is a real correctness gap.

2. ``global_patterns.id`` should be NOT NULL (it's the primary key).
   Migration ``e2dbcbacab3a`` was supposed to fix this via
   ``batch_alter_table().alter_column(nullable=False)`` but the resulting
   ``CREATE TABLE`` on at least one deployment came out as
   ``id VARCHAR(36) PRIMARY KEY`` without explicit ``NOT NULL`` —
   SQLite's VARCHAR PRIMARY KEY does not auto-imply NOT NULL (only INTEGER
   PRIMARY KEY does), so the column reflects as nullable and
   ``alembic check`` flags it on every CI run. The earlier migration is
   already marked applied, so we re-do the fix in a fresh revision.

Both operations are idempotent via inspector guards — safe to run on
deployments that already have the right shape.

Revision ID: 2d61e9b37427
Revises: ec86c86ba298
Create Date: 2026-05-06
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "2d61e9b37427"
down_revision = "ec86c86ba298"
branch_labels = None
depends_on = None


def _has_index(bind, table: str, name: str) -> bool:
    """Check whether an index exists on ``table``.

    Uses a direct ``sqlite_master`` query rather than ``inspect().get_indexes()``
    because SQLAlchemy's reflector silently skips expression-based indexes
    (e.g. partial indexes wrapping a column in ``COALESCE``) — exactly the
    shape of ``uq_prompt_cluster_domain_label``. The bare inspector returns
    an empty list and the guard would falsely believe the index is missing.
    """
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    if bind.dialect.name == "sqlite":
        row = bind.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name = ? AND tbl_name = ?",
            (name, table),
        ).fetchone()
        return row is not None
    return any(idx["name"] == name for idx in insp.get_indexes(table))


def _column_is_nullable(bind, table: str, column: str) -> bool:
    """Return True iff `table.column` exists and is currently nullable."""
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    for col in insp.get_columns(table):
        if col["name"] == column:
            return bool(col.get("nullable", True))
    return False


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Re-create uq_prompt_cluster_domain_label if missing.
    #
    # Uses raw SQL (not ``op.create_index``) because SQLAlchemy's index
    # builder cannot express the ``COALESCE(parent_id, '')`` wrapper —
    # required for SQLite NULL-uniqueness semantics. Mirrors the original
    # ``e7f8a9b0c1d2`` form exactly.
    # ------------------------------------------------------------------
    if not _has_index(bind, "prompt_cluster", "uq_prompt_cluster_domain_label"):
        op.execute(
            "CREATE UNIQUE INDEX uq_prompt_cluster_domain_label "
            "ON prompt_cluster (COALESCE(parent_id, ''), label) "
            "WHERE state = 'domain'"
        )

    # ------------------------------------------------------------------
    # 2. Enforce NOT NULL on global_patterns.id (primary key).
    # ------------------------------------------------------------------
    if _column_is_nullable(bind, "global_patterns", "id"):
        with op.batch_alter_table("global_patterns") as batch_op:
            batch_op.alter_column(
                "id",
                existing_type=sa.VARCHAR(length=36),
                nullable=False,
            )


def downgrade() -> None:
    """No-op: this migration only repairs drift that prior migrations failed
    to fully apply. Dropping the index would re-introduce the bug; setting
    ``id`` back to nullable would corrupt the primary key. The downgrade
    path is safe to invoke (idempotent no-op) so test suites that walk
    head → base don't crash."""
    return
