"""add run_row table foundation p3

Replaces ``probe_run`` with the unified ``run_row`` substrate (Foundation
P3, v0.4.18). The new table generalises probe-mode + seed-mode row state
behind a ``mode`` discriminator, with shared lifecycle columns
(``status``/``started_at``/``completed_at``/``error``) and shared output
payloads (``prompts_generated``/``prompt_results``/``aggregate``/
``taxonomy_delta``/``final_report``) common to both modes. Mode-specific
fields land in ``topic_probe_meta`` / ``seed_agent_meta`` JSON columns —
``scope`` and ``commit_sha`` move from probe-side first-class to JSON
metadata (low query frequency; not query-hot per the spec § 4.1 Q2
analysis).

Atomic upgrade (`transaction_per_migration=True` in env.py): create
``run_row`` + 4 indexes, ``INSERT ... SELECT`` backfill from probe_run,
drop probe_run + its 2 indexes — all in one transaction, no partial
state possible.

Idempotency guard (matched-state) handles three startup states:
1. Fresh upgrade — both probe_run present, run_row absent. Proceed.
2. Already migrated — run_row present, probe_run gone. No-op.
3. Partial completion — both tables present after a prior failed
   upgrade. Abort with operator-readable error so manual inspection
   can establish whether the backfill ran. There is no automatic
   recovery: the partial state could indicate either "backfill ran
   but drop_table failed" or "create_table ran but backfill never
   started" — different states require different operator actions.

Reversible downgrade with NOT NULL re-COALESCE (``intent_hint``,
``repo_full_name``, ``topic`` were NOT NULL with server defaults on the
original probe_run; RunRow makes them nullable to accommodate seed mode
— the reverse-backfill COALESCEs to original defaults to defend against
edge-case nulls). Filters ``WHERE mode='topic_probe'`` so seed-mode rows
(none in PR1, but possible if downgrade runs after PR2) don't bleed
across.

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 4

Revision ID: 58510d3f6b81
Revises: bdd8e96cf489
Create Date: 2026-05-06 20:48:14.843189

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58510d3f6b81"
down_revision: Union[str, Sequence[str], None] = "bdd8e96cf489"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Idempotent — matched-state guard so partial-completion (run_row present
    AND probe_run also present) aborts with operator-readable error rather than
    silently proceeding."""
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    if "run_row" in tables and "probe_run" not in tables:
        return  # Already fully migrated — idempotent no-op

    if "run_row" in tables and "probe_run" in tables:
        raise RuntimeError(
            "run_row table exists but probe_run also still exists — "
            "partial migration detected. Manual cleanup required before retry."
        )

    # Normal upgrade path: probe_run exists, run_row does not.

    # 1. Create run_row table
    op.create_table(
        "run_row",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column(
            "status", sa.String(), nullable=False, server_default="running",
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("repo_full_name", sa.String(), nullable=True),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("intent_hint", sa.String(), nullable=True),
        sa.Column(
            "prompts_generated",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("prompt_results", sa.JSON(), nullable=True),
        sa.Column("aggregate", sa.JSON(), nullable=True),
        sa.Column("taxonomy_delta", sa.JSON(), nullable=True),
        sa.Column("final_report", sa.Text(), nullable=True),
        sa.Column("suite_id", sa.String(), nullable=True),
        sa.Column("topic_probe_meta", sa.JSON(), nullable=True),
        sa.Column("seed_agent_meta", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["prompt_cluster.id"],
            name="fk_run_row_project_id_prompt_cluster",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. Create 4 indexes
    op.create_index(
        "ix_run_row_mode_started", "run_row", ["mode", "started_at"],
    )
    op.create_index(
        "ix_run_row_status_started", "run_row", ["status", "started_at"],
    )
    op.create_index(
        "ix_run_row_project_id", "run_row", ["project_id"],
    )
    op.create_index(
        "ix_run_row_topic", "run_row", ["topic"],
    )

    # 3. Backfill from probe_run with mode='topic_probe' and JSON
    # metadata for the legacy scope + commit_sha columns.
    op.execute(
        """
        INSERT INTO run_row (
            id, mode, status, started_at, completed_at, error,
            project_id, repo_full_name, topic, intent_hint,
            prompts_generated, prompt_results, aggregate, taxonomy_delta,
            final_report, suite_id, topic_probe_meta, seed_agent_meta
        )
        SELECT
            id, 'topic_probe', status, started_at, completed_at, error,
            project_id, repo_full_name, topic, intent_hint,
            prompts_generated, prompt_results, aggregate, taxonomy_delta,
            final_report, suite_id,
            json_object('scope', scope, 'commit_sha', commit_sha) AS topic_probe_meta,
            NULL AS seed_agent_meta
        FROM probe_run
        """
    )

    # 4. Drop probe_run indexes + table
    op.drop_index("ix_probe_run_project_id", table_name="probe_run")
    op.drop_index("ix_probe_run_status_started", table_name="probe_run")
    op.drop_table("probe_run")


def downgrade() -> None:
    """Recreate probe_run + reverse-backfill from run_row WHERE mode='topic_probe'.

    NOT NULL safety: original probe_run had repo_full_name/scope/intent_hint/topic
    NOT NULL with server defaults. Reverse-backfill COALESCEs every NOT-NULL
    column so the back-insert never fails NOT NULL on edge-case rows. Seed-mode
    rows are filtered out by the WHERE clause.
    """
    op.create_table(
        "probe_run",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column(
            "scope",
            sa.String(),
            nullable=False,
            server_default="**/*",
        ),
        sa.Column(
            "intent_hint",
            sa.String(),
            nullable=False,
            server_default="explore",
        ),
        sa.Column("repo_full_name", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("commit_sha", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "prompts_generated",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("prompt_results", sa.JSON(), nullable=True),
        sa.Column("aggregate", sa.JSON(), nullable=True),
        sa.Column("taxonomy_delta", sa.JSON(), nullable=True),
        sa.Column("final_report", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="running",
        ),
        sa.Column("suite_id", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["prompt_cluster.id"],
            name="fk_probe_run_project_id_prompt_cluster",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_probe_run_status_started", "probe_run", ["status", "started_at"],
    )
    op.create_index(
        "ix_probe_run_project_id", "probe_run", ["project_id"],
    )

    op.execute(
        """
        INSERT INTO probe_run (
            id, topic, scope, intent_hint, repo_full_name, project_id,
            commit_sha, started_at, completed_at, prompts_generated,
            prompt_results, aggregate, taxonomy_delta, final_report,
            status, suite_id, error
        )
        SELECT
            id,
            COALESCE(topic, '') AS topic,
            COALESCE(json_extract(topic_probe_meta, '$.scope'), '**/*') AS scope,
            COALESCE(intent_hint, 'explore') AS intent_hint,
            COALESCE(repo_full_name, '') AS repo_full_name,
            project_id,
            json_extract(topic_probe_meta, '$.commit_sha') AS commit_sha,
            started_at, completed_at, prompts_generated,
            prompt_results, aggregate, taxonomy_delta, final_report,
            status, suite_id, error
        FROM run_row
        WHERE mode = 'topic_probe'
        """
    )

    op.drop_index("ix_run_row_topic", table_name="run_row")
    op.drop_index("ix_run_row_project_id", table_name="run_row")
    op.drop_index("ix_run_row_status_started", table_name="run_row")
    op.drop_index("ix_run_row_mode_started", table_name="run_row")
    op.drop_table("run_row")
