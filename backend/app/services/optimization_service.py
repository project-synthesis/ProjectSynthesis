"""Optimization CRUD service.

Provides async database operations for creating, listing, retrieving,
and deleting optimization records via SQLAlchemy async sessions.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.optimization import Optimization
from app.providers.base import CompletionUsage

logger = logging.getLogger(__name__)


def escape_like(s: str) -> str:
    """Escape SQL LIKE wildcards (%, _) in user input.

    Prevents user-supplied ``%`` and ``_`` from acting as wildcards in
    LIKE / ILIKE clauses. The escape character is ``\\``.
    Callers must add ``.escape("\\\\")`` to the SQLAlchemy ``ilike()`` call.
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def accumulate_pipeline_event(event_type: str, event_data: dict) -> dict:
    """Map a single pipeline SSE event to Optimization column→value pairs.

    Centralizes the event_type → column mapping used by both the SSE endpoint
    (optimize.py) and the MCP server (_run_and_persist).  Returns a dict
    suitable for ``updates.update(...)`` or ``update(...).values(**result)``.
    """
    updates: dict = {}
    if event_type == "codebase_context":
        _snapshot = json.dumps(event_data)
        if len(_snapshot) > 65536:
            logger.warning(
                "codebase_context_snapshot truncated from %d chars", len(_snapshot),
            )
            truncated_data = {
                k: v for k, v in event_data.items()
                if k in ("model", "repo", "branch", "files_read_count",
                         "explore_quality", "tech_stack", "coverage_pct")
            }
            truncated_data["_truncated"] = True
            _snapshot = json.dumps(truncated_data)
            if len(_snapshot) > 65536:
                _snapshot = json.dumps({"_truncated": True, "model": event_data.get("model")})
        updates["codebase_context_snapshot"] = _snapshot
        updates["model_explore"] = event_data.get("model")
    elif event_type == "analysis":
        updates["task_type"] = event_data.get("task_type")
        updates["complexity"] = event_data.get("complexity")
        updates["weaknesses"] = json.dumps(event_data.get("weaknesses", []))
        updates["strengths"] = json.dumps(event_data.get("strengths", []))
        updates["model_analyze"] = event_data.get("model")
        updates["analysis_quality"] = event_data.get("analysis_quality")
    elif event_type == "strategy":
        updates["primary_framework"] = event_data.get("primary_framework")
        updates["secondary_frameworks"] = json.dumps(event_data.get("secondary_frameworks", []))
        updates["approach_notes"] = event_data.get("approach_notes")
        updates["strategy_rationale"] = event_data.get("rationale")
        updates["strategy_source"] = event_data.get("strategy_source")
        updates["model_strategy"] = event_data.get("model")
    elif event_type == "optimization":
        updates["optimized_prompt"] = event_data.get("optimized_prompt")
        updates["changes_made"] = json.dumps(event_data.get("changes_made", []))
        updates["framework_applied"] = event_data.get("framework_applied")
        updates["optimization_notes"] = event_data.get("optimization_notes")
        updates["model_optimize"] = event_data.get("model")
    elif event_type == "validation":
        scores = event_data.get("scores", {})
        updates["clarity_score"] = scores.get("clarity_score")
        updates["specificity_score"] = scores.get("specificity_score")
        updates["structure_score"] = scores.get("structure_score")
        updates["faithfulness_score"] = scores.get("faithfulness_score")
        updates["conciseness_score"] = scores.get("conciseness_score")
        updates["overall_score"] = scores.get("overall_score")
        updates["is_improvement"] = event_data.get("is_improvement")
        updates["verdict"] = event_data.get("verdict")
        updates["issues"] = json.dumps(event_data.get("issues", []))
        updates["model_validate"] = event_data.get("model")
        updates["validation_quality"] = event_data.get("validation_quality")
    return updates


class PipelineAccumulator:
    """Accumulates pipeline SSE events into a dict of DB column updates.

    Used by both the SSE endpoint (optimize.py) and the MCP server
    (_run_and_persist) to avoid duplicating the accumulation logic.
    """

    def __init__(self) -> None:
        self.updates: dict = {}
        self.stage_timings: dict = {}
        self.results: dict = {}
        self.pipeline_failed = False
        self.error_message: str | None = None
        self.total_tokens: int = 0
        self.usage_totals = CompletionUsage()

    def process_event(self, event_type: str, event_data: dict) -> None:
        """Process a single pipeline event, accumulating updates."""
        self.updates.update(accumulate_pipeline_event(event_type, event_data))

        # Track per-stage results for MCP return values
        if event_type in ("analysis", "strategy", "optimization", "validation"):
            self.results[event_type] = event_data

        # H3: New pipeline events
        if event_type == "retry_diagnostics":
            # Store latest retry diagnostics (no DB column needed, for SSE only)
            self.results["retry_diagnostics"] = event_data
        elif event_type == "retry_best_selected":
            self.results["retry_best_selected"] = event_data
        elif event_type == "adaptation_snapshot":
            self.updates["adaptation_snapshot"] = json.dumps(event_data)
        elif event_type == "branch_created":
            self.results["branch_created"] = event_data

        # Track stage timings, token counts, and per-stage usage
        if event_type == "stage" and event_data.get("status") == "complete":
            stage_name = event_data.get("stage")
            if stage_name:
                timing: dict = {
                    "duration_ms": event_data.get("duration_ms", 0),
                    "token_count": event_data.get("token_count", 0),
                }
                stage_usage = event_data.get("usage")
                if stage_usage:
                    timing["input_tokens"] = stage_usage.get("input_tokens", 0)
                    timing["output_tokens"] = stage_usage.get("output_tokens", 0)
                    timing["cache_read"] = stage_usage.get("cache_read_input_tokens", 0)
                    timing["cache_creation"] = stage_usage.get("cache_creation_input_tokens", 0)
                    timing["is_estimated"] = stage_usage.get("is_estimated", False)
                    # Accumulate into pipeline-wide usage totals
                    self.usage_totals += CompletionUsage(
                        input_tokens=stage_usage.get("input_tokens", 0),
                        output_tokens=stage_usage.get("output_tokens", 0),
                        cache_read_input_tokens=stage_usage.get("cache_read_input_tokens", 0),
                        cache_creation_input_tokens=stage_usage.get("cache_creation_input_tokens", 0),
                        is_estimated=stage_usage.get("is_estimated", False),
                        model=stage_usage.get("model", ""),
                    )
                self.stage_timings[stage_name] = timing
            self.total_tokens += event_data.get("token_count", 0)

        # Detect non-recoverable errors
        if event_type == "error" and not event_data.get("recoverable", True):
            self.pipeline_failed = True
            self.error_message = event_data.get("error", "Unknown stage failure")

    def finalize(
        self,
        provider_name: str,
        start_time: float,
        error: Exception | None = None,
    ) -> dict:
        """Build final updates dict with stage_durations, duration_ms, status, etc.

        Args:
            provider_name: Name of the LLM provider used.
            start_time: Pipeline start timestamp (from time.time()).
            error: If set, marks the pipeline as failed with this error.

        Returns:
            Dict of Optimization column updates ready for DB persistence.
        """
        import time
        from datetime import datetime, timezone

        if self.stage_timings:
            self.updates["stage_durations"] = json.dumps(self.stage_timings)

        self.updates["duration_ms"] = int((time.time() - start_time) * 1000)
        self.updates["updated_at"] = datetime.now(timezone.utc)
        self.updates["provider_used"] = provider_name

        if error is not None:
            self.updates["status"] = "failed"
            self.updates["error_message"] = str(error)
        elif self.pipeline_failed:
            self.updates["status"] = "failed"
            self.updates["error_message"] = self.error_message
        else:
            self.updates["status"] = "completed"

        # Write accumulated usage/cost columns
        if self.usage_totals.total_tokens > 0:
            self.updates["total_input_tokens"] = self.usage_totals.input_tokens
            self.updates["total_output_tokens"] = self.usage_totals.output_tokens
            self.updates["total_cache_read_tokens"] = self.usage_totals.cache_read_input_tokens
            self.updates["total_cache_creation_tokens"] = self.usage_totals.cache_creation_input_tokens
            self.updates["estimated_cost_usd"] = self.usage_totals.estimated_cost_usd()
            self.updates["usage_is_estimated"] = self.usage_totals.is_estimated

        return self.updates


VALID_SORT_COLUMNS: frozenset[str] = frozenset({
    "created_at", "overall_score", "task_type", "updated_at",
    "duration_ms", "primary_framework", "status",
    "refinement_turns", "branch_count",
})

VALID_ORDERS: frozenset[str] = frozenset({"asc", "desc"})


def validate_sort_params(sort: str, order: str) -> None:
    """Validate sort column and order against whitelist. Raises ValueError on invalid input."""
    if sort not in VALID_SORT_COLUMNS:
        raise ValueError(f"Invalid sort column '{sort}'. Must be one of: {', '.join(sorted(VALID_SORT_COLUMNS))}")
    if order not in VALID_ORDERS:
        raise ValueError(f"Invalid order '{order}'. Must be 'asc' or 'desc'.")


@dataclass
class OptimizationQuery:
    limit: int = 50
    offset: int = 0
    project: str | None = None
    task_type: str | None = None
    framework: str | None = None
    has_repo: bool | None = None
    min_score: float | None = None
    max_score: float | None = None
    status: str | None = None
    search: str | None = None
    search_columns: int = 2  # 2 = raw_prompt+title, 3 = +optimized_prompt, 4 = +project
    sort: str = "created_at"
    order: str = "desc"
    user_id: str | None = None
    deleted_only: bool = False  # True = show soft-deleted items (trash), False = active only


async def query_optimizations(session: AsyncSession, params: OptimizationQuery) -> dict:
    """Build query, count, sort, paginate, and return pagination envelope dict.

    Handles both active and trash queries via ``deleted_only``.
    Search depth is controlled by ``search_columns``:
      2 = raw_prompt + title (default, MCP list_optimizations)
      3 = + optimized_prompt (MCP search_optimizations)
      4 = + project (history router)
    """
    validate_sort_params(params.sort, params.order)

    if params.deleted_only:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        query = select(Optimization).where(
            Optimization.deleted_at.isnot(None),
            Optimization.deleted_at >= cutoff,
        )
    else:
        query = select(Optimization).where(Optimization.deleted_at.is_(None))

    if params.user_id:
        query = query.where(Optimization.user_id == params.user_id)
    if params.project:
        query = query.where(Optimization.project == params.project)
    if params.task_type:
        query = query.where(Optimization.task_type == params.task_type)
    if params.framework:
        query = query.where(Optimization.primary_framework == params.framework)
    if params.has_repo is True:
        query = query.where(Optimization.linked_repo_full_name.isnot(None))
    elif params.has_repo is False:
        query = query.where(Optimization.linked_repo_full_name.is_(None))
    if params.min_score is not None:
        query = query.where(Optimization.overall_score >= params.min_score)
    if params.max_score is not None:
        query = query.where(Optimization.overall_score <= params.max_score)
    if params.status:
        query = query.where(Optimization.status == params.status)
    if params.search:
        escaped = escape_like(params.search)
        pattern = f"%{escaped}%"
        conditions = [
            Optimization.raw_prompt.ilike(pattern, escape="\\"),
            Optimization.title.ilike(pattern, escape="\\"),
        ]
        if params.search_columns >= 3:
            conditions.append(Optimization.optimized_prompt.ilike(pattern, escape="\\"))
        if params.search_columns >= 4:
            conditions.append(Optimization.project.ilike(pattern, escape="\\"))
        from sqlalchemy import or_
        query = query.where(or_(*conditions))

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    count_result = await session.execute(count_query)
    total = count_result.scalar() or 0

    # Sort — trash queries default to deleted_at desc
    if params.deleted_only and params.sort == "created_at":
        query = query.order_by(Optimization.deleted_at.desc())
    else:
        sort_column = getattr(Optimization, params.sort)
        if params.order == "asc":
            query = query.order_by(sort_column.asc())
        else:
            query = query.order_by(sort_column.desc())

    # Paginate
    query = query.offset(params.offset).limit(params.limit)
    result = await session.execute(query)
    items = [opt.to_dict() for opt in result.scalars().all()]

    fetched = len(items)
    has_more = (params.offset + fetched) < total
    return {
        "total": total,
        "count": fetched,
        "offset": params.offset,
        "items": items,
        "has_more": has_more,
        "next_offset": params.offset + fetched if has_more else None,
    }


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
    user_id: Optional[str] = None,
) -> tuple[list[dict], int]:
    """List optimizations with pagination, filtering, and sorting. Returns (items, total)."""
    envelope = await query_optimizations(session, OptimizationQuery(
        limit=limit, offset=offset, project=project, task_type=task_type,
        search=search, sort=sort, order=order, user_id=user_id,
    ))
    return envelope["items"], envelope["total"]


async def compute_stats(
    session: AsyncSession,
    project: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """Compute aggregated stats using SQL aggregates (O(1) memory).

    Args:
        session: Async database session.
        project: Optional project label to scope stats to.
        user_id: When provided, restrict stats to this user's records only.
    """
    base_filter = [Optimization.deleted_at.is_(None)]
    if project:
        base_filter.append(Optimization.project == project)
    if user_id:
        base_filter.append(Optimization.user_id == user_id)

    totals_result = await session.execute(
        select(
            func.count(Optimization.id).label("total"),
            func.avg(Optimization.overall_score).label("avg_score"),
            func.sum(case((Optimization.linked_repo_full_name.isnot(None), 1), else_=0)).label("codebase_aware"),
            func.sum(case((Optimization.is_improvement.is_(True), 1), else_=0)).label("improvements"),
            func.count(Optimization.is_improvement).label("validated"),
            func.sum(Optimization.total_input_tokens).label("total_input_tokens"),
            func.sum(Optimization.total_output_tokens).label("total_output_tokens"),
            func.sum(Optimization.estimated_cost_usd).label("total_cost_usd"),
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
        "total_input_tokens": totals.total_input_tokens or 0,
        "total_output_tokens": totals.total_output_tokens or 0,
        "total_cost_usd": round(float(totals.total_cost_usd), 4) if totals.total_cost_usd else None,
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
    *,
    user_id: Optional[str] = None,
) -> Optional[Optimization]:
    """Get a single optimization ORM object by ID.

    Args:
        session: Async database session.
        optimization_id: The UUID of the optimization to retrieve.
        user_id: When provided, restricts to records owned by this user.
                 Pass None to skip ownership check (single-user/MCP mode).

    Returns:
        Optimization ORM instance if found, None otherwise.
    """
    filters = [
        Optimization.id == optimization_id,
        Optimization.deleted_at.is_(None),
    ]
    if user_id:
        filters.append(Optimization.user_id == user_id)
    result = await session.execute(select(Optimization).where(*filters))
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


async def restore_optimization(
    session: AsyncSession,
    optimization_id: str,
    user_id: Optional[str] = None,
) -> bool:
    """Restore a soft-deleted optimization within the 7-day trash window.

    Args:
        session: Async database session.
        optimization_id: The UUID of the optimization to restore.
        user_id: When provided, restricts restore to records owned by this user.
                 Pass None (e.g. from MCP callers) to skip ownership check.

    Returns:
        True if found and restored, False if not found or recovery window expired.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    filters = [
        Optimization.id == optimization_id,
        Optimization.deleted_at.isnot(None),
        Optimization.deleted_at >= cutoff,
    ]
    if user_id:
        filters.append(Optimization.user_id == user_id)
    result = await session.execute(select(Optimization).where(*filters))
    opt = result.scalar_one_or_none()
    if not opt:
        return False
    opt.deleted_at = None
    return True


async def delete_optimization(
    session: AsyncSession,
    optimization_id: str,
    user_id: Optional[str] = None,
) -> bool:
    """Soft-delete an optimization by setting deleted_at.

    Args:
        session: Async database session.
        optimization_id: The UUID of the optimization to soft-delete.
        user_id: When provided, restricts deletion to records owned by this user.
                 Pass None to skip ownership check (single-user/localhost mode).

    Returns:
        True if soft-deleted, False if not found.
    """
    filters = [Optimization.id == optimization_id, Optimization.deleted_at.is_(None)]
    if user_id:
        filters.append(Optimization.user_id == user_id)
    result = await session.execute(select(Optimization).where(*filters))
    opt = result.scalar_one_or_none()
    if opt is None:
        return False

    opt.deleted_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info("Soft-deleted optimization %s", optimization_id)
    return True


async def batch_delete_optimizations(
    session: AsyncSession,
    user_id: Optional[str],
    ids: list[str],
) -> list[str]:
    """Batch soft-delete optimizations by setting deleted_at.

    All-or-nothing semantics: validates existence and ownership of every ID
    before mutating any rows. Raises HTTPException on validation failure.

    Args:
        session: Async database session (transaction-scoped).
        user_id: Authenticated user's ID — all records must belong to this user.
                 Pass None to skip ownership check (single-user/MCP mode).
        ids: List of optimization UUIDs to soft-delete (1–50 items).

    Returns:
        List of deleted optimization IDs.

    Raises:
        HTTPException 404: If any ID does not exist (or is already deleted).
        HTTPException 403: If any record belongs to a different user.
    """
    from app.errors import forbidden, not_found
    from app.schemas.auth import ERR_INSUFFICIENT_PERMISSIONS

    # Fetch all records matching the provided IDs (including other users' records
    # so we can distinguish 404 vs 403).
    result = await session.execute(
        select(Optimization).where(
            Optimization.id.in_(ids),
            Optimization.deleted_at.is_(None),
        )
    )
    records = {opt.id: opt for opt in result.scalars().all()}

    # Validate: every requested ID must exist
    missing = [oid for oid in ids if oid not in records]
    if missing:
        raise not_found(f"Optimization(s) not found: {', '.join(missing)}")

    # Validate: every record must belong to the authenticated user
    if user_id:
        unauthorized_ids = [oid for oid, opt in records.items() if opt.user_id != user_id]
        if unauthorized_ids:
            raise forbidden("Not authorized to delete one or more optimizations", code=ERR_INSUFFICIENT_PERMISSIONS)

    # All checks passed — mutate
    now = datetime.now(timezone.utc)
    for opt in records.values():
        opt.deleted_at = now
    await session.flush()

    deleted_ids = list(records.keys())
    logger.info("Batch soft-deleted %d optimizations for user %s", len(deleted_ids), user_id)
    return deleted_ids
