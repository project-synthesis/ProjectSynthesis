"""add is_release_gate to validation_suite

Revision ID: 4d16718c337c
Revises: 5576c539720f
Create Date: 2026-05-19 19:21:13.556321

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4d16718c337c'
down_revision: Union[str, Sequence[str], None] = '5576c539720f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """Return True iff `table.column` exists.

    Idempotency guard — same pattern as ``2d61e9b37427_repair_residual_schema_drift``.
    Lets ``test_migration_*_idempotent_upgrade`` re-run ``upgrade()`` against
    a DB that already has the column (after ``alembic stamp <prev>``).
    """
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "validation_suite", "is_release_gate"):
        op.add_column(
            "validation_suite",
            sa.Column(
                "is_release_gate",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "validation_suite", "is_release_gate"):
        op.drop_column("validation_suite", "is_release_gate")
