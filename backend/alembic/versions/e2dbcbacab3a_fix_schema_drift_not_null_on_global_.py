"""Fix schema drift: NOT NULL on global_patterns.id.

Schema-drift fix surfaced by `alembic revision --autogenerate`:
`global_patterns.id` is declared as a non-nullable primary key in `models.py`
but the on-disk SQLite schema left it nullable. Primary keys must be NOT NULL —
a stray NULL row would bypass ORM lookups and silently break promotion/demotion
queries. Forward-only, idempotent via inspector guard.

Note: autogenerate also flags `uq_prompt_cluster_domain_label` as "missing",
but this is a false positive — migration `e7f8a9b0c1d2` creates the index as
`COALESCE(parent_id, '') + label` (expression-based), which SQLAlchemy cannot
reflect. The index is present and correct; we do not touch it here.

Cosmetic type drift (TEXT↔JSON, REAL↔Float) is ignored — SQLite stores these
identically, and re-declaring would create churn without semantic change.

Revision ID: e2dbcbacab3a
Revises: 938041e0f3dd
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e2dbcbacab3a"
down_revision = "938041e0f3dd"
branch_labels = None
depends_on = None


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
    # Guard: only rewrite the column if the drift is actually present.
    if _column_is_nullable(bind, "global_patterns", "id"):
        with op.batch_alter_table("global_patterns") as batch_op:
            batch_op.alter_column(
                "id",
                existing_type=sa.VARCHAR(length=36),
                nullable=False,
            )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migration")
