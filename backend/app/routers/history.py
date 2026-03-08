import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.optimization import Optimization
from app.schemas.optimization import HistoryStatsResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["history"])


@router.get("/api/history")
async def list_history(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    project: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    framework: Optional[str] = Query(None),
    has_repo: Optional[bool] = Query(None),
    min_score: Optional[int] = Query(None, ge=1, le=10),
    max_score: Optional[int] = Query(None, ge=1, le=10),
    status: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """List optimization history with pagination, search, sort, and filter."""
    query = select(Optimization)

    # Filters
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Optimization.raw_prompt.ilike(search_pattern))
            | (Optimization.optimized_prompt.ilike(search_pattern))
            | (Optimization.title.ilike(search_pattern))
            | (Optimization.project.ilike(search_pattern))
        )
    if project:
        query = query.where(Optimization.project == project)
    if task_type:
        query = query.where(Optimization.task_type == task_type)
    if framework:
        query = query.where(Optimization.primary_framework == framework)
    if has_repo is True:
        query = query.where(Optimization.linked_repo_full_name.isnot(None))
    elif has_repo is False:
        query = query.where(Optimization.linked_repo_full_name.is_(None))
    if min_score is not None:
        query = query.where(Optimization.overall_score >= min_score)
    if max_score is not None:
        query = query.where(Optimization.overall_score <= max_score)
    if status:
        query = query.where(Optimization.status == status)

    # Count total before pagination
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # Sorting — whitelist prevents getattr on arbitrary user input
    _VALID_SORT_COLUMNS = {"created_at", "overall_score", "task_type", "updated_at",
                           "duration_ms", "primary_framework", "status"}
    if sort not in _VALID_SORT_COLUMNS:
        sort = "created_at"
    sort_column = getattr(Optimization, sort)
    if order == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Pagination
    query = query.offset(offset).limit(limit)

    result = await session.execute(query)
    optimizations = result.scalars().all()

    fetched = len(optimizations)
    has_more = (offset + fetched) < total
    return {
        "total": total,
        "count": fetched,
        "offset": offset,
        "items": [opt.to_dict() for opt in optimizations],
        "has_more": has_more,
        "next_offset": offset + fetched if has_more else None,
    }


@router.delete("/api/history/{optimization_id}")
async def delete_optimization(
    optimization_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete an optimization record."""
    result = await session.execute(
        select(Optimization).where(Optimization.id == optimization_id)
    )
    optimization = result.scalar_one_or_none()
    if not optimization:
        raise HTTPException(status_code=404, detail="Optimization not found")

    await session.delete(optimization)
    await session.commit()
    return {"deleted": True, "id": optimization_id}


@router.get("/api/history/stats")
async def get_stats(
    project: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """Get aggregated statistics about optimization history."""
    base_query = select(Optimization)
    if project:
        base_query = base_query.where(Optimization.project == project)

    result = await session.execute(base_query)
    optimizations = result.scalars().all()

    if not optimizations:
        return HistoryStatsResponse().model_dump()

    total = len(optimizations)
    scores = [o.overall_score for o in optimizations if o.overall_score is not None]
    avg_score = sum(scores) / len(scores) if scores else None

    # Task type breakdown
    task_types: dict[str, int] = {}
    for o in optimizations:
        if o.task_type:
            k = str(o.task_type)
            task_types[k] = task_types.get(k, 0) + 1

    # Framework breakdown
    frameworks: dict[str, int] = {}
    for o in optimizations:
        if o.primary_framework:
            k = str(o.primary_framework)
            frameworks[k] = frameworks.get(k, 0) + 1

    # Provider breakdown
    providers: dict[str, int] = {}
    for o in optimizations:
        if o.provider_used:
            k = str(o.provider_used)
            providers[k] = providers.get(k, 0) + 1

    # Model usage (count all model fields)
    model_usage: dict[str, int] = {}
    for o in optimizations:
        for model_field in ("model_explore", "model_analyze", "model_strategy",
                            "model_optimize", "model_validate"):
            model = getattr(o, model_field)
            if model:
                model_usage[model] = model_usage.get(model, 0) + 1

    # Codebase-aware count
    codebase_aware = sum(
        1 for o in optimizations if o.linked_repo_full_name is not None
    )

    # Improvement rate
    validated = [o for o in optimizations if o.is_improvement is not None]
    improvement_rate = None
    if validated:
        improvements = sum(1 for o in validated if o.is_improvement)
        improvement_rate = improvements / len(validated)

    return {
        "total_optimizations": total,
        "average_score": round(avg_score, 2) if avg_score is not None else None,
        "task_type_breakdown": dict(sorted(task_types.items(), key=lambda x: -x[1])),
        "framework_breakdown": dict(sorted(frameworks.items(), key=lambda x: -x[1])),
        "provider_breakdown": dict(sorted(providers.items(), key=lambda x: -x[1])),
        "model_usage": model_usage,
        "codebase_aware_count": codebase_aware,
        "improvement_rate": round(improvement_rate, 3) if improvement_rate is not None else None,
    }
