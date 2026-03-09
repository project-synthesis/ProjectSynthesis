import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.dependencies.auth import get_current_user
from app.models.optimization import Optimization
from app.schemas.auth import AuthenticatedUser
from app.services.optimization_service import VALID_SORT_COLUMNS, compute_stats

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
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """List optimization history with pagination, search, sort, and filter."""
    query = select(Optimization).where(
        Optimization.deleted_at.is_(None),
        Optimization.user_id == current_user.id,
    )

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
    if sort not in VALID_SORT_COLUMNS:
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
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Soft-delete an optimization record (user-scoped)."""
    from app.services.optimization_service import get_optimization_orm
    opt = await get_optimization_orm(session, optimization_id)
    if not opt or opt.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Optimization not found")
    opt.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    logger.info("Soft-deleted optimization %s by user %s", optimization_id, current_user.id)
    return {"deleted": True, "id": optimization_id}


@router.get("/api/history/trash")
async def list_trash(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """List soft-deleted optimizations pending purge (deleted within the last 7 days)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    base_filter = [
        Optimization.deleted_at.isnot(None),
        Optimization.deleted_at >= cutoff,
        Optimization.user_id == current_user.id,
    ]

    # Count total matching items
    count_query = select(func.count()).select_from(
        select(Optimization).where(*base_filter).subquery()
    )
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # Paginated items
    query = (
        select(Optimization)
        .where(*base_filter)
        .order_by(Optimization.deleted_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(query)
    items = [opt.to_dict() for opt in result.scalars().all()]
    fetched = len(items)
    has_more = (offset + fetched) < total
    return {
        "total": total,
        "count": fetched,
        "offset": offset,
        "items": items,
        "has_more": has_more,
        "next_offset": offset + fetched if has_more else None,
    }


@router.post("/api/history/{optimization_id}/restore")
async def restore_optimization(
    optimization_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Restore a soft-deleted optimization (clears deleted_at)."""
    from app.services.optimization_service import restore_optimization as svc_restore
    restored = await svc_restore(session, optimization_id, current_user.id)
    if not restored:
        raise HTTPException(status_code=404, detail="Not found in trash")
    await session.commit()
    return {"restored": True, "id": optimization_id}


@router.get("/api/history/stats")
async def get_stats(
    project: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Get aggregated statistics about optimization history (user-scoped)."""
    return await compute_stats(session, project=project, user_id=current_user.id)
