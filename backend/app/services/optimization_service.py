"""Optimization CRUD service.

Provides async database operations for creating, listing, retrieving,
and deleting optimization records via SQLAlchemy async sessions.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.optimization import Optimization

logger = logging.getLogger(__name__)

VALID_SORT_COLUMNS: frozenset[str] = frozenset({
    "created_at", "overall_score", "task_type", "updated_at",
    "duration_ms", "primary_framework", "status",
})


async def create_optimization(
    session: AsyncSession,
    *,
    raw_prompt: str,
    title: Optional[str] = None,
    project: Optional[str] = None,
    tags: Optional[list[str]] = None,
    repo_full_name: Optional[str] = None,
    repo_branch: Optional[str] = None,
) -> Optimization:
    """Create a new optimization record in pending state.

    Args:
        session: Async database session.
        raw_prompt: The raw prompt text to optimize.
        title: Optional title for the optimization.
        project: Optional project grouping key.
        tags: Optional list of tag strings.
        repo_full_name: Optional linked GitHub repo (owner/repo).
        repo_branch: Optional branch name for the linked repo.

    Returns:
        The newly created Optimization ORM instance.
    """
    optimization = Optimization(
        raw_prompt=raw_prompt,
        title=title,
        project=project,
        tags=json.dumps(tags) if tags else "[]",
        linked_repo_full_name=repo_full_name,
        linked_repo_branch=repo_branch,
        status="pending",
    )
    session.add(optimization)
    await session.flush()
    await session.refresh(optimization)
    logger.info("Created optimization %s (status=pending)", optimization.id)
    return optimization


async def list_optimizations(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    project: Optional[str] = None,
    task_type: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
) -> tuple[list[dict], int]:
    """List optimizations with pagination, filtering, and sorting.

    Args:
        session: Async database session.
        limit: Maximum number of results to return.
        offset: Number of results to skip.
        project: Filter by project name.
        task_type: Filter by task type classification.
        search: Search term to match against raw_prompt and title.
        sort: Column name to sort by.
        order: Sort direction ('asc' or 'desc').

    Returns:
        Tuple of (list of optimization dicts, total count).
    """
    query = select(Optimization).where(Optimization.deleted_at.is_(None))
    count_query = select(func.count(Optimization.id)).where(Optimization.deleted_at.is_(None))

    if project:
        query = query.where(Optimization.project == project)
        count_query = count_query.where(Optimization.project == project)

    if task_type:
        query = query.where(Optimization.task_type == task_type)
        count_query = count_query.where(Optimization.task_type == task_type)

    if search:
        search_pattern = f"%{search}%"
        search_filter = (
            Optimization.raw_prompt.ilike(search_pattern)
            | Optimization.title.ilike(search_pattern)
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Sorting — whitelist prevents getattr on arbitrary user input
    if sort not in VALID_SORT_COLUMNS:
        sort = "created_at"
    sort_column = getattr(Optimization, sort)
    if order == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    query = query.limit(limit).offset(offset)

    result = await session.execute(query)
    optimizations = result.scalars().all()

    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    return [opt.to_dict() for opt in optimizations], total


async def compute_stats(
    session: AsyncSession,
    project: Optional[str] = None,
) -> dict:
    """Compute aggregated stats using SQL aggregates (O(1) memory)."""
    base_filter = [Optimization.deleted_at.is_(None)]
    if project:
        base_filter.append(Optimization.project == project)

    totals_result = await session.execute(
        select(
            func.count(Optimization.id).label("total"),
            func.avg(Optimization.overall_score).label("avg_score"),
            func.sum(case((Optimization.linked_repo_full_name.isnot(None), 1), else_=0)).label("codebase_aware"),
            func.sum(case((Optimization.is_improvement.is_(True), 1), else_=0)).label("improvements"),
            func.count(Optimization.is_improvement).label("validated"),
        ).where(*base_filter)
    )
    totals = totals_result.one()
    total = totals.total or 0
    avg_score = round(float(totals.avg_score), 2) if totals.avg_score is not None else None
    improvement_rate = (
        round(totals.improvements / totals.validated, 3) if totals.validated else None
    )

    tt_result = await session.execute(
        select(Optimization.task_type, func.count(Optimization.id))
        .where(*base_filter, Optimization.task_type.isnot(None))
        .group_by(Optimization.task_type)
        .order_by(func.count(Optimization.id).desc())
    )
    task_type_breakdown = {row[0]: row[1] for row in tt_result.fetchall()}

    fw_result = await session.execute(
        select(Optimization.primary_framework, func.count(Optimization.id))
        .where(*base_filter, Optimization.primary_framework.isnot(None))
        .group_by(Optimization.primary_framework)
        .order_by(func.count(Optimization.id).desc())
    )
    framework_breakdown = {row[0]: row[1] for row in fw_result.fetchall()}

    pv_result = await session.execute(
        select(Optimization.provider_used, func.count(Optimization.id))
        .where(*base_filter, Optimization.provider_used.isnot(None))
        .group_by(Optimization.provider_used)
        .order_by(func.count(Optimization.id).desc())
    )
    provider_breakdown = {row[0]: row[1] for row in pv_result.fetchall()}

    model_usage: dict[str, int] = {}
    for field_name in ("model_explore", "model_analyze", "model_strategy",
                       "model_optimize", "model_validate"):
        col = getattr(Optimization, field_name)
        mv_result = await session.execute(
            select(col, func.count(Optimization.id))
            .where(*base_filter, col.isnot(None))
            .group_by(col)
        )
        for row in mv_result.fetchall():
            model_usage[row[0]] = model_usage.get(row[0], 0) + row[1]

    return {
        "total_optimizations": total,
        "average_score": avg_score,
        "task_type_breakdown": task_type_breakdown,
        "framework_breakdown": framework_breakdown,
        "provider_breakdown": provider_breakdown,
        "model_usage": model_usage,
        "codebase_aware_count": totals.codebase_aware or 0,
        "improvement_rate": improvement_rate,
    }


async def get_optimization(
    session: AsyncSession,
    optimization_id: str,
) -> Optional[dict]:
    """Get a single optimization by ID.

    Args:
        session: Async database session.
        optimization_id: The UUID of the optimization to retrieve.

    Returns:
        Optimization dict if found, None otherwise.
    """
    result = await session.execute(
        select(Optimization).where(
            Optimization.id == optimization_id,
            Optimization.deleted_at.is_(None),
        )
    )
    opt = result.scalar_one_or_none()
    if opt is None:
        return None
    return opt.to_dict()


async def get_optimization_orm(
    session: AsyncSession,
    optimization_id: str,
) -> Optional[Optimization]:
    """Get a single optimization ORM object by ID.

    Args:
        session: Async database session.
        optimization_id: The UUID of the optimization to retrieve.

    Returns:
        Optimization ORM instance if found, None otherwise.
    """
    result = await session.execute(
        select(Optimization).where(
            Optimization.id == optimization_id,
            Optimization.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def update_optimization(
    session: AsyncSession,
    optimization_id: str,
    **kwargs,
) -> Optional[dict]:
    """Update fields on an existing optimization.

    Args:
        session: Async database session.
        optimization_id: The UUID of the optimization to update.
        **kwargs: Fields to update (column_name=value pairs).

    Returns:
        Updated optimization dict if found, None otherwise.
    """
    opt = await get_optimization_orm(session, optimization_id)
    if opt is None:
        return None

    for key, value in kwargs.items():
        if hasattr(opt, key):
            # JSON-encode list fields
            if key in ("weaknesses", "strengths", "changes_made", "issues", "tags", "secondary_frameworks"):
                if isinstance(value, list):
                    value = json.dumps(value)
            setattr(opt, key, value)

    await session.flush()
    await session.refresh(opt)
    return opt.to_dict()


async def delete_optimization(
    session: AsyncSession,
    optimization_id: str,
) -> bool:
    """Soft-delete an optimization by setting deleted_at.

    Args:
        session: Async database session.
        optimization_id: The UUID of the optimization to soft-delete.

    Returns:
        True if soft-deleted, False if not found.
    """
    opt = await get_optimization_orm(session, optimization_id)
    if opt is None:
        return False

    opt.deleted_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info("Soft-deleted optimization %s", optimization_id)
    return True
