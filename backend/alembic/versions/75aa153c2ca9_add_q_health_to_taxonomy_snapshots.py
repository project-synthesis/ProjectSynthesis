"""add q_health to taxonomy_snapshots

Revision ID: 75aa153c2ca9
Revises: b2c3d4e5f6g7
Create Date: 2026-04-07 16:09:35.628123

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '75aa153c2ca9'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add member-weighted q_health column to taxonomy_snapshots."""
    with op.batch_alter_table('taxonomy_snapshots', schema=None) as batch_op:
        batch_op.add_column(sa.Column('q_health', sa.Float(), nullable=True))


def downgrade() -> None:
    """Remove q_health column from taxonomy_snapshots."""
    with op.batch_alter_table('taxonomy_snapshots', schema=None) as batch_op:
        batch_op.drop_column('q_health')
