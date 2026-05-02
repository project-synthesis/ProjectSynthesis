"""add task_type_telemetry

Creates the `task_type_telemetry` table for recording heuristic vs LLM
classification events (see ADR on A4 confidence-gated fallback). Also
repairs a long-standing migration gap: the `global_patterns` table was
declared in `app/models.py` (ADR-005) but no migration ever created it —
live dev DBs only have it because `Base.metadata.create_all()` runs at
startup. Fresh DBs bootstrapped purely via `alembic upgrade head` (CI
migration tests, Docker cold starts) were missing the table entirely.

This migration now explicitly creates `global_patterns` when absent so
the alembic-driven schema is self-sufficient. On existing dev DBs the
create is a no-op via the inspector guard, so the migration is safe to
apply idempotently whether the table is already present or not.

Revision ID: 2f3b0645e24d
Revises: b3a7e9f4c2d1
Create Date: 2026-04-23 11:14:43.293687

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2f3b0645e24d'
down_revision: Union[str, Sequence[str], None] = 'b3a7e9f4c2d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, table: str) -> bool:
    insp = sa.inspect(bind)
    return table in insp.get_table_names()


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # --- Fundamental gap repair: create global_patterns if no prior migration did.
    if not _table_exists(bind, 'global_patterns'):
        op.create_table(
            'global_patterns',
            sa.Column('id', sa.String(length=36), nullable=False),
            sa.Column('pattern_text', sa.Text(), nullable=False),
            sa.Column('embedding', sa.LargeBinary(), nullable=True),
            sa.Column('source_cluster_ids', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column('source_project_ids', sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column('cross_project_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('global_source_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
            sa.Column('avg_cluster_score', sa.Float(), nullable=True),
            sa.Column('promoted_at', sa.DateTime(), nullable=False),
            sa.Column('last_validated_at', sa.DateTime(), nullable=False),
            sa.Column('state', sa.String(length=20), nullable=False, server_default=sa.text("'active'")),
            sa.PrimaryKeyConstraint('id'),
        )

    # --- Intended feature: task_type_telemetry table.
    if not _table_exists(bind, 'task_type_telemetry'):
        op.create_table(
            'task_type_telemetry',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('raw_prompt', sa.Text(), nullable=False),
            sa.Column('task_type', sa.String(), nullable=False),
            sa.Column('domain', sa.String(), nullable=False),
            sa.Column('source', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade() -> None:
    """Downgrade schema.

    Drops `task_type_telemetry` only. `global_patterns` is preserved on
    downgrade — it was never owned by a prior revision, and downgrading
    across this migration on a live system would destroy cross-project
    pattern data.
    """
    bind = op.get_bind()
    if _table_exists(bind, 'task_type_telemetry'):
        op.drop_table('task_type_telemetry')
