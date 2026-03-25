"""Refinement endpoints — POST /api/refine (SSE), GET versions, POST rollback."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR
from app.database import get_db
from app.utils.sse import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["refinement"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RefineRequest(BaseModel):
    optimization_id: str = Field(description="ID of the optimization to refine.")
    refinement_request: str = Field(
        ..., min_length=1,
        description="User's refinement request or feedback for the next iteration.",
    )
    branch_id: str | None = Field(
        default=None, description="Branch ID to refine on (latest if omitted).",
    )


class RollbackRequest(BaseModel):
    to_version: int = Field(
        ..., ge=1, description="Version number to roll back to.",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/refine")
async def refine(
    body: RefineRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Run a single refinement turn and stream SSE events."""
    from app.config import DATA_DIR
    from app.services.preferences import PreferencesService
    from app.services.routing import RoutingContext

    routing = getattr(request.app.state, "routing", None)
    if not routing:
        raise HTTPException(status_code=503, detail="Routing service not initialized.")

    _prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = _prefs.load()
    ctx = RoutingContext(preferences=prefs_snapshot, caller="rest")
    decision = routing.resolve(ctx)

    # Refinement still requires a provider (passthrough refinement UX not designed yet).
    if decision.tier == "passthrough":
        logger.warning(
            "POST /api/refine rejected: tier=passthrough optimization_id=%s",
            body.optimization_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Refinement requires a local provider. Configure an API key or install the Claude CLI.",
        )
    provider = decision.provider
    logger.info(
        "POST /api/refine: tier=%s provider=%s optimization_id=%s",
        decision.tier, decision.provider_name, body.optimization_id,
    )

    from app.services.optimization_service import OptimizationService
    from app.services.refinement_service import RefinementService

    svc = OptimizationService(db)
    opt = await svc.get_by_id(body.optimization_id)
    if not opt:
        raise HTTPException(
            status_code=404,
            detail="Optimization not found.",
        )

    logger.info("POST /api/refine: optimization_id=%s branch=%s", body.optimization_id, body.branch_id)

    ref_svc = RefinementService(db=db, provider=provider, prompts_dir=PROMPTS_DIR)

    # Ensure initial turn exists
    versions = await ref_svc.get_versions(body.optimization_id)
    if not versions:
        scores_dict = {
            "clarity": opt.score_clarity,
            "specificity": opt.score_specificity,
            "structure": opt.score_structure,
            "faithfulness": opt.score_faithfulness,
            "conciseness": opt.score_conciseness,
        }
        initial = await ref_svc.create_initial_turn(
            opt.id,
            opt.optimized_prompt,
            scores_dict,
            opt.strategy_used or "auto",
        )
        branch_id = initial.branch_id
    else:
        branch_id = body.branch_id or versions[-1].branch_id

    async def event_stream():
        yield format_sse("routing", {
            "tier": decision.tier, "provider": decision.provider_name,
            "reason": decision.reason, "degraded_from": decision.degraded_from,
        })
        try:
            async for event in ref_svc.create_refinement_turn(
                body.optimization_id, branch_id, body.refinement_request,
            ):
                yield format_sse(event.event, event.data)
        except Exception as exc:
            logger.error("Refinement SSE stream error: %s", exc, exc_info=True)
            yield format_sse("error", {"error": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/refine/{optimization_id}/versions")
async def get_versions(
    optimization_id: str,
    branch_id: str | None = Query(default=None, description="Filter by branch ID."),
    db: AsyncSession = Depends(get_db),
):
    """Return all refinement turns for an optimization, optionally filtered by branch."""
    # Verify optimization exists
    from app.services.optimization_service import OptimizationService
    from app.services.refinement_service import RefinementService
    opt_svc = OptimizationService(db)
    opt = await opt_svc.get_by_id(optimization_id)
    if not opt:
        raise HTTPException(
            status_code=404,
            detail="Optimization not found.",
        )

    ref_svc = RefinementService(db=db, provider=None, prompts_dir=PROMPTS_DIR)
    turns = await ref_svc.get_versions(optimization_id, branch_id=branch_id)

    return {
        "optimization_id": optimization_id,
        "versions": [
            {
                "id": t.id,
                "version": t.version,
                "branch_id": t.branch_id,
                "parent_version": t.parent_version,
                "refinement_request": t.refinement_request,
                "prompt": t.prompt,
                "scores": t.scores,
                "deltas": t.deltas,
                "deltas_from_original": t.deltas_from_original,
                "strategy_used": t.strategy_used,
                "suggestions": t.suggestions,
                "trace_id": t.trace_id,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in turns
        ],
    }


@router.post("/refine/{optimization_id}/rollback")
async def rollback(
    optimization_id: str,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new branch forked from the given version number."""
    from app.services.optimization_service import OptimizationService
    from app.services.refinement_service import RefinementService

    opt_svc = OptimizationService(db)
    opt = await opt_svc.get_by_id(optimization_id)
    if not opt:
        raise HTTPException(
            status_code=404,
            detail="Optimization not found.",
        )

    ref_svc = RefinementService(db=db, provider=None, prompts_dir=PROMPTS_DIR)

    try:
        new_branch = await ref_svc.rollback(optimization_id, to_version=body.to_version)
    except Exception as exc:
        # NoResultFound, ValueError, LookupError → 404; others → 400
        from sqlalchemy.exc import NoResultFound
        status = 404 if isinstance(exc, (ValueError, LookupError, NoResultFound)) else 400
        logger.warning("Rollback failed: %s", exc)
        raise HTTPException(status_code=status, detail="Rollback failed.") from exc

    return {
        "id": new_branch.id,
        "optimization_id": new_branch.optimization_id,
        "parent_branch_id": new_branch.parent_branch_id,
        "forked_at_version": new_branch.forked_at_version,
        "created_at": new_branch.created_at.isoformat() if new_branch.created_at else None,
    }
