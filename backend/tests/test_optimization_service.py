"""Tests for optimization_service CRUD functions."""
import json
from unittest.mock import AsyncMock, MagicMock


async def test_update_optimization_encodes_secondary_frameworks():
    """update_optimization must JSON-encode secondary_frameworks list, not store repr."""
    mock_opt = MagicMock()
    mock_opt.id = "test-id"
    mock_opt.secondary_frameworks = None
    mock_opt.to_dict.return_value = {"secondary_frameworks": ["CO-STAR", "RISEN"]}

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_opt
    mock_session.execute.return_value = mock_result

    from app.services.optimization_service import update_optimization
    await update_optimization(mock_session, "test-id", secondary_frameworks=["CO-STAR", "RISEN"])

    assert mock_opt.secondary_frameworks == json.dumps(["CO-STAR", "RISEN"])


def test_valid_sort_columns_exported():
    from app.services.optimization_service import VALID_SORT_COLUMNS
    assert "created_at" in VALID_SORT_COLUMNS
    assert "overall_score" in VALID_SORT_COLUMNS
    assert "status" in VALID_SORT_COLUMNS
    assert "primary_framework" in VALID_SORT_COLUMNS
    assert "raw_prompt" not in VALID_SORT_COLUMNS


async def test_compute_stats_empty_db(tmp_path):
    """compute_stats returns zero-state dict when no optimizations exist."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.models.optimization  # noqa
    from app.database import Base

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stats.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        from app.services.optimization_service import compute_stats
        result = await compute_stats(session)

    assert result["total_optimizations"] == 0
    assert result["average_score"] is None
    assert result["task_type_breakdown"] == {}
    assert result["framework_breakdown"] == {}
    assert result["provider_breakdown"] == {}
    assert result["model_usage"] == {}
    assert result["codebase_aware_count"] == 0
    assert result["improvement_rate"] is None
    await eng.dispose()


async def test_compute_stats_respects_project_filter(tmp_path):
    """compute_stats must not raise when project filter is provided."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.models.optimization  # noqa
    from app.database import Base

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stats2.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        from app.services.optimization_service import compute_stats
        result = await compute_stats(session, project="my-project")

    assert result["total_optimizations"] == 0
    assert result["framework_breakdown"] == {}
    assert result["provider_breakdown"] == {}
    assert result["model_usage"] == {}
    assert result["codebase_aware_count"] == 0
    assert result["improvement_rate"] is None
    await eng.dispose()


async def test_list_optimizations_with_user_id_filter(tmp_path):
    """list_optimizations must filter by user_id when provided."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.models.optimization  # noqa: F401
    from app.database import Base
    from app.models.optimization import Optimization
    from app.services.optimization_service import list_optimizations

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/list_user.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    # Seed two records with different user_ids
    async with Session() as session:
        session.add(Optimization(id="opt-a", raw_prompt="prompt a", user_id="user-1", status="completed"))
        session.add(Optimization(id="opt-b", raw_prompt="prompt b", user_id="user-2", status="completed"))
        await session.commit()

    async with Session() as session:
        items, total = await list_optimizations(session, user_id="user-1")

    assert total == 1
    assert items[0]["id"] == "opt-a"
    await eng.dispose()
