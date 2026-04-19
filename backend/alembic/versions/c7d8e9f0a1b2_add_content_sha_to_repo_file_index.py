"""Add ``content_sha`` column to ``repo_file_index``.

Enables content-hash embedding deduplication. ``content_sha`` is the
SHA-256 of the ``embed_text`` (path + outline + doc_summary — the
exact input the embedding model sees). Two rows with the same
``content_sha`` share the same embedding vector, so we can copy
instead of re-running the embedder.

Indexed for fast lookup during incremental reindex. Forward-only,
idempotent via inspector guard.

Existing rows get NULL — they'll be re-populated on the next rebuild
or incremental update that touches the file.

Revision ID: c7d8e9f0a1b2
Revises: b549943bc7bd
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b549943bc7bd"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def _has_index(bind, table: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(ix["name"] == index_name for ix in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "repo_file_index", "content_sha"):
        with op.batch_alter_table("repo_file_index") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "content_sha",
                    sa.String(),
                    nullable=True,
                )
            )

    if not _has_index(bind, "repo_file_index", "ix_repo_file_index_content_sha"):
        op.create_index(
            "ix_repo_file_index_content_sha",
            "repo_file_index",
            ["content_sha"],
        )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migration")
