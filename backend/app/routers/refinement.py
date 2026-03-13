"""Refinement + branching API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session, get_session_context
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.routers._sse import sse_event
from app.schemas.auth import AuthenticatedUser
from app.schemas.refinement import ForkRequest, RefineRequest, SelectRequest
from app.services.adaptation_engine import load_adaptation, recompute_adaptation_safe
from app.services.prompt_diff import SCORE_DIMENSIONS
from app.services.refinement_service import (
    fork_branch,
    get_branch,
    get_branches,
    refine,
    select_branch,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["refinement"])


@router.post(
    "/api/optimize/{optimization_id}/refine",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_REFINE))],
)
async def refine_optimization(
    optimization_id: str,
    body: RefineRequest,
    req: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Run one refinement turn on the active branch. Returns SSE stream."""
    if not req.app.state.provider:
        raise HTTPException(503, "LLM provider not initialized")

    adaptation = await load_adaptation(current_user.id, db)

    # Find active branch (uses DI session — safe, runs before generator)
    branches = await get_branches(optimization_id, db)
    active = next((b for b in branches if b["status"] == "active"), None)
    if not active:
        raise HTTPException(404, "No active branch found")

    # Capture values needed by the generator before DI session is torn down
    active_branch_id = active["id"]
    provider = req.app.state.provider

    async def event_stream():
        # Own session: DI-injected session may be closed by FastAPI before
        # the streaming generator completes (known FastAPI lifecycle issue).
        async with get_session_context() as stream_db:
            try:
                async for event in refine(
                    branch_id=active_branch_id,
                    message=body.message,
                    source="user",
                    protect_dimensions=body.protect_dimensions,
                    provider=provider,
                    user_adaptation=adaptation,
                    db=stream_db,
                ):
                    yield sse_event(event.get("event", "refinement_update"), event)
                await stream_db.commit()
            except ValueError as e:
                yield sse_event("error", {"error": str(e), "recoverable": False})
            except Exception:
                logger.exception("Refinement stream error for %s", optimization_id)
                yield sse_event("error", {"error": "Internal error", "recoverable": False})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/api/optimize/{optimization_id}/branches",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_BRANCH_FORK))],
)
async def create_branch(
    optimization_id: str,
    body: ForkRequest,
    req: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Fork a new branch. Returns SSE stream."""
    if not req.app.state.provider:
        raise HTTPException(503, "LLM provider not initialized")

    adaptation = await load_adaptation(current_user.id, db)
    provider = req.app.state.provider

    async def event_stream():
        async with get_session_context() as stream_db:
            try:
                async for event in fork_branch(
                    optimization_id=optimization_id,
                    parent_branch_id=body.parent_branch_id,
                    message=body.message,
                    provider=provider,
                    db=stream_db,
                    label=body.label,
                    user_adaptation=adaptation,
                ):
                    yield sse_event(event.get("event", "branch_update"), event)
                await stream_db.commit()
            except ValueError as e:
                yield sse_event("error", {"error": str(e), "recoverable": False})
            except Exception:
                logger.exception("Branch fork error for %s", optimization_id)
                yield sse_event("error", {"error": "Internal error", "recoverable": False})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get(
    "/api/optimize/{optimization_id}/branches",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def list_branches(
    optimization_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    branches = await get_branches(optimization_id, db)
    return {"branches": branches, "total": len(branches)}


@router.get(
    "/api/optimize/{optimization_id}/branches/{branch_id}",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def get_branch_detail(
    optimization_id: str,
    branch_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    branch = await get_branch(branch_id, db)
    if not branch or branch["optimization_id"] != optimization_id:
        raise HTTPException(404, "Branch not found")
    return branch


@router.post(
    "/api/optimize/{optimization_id}/branches/select",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_BRANCH_SELECT))],
)
async def select_winner(
    optimization_id: str,
    body: SelectRequest,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    try:
        result = await select_branch(
            optimization_id=optimization_id,
            branch_id=body.branch_id,
            user_id=current_user.id,
            reason=body.reason,
            db=db,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Trigger adaptation recomputation (pairwise preferences)
    background_tasks.add_task(
        recompute_adaptation_safe, current_user.id
    )

    return result


@router.get(
    "/api/optimize/{optimization_id}/branches/compare",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def compare_branches(
    optimization_id: str,
    branch_a: str,
    branch_b: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    a = await get_branch(branch_a, db)
    b = await get_branch(branch_b, db)
    if not a or not b:
        raise HTTPException(404, "Branch not found")

    # Compute score deltas
    deltas = {}
    if a.get("scores") and b.get("scores"):
        for dim in (*SCORE_DIMENSIONS, "overall_score"):
            va = a["scores"].get(dim, 0)
            vb = b["scores"].get(dim, 0)
            deltas[dim] = round(va - vb, 1)

    return {"branch_a": a, "branch_b": b, "score_deltas": deltas}
