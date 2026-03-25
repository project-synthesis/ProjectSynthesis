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
from app.models import Optimization, OptimizationPattern
from app.services.heuristic_scorer import HeuristicScorer
from app.services.passthrough import assemble_passthrough_prompt
from app.services.pipeline import PipelineOrchestrator
from app.services.preferences import PreferencesService
from app.services.taxonomy import get_engine as get_taxonomy_engine
from app.utils.sse import format_sse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["optimize"])


class OptimizationDetail(BaseModel):
    id: str = Field(description="Unique optimization ID.")
    trace_id: str = Field(description="Trace ID for pipeline correlation.")
    raw_prompt: str = Field(description="Original user prompt text.")
    optimized_prompt: str | None = Field(default=None, description="Optimized prompt text.")
    task_type: str | None = Field(default=None, description="Classified task type.")
    strategy_used: str | None = Field(default=None, description="Strategy used for optimization.")
    changes_summary: str | None = Field(default=None, description="Summary of changes made during optimization.")
    scores: dict[str, float | None] = Field(
        default_factory=dict,
        description="Per-dimension quality scores (clarity, specificity, structure, faithfulness, conciseness).",
    )
    original_scores: dict[str, float] | None = Field(
        default=None, description="Baseline scores of the original prompt.",
    )
    score_deltas: dict[str, float] | None = Field(default=None, description="Score changes from original to optimized.")
    overall_score: float | None = Field(default=None, description="Weighted overall quality score (1.0-10.0).")
    provider: str | None = Field(default=None, description="LLM provider used (e.g. 'claude_cli', 'anthropic_api').")
    model_used: str | None = Field(default=None, description="Model ID used for optimization.")
    models_by_phase: dict[str, str] | None = Field(
        default=None, description="Per-phase model IDs used during optimization.",
    )
    scoring_mode: str | None = Field(default=None, description="Scoring method: 'hybrid', 'heuristic', or 'skipped'.")
    duration_ms: int | None = Field(default=None, description="Total pipeline duration in milliseconds.")
    status: str = Field(description="Optimization status: 'completed', 'pending', or 'failed'.")
    context_sources: dict[str, bool] | None = Field(
        default=None,
        description="Which context sources were available (guidance, codebase, adaptation).",
    )
    created_at: str | None = Field(default=None, description="ISO 8601 creation timestamp.")
    intent_label: str | None = Field(default=None, description="Short intent classification label (3-6 words).")
    domain: str | None = Field(default=None, description="Domain category (backend, frontend, database, etc.).")
    cluster_id: str | None = Field(default=None, description="Pattern family ID this optimization belongs to.")


class PassthroughPrepareResponse(BaseModel):
    trace_id: str = Field(description="Trace ID to reference when saving the result.")
    optimization_id: str = Field(description="Created optimization record ID.")
    assembled_prompt: str = Field(description="Fully assembled prompt to send to an external LLM.")
    strategy_requested: str = Field(description="Strategy name used for prompt assembly.")


class OptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=20, description="The raw prompt to optimize")
    strategy: str | None = Field(None, description="Strategy override")
    workspace_path: str | None = Field(None, description="Workspace root for guidance file scanning")
    repo_full_name: str | None = Field(None, description="GitHub repo (owner/name) for curated codebase context")
    applied_pattern_ids: list[str] | None = Field(None, description="Pattern IDs to inject into optimizer context")


@router.post("/optimize")
async def optimize(
    body: OptimizeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT)),
):
    from app.services.routing import RoutingContext

    routing = getattr(request.app.state, "routing", None)
    if not routing:
        raise HTTPException(status_code=503, detail="Routing service not initialized.")

    _prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = _prefs.load()

    ctx = RoutingContext(preferences=prefs_snapshot, caller="rest")
    decision = routing.resolve(ctx)

    logger.info(
        "POST /api/optimize: prompt_len=%d strategy=%s tier=%s",
        len(body.prompt), body.strategy, decision.tier,
    )

    effective_strategy = body.strategy or _prefs.get("defaults.strategy", prefs_snapshot) or "auto"

    # Unified context enrichment
    context_service = getattr(request.app.state, "context_service", None)
    if not context_service:
        raise HTTPException(status_code=503, detail="Context enrichment service not initialized.")

    enrichment = await context_service.enrich(
        raw_prompt=body.prompt,
        tier=decision.tier,
        db=db,
        workspace_path=body.workspace_path,
        repo_full_name=body.repo_full_name,
        applied_pattern_ids=body.applied_pattern_ids,
        preferences_snapshot=prefs_snapshot,
    )

    if decision.tier == "passthrough":
        # Inline passthrough — stream assembled template via SSE
        assembled, strategy_name = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=body.prompt,
            strategy_name=effective_strategy,
            codebase_guidance=enrichment.workspace_guidance,
            adaptation_state=enrichment.adaptation_state,
            analysis_summary=enrichment.analysis_summary,
            codebase_context=enrichment.codebase_context,
            applied_patterns=enrichment.applied_patterns,
        )

        trace_id = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        pending = Optimization(
            id=opt_id, raw_prompt=body.prompt, status="pending",
            trace_id=trace_id, provider="web_passthrough",
            strategy_used=strategy_name,
            task_type=enrichment.task_type,
            domain=enrichment.domain_value,
            domain_raw=enrichment.domain_value,
            intent_label=enrichment.intent_label,
            context_sources=enrichment.context_sources_dict,
        )
        db.add(pending)
        await db.commit()
        logger.info(
            "Passthrough prepared: trace_id=%s strategy=%s prompt_len=%d assembled_len=%d",
            trace_id, strategy_name, len(body.prompt), len(assembled),
        )

        async def passthrough_stream():
            yield format_sse("routing", {
                "tier": decision.tier, "provider": decision.provider_name,
                "reason": decision.reason, "degraded_from": decision.degraded_from,
            })
            yield format_sse("passthrough", {
                "assembled_prompt": assembled, "strategy": strategy_name,
                "trace_id": trace_id, "optimization_id": opt_id,
            })

        return StreamingResponse(
            passthrough_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    # Internal pipeline (decision.tier == "internal")
    orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

    async def event_stream():
        yield format_sse("routing", {
            "tier": decision.tier, "provider": decision.provider_name,
            "reason": decision.reason, "degraded_from": decision.degraded_from,
        })
        try:
            async for event in orchestrator.run(
                raw_prompt=body.prompt, provider=decision.provider, db=db,
                strategy_override=effective_strategy if effective_strategy != "auto" else None,
                codebase_guidance=enrichment.workspace_guidance,
                codebase_context=enrichment.codebase_context,
                adaptation_state=enrichment.adaptation_state,
                context_sources=enrichment.context_sources_dict,
                applied_pattern_ids=body.applied_pattern_ids,
                taxonomy_engine=get_taxonomy_engine(app=request.app),
            ):
                yield format_sse(event.event, event.data)
        except Exception as exc:
            logger.error("Pipeline SSE stream error: %s", exc, exc_info=True)
            yield format_sse("error", {"error": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/optimize/{trace_id}")
async def get_optimization(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
) -> OptimizationDetail:
    result = await db.execute(
        select(Optimization).where(Optimization.trace_id == trace_id)
    )
    opt = result.scalar_one_or_none()
    if not opt:
        raise HTTPException(
            status_code=404,
            detail="Optimization not found.",
        )

    cluster_id = await _get_cluster_id(db, opt.id)
    return _serialize_optimization(opt, cluster_id=cluster_id)


async def _get_cluster_id(db: AsyncSession, optimization_id: str) -> str | None:
    """Look up the 'source' cluster for an optimization (at most one)."""
    result = await db.execute(
        select(OptimizationPattern.cluster_id)
        .where(
            OptimizationPattern.optimization_id == optimization_id,
            OptimizationPattern.relationship == "source",
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


def _serialize_optimization(opt: Optimization, *, cluster_id: str | None = None) -> OptimizationDetail:
    """Serialize an Optimization record to the standard API shape."""
    return OptimizationDetail(
        id=opt.id,
        trace_id=opt.trace_id,
        raw_prompt=opt.raw_prompt,
        optimized_prompt=opt.optimized_prompt,
        task_type=opt.task_type,
        strategy_used=opt.strategy_used,
        changes_summary=opt.changes_summary,
        scores={
            "clarity": opt.score_clarity,
            "specificity": opt.score_specificity,
            "structure": opt.score_structure,
            "faithfulness": opt.score_faithfulness,
            "conciseness": opt.score_conciseness,
        },
        original_scores=opt.original_scores,
        score_deltas=opt.score_deltas,
        overall_score=opt.overall_score,
        provider=opt.provider,
        model_used=opt.model_used,
        models_by_phase=opt.models_by_phase,
        scoring_mode=opt.scoring_mode,
        duration_ms=opt.duration_ms,
        status=opt.status,
        context_sources=opt.context_sources,
        created_at=opt.created_at.isoformat() if opt.created_at else None,
        intent_label=opt.intent_label,
        domain=opt.domain,
        cluster_id=cluster_id,
    )


# ---------------------------------------------------------------------------
# Passthrough endpoints — manual prompt assembly without an LLM provider
# ---------------------------------------------------------------------------


class PassthroughSaveRequest(BaseModel):
    trace_id: str = Field(..., description="Trace ID from the prepare step")
    optimized_prompt: str = Field(..., min_length=1, description="The externally-optimized prompt")
    changes_summary: str | None = Field(None, description="Optional summary of changes made")
    domain: str | None = Field(
        None,
        description=(
            "Domain category: 'backend', 'frontend', 'database',"
            " 'devops', 'security', 'fullstack', or 'general'."
        ),
    )
    intent_label: str | None = Field(
        None, description="Short 3-6 word intent classification label.",
    )
    scores: dict[str, float] | None = Field(
        None,
        description="External LLM's self-assessed dimension scores (clarity, specificity, etc.).",
    )
    task_type: str | None = Field(
        None, description="Task classification from external LLM.",
    )
    strategy_used: str | None = Field(
        None, description="Strategy the external LLM reports using.",
    )
    model: str | None = Field(
        None, description="External model identifier.",
    )


@router.post("/optimize/passthrough")
async def passthrough_prepare(
    body: OptimizeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT)),
) -> PassthroughPrepareResponse:
    """Assemble an optimization prompt for manual passthrough to an external LLM.

    Does NOT require a configured provider. Creates a pending Optimization record
    and returns the assembled prompt for the user to copy to their LLM of choice.
    """
    logger.info("POST /api/optimize/passthrough: prompt_len=%d strategy=%s", len(body.prompt), body.strategy)

    _prefs = PreferencesService(DATA_DIR)
    requested_strategy = body.strategy or _prefs.get("defaults.strategy") or "auto"

    # Unified context enrichment
    context_service = getattr(request.app.state, "context_service", None)
    if not context_service:
        raise HTTPException(status_code=503, detail="Context enrichment service not initialized.")

    enrichment = await context_service.enrich(
        raw_prompt=body.prompt,
        tier="passthrough",
        db=db,
        workspace_path=body.workspace_path,
        repo_full_name=body.repo_full_name,
        applied_pattern_ids=body.applied_pattern_ids,
    )

    assembled, strategy_name = assemble_passthrough_prompt(
        prompts_dir=PROMPTS_DIR,
        raw_prompt=body.prompt,
        strategy_name=requested_strategy,
        codebase_guidance=enrichment.workspace_guidance,
        adaptation_state=enrichment.adaptation_state,
        analysis_summary=enrichment.analysis_summary,
        codebase_context=enrichment.codebase_context,
        applied_patterns=enrichment.applied_patterns,
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
        task_type=enrichment.task_type,
        domain=enrichment.domain_value,
        domain_raw=enrichment.domain_value,
        intent_label=enrichment.intent_label,
        context_sources=enrichment.context_sources_dict,
    )
    db.add(pending)
    await db.commit()

    return PassthroughPrepareResponse(
        trace_id=trace_id,
        optimization_id=opt_id,
        assembled_prompt=assembled,
        strategy_requested=strategy_name,
    )


@router.post("/optimize/passthrough/save")
async def passthrough_save(
    body: PassthroughSaveRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.OPTIMIZE_RATE_LIMIT)),
) -> OptimizationDetail:
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
        from app.schemas.pipeline_contracts import DimensionScores
        from app.services.score_blender import blend_scores

        # Heuristic baseline — always computed
        heur_optimized = HeuristicScorer.score_prompt(
            body.optimized_prompt, original=opt.raw_prompt,
        )
        heur_original = HeuristicScorer.score_prompt(opt.raw_prompt)

        # Fetch historical stats for z-score normalization
        historical_stats: dict | None = None
        try:
            from app.services.optimization_service import OptimizationService
            opt_svc = OptimizationService(db)
            historical_stats = await opt_svc.get_score_distribution(
                exclude_scoring_modes=["heuristic"],
            )
        except Exception as exc:
            logger.debug("Historical stats unavailable for normalization: %s", exc)

        if body.scores:
            # Hybrid path: external LLM scores + heuristic blending.
            # No bias correction — z-score normalization and dimension-
            # specific heuristic weights already guard against LLM
            # overconfidence.
            try:
                external_dims = DimensionScores.from_dict(
                    {k: float(v) for k, v in body.scores.items()},
                )
                blended_opt = blend_scores(
                    external_dims, heur_optimized, historical_stats,
                )
                opt_dims = blended_opt.to_dimension_scores()
                scoring_mode = "hybrid_passthrough"
            except Exception as exc:
                logger.warning(
                    "Hybrid blending failed, falling back to heuristic: %s", exc,
                )
                opt_dims = DimensionScores.from_dict(heur_optimized)
                scoring_mode = "heuristic"
        else:
            # Heuristic-only path — use raw heuristic scores directly.
            # No blending: z-score normalization is designed for LLM
            # scores, not model-independent heuristics.
            opt_dims = DimensionScores.from_dict(heur_optimized)
            scoring_mode = "heuristic"

        # Original scores — symmetric with optimized path
        if scoring_mode == "hybrid_passthrough":
            blended_orig = blend_scores(
                DimensionScores.from_dict(heur_original),
                heur_original, historical_stats,
            )
            orig_dims = blended_orig.to_dimension_scores()
        else:
            orig_dims = DimensionScores.from_dict(heur_original)

        optimized_scores = opt_dims.to_dict()
        original_scores = orig_dims.to_dict()
        overall = opt_dims.overall
        deltas = DimensionScores.compute_deltas(orig_dims, opt_dims)

    # Normalize strategy if external LLM returned a verbose name
    effective_strategy = opt.strategy_used
    if body.strategy_used:
        from app.services.strategy_loader import StrategyLoader
        strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")
        effective_strategy = strategy_loader.normalize_strategy(body.strategy_used)

    # Update record
    opt.optimized_prompt = body.optimized_prompt
    opt.changes_summary = body.changes_summary or ""
    opt.task_type = body.task_type or opt.task_type or "general"
    opt.strategy_used = effective_strategy
    opt.domain = body.domain or opt.domain or "general"
    opt.domain_raw = body.domain or opt.domain_raw or "general"
    opt.intent_label = body.intent_label or opt.intent_label or "general"
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
    opt.model_used = body.model or "external"
    opt.models_by_phase = {"optimize": body.model or "external"}
    await db.commit()
    await db.refresh(opt)

    # Publish event
    from app.services.event_bus import event_bus

    event_bus.publish("optimization_created", {
        "id": opt.id,
        "trace_id": opt.trace_id,
        "task_type": opt.task_type,
        "intent_label": opt.intent_label or "general",
        "domain": opt.domain or "general",
        "domain_raw": getattr(opt, "domain_raw", None) or "general",
        "strategy_used": opt.strategy_used,
        "overall_score": overall,
        "provider": "web_passthrough",
        "status": "completed",
    })

    logger.info(
        "Passthrough saved: trace_id=%s overall=%s scoring_mode=%s",
        body.trace_id, f"{overall:.2f}" if overall is not None else "N/A", scoring_mode,
    )

    # Passthrough optimizations have no family yet (extraction is async)
    return _serialize_optimization(opt, cluster_id=None)
