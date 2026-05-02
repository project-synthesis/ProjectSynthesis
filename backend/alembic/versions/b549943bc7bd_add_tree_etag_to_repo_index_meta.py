"""Add ``tree_etag`` column to ``repo_index_meta``.

Enables ETag-based conditional requests against the GitHub tree endpoint
(``If-None-Match`` → 304 Not Modified). GitHub counts 304 responses as
"no content served" for the primary rate limit, so we can poll
aggressively without burning quota when nothing has changed.

Populated by :meth:`GitHubClient.get_tree_with_cache`, consumed by
``RepoIndexService.build_index`` and ``incremental_update``.

Forward-only, idempotent via inspector guard. Existing rows get NULL —
the next fetch populates the column naturally.

Revision ID: b549943bc7bd
Revises: 8ecce36187b5
Create Date: 2026-04-18
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b549943bc7bd"
down_revision = "8ecce36187b5"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "repo_index_meta", "tree_etag"):
        with op.batch_alter_table("repo_index_meta") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "tree_etag",
                    sa.String(),
                    nullable=True,
                )
            )


def downgrade() -> None:
    raise NotImplementedError("Forward-only migration")
