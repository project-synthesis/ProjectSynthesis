"""Add ondelete=CASCADE to FKs referencing optimizations.id.

Four FK columns reference ``optimizations.id`` without a cascade rule,
forcing any deletion path to hand-roll cascade ordering (see
``services/gc.py::_gc_failed_optimizations``). This migration aligns the
database with the "prompts are the primitive, everything else is derived"
architecture contract: deleting an Optimization row cascades to its
Feedback / OptimizationPattern / RefinementBranch / RefinementTurn
dependents at the storage layer.

Already correct and left unchanged:
- ``prompt_templates.source_optimization_id`` (``ondelete="SET NULL"`` —
  templates are immutable forks that outlive their source optimization)
- ``prompt_templates.source_cluster_id`` (``ondelete="SET NULL"``)

SQLite cannot ALTER a constraint in place; ``batch_alter_table`` with
``recreate="always"`` is the canonical workaround used elsewhere in this
repo (see ``f1e2d3c4b5a6_template_entity.py``).

Revision ID: a2f6d8e31b09
Revises: cc9c44e78f78
Create Date: 2026-04-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a2f6d8e31b09"
down_revision: str | Sequence[str] | None = "cc9c44e78f78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, fk_target) — order matters for a human reviewer, not DDL.
_FK_TARGETS: tuple[tuple[str, str], ...] = (
    ("feedbacks", "optimization_id"),
    ("optimization_patterns", "optimization_id"),
    ("refinement_branches", "optimization_id"),
    ("refinement_turns", "optimization_id"),
)


def _fk_name(inspector: sa.Inspector, table: str, column: str) -> str | None:
    """Return the actual FK constraint name for (table, column) if one exists."""
    for fk in inspector.get_foreign_keys(table):
        if fk.get("constrained_columns") == [column]:
            return fk.get("name")
    return None


def upgrade() -> None:
    """Recreate each of the four constraints with ``ondelete="CASCADE"``.

    Idempotent: checks the existing FK options before rebuilding. A repeat
    run on an already-migrated DB is a no-op.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, column in _FK_TARGETS:
        # Skip if already CASCADE (idempotency guard).
        existing = [
            fk for fk in inspector.get_foreign_keys(table)
            if fk.get("constrained_columns") == [column]
        ]
        if existing and (existing[0].get("options") or {}).get("ondelete", "").upper() == "CASCADE":
            continue

        old_name = _fk_name(inspector, table, column)
        new_name = f"fk_{table}_{column}_optimizations"

        with op.batch_alter_table(table, recreate="always") as batch:
            if old_name:
                batch.drop_constraint(old_name, type_="foreignkey")
            batch.create_foreign_key(
                new_name,
                "optimizations",
                [column],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade() -> None:
    """Restore the plain FK (no cascade rule)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table, column in _FK_TARGETS:
        old_name = _fk_name(inspector, table, column)
        new_name = f"fk_{table}_{column}_optimizations"

        with op.batch_alter_table(table, recreate="always") as batch:
            if old_name:
                batch.drop_constraint(old_name, type_="foreignkey")
            batch.create_foreign_key(
                new_name,
                "optimizations",
                [column],
                ["id"],
            )
