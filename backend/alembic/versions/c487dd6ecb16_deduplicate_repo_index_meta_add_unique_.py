"""deduplicate_repo_index_meta_add_unique_constraint

Revision ID: c487dd6ecb16
Revises: 75aa153c2ca9
Create Date: 2026-04-09 21:28:41.473885

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c487dd6ecb16'
down_revision: Union[str, Sequence[str], None] = '75aa153c2ca9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Deduplicate repo_index_meta rows and add unique constraint."""
    # Step 1: Remove duplicate (repo_full_name, branch) rows, keeping the one
    # with the most recent indexed_at (fallback to updated_at).
    op.execute("""
        DELETE FROM repo_index_meta
        WHERE id NOT IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY repo_full_name, branch
                           ORDER BY
                               CASE WHEN indexed_at IS NOT NULL THEN 0 ELSE 1 END,
                               indexed_at DESC,
                               updated_at DESC
                       ) AS rn
                FROM repo_index_meta
            )
            WHERE rn = 1
        )
    """)

    # Step 2: Add unique constraint via batch (SQLite requires table recreation)
    with op.batch_alter_table('repo_index_meta', schema=None) as batch_op:
        batch_op.create_index(
            'idx_repo_index_meta_repo_branch',
            ['repo_full_name', 'branch'],
            unique=True,
        )


def downgrade() -> None:
    """Drop unique constraint (does not restore deleted duplicates)."""
    with op.batch_alter_table('repo_index_meta', schema=None) as batch_op:
        batch_op.drop_index('idx_repo_index_meta_repo_branch')
