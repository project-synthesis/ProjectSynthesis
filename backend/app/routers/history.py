"""History endpoint — sorted/filtered optimization list."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Feedback, OptimizationPattern
from app.services.optimization_service import VALID_SORT_COLUMNS, OptimizationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["history"])


def _validate_sort_by(sort_by: str = Query("created_at")) -> str:
    if sort_by not in VALID_SORT_COLUMNS:
        raise HTTPException(422, f"Invalid sort column. Must be one of: {', '.join(sorted(VALID_SORT_COLUMNS))}")
    return sort_by


class HistoryItem(BaseModel):
    id: str = Field(description="Unique optimization ID.")
    trace_id: str | None = Field(default=None, description="Trace ID for pipeline correlation.")
    created_at: str | None = Field(default=None, description="ISO 8601 creation timestamp.")
    task_type: str | None = Field(default=None, description="Classified task type.")
    strategy_used: str | None = Field(default=None, description="Strategy used for optimization.")
    overall_score: float | None = Field(default=None, description="Weighted overall quality score.")
    status: str = Field(description="Optimization status.")
    duration_ms: int | None = Field(default=None, description="Pipeline duration in milliseconds.")
    provider: str | None = Field(default=None, description="LLM provider used.")
    models_by_phase: dict[str, str] | None = Field(default=None, description="Per-phase model IDs used.")
    raw_prompt: str | None = Field(default=None, description="Truncated original prompt (first 100 chars).")
    optimized_prompt: str | None = Field(default=None, description="Truncated optimized prompt (first 100 chars).")
    intent_label: str | None = Field(default=None, description="Short intent classification label.")
    domain: str | None = Field(default=None, description="Domain category.")
    cluster_id: str | None = Field(default=None, description="Pattern family ID.")
    feedback_rating: str | None = Field(default=None, description="Latest feedback rating.")


class HistoryResponse(BaseModel):
    total: int = Field(description="Total number of matching optimizations.")
    count: int = Field(description="Number of items in this page.")
    offset: int = Field(description="Current pagination offset.")
    has_more: bool = Field(description="Whether more pages exist.")
    next_offset: int | None = Field(default=None, description="Offset for the next page, or null if no more.")
    items: list[HistoryItem] = Field(description="Optimization items for this page.")


@router.get("/history")
async def get_history(
    offset: int = Query(0, ge=0, description="Pagination offset (items to skip)."),
    limit: int = Query(50, ge=1, le=100, description="Items per page (1-100)."),
    sort_by: str = Depends(_validate_sort_by),
    sort_order: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction: 'asc' or 'desc'."),
    task_type: str | None = Query(None, description="Filter by task type (optional)."),
    status: str | None = Query(None, description="Filter by status (optional)."),
    db: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    svc = OptimizationService(db)
    result = await svc.list_optimizations(
        offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order,
        task_type=task_type, status=status,
    )

    # Batch-fetch family IDs for all returned items in a single query (not N+1).
    items = result["items"]
    family_map: dict[str, str] = {}
    if items:
        opt_ids = [opt.id for opt in items]
        family_rows = (
            await db.execute(
                select(
                    OptimizationPattern.optimization_id,
                    OptimizationPattern.cluster_id,
                )
                .where(
                    OptimizationPattern.optimization_id.in_(opt_ids),
                    OptimizationPattern.relationship == "source",
                )
            )
        ).all()
        family_map = {row.optimization_id: row.cluster_id for row in family_rows}

    # Batch-fetch latest feedback rating per optimization (not N+1).
    feedback_map: dict[str, str] = {}
    if items:
        fb_rows = (
            await db.execute(
                select(Feedback.optimization_id, Feedback.rating)
                .where(Feedback.optimization_id.in_(opt_ids))
                .order_by(Feedback.created_at.desc())
            )
        ).all()
        for row in fb_rows:
            if row.optimization_id not in feedback_map:
                feedback_map[row.optimization_id] = row.rating

    return HistoryResponse(
        total=result["total"],
        count=result["count"],
        offset=result["offset"],
        has_more=result["has_more"],
        next_offset=result["next_offset"],
        items=[
            HistoryItem(
                id=opt.id,
                trace_id=opt.trace_id,
                created_at=opt.created_at.isoformat() if opt.created_at else None,
                task_type=opt.task_type,
                strategy_used=opt.strategy_used,
                overall_score=opt.overall_score,
                status=opt.status,
                duration_ms=opt.duration_ms,
                provider=opt.provider,
                models_by_phase=opt.models_by_phase,
                raw_prompt=opt.raw_prompt[:100] if opt.raw_prompt else None,
                optimized_prompt=opt.optimized_prompt[:100] if opt.optimized_prompt else None,
                intent_label=opt.intent_label,
                domain=opt.domain,
                cluster_id=family_map.get(opt.id),
                feedback_rating=feedback_map.get(opt.id),
            )
            for opt in items
        ],
    )
