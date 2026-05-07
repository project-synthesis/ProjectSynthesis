"""Consolidate startup-time DDL hooks from main.py into Alembic.

Foundation P3 prework — closes the audit-trail gap left by ~12 idempotent
``ALTER TABLE ADD COLUMN`` / ``CREATE INDEX`` blocks that were running in
``app/main.py`` lifespan (lines ~600–907) on every boot. Those hooks were
defense-in-depth at the time but produced four real problems:

1. **Type drift** — raw SQL types (``TEXT``, ``REAL``, ``VARCHAR``) didn't
   match SQLAlchemy declarations (``JSON``, ``Float``, ``String``).
2. **Hidden migration state** — ``alembic upgrade head`` never told the
   full story; lifespan finished the job.
3. **Order-of-startup brittleness** — failures masked by ``try/except: pass``.
4. **Audit-trail gap** — column additions weren't versioned in Alembic.

This migration replaces all of those hooks with a single forward-only,
idempotent revision. Every statement is guarded by an inspector check, so
the upgrade is a no-op on existing dev DBs (where lifespan already added
the columns) and a real DDL on fresh DBs migrated purely via
``alembic upgrade head``.

Columns/indexes covered:

* ``optimizations.routing_tier`` (String) + DML backfill
  (``mcp_sampling`` → ``sampling``; ``%passthrough%`` → ``passthrough``;
  remaining NULL → ``internal``).
* ``optimizations.optimized_embedding`` (LargeBinary).
* ``optimizations.transformation_embedding`` (LargeBinary).
* ``optimizations.phase_weights_json`` (JSON — match model declaration,
  not the legacy ``TEXT`` from the lifespan hook).
* ``optimizations`` index ``ix_optimizations_project_id``
  (single-column counterpart to ``ix_optimizations_project_created``).
* ``meta_patterns.global_source_count`` (Integer NOT NULL DEFAULT 0).
* ``optimization_patterns.global_pattern_id`` (String(36) FK to
  ``global_patterns.id``).
* ``prompt_cluster.weighted_member_sum`` (Float NOT NULL DEFAULT 0.0).
* ``taxonomy_snapshots`` index ``ix_taxonomy_snapshot_created_at``.
* ``linked_repos.project_node_id`` (String(36) FK to
  ``prompt_cluster.id``).
* ``repo_index_meta.explore_synthesis`` (Text).
* ``repo_index_meta.synthesis_status`` (String NOT NULL DEFAULT 'pending')
  + DML backfill (``synthesis_status='ready'`` where
  ``explore_synthesis IS NOT NULL AND synthesis_status='pending'``).
* ``repo_index_meta.synthesis_error`` (Text).
* ``repo_file_index.content`` (Text — full source for curated context).
* ``repo_file_index`` unique index
  ``idx_repo_file_index_repo_branch_path`` on
  ``(repo_full_name, branch, file_path)``.

Note on ``optimizations.project_id``: already added by migration
``f1e2d3c4b5a6`` (template_entity self-heal step 3a). This migration only
adds the missing index ``ix_optimizations_project_id`` (the model has
``index=True`` on the FK; without an explicit migration, deployments that
went through the lifespan hook chain have the index but
``alembic upgrade head``-only DBs do not).

Note on ``global_patterns`` table: already created by migration
``2f3b0645e24d`` (gap-repair step). This migration does NOT re-create it.

Revision ID: bdd8e96cf489
Revises: 2d61e9b37427
Create Date: 2026-05-06
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "bdd8e96cf489"
down_revision = "2d61e9b37427"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return any(col["name"] == column for col in insp.get_columns(table))


def _has_index(bind, table: str, name: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    if bind.dialect.name == "sqlite":
        # Use sqlite_master so partial / expression indexes round-trip.
        row = bind.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name = ? AND tbl_name = ?",
            (name, table),
        ).fetchone()
        return row is not None
    return any(idx["name"] == name for idx in insp.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # optimizations
    # ------------------------------------------------------------------
    if not _has_column(bind, "optimizations", "routing_tier"):
        with op.batch_alter_table("optimizations") as batch_op:
            batch_op.add_column(sa.Column("routing_tier", sa.String(), nullable=True))

    if not _has_column(bind, "optimizations", "optimized_embedding"):
        with op.batch_alter_table("optimizations") as batch_op:
            batch_op.add_column(
                sa.Column("optimized_embedding", sa.LargeBinary(), nullable=True)
            )

    if not _has_column(bind, "optimizations", "transformation_embedding"):
        with op.batch_alter_table("optimizations") as batch_op:
            batch_op.add_column(
                sa.Column("transformation_embedding", sa.LargeBinary(), nullable=True)
            )

    if not _has_column(bind, "optimizations", "phase_weights_json"):
        # JSON (matches model declaration). The compare_type callback in
        # alembic/env.py treats JSON↔TEXT as equivalent on SQLite, so
        # legacy DBs that already have a TEXT-affinity column don't drift.
        with op.batch_alter_table("optimizations") as batch_op:
            batch_op.add_column(
                sa.Column("phase_weights_json", sa.JSON(), nullable=True)
            )

    if not _has_index(bind, "optimizations", "ix_optimizations_project_id"):
        op.create_index(
            "ix_optimizations_project_id",
            "optimizations",
            ["project_id"],
            unique=False,
        )

    # ------------------------------------------------------------------
    # optimizations: routing_tier DML backfill (idempotent — only
    # touches NULL rows).
    # ------------------------------------------------------------------
    bind.execute(sa.text("""
        UPDATE optimizations
           SET routing_tier = 'sampling'
         WHERE routing_tier IS NULL
           AND provider = 'mcp_sampling'
    """))
    bind.execute(sa.text("""
        UPDATE optimizations
           SET routing_tier = 'passthrough'
         WHERE routing_tier IS NULL
           AND provider LIKE '%passthrough%'
    """))
    bind.execute(sa.text("""
        UPDATE optimizations
           SET routing_tier = 'internal'
         WHERE routing_tier IS NULL
    """))

    # ------------------------------------------------------------------
    # meta_patterns
    # ------------------------------------------------------------------
    if not _has_column(bind, "meta_patterns", "global_source_count"):
        with op.batch_alter_table("meta_patterns") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "global_source_count",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )

    # ------------------------------------------------------------------
    # optimization_patterns
    # ------------------------------------------------------------------
    if not _has_column(bind, "optimization_patterns", "global_pattern_id"):
        # No inline FK — SQLite can't ALTER TABLE ADD CONSTRAINT, and
        # the referential intent is declared ORM-side in models.py
        # (FK to ``global_patterns.id``).
        with op.batch_alter_table("optimization_patterns") as batch_op:
            batch_op.add_column(
                sa.Column("global_pattern_id", sa.String(length=36), nullable=True)
            )

    # ------------------------------------------------------------------
    # prompt_cluster
    #
    # NOTE: do NOT use ``op.batch_alter_table`` here. ``prompt_cluster``
    # carries the partial unique index ``uq_prompt_cluster_domain_label``,
    # which is expression-based (``COALESCE(parent_id, '')``). SQLAlchemy's
    # reflector cannot read that expression form, so the batch's
    # copy-and-recreate sequence drops and never restores the index. Raw
    # ``ALTER TABLE ADD COLUMN`` is safe on SQLite for nullable columns
    # with constant server defaults; it doesn't touch the table's index
    # set. Same reasoning is documented at:
    #   alembic/versions/2d61e9b37427_repair_residual_schema_drift_uq_domain_.py
    # ------------------------------------------------------------------
    if not _has_column(bind, "prompt_cluster", "weighted_member_sum"):
        op.execute(
            "ALTER TABLE prompt_cluster "
            "ADD COLUMN weighted_member_sum FLOAT NOT NULL DEFAULT 0.0"
        )

    # ------------------------------------------------------------------
    # taxonomy_snapshots
    # ------------------------------------------------------------------
    if not _has_index(bind, "taxonomy_snapshots", "ix_taxonomy_snapshot_created_at"):
        # Matches model declaration (Index(name, created_at.desc())).
        op.create_index(
            "ix_taxonomy_snapshot_created_at",
            "taxonomy_snapshots",
            [sa.text("created_at DESC")],
            unique=False,
        )

    # ------------------------------------------------------------------
    # linked_repos
    # ------------------------------------------------------------------
    if not _has_column(bind, "linked_repos", "project_node_id"):
        # No inline FK — SQLite can't ALTER TABLE ADD CONSTRAINT, and
        # the referential intent is declared ORM-side in models.py
        # (FK to ``prompt_cluster.id``).
        with op.batch_alter_table("linked_repos") as batch_op:
            batch_op.add_column(
                sa.Column("project_node_id", sa.String(length=36), nullable=True)
            )

    # ------------------------------------------------------------------
    # repo_index_meta
    # ------------------------------------------------------------------
    if not _has_column(bind, "repo_index_meta", "explore_synthesis"):
        with op.batch_alter_table("repo_index_meta") as batch_op:
            batch_op.add_column(
                sa.Column("explore_synthesis", sa.Text(), nullable=True)
            )

    if not _has_column(bind, "repo_index_meta", "synthesis_status"):
        with op.batch_alter_table("repo_index_meta") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "synthesis_status",
                    sa.String(),
                    nullable=False,
                    server_default="pending",
                )
            )

    if not _has_column(bind, "repo_index_meta", "synthesis_error"):
        with op.batch_alter_table("repo_index_meta") as batch_op:
            batch_op.add_column(
                sa.Column("synthesis_error", sa.Text(), nullable=True)
            )

    # repo_index_meta: synthesis_status DML backfill (idempotent — only
    # touches rows where the synthesis text is present but the status is
    # still 'pending'). The DDL above only runs on fresh DBs; legacy DBs
    # have the columns from the lifespan hook + may have rows that were
    # never marked 'ready' because the original backfill failed silently.
    bind.execute(sa.text("""
        UPDATE repo_index_meta
           SET synthesis_status = 'ready'
         WHERE explore_synthesis IS NOT NULL
           AND synthesis_status = 'pending'
    """))

    # ------------------------------------------------------------------
    # repo_file_index
    # ------------------------------------------------------------------
    if not _has_column(bind, "repo_file_index", "content"):
        with op.batch_alter_table("repo_file_index") as batch_op:
            batch_op.add_column(sa.Column("content", sa.Text(), nullable=True))

    if not _has_index(bind, "repo_file_index", "idx_repo_file_index_repo_branch_path"):
        op.create_index(
            "idx_repo_file_index_repo_branch_path",
            "repo_file_index",
            ["repo_full_name", "branch", "file_path"],
            unique=True,
        )


def downgrade() -> None:
    """No-op.

    Reversing this migration would require dropping columns and indexes
    that downstream code (services, models, every active deployment)
    reads on every request. Like ``2d61e9b37427`` (the prior repair
    revision), this downgrade is intentionally a safe no-op so that test
    suites which walk head → base do not crash. Restore from a
    pre-migration backup of ``data/synthesis.db`` if a true reversal is
    required.
    """
    return
