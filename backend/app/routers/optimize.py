"""Optimization endpoints — POST /api/optimize (SSE), GET /api/optimize/{trace_id}, passthrough."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, PROMPTS_DIR, settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import Optimization
from app.services.heuristic_scorer import HeuristicScorer
from app.services.passthrough import assemble_passthrough_prompt
from app.services.pipeline import PipelineOrchestrator
from app.services.preferences import PreferencesService
from app.utils.sse import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=20, description="The raw prompt to optimize")
    strategy: str | None = Field(None, description="Strategy override")
    workspace_path: str | None = Field(None, description="Workspace root for guidance file scanning")
    applied_pattern_ids: list[str] | None = Field(None, description="Pattern IDs to inject into optimizer context")


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
            applied_pattern_ids=body.applied_pattern_ids,
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

    return _serialize_optimization(opt)


def _serialize_optimization(opt: Optimization) -> dict:
    """Serialize an Optimization record to the standard API shape."""
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


# ---------------------------------------------------------------------------
# Passthrough endpoints — manual prompt assembly without an LLM provider
# ---------------------------------------------------------------------------


class PassthroughSaveRequest(BaseModel):
    trace_id: str = Field(..., description="Trace ID from the prepare step")
    optimized_prompt: str = Field(..., min_length=1, description="The externally-optimized prompt")
    changes_summary: str | None = Field(None, description="Optional summary of changes made")


@router.post("/optimize/passthrough")
async def passthrough_prepare(
    body: OptimizeRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT)),
):
    """Assemble an optimization prompt for manual passthrough to an external LLM.

    Does NOT require a configured provider. Creates a pending Optimization record
    and returns the assembled prompt for the user to copy to their LLM of choice.
    """
    logger.info("POST /api/optimize/passthrough: prompt_len=%d strategy=%s", len(body.prompt), body.strategy)

    _prefs = PreferencesService(DATA_DIR)
    requested_strategy = body.strategy or _prefs.get("defaults.strategy") or "auto"

    assembled, strategy_name = assemble_passthrough_prompt(
        prompts_dir=PROMPTS_DIR,
        raw_prompt=body.prompt,
        strategy_name=requested_strategy,
    )

    trace_id = str(uuid.uuid4())
    opt_id = str(uuid.uuid4())

    pending = Optimization(
        id=opt_id,
        raw_prompt=body.prompt,
        status="pending",
        trace_id=trace_id,
        provider="web_passthrough",
        strategy_used=strategy_name,
        task_type="general",
    )
    db.add(pending)
    await db.commit()

    return {
        "trace_id": trace_id,
        "optimization_id": opt_id,
        "assembled_prompt": assembled,
        "strategy_requested": strategy_name,
    }


@router.post("/optimize/passthrough/save")
async def passthrough_save(
    body: PassthroughSaveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save an externally-optimized prompt result.

    Applies heuristic scoring unless the user has disabled scoring in preferences.
    Looks up the pending Optimization by trace_id, updates the record to completed,
    and publishes an optimization_created event.
    """
    result = await db.execute(
        select(Optimization).where(Optimization.trace_id == body.trace_id)
    )
    opt = result.scalar_one_or_none()
    if not opt:
        raise HTTPException(404, "No pending optimization for this trace_id")

    _prefs = PreferencesService(DATA_DIR)
    scoring_enabled = _prefs.get("pipeline.enable_scoring")
    # Default to True if preference is not set (first-run, no preferences file)
    if scoring_enabled is None:
        scoring_enabled = True

    optimized_scores: dict[str, float] | None = None
    original_scores: dict[str, float] | None = None
    deltas: dict[str, float] | None = None
    overall: float | None = None
    scoring_mode = "skipped"

    if scoring_enabled:
        # Heuristic-only scoring (no LLM needed)
        optimized_scores = HeuristicScorer.score_prompt(
            body.optimized_prompt, original=opt.raw_prompt,
        )
        overall = round(sum(optimized_scores.values()) / len(optimized_scores), 2)

        # Score the original prompt too so we can compute deltas
        original_scores = HeuristicScorer.score_prompt(opt.raw_prompt)
        deltas = {
            dim: round(optimized_scores[dim] - original_scores[dim], 2)
            for dim in optimized_scores
        }
        scoring_mode = "heuristic"

    # Update record
    opt.optimized_prompt = body.optimized_prompt
    opt.changes_summary = body.changes_summary or ""
    if optimized_scores:
        opt.score_clarity = optimized_scores["clarity"]
        opt.score_specificity = optimized_scores["specificity"]
        opt.score_structure = optimized_scores["structure"]
        opt.score_faithfulness = optimized_scores["faithfulness"]
        opt.score_conciseness = optimized_scores["conciseness"]
    opt.overall_score = overall
    opt.original_scores = original_scores
    opt.score_deltas = deltas
    opt.scoring_mode = scoring_mode
    opt.status = "completed"
    opt.model_used = "external"
    await db.commit()
    await db.refresh(opt)

    # Publish event
    from app.services.event_bus import event_bus

    event_bus.publish("optimization_created", {
        "id": opt.id,
        "trace_id": opt.trace_id,
        "task_type": opt.task_type,
        "strategy_used": opt.strategy_used,
        "overall_score": overall,
        "provider": "web_passthrough",
        "status": "completed",
    })

    logger.info(
        "Passthrough saved: trace_id=%s overall=%.2f scoring_mode=heuristic",
        body.trace_id, overall,
    )

    return _serialize_optimization(opt)
