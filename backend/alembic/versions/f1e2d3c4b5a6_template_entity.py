"""Template architecture: add prompt_templates, drop state='template'.

See docs/superpowers/specs/2026-04-18-template-architecture-design.md
§Migration.

Revision ID: f1e2d3c4b5a6
Revises: bad4ceeb3451
Create Date: 2026-04-18
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "f1e2d3c4b5a6"
down_revision = "bad4ceeb3451"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.template_entity")


def _table_exists(bind, name: str) -> bool:
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _column_exists(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def _index_exists(bind, table: str, name: str) -> bool:
    insp = sa.inspect(bind)
    return any(ix["name"] == name for ix in insp.get_indexes(table))


def _root_domain_label(cluster_id: str, parent_by_id: dict, label_by_id: dict, state_by_id: dict) -> str:
    current_id = cluster_id
    seen: set[str] = set()
    for _ in range(8):
        if current_id in seen:
            return "general"
        seen.add(current_id)
        parent_id = parent_by_id.get(current_id)
        state = state_by_id.get(current_id)
        label = label_by_id.get(current_id) or ""
        if not parent_id:
            return label if state == "domain" and label else "general"
        if parent_id not in parent_by_id:
            return label if state == "domain" and label else "general"
        current_id = parent_id
    return "general"


def upgrade() -> None:
    """Upgrade is idempotent and safe to re-run after partial failure:
    - DDL guarded by `_table_exists`/`_column_exists`/`_index_exists`.
    - Data inserts guarded by the partial unique index pre-check (see step 4).
    - `state='template'` → `state='mature'` revert is idempotent (`WHERE state='template'`).

    If the invariant check at the end raises RuntimeError, inspect logs, fix any
    data issue (e.g., orphaned rows, unexpected states), and re-run — the second
    run will skip already-migrated DDL/rows and converge.

    Column-name corrections vs. original template spec:
    - `optimizations.selected_strategy` → actual column is `strategy_used`
    - `optimization_patterns.pattern_id` → actual column is `meta_pattern_id`
    """
    bind = op.get_bind()

    # 1. Create prompt_templates
    if not _table_exists(bind, "prompt_templates"):
        op.create_table(
            "prompt_templates",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("source_cluster_id", sa.String,
                      sa.ForeignKey("prompt_cluster.id", ondelete="SET NULL"),
                      nullable=True, index=True),
            sa.Column("source_optimization_id", sa.String,
                      sa.ForeignKey("optimizations.id", ondelete="SET NULL"),
                      nullable=True),
            sa.Column("project_id", sa.String,
                      sa.ForeignKey("prompt_cluster.id", ondelete="SET NULL"),
                      nullable=True, index=True),
            sa.Column("label", sa.String, nullable=False),
            sa.Column("prompt", sa.Text, nullable=False),
            sa.Column("strategy", sa.String, nullable=True),
            sa.Column("score", sa.Float, nullable=False),
            sa.Column("pattern_ids", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("domain_label", sa.String, nullable=False),
            sa.Column("promoted_at", sa.DateTime, nullable=False),
            sa.Column("retired_at", sa.DateTime, nullable=True, index=True),
            sa.Column("retired_reason", sa.String(50), nullable=True),
            sa.Column("usage_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_used_at", sa.DateTime, nullable=True),
        )

    # 2. Partial indexes (SQLite: use raw DDL)
    if not _index_exists(bind, "prompt_templates", "uq_template_source_optimization_live"):
        op.execute(
            "CREATE UNIQUE INDEX uq_template_source_optimization_live "
            "ON prompt_templates (source_cluster_id, source_optimization_id) "
            "WHERE source_cluster_id IS NOT NULL "
            "AND source_optimization_id IS NOT NULL "
            "AND retired_at IS NULL"
        )
    if not _index_exists(bind, "prompt_templates", "idx_template_project_domain_active"):
        op.execute(
            "CREATE INDEX idx_template_project_domain_active "
            "ON prompt_templates (project_id, domain_label, promoted_at) "
            "WHERE retired_at IS NULL"
        )

    # 3. Add template_count column (batch rebuild for SQLite NOT NULL DEFAULT)
    if not _column_exists(bind, "prompt_cluster", "template_count"):
        with op.batch_alter_table("prompt_cluster") as batch:
            batch.add_column(sa.Column(
                "template_count", sa.Integer, nullable=False, server_default="0"
            ))

    # 3a. Self-heal pre-existing ORM/alembic drift: `optimizations.project_id`
    # is declared in `app/models.py` (ADR-005 denormalized FK) but no prior
    # alembic revision adds it. Databases bootstrapped via
    # `Base.metadata.create_all()` already have it; databases migrated purely
    # via `alembic upgrade` from scratch do not. This migration's data step
    # (section 5) reads `optimizations.project_id`, so defensively add it
    # here if missing. Idempotent via `_column_exists`.
    #
    # Note: no inline ForeignKey here. The referential-integrity intent is
    # declared ORM-side in `app/models.py` (FK to `prompt_cluster.id`,
    # `ondelete="SET NULL"`) — matching what `Base.metadata.create_all()`
    # produces for a fresh database.
    if not _column_exists(bind, "optimizations", "project_id"):
        with op.batch_alter_table("optimizations") as batch:
            batch.add_column(sa.Column("project_id", sa.String, nullable=True))

    # 4. Build parent-chain lookup for root_domain_label
    domain_rows = bind.execute(sa.text(
        "SELECT id, parent_id, label, state FROM prompt_cluster"
    )).fetchall()
    parent_by_id = {r.id: r.parent_id for r in domain_rows}
    label_by_id = {r.id: r.label for r in domain_rows}
    state_by_id = {r.id: r.state for r in domain_rows}

    # 5. Data migration: state='template' → prompt_templates row + state='mature'
    #    NOTE: optimizations column is `strategy_used` (not `selected_strategy`)
    template_clusters = bind.execute(sa.text(
        "SELECT id, label, domain FROM prompt_cluster WHERE state = 'template'"
    )).fetchall()

    # naive UTC — consistent with SQLAlchemy DateTime (no timezone) used throughout
    # the schema; equivalent to models._utcnow() at the SQLite storage layer.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for cluster in template_clusters:
        top_opt = bind.execute(sa.text(
            "SELECT id, optimized_prompt, strategy_used, overall_score, project_id "
            "FROM optimizations WHERE cluster_id = :cid "
            "ORDER BY overall_score DESC, created_at DESC LIMIT 1"
        ), {"cid": cluster.id}).fetchone()

        if top_opt is None:
            logger.warning(
                "state='template' cluster %s has no optimizations; reverting state only",
                cluster.id,
            )
        else:
            existing = bind.execute(sa.text(
                "SELECT id FROM prompt_templates "
                "WHERE source_cluster_id = :cid "
                "AND source_optimization_id = :oid "
                "AND retired_at IS NULL"
            ), {"cid": cluster.id, "oid": top_opt.id}).fetchone()

            if not existing:
                domain_label = _root_domain_label(cluster.id, parent_by_id, label_by_id, state_by_id)
                # NOTE: optimization_patterns uses `meta_pattern_id` (not `pattern_id`)
                pattern_ids = [row.meta_pattern_id for row in bind.execute(sa.text(
                    "SELECT meta_pattern_id FROM optimization_patterns "
                    "WHERE optimization_id = :oid AND meta_pattern_id IS NOT NULL"
                ), {"oid": top_opt.id}).fetchall()]

                bind.execute(sa.text("""
                    INSERT INTO prompt_templates (
                        id, source_cluster_id, source_optimization_id, project_id,
                        label, prompt, strategy, score, pattern_ids, domain_label,
                        promoted_at, usage_count
                    ) VALUES (
                        :id, :cid, :oid, :pid,
                        :label, :prompt, :strategy, :score, :patterns, :domain,
                        :promoted, 0
                    )
                """), {
                    "id": uuid.uuid4().hex,
                    "cid": cluster.id,
                    "oid": top_opt.id,
                    "pid": top_opt.project_id,
                    "label": cluster.label or "untitled",
                    "prompt": top_opt.optimized_prompt or "",
                    "strategy": top_opt.strategy_used,
                    "score": float(top_opt.overall_score or 0),
                    "patterns": json.dumps(pattern_ids),
                    "domain": domain_label,
                    "promoted": now,
                })

        bind.execute(sa.text(
            "UPDATE prompt_cluster SET state = 'mature' "
            "WHERE id = :cid AND state = 'template'"
        ), {"cid": cluster.id})

    # 6. Backfill template_count (idempotent — overwrites)
    bind.execute(sa.text("""
        UPDATE prompt_cluster SET template_count = (
            SELECT COUNT(*) FROM prompt_templates
            WHERE source_cluster_id = prompt_cluster.id
              AND retired_at IS NULL
        )
    """))

    # 7. Invariant assertion
    remaining = bind.execute(sa.text(
        "SELECT COUNT(*) FROM prompt_cluster WHERE state = 'template'"
    )).scalar()
    if remaining:
        raise RuntimeError(
            f"migration invariant failed: {remaining} clusters still in state='template'"
        )


def downgrade() -> None:
    raise NotImplementedError(
        "Template architecture migration is forward-only. "
        "To revert, restore from a pre-migration backup of data/synthesis.db."
    )
