"""Alembic environment configuration for async SQLAlchemy."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.types import JSON, Float, Numeric

from alembic import context
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to):
    """Exclude FK constraints from autogenerate — SQLite does not reliably
    preserve FK metadata through ALTER TABLE RENAME operations."""
    if type_ == "foreign_key_constraint":
        return False
    return True


def _compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """Suppress spurious SQLite affinity drift on JSON↔TEXT and Float↔REAL.

    SQLite has only 5 storage classes (NULL, INTEGER, REAL, TEXT, BLOB) — column
    "types" are advisory and resolve to one of those affinities. SQLAlchemy
    declares ``JSON`` and ``Float`` columns; the inspector reflects them as
    ``TEXT`` and ``REAL`` respectively when the table was originally created via
    an older raw-SQL path or a legacy startup-time auto-creation hook (as is
    the case for ``optimizations.phase_weights_json`` and a handful of
    ``global_patterns`` JSON columns). Re-declaring the columns via
    ``batch_alter_table`` is pure churn — same on-disk bytes, same query
    behavior — so we treat the pair as equivalent and let ``alembic check``
    pass cleanly. Real type changes (e.g. INTEGER↔TEXT) still fail-loud because
    they cross affinity boundaries.
    """
    dialect = context.dialect.name if context else ""
    if dialect != "sqlite":
        return None  # let Alembic's default comparison run on non-SQLite

    inspected_cls = type(inspected_type)
    metadata_cls = type(metadata_type)

    # JSON ↔ TEXT (SQLAlchemy stores JSON as a TEXT-affinity column)
    if isinstance(metadata_type, JSON) and inspected_cls.__name__ == "TEXT":
        return False
    if isinstance(inspected_type, JSON) and metadata_cls.__name__ == "TEXT":
        return False

    # Float ↔ REAL (Float is just a REAL with optional precision)
    if isinstance(metadata_type, (Float, Numeric)) and inspected_cls.__name__ == "REAL":
        return False
    if isinstance(inspected_type, (Float, Numeric)) and metadata_cls.__name__ == "REAL":
        return False

    return None  # fall back to Alembic's default comparison


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        include_object=_include_object,
        compare_type=_compare_type,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        include_object=_include_object,
        compare_type=_compare_type,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
