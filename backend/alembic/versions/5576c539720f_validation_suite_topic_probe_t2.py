"""validation_suite_topic_probe_t2

Create the ``validation_suite`` table + extend ``run_row`` with the
``fk_run_row_suite_id`` FK + ``ix_run_row_suite_id`` index per Tier 2
spec section 3 (``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``).

A ``validation_suite`` is a frozen prompt fixture forked from a completed
``topic_probe`` ``RunRow``. Replays write new ``RunRow(mode='replay_run',
suite_id=...)`` rows; the regression alarm joins on ``suite_id`` and
compares each replay's aggregate against ``baseline_scores``.

Schema (11 columns):

* ``id`` (String PK)
* ``source_run_id`` (String FK → ``run_row.id``, ``ondelete=SET NULL``,
  nullable — SET NULL semantics require nullable; the suite stays intact
  for replay-history audit even after its source run is later deleted.
  NOT NULL would directly contradict ``ondelete=SET NULL``.)
* ``prompts_snapshot`` (JSON NOT NULL)
* ``baseline_scores`` (JSON NOT NULL)
* ``tolerance_abs`` (Float NOT NULL DEFAULT 0.5)
* ``label`` (String(120) NOT NULL)
* ``project_id`` (String FK → ``prompt_cluster.id``, ``ondelete=SET NULL``,
  nullable — ADR-005 multi-project)
* ``repo_full_name`` (String nullable — informational ``repo_drift`` flag
  on replay)
* ``created_at`` (DateTime NOT NULL)
* ``retired_at`` (DateTime nullable — soft-delete; immutable suites)
* ``retired_reason`` (String(500) nullable — audit trail)

Indexes (3):

* ``ix_validation_suite_project_id`` — single-column on ``project_id``,
  backs the project-filtered list endpoint.
* ``ix_validation_suite_source_run_id`` — single-column on
  ``source_run_id``, backs the "which suites came from this run"
  lookup.
* ``ix_validation_suite_active`` — partial index on ``created_at DESC``
  ``WHERE retired_at IS NULL``. Serves the default list ordering
  (reverse-chrono, active suites only).

``RunRow`` extension:

* ``fk_run_row_suite_id`` — named FK on ``run_row.suite_id`` →
  ``validation_suite.id`` ``ondelete=SET NULL``. The ``suite_id`` column
  itself was added in P3 prework migration ``58510d3f6b81`` without a
  ``ForeignKey()`` declaration; T2 supplies the constraint.
* ``ix_run_row_suite_id`` — composite on ``(suite_id, started_at DESC)``.
  Backs the regression-alarm "latest replay per suite" query.

SQLite cannot ``ALTER`` an existing table to add a constraint in place;
``batch_alter_table(recreate="always")`` triggers the canonical
table-rebuild workaround. Precedent:
``alembic/versions/a2f6d8e31b09_cascade_optimization_fks.py`` lines 76
and 97 — the only other FK-modifying migration in tree uses this exact
pattern.

Idempotency: ``inspector.has_table("validation_suite")`` guards the
table creation block per the
``bdd8e96cf489_consolidate_lifespan_ddl_into_alembic.py`` precedent.

Revision ID: 5576c539720f
Revises: 58510d3f6b81
Create Date: 2026-05-11
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5576c539720f"
down_revision: str | Sequence[str] | None = "58510d3f6b81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``validation_suite`` + extend ``run_row`` with FK + index."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("validation_suite"):
        op.create_table(
            "validation_suite",
            sa.Column("id", sa.String(), primary_key=True),
            # SET NULL semantics require nullable=True; suite stays intact when
            # source run row is later deleted. NOT NULL would conflict.
            sa.Column("source_run_id", sa.String(), nullable=True),
            sa.Column("prompts_snapshot", sa.JSON(), nullable=False),
            sa.Column("baseline_scores", sa.JSON(), nullable=False),
            sa.Column(
                "tolerance_abs",
                sa.Float(),
                nullable=False,
                server_default="0.5",
            ),
            sa.Column("label", sa.String(120), nullable=False),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("repo_full_name", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.Column("retired_reason", sa.String(500), nullable=True),
            sa.ForeignKeyConstraint(
                ["source_run_id"], ["run_row.id"], ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["prompt_cluster.id"], ondelete="SET NULL",
            ),
        )
        op.create_index(
            "ix_validation_suite_project_id",
            "validation_suite",
            ["project_id"],
        )
        op.create_index(
            "ix_validation_suite_source_run_id",
            "validation_suite",
            ["source_run_id"],
        )
        # DESC on created_at matches default reverse-chrono list ordering.
        # Partial predicate `retired_at IS NULL` keeps the index tight on the
        # active set (immutable suites means the predicate is stable per row).
        op.create_index(
            "ix_validation_suite_active",
            "validation_suite",
            [sa.text("created_at DESC")],
            postgresql_where=sa.text("retired_at IS NULL"),
            sqlite_where=sa.text("retired_at IS NULL"),
        )

    # SQLite cannot ALTER a constraint in place; batch_alter_table with
    # recreate="always" triggers the canonical table-rebuild workaround.
    # Precedent: alembic/versions/a2f6d8e31b09_cascade_optimization_fks.py
    # lines 76 + 97. Without recreate="always" the FK addition silently
    # no-ops on SQLite.
    with op.batch_alter_table("run_row", recreate="always") as batch:
        batch.create_foreign_key(
            "fk_run_row_suite_id",
            "validation_suite",
            ["suite_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # DESC on started_at serves the "latest replay per suite_id"
    # regression-alarm query pattern.
    op.create_index(
        "ix_run_row_suite_id",
        "run_row",
        ["suite_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    """Reverse the upgrade — preserve no dangling references."""
    # Step 1: null out run_row.suite_id BEFORE the table is dropped so no
    # dangling-reference state persists if the migration is later re-upgraded.
    op.execute("UPDATE run_row SET suite_id = NULL WHERE suite_id IS NOT NULL")
    op.drop_index("ix_run_row_suite_id", table_name="run_row")
    # recreate="always" is also required on downgrade for SQLite to actually
    # drop the FK constraint via table rebuild.
    with op.batch_alter_table("run_row", recreate="always") as batch:
        batch.drop_constraint("fk_run_row_suite_id", type_="foreignkey")
    op.drop_index("ix_validation_suite_active", table_name="validation_suite")
    op.drop_index("ix_validation_suite_source_run_id", table_name="validation_suite")
    op.drop_index("ix_validation_suite_project_id", table_name="validation_suite")
    op.drop_table("validation_suite")
    # NOTE: existing run_row rows with mode='replay_run' preserved as harmless
    # strings (no longer joinable to a suite, but consistent with the spec's
    # "harmless on downgrade" contract — the row metadata is intact, only the
    # FK relationship is gone).
