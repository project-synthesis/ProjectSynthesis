"""add scored_count to prompt_cluster

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-31 14:00:00.000000

Tracks the number of members with a non-null overall_score, so the
avg_score running mean uses the correct denominator instead of
total member_count (which includes unscored members).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b2c3d4e5f6g7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("prompt_cluster", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("scored_count", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("prompt_cluster", schema=None) as batch_op:
        batch_op.drop_column("scored_count")
