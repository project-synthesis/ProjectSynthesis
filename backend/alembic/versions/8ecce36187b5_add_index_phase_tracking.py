"""Add per-phase indexing state to repo_index_meta.

Adds ``index_phase`` (pending | fetching_tree | embedding | synthesizing |
ready | error), ``files_seen``, ``files_total`` so the UI can show accurate
progress instead of reporting "ready" while synthesis is still running.

Orthogonal to the existing ``status`` + ``synthesis_status`` pair:
``status`` = file index terminal state, ``synthesis_status`` = Haiku
synthesis terminal state, ``index_phase`` = live pipeline cursor driving
the frontend's connectionState flow.

Forward-only, idempotent via inspector guard.

Revision ID: 8ecce36187b5
Revises: e2dbcbacab3a
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "8ecce36187b5"
down_revision = "e2dbcbacab3a"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "repo_index_meta", "index_phase"):
        with op.batch_alter_table("repo_index_meta") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "index_phase",
                    sa.String(),
                    server_default="pending",
                    nullable=False,
                )
            )

    if not _has_column(bind, "repo_index_meta", "files_seen"):
        with op.batch_alter_table("repo_index_meta") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "files_seen",
                    sa.Integer(),
                    server_default="0",
                    nullable=False,
                )
            )

    if not _has_column(bind, "repo_index_meta", "files_total"):
        with op.batch_alter_table("repo_index_meta") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "files_total",
                    sa.Integer(),
                    server_default="0",
                    nullable=False,
                )
            )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migration")
