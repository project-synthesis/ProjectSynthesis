"""add qualifier_embedding column

Revision ID: bad4ceeb3451
Revises: e7f8a9b0c1d2
Create Date: 2026-04-15 23:16:37.293873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bad4ceeb3451'
down_revision: Union[str, Sequence[str], None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """Return True if the column already exists in the table (idempotency guard)."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    """Add qualifier_embedding column to optimizations table."""
    if not _column_exists("optimizations", "qualifier_embedding"):
        with op.batch_alter_table("optimizations", schema=None) as batch_op:
            batch_op.add_column(sa.Column("qualifier_embedding", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    """Drop qualifier_embedding column from optimizations table."""
    if _column_exists("optimizations", "qualifier_embedding"):
        with op.batch_alter_table("optimizations", schema=None) as batch_op:
            batch_op.drop_column("qualifier_embedding")
