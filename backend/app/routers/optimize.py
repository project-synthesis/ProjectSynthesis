import json
import uuid
import time
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_session
from app.models.optimization import Optimization
from app.schemas.optimization import OptimizeRequest, PatchOptimizationRequest, RetryRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimize"])

# Will be set by main.py lifespan
_provider = None
_pipeline = None


def set_provider(provider):
    global _provider
    _provider = provider


def set_pipeline(pipeline):
    global _pipeline
    _pipeline = pipeline


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@router.post("/api/optimize")
async def optimize_prompt(
    request: OptimizeRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
    retry_of: str | None = None,
):
    """Run the optimization pipeline with SSE streaming."""
    if not _provider:
        raise HTTPException(status_code=503, detail="LLM provider not initialized")

    opt_id = str(uuid.uuid4())
    start_time = time.time()

    # Create initial record
    optimization = Optimization(
        id=opt_id,
        raw_prompt=request.prompt,
        status="running",
        project=request.project,
        tags=json.dumps(request.tags or []),
        title=request.title,
        linked_repo_full_name=request.repo_full_name,
        linked_repo_branch=request.repo_branch,
        retry_of=retry_of,
    )
    session.add(optimization)
    await session.commit()

    async def event_stream():
        nonlocal optimization
        try:
            # Import pipeline here to avoid circular imports
            from app.services.pipeline import run_pipeline

            async for event_type, event_data in run_pipeline(
                provider=_provider,
                raw_prompt=request.prompt,
                optimization_id=opt_id,
                strategy_override=request.strategy,
                repo_full_name=request.repo_full_name,
                repo_branch=request.repo_branch,
                session_id=req.cookies.get("session_id"),
            ):
                yield _sse_event(event_type, event_data)

                # Update the optimization record with pipeline results
                if event_type == "analysis":
                    optimization.task_type = event_data.get("task_type")
                    optimization.complexity = event_data.get("complexity")
                    optimization.weaknesses = json.dumps(event_data.get("weaknesses", []))
                    optimization.strengths = json.dumps(event_data.get("strengths", []))
                    optimization.model_analyze = event_data.get("model")
                elif event_type == "strategy":
                    optimization.primary_framework = event_data.get("primary_framework")
                    optimization.strategy_rationale = event_data.get("rationale")
                    optimization.model_strategy = event_data.get("model")
                elif event_type == "optimization":
                    optimization.optimized_prompt = event_data.get("optimized_prompt")
                    optimization.changes_made = json.dumps(event_data.get("changes_made", []))
                    optimization.framework_applied = event_data.get("framework_applied")
                    optimization.optimization_notes = event_data.get("optimization_notes")
                    optimization.model_optimize = event_data.get("model")
                elif event_type == "validation":
                    scores = event_data.get("scores", event_data)
                    optimization.clarity_score = scores.get("clarity_score")
                    optimization.specificity_score = scores.get("specificity_score")
                    optimization.structure_score = scores.get("structure_score")
                    optimization.faithfulness_score = scores.get("faithfulness_score")
                    optimization.conciseness_score = scores.get("conciseness_score")
                    optimization.overall_score = scores.get("overall_score")
                    optimization.is_improvement = event_data.get("is_improvement")
                    optimization.verdict = event_data.get("verdict")
                    optimization.issues = json.dumps(event_data.get("issues", []))
                    optimization.model_validate = event_data.get("model")

            # Finalize
            duration_ms = int((time.time() - start_time) * 1000)
            optimization.duration_ms = duration_ms
            optimization.status = "completed"
            optimization.provider_used = _provider.name
            optimization.updated_at = datetime.now(timezone.utc)

            # Persist final state
            async with (await _get_fresh_session()) as s:
                merged = await s.merge(optimization)
                await s.commit()

            yield _sse_event("complete", {
                "optimization_id": opt_id,
                "total_duration_ms": duration_ms,
            })

        except Exception as e:
            logger.exception(f"Pipeline error for {opt_id}: {e}")
            duration_ms = int((time.time() - start_time) * 1000)
            optimization.status = "failed"
            optimization.error_message = str(e)
            optimization.duration_ms = duration_ms
            optimization.updated_at = datetime.now(timezone.utc)

            try:
                async with (await _get_fresh_session()) as s:
                    await s.merge(optimization)
                    await s.commit()
            except Exception:
                logger.exception("Failed to save error state")

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


async def _get_fresh_session():
    """Get a new database session for background updates."""
    from app.database import async_session
    return async_session()


@router.get("/api/optimize/{optimization_id}")
async def get_optimization(
    optimization_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get a single optimization by ID."""
    result = await session.execute(
        select(Optimization).where(Optimization.id == optimization_id)
    )
    optimization = result.scalar_one_or_none()
    if not optimization:
        raise HTTPException(status_code=404, detail="Optimization not found")
    return optimization.to_dict()


@router.patch("/api/optimize/{optimization_id}")
async def patch_optimization(
    optimization_id: str,
    patch: PatchOptimizationRequest,
    session: AsyncSession = Depends(get_session),
):
    """Update metadata on an optimization."""
    result = await session.execute(
        select(Optimization).where(Optimization.id == optimization_id)
    )
    optimization = result.scalar_one_or_none()
    if not optimization:
        raise HTTPException(status_code=404, detail="Optimization not found")

    if patch.title is not None:
        optimization.title = patch.title
    if patch.tags is not None:
        optimization.tags = json.dumps(patch.tags)
    if patch.version is not None:
        optimization.version = patch.version
    if patch.project is not None:
        optimization.project = patch.project

    optimization.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return optimization.to_dict()


@router.post("/api/optimize/{optimization_id}/retry")
async def retry_optimization(
    optimization_id: str,
    body: RetryRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
):
    """Retry an optimization with optional strategy override."""
    result = await session.execute(
        select(Optimization).where(Optimization.id == optimization_id)
    )
    original = result.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Optimization not found")

    # Create a new optimize request based on the original
    retry_request = OptimizeRequest(
        prompt=original.raw_prompt,
        project=original.project,
        tags=json.loads(original.tags) if original.tags else None,
        title=original.title,
        strategy=body.strategy,
        repo_full_name=original.linked_repo_full_name,
        repo_branch=original.linked_repo_branch,
    )

    # Reuse the optimize endpoint logic, linking retry to original
    return await optimize_prompt(retry_request, req, session, retry_of=optimization_id)
