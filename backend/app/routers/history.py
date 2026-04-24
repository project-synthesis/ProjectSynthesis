"""History endpoint — sorted/filtered optimization list + per-id delete."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from app.dependencies.rate_limit import RateLimit
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Feedback, Optimization, OptimizationPattern
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
    routing_tier: str | None = Field(
        default=None, description="Execution tier: internal, sampling, or passthrough.",
    )
    models_by_phase: dict[str, str] | None = Field(default=None, description="Per-phase model IDs used.")
    raw_prompt: str | None = Field(default=None, description="Truncated original prompt (first 100 chars).")
    optimized_prompt: str | None = Field(default=None, description="Truncated optimized prompt (first 100 chars).")
    intent_label: str | None = Field(default=None, description="Short intent classification label.")
    domain: str | None = Field(default=None, description="Domain category.")
    cluster_id: str | None = Field(default=None, description="Pattern family ID.")
    project_id: str | None = Field(default=None, description="Project node ID.")  # ADR-005
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
                routing_tier=opt.routing_tier,
                models_by_phase=opt.models_by_phase,
                raw_prompt=opt.raw_prompt[:100] if opt.raw_prompt else None,
                optimized_prompt=opt.optimized_prompt[:100] if opt.optimized_prompt else None,
                intent_label=opt.intent_label,
                domain=opt.domain,
                cluster_id=family_map.get(opt.id),
                project_id=opt.project_id,
                feedback_rating=feedback_map.get(opt.id),
            )
            for opt in items
        ],
    )


class DeleteOptimizationResponse(BaseModel):
    """Envelope returned by ``DELETE /api/optimizations/{id}``.

    Mirrors ``DeleteOptimizationsResult`` but with lists (JSON-safe) so
    clients can refresh cluster-scoped UI after the cascade fires.
    Isomorphic with ``DeleteOptimizationsResponse`` (bulk endpoint) —
    ``requested`` is always 1 for the single endpoint.
    """

    deleted: int = Field(description="Rows removed (1 on success).")
    requested: int = Field(
        description="Rows the caller requested to delete (always 1 for this endpoint).",
    )
    affected_cluster_ids: list[str] = Field(
        default_factory=list,
        description="Cluster ids whose member counts need reconciliation.",
    )
    affected_project_ids: list[str] = Field(
        default_factory=list,
        description="Project ids whose opt counts need reconciliation.",
    )


@router.delete("/optimizations/{optimization_id}")
async def delete_optimization(
    optimization_id: str = Path(
        ..., description="Optimization id to delete (uuid)."
    ),
    db: AsyncSession = Depends(get_db),
) -> DeleteOptimizationResponse:
    """Delete one optimization and cascade dependents.

    Translates the service's "silent no-op on unknown id" into a proper
    404 so REST clients can distinguish typos from successful deletes.
    Cascade semantics + event emission + dirty-marking are owned by
    ``OptimizationService.delete_optimizations`` (see the service for
    full contract). This router is a thin HTTP translator.
    """
    svc = OptimizationService(db)
    # Probe before delete so we can 404 unknown ids. The service itself
    # returns deleted=0 silently — fine for bulk_reset/gc_sweep callers,
    # but bad for a REST client typing a wrong id.
    exists = await db.execute(
        select(Optimization.id).where(Optimization.id == optimization_id)
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Optimization not found")

    result = await svc.delete_optimizations(
        [optimization_id], reason="user_request",
    )
    return DeleteOptimizationResponse(
        deleted=result.deleted,
        requested=1,
        affected_cluster_ids=sorted(result.affected_cluster_ids),
        affected_project_ids=sorted(result.affected_project_ids),
    )


class BulkDeleteRequest(BaseModel):
    """Request body for POST /api/optimizations/delete.

    Bulk-capable delete endpoint that mirrors the service primitive's
    bulk semantics. Single-item callers should prefer the single
    DELETE /api/optimizations/{id} endpoint for REST purity; the UI
    uses this one uniformly (ids=[id] for single).
    """

    ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Optimization ids to delete (1-100 at a time).",
    )
    reason: str = Field(
        default="user_request",
        max_length=64,
        description="Audit-trail reason propagated on the optimization_deleted event.",
    )


class DeleteOptimizationsResponse(BaseModel):
    """Envelope returned by POST /api/optimizations/delete.

    Isomorphic with DeleteOptimizationResponse (single endpoint). The
    ``requested`` field lets the UI diff ``deleted`` vs ``requested`` to
    show 'X deleted, Y were already gone' without a second call.
    """

    deleted: int = Field(description="Rows actually removed.")
    requested: int = Field(description="Rows the caller asked to delete.")
    affected_cluster_ids: list[str] = Field(
        default_factory=list,
        description="Cluster ids whose member counts need reconciliation.",
    )
    affected_project_ids: list[str] = Field(
        default_factory=list,
        description="Project ids whose opt counts need reconciliation.",
    )


@router.post(
    "/optimizations/delete",
    response_model=DeleteOptimizationsResponse,
    dependencies=[Depends(RateLimit(lambda: "10/minute"))],
)
async def bulk_delete_optimizations(
    body: BulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
) -> DeleteOptimizationsResponse:
    """Delete 1-100 optimizations in a single call.

    Thin HTTP translator on top of
    ``OptimizationService.delete_optimizations``. The service emits one
    ``optimization_deleted`` event per deleted row and a single
    aggregated ``taxonomy_changed`` event at the end — exactly the
    behavior the UI needs for surgical SSE updates.

    No 404 translation here (unlike the single endpoint): when some ids
    don't exist, ``deleted < requested`` and the caller diffs. Matches
    the service contract.
    """
    svc = OptimizationService(db)
    result = await svc.delete_optimizations(body.ids, reason=body.reason)
    return DeleteOptimizationsResponse(
        deleted=result.deleted,
        requested=len(body.ids),
        affected_cluster_ids=sorted(result.affected_cluster_ids),
        affected_project_ids=sorted(result.affected_project_ids),
    )
