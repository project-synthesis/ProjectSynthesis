"""Add improvement_score column to optimizations.

Schema-drift fix: models.py declares `improvement_score` but no prior migration
added the column. Safe forward-only additive ALTER; idempotent via inspector check.

Revision ID: 938041e0f3dd
Revises: f1e2d3c4b5a6
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "938041e0f3dd"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "optimizations", "improvement_score"):
        with op.batch_alter_table("optimizations") as batch_op:
            batch_op.add_column(sa.Column("improvement_score", sa.Float(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("Forward-only migration")
