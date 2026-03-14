import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def utcnow():
    """Shared UTC-now factory for all ORM model default/onupdate columns."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


# Ensure data directory exists for SQLite
db_url = settings.DATABASE_URL
if "sqlite" in db_url:
    db_path = db_url.split("///")[-1]
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

def _register_sqlite_pragmas(eng) -> None:
    """Register SQLite PRAGMA tuning on every new connection."""
    if "sqlite" not in str(eng.url):
        return

    @event.listens_for(eng.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

_register_sqlite_pragmas(engine)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions outside FastAPI DI."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _migrate_add_missing_columns() -> None:
    """Add any new columns to existing tables (SQLite ALTER TABLE migration).

    SQLAlchemy create_all is idempotent for table creation but does NOT add
    new columns to existing tables. This function bridges that gap by issuing
    ALTER TABLE … ADD COLUMN statements when a column is absent.

    New columns must be added here AND to the SQLAlchemy model.
    """
    import sqlalchemy as sa

    # Map: table_name -> {column_name: column_type_sql}
    _new_columns: dict[str, dict[str, str]] = {
        "user_adaptation": {
            "issue_frequency": "TEXT",
            "adaptation_version": "INTEGER DEFAULT 0",
            "damping_level": "REAL DEFAULT 0.15",
            "consistency_score": "REAL DEFAULT 0.5",
        },
        "optimizations": {
            "secondary_frameworks": "TEXT",
            "approach_notes": "TEXT",
            "strategy_source": "TEXT",
            "deleted_at": "DATETIME",          # soft-delete
            "user_id": "TEXT",                 # authenticated user who created this record
            "analysis_quality": "VARCHAR(20)",  # pipeline quality flag for analysis stage
            "validation_quality": "VARCHAR(20)", # pipeline quality flag for validation stage
            "row_version": "INTEGER NOT NULL DEFAULT 0",  # optimistic locking counter
            "stage_durations": "TEXT",           # JSON dict of per-stage timing
            "total_input_tokens": "INTEGER",       # H2: cost tracking
            "total_output_tokens": "INTEGER",
            "total_cache_read_tokens": "INTEGER",
            "total_cache_creation_tokens": "INTEGER",
            "estimated_cost_usd": "REAL",
            "usage_is_estimated": "BOOLEAN",
            "model_explore": "TEXT",              # per-stage model tracking
            "model_analyze": "TEXT",
            "model_strategy": "TEXT",
            "model_optimize": "TEXT",
            "model_validate": "TEXT",
            "retry_history": "TEXT",
            "per_instruction_compliance": "TEXT",
            "session_id": "TEXT",
            "refinement_turns": "INTEGER DEFAULT 0",
            "active_branch_id": "TEXT",
            "branch_count": "INTEGER DEFAULT 0",
            "adaptation_snapshot": "TEXT",
            "framework": "TEXT",
            "active_guardrails": "TEXT",
        },
        "github_tokens": {
            "avatar_url": "TEXT",              # cached avatar URL
        },
        "refresh_tokens": {
            "device_id": "TEXT",               # per-device revocation (nullable; absent on legacy tokens)
        },
        "users": {
            "email": "TEXT",
            "avatar_url": "TEXT",
            "display_name": "TEXT",
            "onboarding_completed_at": "DATETIME",
            "onboarding_step": "INTEGER",
            "preferences": "TEXT",
            "last_login_at": "DATETIME",
        },
        "repo_index_meta": {
            "head_sha": "TEXT",  # branch HEAD commit SHA when index was built
        },
    }

    async with engine.begin() as conn:
        for table_name, columns in _new_columns.items():
            # Fetch existing column names via PRAGMA (SQLite) or information_schema
            if "sqlite" in str(engine.url):
                existing_cols_result = await conn.execute(
                    sa.text(f"PRAGMA table_info({table_name})")
                )
                existing_cols = {row[1] for row in existing_cols_result.fetchall()}
            else:
                existing_cols_result = await conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ),
                    {"t": table_name},
                )
                existing_cols = {row[0] for row in existing_cols_result.fetchall()}

            for col_name, col_type in columns.items():
                if col_name not in existing_cols:
                    await conn.execute(
                        sa.text(
                            f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
                        )
                    )
                    logger.info("Migration: added column %s.%s", table_name, col_name)


async def _migrate_add_missing_indexes(eng: AsyncEngine | None = None) -> None:
    """Idempotently create missing indices on existing tables.

    Checks sqlite_master (SQLite) or pg_indexes (PostgreSQL) before issuing
    CREATE INDEX — safe to run on every startup.

    Args:
        eng: Optional engine to use. Defaults to the module-level engine.
    """
    import sqlalchemy as sa

    _eng = eng or engine

    _new_indexes: list[tuple[str, str, str]] = [
        # (index_name, table_name, column_name)
        ("idx_optimizations_status", "optimizations", "status"),
        ("idx_optimizations_overall_score", "optimizations", "overall_score"),
        ("idx_optimizations_primary_framework", "optimizations", "primary_framework"),
        ("idx_optimizations_is_improvement", "optimizations", "is_improvement"),
        ("idx_optimizations_linked_repo", "optimizations", "linked_repo_full_name"),
        ("idx_optimizations_retry_of", "optimizations", "retry_of"),
        ("idx_optimizations_user_listing", "optimizations",
         "user_id, deleted_at, created_at DESC"),
        # H3 tables — indexes also defined in ORM __table_args__ (safety net for migrations)
        ("ix_feedback_user_created", "feedback", "user_id, created_at"),
        ("ix_feedback_optimization_id", "feedback", "optimization_id"),
        ("ix_branch_optimization", "refinement_branch", "optimization_id"),
        ("ix_branch_opt_status", "refinement_branch", "optimization_id, status"),
        ("ix_pairwise_user", "pairwise_preference", "user_id"),
        ("ix_pairwise_optimization", "pairwise_preference", "optimization_id"),
        ("ix_pairwise_user_created", "pairwise_preference", "user_id, created_at"),
        # H4 tables — indexes also defined in ORM __table_args__ (safety net for migrations)
        ("ix_framework_perf_user_task", "framework_performance", "user_id, task_type"),
        ("ix_adaptation_events_user_created", "adaptation_events", "user_id, created_at"),
    ]

    async with _eng.begin() as conn:
        if "sqlite" in str(_eng.url):
            existing_result = await conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='index'")
            )
            existing_indexes = {row[0] for row in existing_result.fetchall()}
        else:
            existing_result = await conn.execute(
                sa.text("SELECT indexname FROM pg_indexes")
            )
            existing_indexes = {row[0] for row in existing_result.fetchall()}

        for idx_name, tbl, col in _new_indexes:
            if idx_name not in existing_indexes:
                await conn.execute(
                    sa.text(f"CREATE INDEX {idx_name} ON {tbl} ({col})")
                )
                logger.info("Migration: created index %s on %s.%s", idx_name, tbl, col)


async def create_tables():
    """Create all tables on startup. Acts as simple migration."""
    # Import all models so they register with Base.metadata
    import app.models.adaptation_event  # noqa: F401
    import app.models.audit_log  # noqa: F401
    import app.models.auth  # noqa: F401
    import app.models.branch  # noqa: F401
    import app.models.feedback  # noqa: F401
    import app.models.framework_performance  # noqa: F401
    import app.models.github  # noqa: F401
    import app.models.onboarding_event  # noqa: F401
    import app.models.optimization  # noqa: F401
    import app.models.repo_index  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Idempotently add any new columns to existing tables
    await _migrate_add_missing_columns()
    await _migrate_add_missing_indexes()

    logger.info("Database tables created/verified")


async def check_db_connection() -> bool:
    """Check if the database is reachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
        return True
    except Exception as e:
        logger.error("Database connection check failed: %s", e)
        return False
