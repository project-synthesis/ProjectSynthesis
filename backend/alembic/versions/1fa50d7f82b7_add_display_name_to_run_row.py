"""add display_name to run_row

Revision ID: 1fa50d7f82b7
Revises: d52c66bf6d52
Create Date: 2026-05-20 22:02:20.429368

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = '1fa50d7f82b7'
down_revision: Union[str, Sequence[str], None] = 'd52c66bf6d52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """Idempotency guard — same pattern as d52c66bf6d52 (T3.3) + 4d16718c337c."""
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def _index_exists(bind, table: str, name: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(ix["name"] == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    """Add ``run_row.display_name`` operator-writable label column.

    Separate from ``topic`` (system-set, semantic) so renames don't lose
    the audit trail.

    **DESC-index preservation contract** (T3.3 lesson, d52c66bf6d52):
    ``batch_alter_table`` rebuilds the table by copying + re-creates
    indexes from SQLAlchemy reflection. The reflector does NOT preserve
    per-column ASC/DESC ordering on composite indexes. The original
    ``ix_run_row_suite_id`` was created with ``["suite_id",
    sa.text("started_at DESC")]`` per Tier 2 spec. Drop + recreate
    AFTER batch_alter to restore DESC.
    """
    bind = op.get_bind()

    if not _column_exists(bind, "run_row", "display_name"):
        with op.batch_alter_table("run_row") as batch_op:
            batch_op.add_column(
                sa.Column("display_name", sa.String(), nullable=True)
            )

        if _index_exists(bind, "run_row", "ix_run_row_suite_id"):
            op.drop_index("ix_run_row_suite_id", table_name="run_row")
        op.create_index(
            "ix_run_row_suite_id",
            "run_row",
            ["suite_id", sa.text("started_at DESC")],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "run_row", "display_name"):
        with op.batch_alter_table("run_row") as batch_op:
            batch_op.drop_column("display_name")
        if _index_exists(bind, "run_row", "ix_run_row_suite_id"):
            op.drop_index("ix_run_row_suite_id", table_name="run_row")
        op.create_index(
            "ix_run_row_suite_id",
            "run_row",
            ["suite_id", sa.text("started_at DESC")],
        )
