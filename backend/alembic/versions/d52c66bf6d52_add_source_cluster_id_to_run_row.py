"""add source_cluster_id to run_row

Revision ID: d52c66bf6d52
Revises: 4d16718c337c
Create Date: 2026-05-20 00:39:41.115896

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd52c66bf6d52'
down_revision: Union[str, Sequence[str], None] = '4d16718c337c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """Idempotency guard — same pattern as 2d61e9b37427 + T3.1 4d16718c337c."""
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
    """Add ``run_row.source_cluster_id`` FK column + supporting index.

    SQLite cannot ALTER a table to add a constraint in place; ``batch_alter_table``
    triggers the canonical table-rebuild workaround. Precedent:
    ``d9e0f1a2b3c4_add_dominant_project_id_to_prompt_cluster.py`` lines 50-63.
    """
    bind = op.get_bind()

    if not _column_exists(bind, "run_row", "source_cluster_id"):
        with op.batch_alter_table("run_row") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "source_cluster_id",
                    sa.String(),
                    sa.ForeignKey(
                        "prompt_cluster.id",
                        name="fk_run_row_source_cluster_id",
                        ondelete="SET NULL",
                    ),
                    nullable=True,
                )
            )

    if not _index_exists(bind, "run_row", "ix_run_row_source_cluster_id"):
        op.create_index(
            "ix_run_row_source_cluster_id", "run_row", ["source_cluster_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _index_exists(bind, "run_row", "ix_run_row_source_cluster_id"):
        op.drop_index("ix_run_row_source_cluster_id", "run_row")
    if _column_exists(bind, "run_row", "source_cluster_id"):
        with op.batch_alter_table("run_row") as batch_op:
            batch_op.drop_column("source_cluster_id")
