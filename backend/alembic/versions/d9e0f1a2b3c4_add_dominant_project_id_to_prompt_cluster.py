"""Add ``dominant_project_id`` column to ``prompt_cluster`` (ADR-005 hardening).

Denormalised pointer to the project node whose members dominate this cluster.
Computed as the majority ``project_id`` among the cluster's optimisations, with
a tie-break preferring non-Legacy projects. Enables O(1) index-backed tree
filtering (``/api/clusters/tree?project_id=X``) and cheap cross-project
inspection without re-counting memberships at read time.

Refreshed by warm Phase 0 and cold path after any membership change.

Backfill strategy (forward-only, idempotent):
  For every non-structural cluster (state NOT IN domain/project/archived),
  pick the ``project_id`` with the highest member count. Ties are resolved
  by preferring non-Legacy projects, then by lexical project_id — deterministic
  across runs. Clusters with zero members stay NULL.

Revision ID: d9e0f1a2b3c4
Revises: c7d8e9f0a1b2
Create Date: 2026-04-19
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d9e0f1a2b3c4"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def _has_index(bind, table: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "prompt_cluster", "dominant_project_id"):
        with op.batch_alter_table("prompt_cluster") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "dominant_project_id",
                    sa.String(),
                    sa.ForeignKey(
                        "prompt_cluster.id",
                        name="fk_prompt_cluster_dominant_project_id",
                        ondelete="SET NULL",
                    ),
                    nullable=True,
                )
            )

    # Backfill majority project per non-structural cluster.
    # Tie-break: non-Legacy project wins; then lexical project_id (deterministic).
    backfill_sql = sa.text(
        """
        UPDATE prompt_cluster
        SET dominant_project_id = (
            SELECT o.project_id
            FROM optimizations o
            LEFT JOIN prompt_cluster p ON p.id = o.project_id
            WHERE o.cluster_id = prompt_cluster.id
              AND o.project_id IS NOT NULL
            GROUP BY o.project_id
            ORDER BY COUNT(*) DESC,
                     CASE WHEN COALESCE(p.label, '') = 'Legacy' THEN 1 ELSE 0 END ASC,
                     o.project_id ASC
            LIMIT 1
        )
        WHERE state NOT IN ('domain', 'project', 'archived')
        """
    )
    bind.execute(backfill_sql)

    if not _has_index(
        bind, "prompt_cluster", "ix_prompt_cluster_dominant_project_id"
    ):
        op.create_index(
            "ix_prompt_cluster_dominant_project_id",
            "prompt_cluster",
            ["dominant_project_id"],
        )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migration")
