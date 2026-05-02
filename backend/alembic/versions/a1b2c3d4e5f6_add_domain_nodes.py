"""add cluster_metadata column and seed domain nodes

Revision ID: a1b2c3d4e5f6
Revises: 3a9184b6d2ab
Create Date: 2026-03-29 16:00:00.000000

ADR-004: Unified Domain Taxonomy.  Domains become PromptCluster nodes
with ``state='domain'``.  This migration:

1. Adds ``cluster_metadata`` JSON column to ``prompt_cluster``.
2. Creates ``ix_prompt_cluster_state_label`` composite index.
3. Inserts 7 seed domain nodes with colors and keyword metadata.
4. Re-parents existing clusters under matching domain nodes.
5. Backfills ``Optimization.domain`` from ``domain_raw`` where resolvable.

Idempotent — safe to re-run (checks for existing domain nodes).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.migration")

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "3a9184b6d2ab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ---------------------------------------------------------------------------
# Seed domain definitions
# ---------------------------------------------------------------------------

SEED_DOMAINS: list[dict] = [
    {
        "label": "backend",
        "color_hex": "#b44aff",
        "signal_keywords": [
            ["api", 0.8], ["endpoint", 0.9], ["server", 0.8],
            ["middleware", 0.9], ["fastapi", 1.0], ["django", 1.0],
            ["flask", 1.0], ["authentication", 0.7], ["route", 0.6],
        ],
    },
    {
        "label": "frontend",
        "color_hex": "#ff4895",
        "signal_keywords": [
            ["react", 1.0], ["svelte", 1.0], ["component", 0.8],
            ["css", 0.9], ["ui", 0.8], ["layout", 0.7],
            ["responsive", 0.8], ["tailwind", 0.9], ["vue", 1.0],
        ],
    },
    {
        "label": "database",
        "color_hex": "#36b5ff",
        "signal_keywords": [
            ["sql", 1.0], ["migration", 0.9], ["schema", 0.8],
            ["query", 0.7], ["postgresql", 1.0], ["sqlite", 1.0],
            ["orm", 0.8], ["table", 0.6],
        ],
    },
    {
        "label": "devops",
        "color_hex": "#6366f1",
        "signal_keywords": [
            ["docker", 1.0], ["ci/cd", 1.0], ["kubernetes", 1.0],
            ["terraform", 1.0], ["nginx", 0.9], ["monitoring", 0.7],
            ["deploy", 0.8],
        ],
    },
    {
        "label": "security",
        "color_hex": "#ff2255",
        "signal_keywords": [
            ["auth", 0.7], ["encryption", 1.0], ["vulnerability", 1.0],
            ["cors", 0.9], ["jwt", 0.9], ["oauth", 0.9], ["sanitize", 0.8],
            ["injection", 0.9], ["xss", 1.0], ["csrf", 1.0],
        ],
    },
    {
        "label": "fullstack",
        "color_hex": "#d946ef",
        "signal_keywords": [
            ["fullstack", 1.0], ["full-stack", 1.0], ["full stack", 1.0],
            ["end-to-end", 0.7], ["system-wide", 0.6], ["comprehensive", 0.3],
            ["frontend", 0.5], ["backend", 0.5],
            ["react", 0.4], ["svelte", 0.4], ["vue", 0.4],
            ["api", 0.4], ["endpoint", 0.3],
            ["component", 0.3], ["server", 0.3],
            ["ui", 0.3], ["database", 0.3],
        ],
    },
    {
        "label": "data",
        "color_hex": "#b49982",
        "signal_keywords": [
            ["data science", 1.0], ["machine learning", 1.0], ["dataset", 1.0],
            ["pandas", 0.9], ["numpy", 0.9], ["sklearn", 0.9], ["jupyter", 0.9],
            ["model training", 0.9], ["prediction", 0.8], ["classification", 0.8],
            ["visualization", 0.7], ["analytics", 0.7], ["etl", 0.8],
            ["statistics", 0.7], ["regression", 0.8], ["notebook", 0.7],
            ["feature engineering", 0.9], ["churn", 0.6], ["sentiment", 0.6],
        ],
    },
    {
        "label": "general",
        "color_hex": "#7a7a9e",
        "signal_keywords": [],
    },
]


def upgrade() -> None:
    """Add cluster_metadata column, indexes, seed domain nodes, re-parent, backfill."""
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Step 1: Add cluster_metadata column
    # ------------------------------------------------------------------
    # Check if column already exists (idempotent)
    inspector = sa.inspect(conn)
    existing_cols = {c["name"] for c in inspector.get_columns("prompt_cluster")}
    if "cluster_metadata" not in existing_cols:
        with op.batch_alter_table("prompt_cluster", schema=None) as batch_op:
            batch_op.add_column(sa.Column("cluster_metadata", sa.JSON(), nullable=True))
        logger.info("Added cluster_metadata column to prompt_cluster")
    else:
        logger.info("cluster_metadata column already exists — skipping")

    # ------------------------------------------------------------------
    # Step 2: Add indexes (idempotent via try/except)
    # ------------------------------------------------------------------
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("prompt_cluster")}

    if "ix_prompt_cluster_state_label" not in existing_indexes:
        op.create_index(
            "ix_prompt_cluster_state_label",
            "prompt_cluster",
            ["state", "label"],
        )
        logger.info("Created ix_prompt_cluster_state_label index")

    # Partial unique index for domain label uniqueness
    if "uq_prompt_cluster_domain_label" not in existing_indexes:
        op.create_index(
            "uq_prompt_cluster_domain_label",
            "prompt_cluster",
            ["label"],
            unique=True,
            sqlite_where=sa.text("state = 'domain'"),
        )
        logger.info("Created uq_prompt_cluster_domain_label partial unique index")

    # ------------------------------------------------------------------
    # Step 3: Seed domain nodes (idempotent)
    # ------------------------------------------------------------------
    existing_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM prompt_cluster WHERE state = 'domain'")
    ).scalar()

    if existing_count >= len(SEED_DOMAINS):
        logger.info(
            "Domain nodes already exist (%d) — skipping seed", existing_count
        )
    else:
        # Delete any partial seeds to avoid conflicts
        if existing_count > 0:
            op.execute(
                sa.text("DELETE FROM prompt_cluster WHERE state = 'domain'")
            )
            logger.info("Cleared %d partial domain nodes for re-seed", existing_count)

        for domain_def in SEED_DOMAINS:
            node_id = str(uuid.uuid4())
            meta = json.dumps({
                "source": "seed",
                "signal_keywords": domain_def["signal_keywords"],
                "discovered_at": None,
                "proposed_by_snapshot": None,
            })
            op.execute(
                sa.text(
                    "INSERT INTO prompt_cluster "
                    "(id, label, state, domain, task_type, color_hex, "
                    "persistence, member_count, usage_count, prune_flag_count, "
                    "cluster_metadata) "
                    "VALUES (:id, :label, 'domain', :label, 'general', "
                    ":color_hex, 1.0, 0, 0, 0, :meta)"
                ).bindparams(
                    id=node_id,
                    label=domain_def["label"],
                    color_hex=domain_def["color_hex"],
                    meta=meta,
                )
            )
        logger.info("Inserted %d seed domain nodes", len(SEED_DOMAINS))

    # ------------------------------------------------------------------
    # Step 4: Re-parent existing clusters under matching domain nodes
    # ------------------------------------------------------------------
    reparented = conn.execute(
        sa.text("""
            UPDATE prompt_cluster
            SET parent_id = (
                SELECT d.id FROM prompt_cluster d
                WHERE d.state = 'domain' AND d.label = prompt_cluster.domain
            )
            WHERE state != 'domain'
              AND parent_id IS NULL
              AND domain IN (SELECT label FROM prompt_cluster WHERE state = 'domain')
        """)
    ).rowcount
    if reparented:
        logger.info("Re-parented %d clusters under domain nodes", reparented)

    # ------------------------------------------------------------------
    # Step 5: Backfill Optimization.domain from domain_raw
    # ------------------------------------------------------------------
    for domain_def in SEED_DOMAINS:
        if domain_def["label"] in ("general", "fullstack"):
            continue
        updated = conn.execute(
            sa.text("""
                UPDATE optimizations
                SET domain = :label
                WHERE domain = 'general'
                  AND domain_raw IS NOT NULL
                  AND (domain_raw = :label OR domain_raw LIKE :prefix)
            """),
            {"label": domain_def["label"], "prefix": domain_def["label"] + ":%"},
        ).rowcount
        if updated:
            logger.info("Backfilled %d optimizations → '%s'", updated, domain_def["label"])

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    final_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM prompt_cluster WHERE state = 'domain'")
    ).scalar()
    if final_count < len(SEED_DOMAINS):
        raise RuntimeError(
            f"Migration validation failed: expected ≥{len(SEED_DOMAINS)} domain nodes, found {final_count}"
        )
    logger.info(
        "Migration complete: %d domain nodes, %d clusters re-parented",
        final_count, reparented,
    )


def downgrade() -> None:
    """Remove domain nodes, un-parent clusters, revert backfilled domains."""
    conn = op.get_bind()

    # Revert backfilled optimization domains to "general"
    conn.execute(
        sa.text("""
            UPDATE optimizations
            SET domain = 'general'
            WHERE domain != 'general'
              AND domain_raw IS NOT NULL
              AND domain IN (
                  SELECT label FROM prompt_cluster WHERE state = 'domain' AND label != 'general'
              )
        """)
    )

    # Un-parent clusters that point to domain nodes
    conn.execute(
        sa.text("""
            UPDATE prompt_cluster
            SET parent_id = NULL
            WHERE parent_id IN (
                SELECT id FROM prompt_cluster WHERE state = 'domain'
            )
            AND state != 'domain'
        """)
    )

    # Delete domain nodes
    conn.execute(
        sa.text("DELETE FROM prompt_cluster WHERE state = 'domain'")
    )

    # Drop indexes
    try:
        op.drop_index("uq_prompt_cluster_domain_label", table_name="prompt_cluster")
    except Exception:
        pass
    try:
        op.drop_index("ix_prompt_cluster_state_label", table_name="prompt_cluster")
    except Exception:
        pass

    # Remove column
    with op.batch_alter_table("prompt_cluster", schema=None) as batch_op:
        batch_op.drop_column("cluster_metadata")
