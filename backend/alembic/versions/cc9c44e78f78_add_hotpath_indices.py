"""Add hotpath indices on optimizations + feedbacks.

Covers every column listed in ``OptimizationService.VALID_SORT_COLUMNS``
plus the ``feedbacks.optimization_id`` FK (which is the join column for
every feedback lookup). Without these, list/sort endpoints full-scan the
``optimizations`` table — O(n) per request — and feedback-by-optimization
lookups full-scan ``feedbacks``.

Regression vs PR #1: the original build had an idempotent startup hook
(``_migrate_add_missing_indexes``) that created equivalent indices on
each boot. That hook was dropped during the v2 rebuild. This migration
replaces it with a proper Alembic revision — indices are now
versioned alongside the schema, visible to ``alembic current``, and
rolled back cleanly by ``downgrade()``.

Revision ID: cc9c44e78f78
Revises: d9e0f1a2b3c4
Create Date: 2026-04-20
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cc9c44e78f78"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create hotpath indices. IF NOT EXISTS-safe via inspector guard below."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_index(table: str, name: str) -> bool:
        return any(idx["name"] == name for idx in inspector.get_indexes(table))

    # ---- optimizations: every VALID_SORT_COLUMNS entry ----
    for name, column in (
        ("ix_optimizations_created_at", "created_at"),
        ("ix_optimizations_overall_score", "overall_score"),
        ("ix_optimizations_task_type", "task_type"),
        ("ix_optimizations_status", "status"),
        ("ix_optimizations_strategy_used", "strategy_used"),
        ("ix_optimizations_intent_label", "intent_label"),
        ("ix_optimizations_domain", "domain"),
    ):
        if not _has_index("optimizations", name):
            op.create_index(name, "optimizations", [column], unique=False)

    # Composite: the most common filter+sort pair is
    # "WHERE project_id = ? ORDER BY created_at DESC".
    # project_id is already column-level indexed (see models.py:91), but
    # the composite lets SQLite serve both the filter AND the sort from
    # one B-tree — which matters at ≥ 1000 rows.
    if not _has_index("optimizations", "ix_optimizations_project_created"):
        op.create_index(
            "ix_optimizations_project_created",
            "optimizations",
            ["project_id", sa.text("created_at DESC")],
            unique=False,
        )

    # ---- feedbacks: FK join column ----
    if not _has_index("feedbacks", "ix_feedbacks_optimization_id"):
        op.create_index(
            "ix_feedbacks_optimization_id",
            "feedbacks",
            ["optimization_id"],
            unique=False,
        )


def downgrade() -> None:
    """Drop every index created by upgrade(). Guarded for idempotency."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _has_index(table: str, name: str) -> bool:
        return any(idx["name"] == name for idx in inspector.get_indexes(table))

    for name in (
        "ix_feedbacks_optimization_id",
    ):
        if _has_index("feedbacks", name):
            op.drop_index(name, table_name="feedbacks")

    for name in (
        "ix_optimizations_project_created",
        "ix_optimizations_domain",
        "ix_optimizations_intent_label",
        "ix_optimizations_strategy_used",
        "ix_optimizations_status",
        "ix_optimizations_task_type",
        "ix_optimizations_overall_score",
        "ix_optimizations_created_at",
    ):
        if _has_index("optimizations", name):
            op.drop_index(name, table_name="optimizations")
