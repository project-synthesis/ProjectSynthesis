"""Drop duplicate unnamed FKs left behind by a2f6d8e31b09.

The preceding cascade migration (``a2f6d8e31b09``) used
``batch_alter_table(recreate="always")`` with a ``drop_constraint(name, ...)``
call keyed on the reflected FK name. The original FKs were created without
explicit names, so ``inspector.get_foreign_keys()`` returned ``name=None``
for each — ``_fk_name()`` returned ``None`` and the drop was silently
skipped. Every targeted table ended up with BOTH the old unnamed
``NO ACTION`` FK and the new named ``CASCADE`` FK side-by-side in
``sqlite_master``.

Inspected state after ``a2f6d8e31b09``::

    CREATE TABLE "feedbacks" (
        ...
        CONSTRAINT fk_feedbacks_optimization_id_optimizations
            FOREIGN KEY(optimization_id) REFERENCES optimizations (id)
            ON DELETE CASCADE,
        FOREIGN KEY(optimization_id) REFERENCES optimizations (id)
    )

The cascade still behaves correctly (SQLite honours CASCADE whenever at
least one matching FK declares it), but the duplicate is noise and
could mislead future inspectors that pick the first match.

Fix strategy: raw SQLite table-rebuild dance. Reflect the table with
SQLAlchemy (which dedupes FKs by ``(column, target)`` and keeps the
named CASCADE one), emit a clean ``CREATE TABLE`` from the reflected
metadata, copy rows across, swap the tables, recreate indexes. We avoid
``batch_alter_table`` here because with ``recreate="always"`` it
preserves both FKs when reflecting the source directly from
``sqlite_master``, and with ``copy_from`` + no ops it skips the rebuild
entirely.

Revision ID: b3a7e9f4c2d1
Revises: a2f6d8e31b09
Create Date: 2026-04-20
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.schema import CreateTable

from alembic import op

revision: str = "b3a7e9f4c2d1"
down_revision: str | Sequence[str] | None = "a2f6d8e31b09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES: tuple[str, ...] = (
    "feedbacks",
    "optimization_patterns",
    "refinement_branches",
    "refinement_turns",
)


def _opt_fk_count(bind: sa.engine.Connection, table: str) -> int:
    """Count FKs on (table.optimization_id → optimizations.id).

    PRAGMA is the ground truth — SQLAlchemy's SQL-parse reflection path
    dedupes unnamed duplicates, which is exactly what we're trying to
    detect here.
    """
    rows = bind.exec_driver_sql(
        f"PRAGMA foreign_key_list({table!r})"
    ).fetchall()
    # PRAGMA columns: id, seq, table, from, to, on_update, on_delete, match
    return sum(
        1 for r in rows
        if r[3] == "optimization_id" and r[2] == "optimizations"
    )


def _rebuild_table(bind: sa.engine.Connection, table: str) -> None:
    """Raw SQLite table-rebuild, preserving rows and indexes.

    1. Stash user-defined index DDL (auto-indexes regenerate automatically).
    2. Reflect the live table (reflection dedupes FKs).
    3. Pre-load every FK target table into the same MetaData so the
       ``ForeignKey.column`` resolver can find them when emitting DDL.
    4. ``CREATE TABLE <table>__rebuild_tmp`` from reflected schema.
    5. Copy rows, drop old, rename new into place.
    6. Replay the stashed index DDL.

    FK enforcement is flipped off for the duration of the swap so the
    transient rename can't trip referential checks.
    """
    # Capture user-defined index DDL BEFORE we touch the table —
    # ``DROP TABLE`` will cascade-delete the indexes.
    index_rows = bind.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master "
        f"WHERE type='index' AND tbl_name={table!r} AND sql IS NOT NULL"
    ).fetchall()

    # Reflect the live table; SQLAlchemy dedupes FKs by (cols, target)
    # and keeps the named CASCADE FK, discarding the unnamed duplicate.
    # Pre-load every FK target into the same MetaData so FK resolution
    # works when we emit CREATE TABLE (otherwise ``ForeignKey.column``
    # raises ``NoReferencedTableError``).
    meta = sa.MetaData()
    src = sa.Table(table, meta, autoload_with=bind)
    for fk in src.foreign_key_constraints:
        target_table = fk.elements[0].target_fullname.split(".")[0]
        if target_table not in meta.tables:
            sa.Table(target_table, meta, autoload_with=bind)

    # Clone the reflected table under a temporary name, attached to the
    # SAME MetaData so FK targets resolve.
    tmp_name = f"{table}__rebuild_tmp"
    cols = [c._copy() for c in src.columns]
    # Primary key rides on columns via ``primary_key=True`` — re-emit
    # only the non-PK constraints (FKs, uniques, checks).
    constraints = [
        ck._copy() for ck in src.constraints
        if not isinstance(ck, sa.PrimaryKeyConstraint)
    ]
    tmp_table = sa.Table(tmp_name, meta, *cols, *constraints)

    col_list = ", ".join(f'"{c.name}"' for c in src.columns)

    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        bind.execute(CreateTable(tmp_table))
        bind.exec_driver_sql(
            f'INSERT INTO "{tmp_name}" ({col_list}) '
            f'SELECT {col_list} FROM "{table}"'
        )
        bind.exec_driver_sql(f'DROP TABLE "{table}"')
        bind.exec_driver_sql(f'ALTER TABLE "{tmp_name}" RENAME TO "{table}"')
        # Replay stashed index DDL — the captured CREATE statements
        # already reference the original table name.
        for _name, ddl in index_rows:
            bind.exec_driver_sql(ddl)
    finally:
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    """Rebuild each affected table, collapsing duplicate FKs.

    Idempotent: tables already at one FK are skipped.
    """
    bind = op.get_bind()
    for table in _TABLES:
        if _opt_fk_count(bind, table) <= 1:
            continue
        _rebuild_table(bind, table)


def downgrade() -> None:
    """No-op: we can't faithfully re-introduce an unnamed duplicate FK.

    The old state was a bug; restoring it would require hand-crafted SQL
    and has no operational value. If a rollback is needed, downgrading
    past ``a2f6d8e31b09`` restores the pre-cascade schema cleanly.
    """
