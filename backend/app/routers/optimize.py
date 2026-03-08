import asyncio
import json
import logging
import datetime as dt
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_session
from app.models.optimization import Optimization
from app.schemas.optimization import OptimizeRequest, PatchOptimizationRequest, RetryRequest
from app.config import settings
from app.services.url_fetcher import fetch_url_contexts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimize"])

# Deprecated: use req.app.state.provider. Kept for backward compat with main.py lifespan.
_provider = None


def set_provider(provider):
    """Deprecated: provider is now read from app.state. Kept for main.py compat."""
    pass  # no-op — provider injected via app.state at startup


def _default_serializer(obj: object) -> str:
    """JSON fallback serializer for types not handled by default."""
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, Exception):
        return str(obj)
    return repr(obj)  # Last resort — never silently drop data


def _sse_event(event_type: str, data: dict) -> str:
    """Format an SSE event with safe JSON serialization.

    Uses a fallback serializer so that non-serializable values (datetimes,
    exceptions, etc.) never crash the stream silently.
    """
    try:
        payload = json.dumps(data, default=_default_serializer)
    except Exception as e:
        logger.error("SSE serialization failed for event %s: %s", event_type, e)
        payload = json.dumps({"error": f"Serialization error: {e}"})
    return f"event: {event_type}\ndata: {payload}\n\n"


@router.post("/api/optimize")
async def optimize_prompt(
    request: OptimizeRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
    retry_of: str | None = None,
):
    """Run the optimization pipeline with SSE streaming."""
    if not req.app.state.provider:
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

            total_tokens = 0
            pipeline_failed = False
            pipeline_error_message = None

            # N26/N30: pre-fetch URL contexts with HTML stripping (shared service)
            url_fetched = await fetch_url_contexts(request.url_contexts)

            async with asyncio.timeout(settings.PIPELINE_TIMEOUT_SECONDS):
                async for event_type, event_data in run_pipeline(
                    provider=req.app.state.provider,
                    raw_prompt=request.prompt,
                    optimization_id=opt_id,
                    strategy_override=request.strategy,
                    repo_full_name=request.repo_full_name,
                    repo_branch=request.repo_branch,
                    session_id=req.session.get("session_id"),
                    github_token=request.github_token,          # N23
                    file_contexts=request.file_contexts,        # N24
                    instructions=request.instructions,          # N25
                    url_fetched_contexts=url_fetched,           # N26
                ):
                    yield _sse_event(event_type, event_data)

                    # Track total tokens from stage complete events
                    if event_type == "stage" and event_data.get("status") == "complete":
                        total_tokens += event_data.get("token_count", 0)

                    # Detect non-recoverable pipeline errors (failed stage)
                    if event_type == "error" and not event_data.get("recoverable", True):
                        pipeline_failed = True
                        pipeline_error_message = event_data.get("error", "Unknown stage failure")

                    # Update the optimization record with pipeline results
                    if event_type == "codebase_context":
                        optimization.codebase_context_snapshot = json.dumps(event_data)
                        optimization.model_explore = event_data.get("model")
                    elif event_type == "analysis":
                        optimization.task_type = event_data.get("task_type")
                        optimization.complexity = event_data.get("complexity")
                        optimization.weaknesses = json.dumps(event_data.get("weaknesses", []))
                        optimization.strengths = json.dumps(event_data.get("strengths", []))
                        optimization.model_analyze = event_data.get("model")
                    elif event_type == "strategy":
                        optimization.primary_framework = event_data.get("primary_framework")
                        optimization.secondary_frameworks = json.dumps(
                            event_data.get("secondary_frameworks", [])
                        )
                        optimization.approach_notes = event_data.get("approach_notes")
                        optimization.strategy_rationale = event_data.get("rationale")
                        optimization.strategy_source = event_data.get("strategy_source")
                        optimization.model_strategy = event_data.get("model")
                    elif event_type == "optimization":
                        optimization.optimized_prompt = event_data.get("optimized_prompt")
                        optimization.changes_made = json.dumps(event_data.get("changes_made", []))
                        optimization.framework_applied = event_data.get("framework_applied")
                        optimization.optimization_notes = event_data.get("optimization_notes")
                        optimization.model_optimize = event_data.get("model")
                    elif event_type == "validation":
                        if "scores" not in event_data:
                            logger.error(
                                "Validation event missing 'scores' sub-dict for opt %s; keys: %s",
                                opt_id, list(event_data.keys())
                            )
                        scores = event_data.get("scores", {})
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
                optimization.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                optimization.provider_used = req.app.state.provider.name

                if pipeline_failed:
                    # A stage failed and subsequent stages were skipped
                    optimization.status = "failed"
                    optimization.error_message = pipeline_error_message
                else:
                    optimization.status = "completed"

                # Persist final state
                async with async_session() as s:
                    await s.merge(optimization)
                    await s.commit()

                if not pipeline_failed:
                    yield _sse_event("complete", {
                        "optimization_id": opt_id,
                        "total_duration_ms": duration_ms,
                        "total_tokens": total_tokens,
                    })

        except asyncio.TimeoutError:
            logger.error(
                "Pipeline timeout (%ds) for opt %s",
                settings.PIPELINE_TIMEOUT_SECONDS, opt_id,
            )
            optimization.status = "failed"
            optimization.error_message = (
                f"Pipeline timed out after {settings.PIPELINE_TIMEOUT_SECONDS}s"
            )
            optimization.duration_ms = int((time.time() - start_time) * 1000)
            optimization.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            async with async_session() as s:
                await s.merge(optimization)
                await s.commit()
            yield _sse_event("error", {
                "stage": "pipeline",
                "error": f"Pipeline timed out after {settings.PIPELINE_TIMEOUT_SECONDS}s",
                "recoverable": False,
            })
            return

        except Exception as e:
            logger.exception(f"Pipeline error for {opt_id}: {e}")
            duration_ms = int((time.time() - start_time) * 1000)
            optimization.status = "failed"
            optimization.error_message = str(e)
            optimization.duration_ms = duration_ms
            optimization.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

            try:
                async with async_session() as s:
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
        optimization.title = patch.title  # type: ignore[assignment]
    if patch.tags is not None:
        optimization.tags = json.dumps(patch.tags)  # type: ignore[assignment]
    if patch.version is not None:
        optimization.version = patch.version  # type: ignore[assignment]
    if patch.project is not None:
        optimization.project = patch.project  # type: ignore[assignment]

    optimization.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
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
        prompt=str(original.raw_prompt),
        project=str(original.project) if original.project else None,
        tags=json.loads(str(original.tags)) if original.tags else None,
        title=str(original.title) if original.title else None,
        strategy=body.strategy,
        repo_full_name=str(original.linked_repo_full_name) if original.linked_repo_full_name else None,
        repo_branch=str(original.linked_repo_branch) if original.linked_repo_branch else None,
        file_contexts=body.file_contexts,    # N32
        instructions=body.instructions,      # N32
        url_contexts=body.url_contexts,      # N32
        github_token=body.github_token,      # N40: re-run Explore on retry
    )

    # Reuse the optimize endpoint logic, linking retry to original
    return await optimize_prompt(retry_request, req, session, retry_of=optimization_id)
