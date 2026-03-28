"""add heuristic_flags and suggestions to optimization

Revision ID: 3a9184b6d2ab
Revises: 8820d6fbe0c3
Create Date: 2026-03-27 20:29:40.652249

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a9184b6d2ab'
down_revision: Union[str, Sequence[str], None] = '8820d6fbe0c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add heuristic_flags and suggestions JSON columns to optimizations table."""
    with op.batch_alter_table("optimizations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("heuristic_flags", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("suggestions", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove heuristic_flags and suggestions columns."""
    with op.batch_alter_table("optimizations", schema=None) as batch_op:
        batch_op.drop_column("suggestions")
        batch_op.drop_column("heuristic_flags")
