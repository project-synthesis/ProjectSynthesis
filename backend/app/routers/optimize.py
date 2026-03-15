"""Optimization endpoints — POST /api/optimize (SSE) and GET /api/optimize/{trace_id}."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR
from app.database import get_db
from app.models import Optimization
from app.services.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="The raw prompt to optimize")
    strategy: str | None = Field(None, description="Strategy override")


def _format_sse(event_type: str, data: dict) -> str:
    payload = json.dumps({"event": event_type, **data})
    return f"data: {payload}\n\n"


@router.post("/optimize")
async def optimize(
    body: OptimizeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    provider = getattr(request.app.state, "provider", None)
    if not provider:
        raise HTTPException(status_code=503, detail="No LLM provider available.")

    orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

    async def event_stream():
        async for event in orchestrator.run(
            raw_prompt=body.prompt, provider=provider, db=db,
            strategy_override=body.strategy,
        ):
            yield _format_sse(event.event, event.data)

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
        raise HTTPException(status_code=404, detail="Optimization not found")

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
