"""add probe_run table for Topic Probe Tier 1

Revision ID: ec86c86ba298
Revises: d3f5a8c91024
Create Date: 2026-04-29 02:16:03.687412

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ec86c86ba298'
down_revision: Union[str, Sequence[str], None] = 'd3f5a8c91024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent — uses inspector.get_table_names() guard per codebase convention."""
    from sqlalchemy import inspect
    bind = op.get_bind()
    inspector = inspect(bind)
    if "probe_run" in inspector.get_table_names():
        return  # Already migrated; no-op for idempotency

    op.create_table(
        "probe_run",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False, server_default="**/*"),
        sa.Column("intent_hint", sa.String(), nullable=False, server_default="explore"),
        sa.Column("repo_full_name", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("prompts_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_results", sa.JSON(), nullable=True),
        sa.Column("aggregate", sa.JSON(), nullable=True),
        sa.Column("taxonomy_delta", sa.JSON(), nullable=True),
        sa.Column("final_report", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("suite_id", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["prompt_cluster.id"],
            name="fk_probe_run_project_id_prompt_cluster",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_probe_run_status_started", "probe_run", ["status", "started_at"], unique=False,
    )
    op.create_index(
        "ix_probe_run_project_id", "probe_run", ["project_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_probe_run_project_id", table_name="probe_run")
    op.drop_index("ix_probe_run_status_started", table_name="probe_run")
    op.drop_table("probe_run")
