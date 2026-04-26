"""C4 + T1.3-lite — heuristic_baseline_scores + pattern usefulness counters

Revision ID: d3f5a8c91024
Revises: c2d4e6f8a0b2
Create Date: 2026-04-26

Adds:
- ``optimizations.heuristic_baseline_scores`` (JSON nullable) — deterministic
  HeuristicScorer.score_prompt(raw_prompt) snapshot. Distinct from
  ``original_scores`` which is LLM+heuristic blended and contaminated by
  A/B presentation noise.
- ``optimization_patterns.useful_count`` (Integer NOT NULL DEFAULT 0)
- ``optimization_patterns.unused_count`` (Integer NOT NULL DEFAULT 0)
  — incremented post-scoring based on host optimization's overall_score.

Hand-crafted (autogen detected unrelated drift from index/type history;
this migration is intentionally narrow).
"""

import sqlalchemy as sa
from alembic import op

revision = "d3f5a8c91024"
down_revision = "c2d4e6f8a0b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    opt_cols = {c["name"] for c in inspector.get_columns("optimizations")}
    if "heuristic_baseline_scores" not in opt_cols:
        with op.batch_alter_table("optimizations") as batch_op:
            batch_op.add_column(
                sa.Column("heuristic_baseline_scores", sa.JSON(), nullable=True),
            )

    pat_cols = {c["name"] for c in inspector.get_columns("optimization_patterns")}
    add_useful = "useful_count" not in pat_cols
    add_unused = "unused_count" not in pat_cols
    if add_useful or add_unused:
        with op.batch_alter_table("optimization_patterns") as batch_op:
            if add_useful:
                batch_op.add_column(
                    sa.Column(
                        "useful_count",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    ),
                )
            if add_unused:
                batch_op.add_column(
                    sa.Column(
                        "unused_count",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    ),
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    pat_cols = {c["name"] for c in inspector.get_columns("optimization_patterns")}
    drop_unused = "unused_count" in pat_cols
    drop_useful = "useful_count" in pat_cols
    if drop_unused or drop_useful:
        with op.batch_alter_table("optimization_patterns") as batch_op:
            if drop_unused:
                batch_op.drop_column("unused_count")
            if drop_useful:
                batch_op.drop_column("useful_count")

    opt_cols = {c["name"] for c in inspector.get_columns("optimizations")}
    if "heuristic_baseline_scores" in opt_cols:
        with op.batch_alter_table("optimizations") as batch_op:
            batch_op.drop_column("heuristic_baseline_scores")
