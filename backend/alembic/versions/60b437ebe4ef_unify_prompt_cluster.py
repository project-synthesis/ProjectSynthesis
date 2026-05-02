"""unify prompt cluster

Merges pattern_families + taxonomy_nodes into a single prompt_cluster table.
Uses create-copy-swap for column renames (SQLite compat).

Revision ID: 60b437ebe4ef
Revises: 566d427e2067
Create Date: 2026-03-21 22:18:00.720442

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '60b437ebe4ef'
down_revision: Union[str, Sequence[str], None] = '566d427e2067'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Unify PatternFamily + TaxonomyNode into PromptCluster."""

    # ── Phase 1: Create prompt_cluster table ──────────────────────────
    op.create_table(
        "prompt_cluster",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("centroid_embedding", sa.LargeBinary(), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("avg_score", sa.Float(), nullable=True),
        sa.Column("coherence", sa.Float(), nullable=True),
        sa.Column("separation", sa.Float(), nullable=True),
        sa.Column("stability", sa.Float(), nullable=True),
        sa.Column("persistence", sa.Float(), nullable=True),
        sa.Column("umap_x", sa.Float(), nullable=True),
        sa.Column("umap_y", sa.Float(), nullable=True),
        sa.Column("umap_z", sa.Float(), nullable=True),
        sa.Column("color_hex", sa.String(7), nullable=True),
        sa.Column("preferred_strategy", sa.String(50), nullable=True),
        sa.Column("prune_flag_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("promoted_at", sa.DateTime(), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["parent_id"], ["prompt_cluster.id"]),
    )
    op.create_index("ix_prompt_cluster_parent_id", "prompt_cluster", ["parent_id"])
    op.create_index("ix_prompt_cluster_state", "prompt_cluster", ["state"])
    op.create_index("ix_prompt_cluster_domain_state", "prompt_cluster", ["domain", "state"])
    op.create_index("ix_prompt_cluster_persistence", "prompt_cluster", ["persistence"])
    op.create_index("ix_prompt_cluster_created_at", "prompt_cluster", [sa.text("created_at DESC")])

    # ── Phase 2: Migrate pattern_families → prompt_cluster ────────────
    op.execute("""
        INSERT INTO prompt_cluster (
            id, parent_id, label, state, domain, task_type,
            centroid_embedding, member_count, usage_count, avg_score,
            coherence, separation, stability, persistence,
            umap_x, umap_y, umap_z, color_hex,
            preferred_strategy, prune_flag_count,
            last_used_at, promoted_at, archived_at,
            created_at, updated_at
        )
        SELECT
            id,
            NULL,
            intent_label,
            'active',
            COALESCE(domain, 'general'),
            COALESCE(task_type, 'general'),
            centroid_embedding,
            member_count,
            usage_count,
            avg_score,
            NULL, NULL, 0.0, 0.5,
            NULL, NULL, NULL, NULL,
            NULL, 0,
            NULL, NULL, NULL,
            created_at,
            updated_at
        FROM pattern_families
    """)

    # ── Phase 3: Copy-swap meta_patterns (family_id → cluster_id) ─────
    op.execute("""
        CREATE TABLE meta_patterns_new (
            id VARCHAR NOT NULL PRIMARY KEY,
            cluster_id VARCHAR NOT NULL REFERENCES prompt_cluster(id),
            pattern_text TEXT NOT NULL,
            embedding BLOB,
            source_count INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """)
    op.execute("""
        INSERT INTO meta_patterns_new (id, cluster_id, pattern_text, embedding, source_count, created_at, updated_at)
        SELECT id, family_id, pattern_text, embedding, source_count, created_at, updated_at
        FROM meta_patterns
    """)
    op.drop_table("meta_patterns")
    op.execute("ALTER TABLE meta_patterns_new RENAME TO meta_patterns")
    op.create_index("ix_meta_patterns_cluster_id", "meta_patterns", ["cluster_id"])

    # ── Phase 4: Copy-swap optimization_patterns ──────────────────────
    # (family_id → cluster_id, add similarity column)
    op.drop_index("ix_optpat_optid_rel", table_name="optimization_patterns")
    op.execute("""
        CREATE TABLE optimization_patterns_new (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            optimization_id VARCHAR NOT NULL REFERENCES optimizations(id),
            cluster_id VARCHAR NOT NULL REFERENCES prompt_cluster(id),
            meta_pattern_id VARCHAR REFERENCES meta_patterns(id),
            relationship VARCHAR(20) NOT NULL DEFAULT 'source',
            similarity FLOAT,
            created_at DATETIME NOT NULL
        )
    """)
    op.execute("""
        INSERT INTO optimization_patterns_new (
            id, optimization_id, cluster_id, meta_pattern_id,
            relationship, similarity, created_at
        )
        SELECT id, optimization_id, family_id, meta_pattern_id, relationship, NULL, created_at
        FROM optimization_patterns
    """)
    op.drop_table("optimization_patterns")
    op.execute("ALTER TABLE optimization_patterns_new RENAME TO optimization_patterns")
    op.create_index("ix_optimization_pattern_opt_rel", "optimization_patterns", ["optimization_id", "relationship"])
    op.create_index("ix_optimization_pattern_cluster", "optimization_patterns", ["cluster_id"])

    # ── Phase 5: Copy-swap optimizations (taxonomy_node_id → cluster_id)
    op.execute("""
        CREATE TABLE optimizations_new (
            id VARCHAR NOT NULL PRIMARY KEY,
            created_at DATETIME NOT NULL,
            raw_prompt TEXT NOT NULL,
            optimized_prompt TEXT,
            task_type VARCHAR,
            strategy_used VARCHAR,
            changes_summary TEXT,
            score_clarity FLOAT,
            score_specificity FLOAT,
            score_structure FLOAT,
            score_faithfulness FLOAT,
            score_conciseness FLOAT,
            overall_score FLOAT,
            provider VARCHAR,
            model_used VARCHAR,
            scoring_mode VARCHAR,
            duration_ms INTEGER,
            repo_full_name VARCHAR,
            codebase_context_snapshot TEXT,
            status VARCHAR NOT NULL,
            trace_id VARCHAR,
            tokens_total INTEGER,
            tokens_by_phase JSON,
            context_sources JSON,
            original_scores JSON,
            score_deltas JSON,
            intent_label VARCHAR,
            domain VARCHAR,
            embedding BLOB,
            cluster_id VARCHAR REFERENCES prompt_cluster(id),
            domain_raw VARCHAR
        )
    """)
    op.execute("""
        INSERT INTO optimizations_new (
            id, created_at, raw_prompt, optimized_prompt, task_type, strategy_used,
            changes_summary, score_clarity, score_specificity, score_structure,
            score_faithfulness, score_conciseness, overall_score, provider, model_used,
            scoring_mode, duration_ms, repo_full_name, codebase_context_snapshot,
            status, trace_id, tokens_total, tokens_by_phase, context_sources,
            original_scores, score_deltas, intent_label, domain, embedding,
            cluster_id, domain_raw
        )
        SELECT
            id, created_at, raw_prompt, optimized_prompt, task_type, strategy_used,
            changes_summary, score_clarity, score_specificity, score_structure,
            score_faithfulness, score_conciseness, overall_score, provider, model_used,
            scoring_mode, duration_ms, repo_full_name, codebase_context_snapshot,
            status, trace_id, tokens_total, tokens_by_phase, context_sources,
            original_scores, score_deltas, intent_label, domain, embedding,
            taxonomy_node_id, domain_raw
        FROM optimizations
    """)
    op.drop_table("optimizations")
    op.execute("ALTER TABLE optimizations_new RENAME TO optimizations")

    # ── Phase 6: Add legacy column to taxonomy_snapshots ──────────────
    op.add_column("taxonomy_snapshots", sa.Column("legacy", sa.Boolean(), nullable=False, server_default="0"))

    # ── Phase 7: Mark existing snapshots as legacy ────────────────────
    op.execute("UPDATE taxonomy_snapshots SET legacy = 1")

    # ── Phase 8: Drop old tables ──────────────────────────────────────
    op.drop_index("ix_taxonomy_state", table_name="taxonomy_nodes")
    op.drop_index("ix_taxonomy_persistence", table_name="taxonomy_nodes")
    op.drop_index("ix_taxonomy_parent", table_name="taxonomy_nodes")
    op.drop_table("taxonomy_nodes")
    op.drop_table("pattern_families")


def downgrade() -> None:
    """Downgrade not supported — restore from backup.

    This migration merges two source tables (pattern_families, taxonomy_nodes)
    into a single prompt_cluster table with lossy column renames. Automatic
    reversal would require recreating data that may not round-trip perfectly.
    """
    raise NotImplementedError(
        "Downgrade from prompt_cluster unification is not supported. "
        "Restore the database from a backup taken before this migration."
    )
