import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_session
from app.dependencies.auth import get_current_user
from app.models.optimization import Optimization
from app.schemas.auth import AuthenticatedUser
from app.schemas.optimization import OptimizeRequest, PatchOptimizationRequest, RetryRequest
from app.services.optimization_service import accumulate_pipeline_event
from app.services.settings_service import load_settings
from app.services.url_fetcher import fetch_url_contexts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["optimize"])


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
    retry_of: str | None = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Run the optimization pipeline with SSE streaming."""
    if not req.app.state.provider:
        raise HTTPException(status_code=503, detail="LLM provider not initialized")

    opt_id = str(uuid.uuid4())
    start_time = time.time()

    async def event_stream():
        # Session 1: create the record in pending state
        async with async_session() as s:
            s.add(Optimization(
                id=opt_id,
                raw_prompt=request.prompt,
                status="running",
                project=request.project,
                tags=json.dumps(request.tags or []),
                title=request.title,
                linked_repo_full_name=request.repo_full_name,
                linked_repo_branch=request.repo_branch,
                retry_of=retry_of,
                user_id=current_user.id,
            ))
            await s.commit()

        # Accumulate field updates as pipeline events arrive (no ORM object needed)
        updates: dict = {}
        total_tokens = 0
        stage_timings: dict = {}
        pipeline_failed = False
        pipeline_error_message = None

        try:
            # Import pipeline here to avoid circular imports
            from app.services.pipeline import run_pipeline

            # N26/N30: pre-fetch URL contexts with HTML stripping (shared service)
            url_fetched = await fetch_url_contexts(request.url_contexts)

            # Compute effective timeout: user setting capped by config.py ceiling
            user_settings = load_settings()
            effective_timeout = min(
                user_settings.get("pipeline_timeout", settings.PIPELINE_TIMEOUT_SECONDS),
                settings.PIPELINE_TIMEOUT_SECONDS,  # config.py ceiling
            )

            async with asyncio.timeout(effective_timeout):
                async for event_type, event_data in run_pipeline(
                    provider=req.app.state.provider,
                    raw_prompt=request.prompt,
                    optimization_id=opt_id,
                    strategy_override=request.strategy,
                    repo_full_name=request.repo_full_name,
                    repo_branch=request.repo_branch,
                    session_id=req.session.get("session_id"),
                    github_token=request.github_token,
                    file_contexts=request.file_contexts,
                    instructions=request.instructions,
                    url_fetched_contexts=url_fetched,
                ):
                    yield _sse_event(event_type, event_data)

                    # Track total tokens and per-stage durations from stage complete events
                    if event_type == "stage" and event_data.get("status") == "complete":
                        total_tokens += event_data.get("token_count", 0)
                        _sname = event_data.get("stage")
                        if _sname:
                            stage_timings[_sname] = {
                                "duration_ms": event_data.get("duration_ms", 0),
                                "token_count": event_data.get("token_count", 0),
                            }

                    # Detect non-recoverable pipeline errors (failed stage)
                    if event_type == "error" and not event_data.get("recoverable", True):
                        pipeline_failed = True
                        pipeline_error_message = event_data.get("error", "Unknown stage failure")

                    # Accumulate DB field updates from pipeline events
                    if event_type == "validation" and "scores" not in event_data:
                        logger.error(
                            "Validation event missing 'scores' sub-dict for opt %s; keys: %s",
                            opt_id, list(event_data.keys())
                        )
                    updates.update(accumulate_pipeline_event(event_type, event_data))

                # Persist per-stage durations
                if stage_timings:
                    updates["stage_durations"] = json.dumps(stage_timings)

                # Finalize — success or partial failure
                duration_ms = int((time.time() - start_time) * 1000)
                updates["duration_ms"] = duration_ms
                updates["updated_at"] = datetime.now(timezone.utc)
                updates["provider_used"] = req.app.state.provider.name

                if pipeline_failed:
                    updates["status"] = "failed"
                    updates["error_message"] = pipeline_error_message
                else:
                    updates["status"] = "completed"

                async with async_session() as s:
                    await s.execute(
                        update(Optimization)
                        .where(Optimization.id == opt_id)
                        .values(**updates)
                    )
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
                effective_timeout, opt_id,
            )
            if stage_timings:
                updates["stage_durations"] = json.dumps(stage_timings)
            updates["status"] = "failed"
            updates["error_message"] = (
                f"Pipeline timed out after {effective_timeout}s"
            )
            updates["duration_ms"] = int((time.time() - start_time) * 1000)
            updates["updated_at"] = datetime.now(timezone.utc)
            async with async_session() as s:
                await s.execute(
                    update(Optimization)
                    .where(Optimization.id == opt_id)
                    .values(**updates)
                )
                await s.commit()
            yield _sse_event("error", {
                "stage": "pipeline",
                "error": f"Pipeline timed out after {effective_timeout}s",
                "recoverable": False,
            })
            return

        except Exception as e:
            logger.exception(f"Pipeline error for {opt_id}: {e}")
            if stage_timings:
                updates["stage_durations"] = json.dumps(stage_timings)
            updates["status"] = "failed"
            updates["error_message"] = str(e)
            updates["duration_ms"] = int((time.time() - start_time) * 1000)
            updates["updated_at"] = datetime.now(timezone.utc)

            try:
                async with async_session() as s:
                    await s.execute(
                        update(Optimization)
                        .where(Optimization.id == opt_id)
                        .values(**updates)
                    )
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
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a single optimization by ID."""
    result = await session.execute(
        select(Optimization).where(
            Optimization.id == optimization_id,
            Optimization.deleted_at.is_(None),
            Optimization.user_id == current_user.id,
        )
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
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Update metadata on an optimization."""
    result = await session.execute(
        select(Optimization).where(
            Optimization.id == optimization_id,
            Optimization.deleted_at.is_(None),
            Optimization.user_id == current_user.id,
        )
    )
    optimization = result.scalar_one_or_none()
    if not optimization:
        raise HTTPException(status_code=404, detail="Optimization not found")

    if patch.expected_version is not None and optimization.row_version != patch.expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "VERSION_CONFLICT",
                "message": "Record was modified by another request. Refetch and retry.",
                "current_version": optimization.row_version,
            },
        )

    if patch.title is not None:
        optimization.title = patch.title  # type: ignore[assignment]
    if patch.tags is not None:
        optimization.tags = json.dumps(patch.tags)  # type: ignore[assignment]
    if patch.version is not None:
        optimization.version = patch.version  # type: ignore[assignment]
    if patch.project is not None:
        optimization.project = patch.project  # type: ignore[assignment]

    optimization.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    optimization.row_version += 1
    await session.commit()
    return optimization.to_dict()


@router.post("/api/optimize/{optimization_id}/retry")
async def retry_optimization(
    optimization_id: str,
    body: RetryRequest,
    req: Request,
    session: AsyncSession = Depends(get_session),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Retry an optimization with optional strategy override."""
    result = await session.execute(
        select(Optimization).where(
            Optimization.id == optimization_id,
            Optimization.deleted_at.is_(None),
            Optimization.user_id == current_user.id,
        )
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

    # Reuse the optimize endpoint logic, linking retry to original.
    # current_user must be passed explicitly — FastAPI won't inject it for inner calls.
    return await optimize_prompt(
        retry_request, req,
        retry_of=optimization_id,
        current_user=current_user,
    )
