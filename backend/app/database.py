import logging
import os
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


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
        "optimizations": {
            "secondary_frameworks": "TEXT",
            "approach_notes": "TEXT",
            "strategy_source": "TEXT",
            "deleted_at": "DATETIME",          # soft-delete
            "user_id": "TEXT",                 # authenticated user who created this record
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
            "last_login_at": "DATETIME",
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


async def _migrate_add_missing_indexes(eng=None) -> None:
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
    import app.models.auth  # noqa: F401
    import app.models.github  # noqa: F401
    import app.models.optimization  # noqa: F401

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
        logger.error(f"Database connection check failed: {e}")
        return False
