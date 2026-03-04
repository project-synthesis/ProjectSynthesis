"""Optimization CRUD and pipeline trigger endpoints.

Provides REST endpoints for creating, listing, retrieving, and deleting
optimizations. The POST /{id}/run endpoint triggers the full pipeline
and returns a Server-Sent Events stream.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session, async_session
from app.models.optimization import Optimization
from app.schemas.optimization import (
    OptimizeRequest,
    PatchOptimizationRequest,
)
from app.services.optimization_service import (
    create_optimization,
    list_optimizations,
    get_optimization,
    delete_optimization,
    update_optimization,
)
from app.services.pipeline import run_pipeline

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimizations"])

# Set by main.py lifespan handler
_provider = None


def set_provider(provider):
    """Inject the detected LLM provider at startup."""
    global _provider
    _provider = provider


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@router.post("/api/optimizations")
async def create_optimization_endpoint(
    request: OptimizeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a new optimization record.

    Returns the created optimization in pending state.
    """
    optimization = await create_optimization(
        session,
        raw_prompt=request.prompt,
        title=request.title,
        project=request.project,
        tags=request.tags,
        repo_full_name=request.repo_full_name,
        repo_branch=request.repo_branch,
    )
    return optimization.to_dict()


@router.get("/api/optimizations")
async def list_optimizations_endpoint(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    session: AsyncSession = Depends(get_session),
):
    """List optimizations with pagination and filtering.

    Returns a paginated list of optimization records with total count.
    """
    items, total = await list_optimizations(
        session,
        limit=limit,
        offset=offset,
        project=project,
        task_type=task_type,
        search=search,
        sort=sort,
        order=order,
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/optimizations/{optimization_id}")
async def get_optimization_endpoint(
    optimization_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a single optimization by ID.

    Returns 404 if the optimization does not exist.
    """
    result = await get_optimization(session, optimization_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Optimization not found")
    return result


@router.delete("/api/optimizations/{optimization_id}")
async def delete_optimization_endpoint(
    optimization_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete an optimization by ID.

    Returns 404 if the optimization does not exist.
    """
    deleted = await delete_optimization(session, optimization_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Optimization not found")
    return {"deleted": True, "id": optimization_id}


@router.post("/api/optimizations/{optimization_id}/run")
async def run_optimization_pipeline(
    optimization_id: str,
    req: Request,
    session: AsyncSession = Depends(get_session),
):
    """Trigger the optimization pipeline for an existing record.

    Returns a Server-Sent Events (SSE) stream with real-time progress
    updates for each pipeline stage.

    SSE event format:
        event: {type}
        data: {"stage": "analyze", "status": "running", "progress": 50, ...}

    The stream emits events for each stage transition (started/complete/failed),
    intermediate results (analysis, strategy, optimization, validation),
    and a final 'complete' event with the optimization_id and total duration.
    """
    if not _provider:
        raise HTTPException(
            status_code=503,
            detail="LLM provider not initialized",
        )

    # Load the optimization record
    from sqlalchemy import select

    result = await session.execute(
        select(Optimization).where(Optimization.id == optimization_id)
    )
    optimization = result.scalar_one_or_none()
    if not optimization:
        raise HTTPException(status_code=404, detail="Optimization not found")

    if optimization.status == "running":
        raise HTTPException(
            status_code=409,
            detail="Optimization is already running",
        )

    # Mark as running
    optimization.status = "running"
    optimization.error_message = None
    await session.commit()

    # Look up GitHub token if a repo is linked
    github_token = None
    if optimization.linked_repo_full_name:
        session_id = req.cookies.get("session_id")
        if session_id:
            from app.services.github_service import get_token_for_session

            async with async_session() as gh_session:
                github_token = await get_token_for_session(
                    gh_session, session_id
                )

    async def event_stream():
        """Async generator producing SSE events from the pipeline."""
        start_time = time.monotonic()

        # Accumulate pipeline results for DB persistence
        analysis_data = {}
        strategy_data = {}
        optimization_data = {}
        validation_data = {}

        try:
            async for event_type, event_data in run_pipeline(
                provider=_provider,
                raw_prompt=optimization.raw_prompt,
                optimization_id=optimization_id,
                strategy_override=None,
                repo_full_name=optimization.linked_repo_full_name,
                repo_branch=optimization.linked_repo_branch,
                session_id=req.cookies.get("session_id"),
            ):
                # Collect results from each stage
                if event_type == "analysis":
                    analysis_data = event_data
                elif event_type == "strategy":
                    strategy_data = event_data
                elif event_type == "optimization":
                    optimization_data = event_data
                elif event_type == "validation":
                    validation_data = event_data

                # Forward as SSE
                yield _sse_event(event_type, event_data if isinstance(event_data, dict) else {"data": event_data})

            # Persist pipeline results to the database
            duration_ms = int((time.monotonic() - start_time) * 1000)
            try:
                async with async_session() as persist_session:
                    from sqlalchemy import select as sel

                    res = await persist_session.execute(
                        sel(Optimization).where(
                            Optimization.id == optimization_id
                        )
                    )
                    opt = res.scalar_one_or_none()
                    if opt:
                        opt.status = "completed"
                        opt.task_type = analysis_data.get("task_type")
                        opt.complexity = analysis_data.get("complexity")
                        opt.weaknesses = json.dumps(analysis_data.get("weaknesses", []))
                        opt.strengths = json.dumps(analysis_data.get("strengths", []))
                        opt.primary_framework = strategy_data.get("primary_framework")
                        opt.strategy_rationale = strategy_data.get("rationale")
                        opt.optimized_prompt = optimization_data.get("optimized_prompt", "")
                        opt.changes_made = json.dumps(optimization_data.get("changes_made", []))
                        opt.framework_applied = optimization_data.get("framework_applied")
                        opt.optimization_notes = optimization_data.get("optimization_notes")
                        opt.clarity_score = validation_data.get("clarity_score")
                        opt.specificity_score = validation_data.get("specificity_score")
                        opt.structure_score = validation_data.get("structure_score")
                        opt.faithfulness_score = validation_data.get("faithfulness_score")
                        opt.conciseness_score = validation_data.get("conciseness_score")
                        opt.overall_score = validation_data.get("overall_score")
                        opt.is_improvement = validation_data.get("is_improvement")
                        opt.verdict = validation_data.get("verdict")
                        opt.issues = json.dumps(validation_data.get("issues", []))
                        opt.provider_used = _provider.name
                        opt.duration_ms = duration_ms
                        opt.model_analyze = analysis_data.get("model")
                        opt.model_strategy = strategy_data.get("model")
                        opt.model_optimize = optimization_data.get("model")
                        opt.model_validate = validation_data.get("model")
                        opt.updated_at = datetime.now(timezone.utc)
                        await persist_session.commit()
            except Exception:
                logger.exception("Failed to persist pipeline results")

            yield _sse_event("complete", {
                "optimization_id": optimization_id,
                "duration_ms": duration_ms,
            })

        except Exception as e:
            logger.exception(
                "Pipeline streaming error for %s: %s",
                optimization_id,
                e,
            )
            # Persist failure state
            try:
                async with async_session() as err_session:
                    from sqlalchemy import select as sel2

                    res = await err_session.execute(
                        sel2(Optimization).where(
                            Optimization.id == optimization_id
                        )
                    )
                    opt = res.scalar_one_or_none()
                    if opt:
                        opt.status = "failed"
                        opt.error_message = str(e)
                        opt.duration_ms = int(
                            (time.monotonic() - start_time) * 1000
                        )
                        opt.updated_at = datetime.now(timezone.utc)
                        await err_session.commit()
            except Exception:
                logger.exception("Failed to persist error state")

            yield _sse_event("error", {
                "stage": "pipeline",
                "error": str(e),
                "recoverable": False,
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
