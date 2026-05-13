"""Refinement endpoints — POST /api/refine (SSE), GET versions, POST rollback."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.write_queue import get_write_queue
from app.services.write_queue import WriteQueue
from app.tools._shared import PROMPTS_DIR
from app.tools.refine import handle_refine
from app.utils.sse import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["refinement"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RefineRequest(BaseModel):
    optimization_id: str = Field(..., min_length=1, description="ID of the optimization to refine.")
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
):
    """Run a single refinement turn and stream SSE events.

    Cycle 2 restructure: drops `Depends(get_db)` and delegates to
    `tools/refine.py:handle_refine` which owns the session+queue pattern
    (short read session → LLM phase → WriteQueue persist). REST and MCP
    paths share the substrate byte-for-byte. `handle_refine` returns
    `RefineOutput` (not an async generator); the router synthesizes a
    2-event SSE stream (`started` + `refinement_turn`) preserving the
    client-facing event contract.
    """
    async def event_stream():
        yield format_sse("started", {"optimization_id": body.optimization_id})
        try:
            result = await handle_refine(
                optimization_id=body.optimization_id,
                refinement_request=body.refinement_request,
                branch_id=body.branch_id,
                ctx=None,
            )
            yield format_sse("refinement_turn", result.model_dump())
        except ValueError as exc:
            logger.warning("Refinement rejected: %s", exc)
            yield format_sse("error", {"error": str(exc)})
        except Exception as exc:
            logger.error("Refinement SSE stream error: %s", exc, exc_info=True)
            from app.providers.base import ProviderError
            if isinstance(exc, ProviderError):
                msg = f"Provider error: {type(exc).__name__}"
            else:
                msg = "An internal error occurred during refinement"
            yield format_sse("error", {"error": msg})

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
    from app.services.optimization_service import OptimizationService
    from app.services.refinement_service import RefinementService
    opt_svc = OptimizationService(db)
    opt = await opt_svc.get_by_id(optimization_id)
    if not opt:
        raise HTTPException(
            status_code=404,
            detail="Optimization not found.",
        )

    ref_svc = RefinementService(prompts_dir=PROMPTS_DIR)
    turns = await ref_svc.get_versions(db, optimization_id, branch_id=branch_id)

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
    write_queue: WriteQueue = Depends(get_write_queue),
):
    """Create a new branch forked from the given version number.

    Cycle 2 restructure: `RefinementService.rollback()` returns a pure
    `RollbackPayload` (un-attached `branch_kwargs`). The actual `INSERT`
    routes through `WriteQueue.submit(operation_label="refine_rollback")`
    so SQLite writes funnel through the single-writer queue.
    """
    from app.services.optimization_service import OptimizationService
    from app.services.refinement_service import RefinementService

    opt_svc = OptimizationService(db)
    opt = await opt_svc.get_by_id(optimization_id)
    if not opt:
        raise HTTPException(
            status_code=404,
            detail="Optimization not found.",
        )

    ref_svc = RefinementService(prompts_dir=PROMPTS_DIR)

    try:
        payload = await ref_svc.rollback(
            db, optimization_id, to_version=body.to_version,
        )
    except Exception as exc:
        # NoResultFound, ValueError, LookupError → 404; others → 400
        from sqlalchemy.exc import NoResultFound
        status = 404 if isinstance(exc, (ValueError, LookupError, NoResultFound)) else 400
        logger.warning("Rollback failed: %s", exc)
        raise HTTPException(status_code=status, detail="Rollback failed.") from exc

    branch_kwargs = payload.branch_kwargs
    seed_turn_kwargs = payload.seed_turn_kwargs

    async def _persist_rollback(write_db: AsyncSession) -> dict:
        """Persist the new RefinementBranch + seed RefinementTurn atomically.

        v0.4.22 soak-gate Day 1 fix: prior to this change, the callback
        inserted ONLY the new branch row, leaving subsequent refines on
        the rolled-back branch broken (zero turns → latest_turn_snapshot
        is None → ValueError). The seed turn — carrying the content of
        the version being rolled back to — fixes that. Both rows commit
        in the same write transaction; either both succeed or neither
        persists (RefinementTurn has a FK to RefinementBranch.id with no
        ON DELETE clause so the FK enforces transactional cohesion).
        """
        from datetime import UTC, datetime

        from app.models import RefinementBranch, RefinementTurn

        new_branch = RefinementBranch(**branch_kwargs)
        write_db.add(new_branch)
        # Flush the branch BEFORE inserting the seed turn so the FK
        # constraint on RefinementTurn.branch_id sees the branch row.
        await write_db.flush()

        seed_turn = RefinementTurn(**seed_turn_kwargs)
        write_db.add(seed_turn)

        await write_db.commit()
        await write_db.refresh(new_branch)
        return {
            "id": new_branch.id,
            "optimization_id": new_branch.optimization_id,
            "parent_branch_id": new_branch.parent_branch_id,
            "forked_at_version": new_branch.forked_at_version,
            "created_at": (
                new_branch.created_at.isoformat()
                if new_branch.created_at
                else datetime.now(UTC).isoformat()
            ),
        }

    return await write_queue.submit(
        _persist_rollback,
        operation_label="refine_rollback",
    )
