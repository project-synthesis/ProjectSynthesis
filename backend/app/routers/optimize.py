"""Optimization endpoints — POST /api/optimize (SSE) and GET /api/optimize/{trace_id}."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, PROMPTS_DIR, settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import Optimization
from app.services.pipeline import PipelineOrchestrator
from app.services.preferences import PreferencesService
from app.utils.sse import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=20, description="The raw prompt to optimize")
    strategy: str | None = Field(None, description="Strategy override")
    workspace_path: str | None = Field(None, description="Workspace root for guidance file scanning")


@router.post("/optimize")
async def optimize(
    body: OptimizeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT)),
):
    provider = getattr(request.app.state, "provider", None)
    if not provider:
        raise HTTPException(
            status_code=503,
            detail="No LLM provider available. Set ANTHROPIC_API_KEY or install the Claude CLI.",
        )

    logger.info("POST /api/optimize: prompt_len=%d strategy=%s", len(body.prompt), body.strategy)

    # Scan workspace for guidance files
    guidance = None
    if body.workspace_path:
        from pathlib import Path

        from app.services.roots_scanner import RootsScanner
        scanner = RootsScanner()
        guidance = scanner.scan(Path(body.workspace_path))

    orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

    _prefs = PreferencesService(DATA_DIR)
    effective_strategy = body.strategy or _prefs.get("defaults.strategy") or "auto"

    async def event_stream():
        async for event in orchestrator.run(
            raw_prompt=body.prompt, provider=provider, db=db,
            strategy_override=effective_strategy if effective_strategy != "auto" else None,
            codebase_guidance=guidance,
        ):
            yield format_sse(event.event, event.data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/optimize/{trace_id}")
async def get_optimization(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Optimization).where(Optimization.trace_id == trace_id)
    )
    opt = result.scalar_one_or_none()
    if not opt:
        raise HTTPException(
            status_code=404,
            detail="Optimization with trace_id '%s' not found." % trace_id,
        )

    return {
        "id": opt.id,
        "trace_id": opt.trace_id,
        "raw_prompt": opt.raw_prompt,
        "optimized_prompt": opt.optimized_prompt,
        "task_type": opt.task_type,
        "strategy_used": opt.strategy_used,
        "changes_summary": opt.changes_summary,
        "scores": {
            "clarity": opt.score_clarity,
            "specificity": opt.score_specificity,
            "structure": opt.score_structure,
            "faithfulness": opt.score_faithfulness,
            "conciseness": opt.score_conciseness,
        },
        "original_scores": opt.original_scores,
        "score_deltas": opt.score_deltas,
        "overall_score": opt.overall_score,
        "provider": opt.provider,
        "model_used": opt.model_used,
        "scoring_mode": opt.scoring_mode,
        "duration_ms": opt.duration_ms,
        "status": opt.status,
        "context_sources": opt.context_sources,
        "created_at": opt.created_at.isoformat() if opt.created_at else None,
    }
