"""Optimization endpoints — POST /api/optimize (SSE), GET /api/optimize/{trace_id}, passthrough."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config as _cfg
from app.config import PROMPTS_DIR, settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import Optimization, OptimizationPattern
from app.services.heuristic_scorer import HeuristicScorer
from app.services.heuristic_suggestions import generate_heuristic_suggestions
from app.services.passthrough import assemble_passthrough_prompt
from app.services.pipeline import PipelineOrchestrator
from app.services.pipeline_constants import (
    MAX_DOMAIN_RAW_LENGTH,
    MAX_INTENT_LABEL_LENGTH,
    VALID_TASK_TYPES,
)
from app.services.preferences import PreferencesService
from app.services.taxonomy import get_engine as get_taxonomy_engine
from app.utils.sse import format_sse
from app.utils.text_cleanup import split_prompt_and_changes, title_case_label, validate_intent_label

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
    routing_tier: str | None = Field(
        default=None, description="Execution tier: internal, sampling, or passthrough.",
    )
    model_used: str | None = Field(default=None, description="Model ID used for optimization.")
    models_by_phase: dict[str, str] | None = Field(
        default=None, description="Per-phase model IDs used during optimization.",
    )
    scoring_mode: str | None = Field(default=None, description="Scoring method: 'hybrid', 'heuristic', or 'skipped'.")
    duration_ms: int | None = Field(default=None, description="Total pipeline duration in milliseconds.")
    status: str = Field(description="Optimization status: 'completed', 'pending', or 'failed'.")
    context_sources: dict | None = Field(
        default=None,
        description="Which context sources were available (guidance, codebase, adaptation, or batch seed metadata).",
    )
    created_at: str | None = Field(default=None, description="ISO 8601 creation timestamp.")
    intent_label: str | None = Field(default=None, description="Short intent classification label (3-6 words).")
    domain: str | None = Field(default=None, description="Domain category (backend, frontend, database, etc.).")
    cluster_id: str | None = Field(default=None, description="Pattern family ID this optimization belongs to.")
    heuristic_flags: list[str] = Field(
        default_factory=list,
        description="Dimensions where LLM and heuristic scores diverge significantly.",
    )
    suggestions: list[dict[str, str]] = Field(
        default_factory=list,
        description="Follow-up improvement suggestions, each with 'text' and 'source' keys.",
    )


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
    applied_pattern_ids: list[str] | None = Field(
        None, max_length=20, description="Pattern IDs to inject into optimizer context",
    )


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

    _prefs = PreferencesService(_cfg.DATA_DIR)
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

    # Default workspace_path to PROJECT_ROOT for web UI requests (which
    # don't send workspace_path). Ensures CLAUDE.md context is always available.
    from app.config import PROJECT_ROOT
    effective_workspace = body.workspace_path or str(PROJECT_ROOT)

    enrichment = await context_service.enrich(
        raw_prompt=body.prompt,
        tier=decision.tier,
        db=db,
        workspace_path=effective_workspace,
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
            trace_id=trace_id, provider="web_passthrough", routing_tier="passthrough",
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
                domain_resolver=getattr(request.app.state, "domain_resolver", None),
            ):
                yield format_sse(event.event, event.data)
        except Exception as exc:
            logger.error("Pipeline SSE stream error: %s", exc, exc_info=True)
            from app.providers.base import ProviderError
            if isinstance(exc, ProviderError):
                msg = f"Provider error: {type(exc).__name__}"
            else:
                msg = "An internal error occurred during optimization"
            yield format_sse("error", {"error": msg})

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


# ---------------------------------------------------------------------------
# PATCH /api/optimize/{optimization_id} — update optimization metadata
# ---------------------------------------------------------------------------


class OptimizationUpdateRequest(BaseModel):
    """Partial update for an optimization record (currently intent_label only)."""

    intent_label: str | None = Field(None, min_length=1, max_length=100)


@router.patch(
    "/optimize/{optimization_id}",
    response_model=OptimizationDetail,
)
async def update_optimization(
    optimization_id: str,
    body: OptimizationUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OptimizationDetail:
    """Update an optimization's metadata (e.g., rename its intent_label).

    Uses the optimization ``id`` (UUID), not ``trace_id``.
    """
    result = await db.execute(
        select(Optimization).where(Optimization.id == optimization_id)
    )
    opt = result.scalar_one_or_none()
    if opt is None:
        raise HTTPException(status_code=404, detail="Optimization not found.")

    changed = False
    if body.intent_label is not None:
        old_label = opt.intent_label
        opt.intent_label = validate_intent_label(
            title_case_label(body.intent_label.strip()),
            opt.raw_prompt,
        )[:MAX_INTENT_LABEL_LENGTH]
        if opt.intent_label != old_label:
            changed = True
            logger.info(
                "Optimization renamed: id=%s '%s' -> '%s'",
                optimization_id, old_label, opt.intent_label,
            )

    if changed:
        await db.commit()
        # Publish event so SSE subscribers (Navigator, ActivityPanel) update
        try:
            from app.services.event_bus import EventBus

            await EventBus.publish("optimization_updated", {
                "id": opt.id,
                "trace_id": opt.trace_id,
                "intent_label": opt.intent_label,
            })
        except Exception:
            pass  # non-critical

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
        routing_tier=opt.routing_tier,
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
        heuristic_flags=opt.heuristic_flags or [],
        suggestions=opt.suggestions or [],
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
            "Domain from known domain nodes (e.g., 'backend', 'frontend',"
            " 'database', 'devops', 'security'). Use 'primary: qualifier' for cross-cutting."
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

    _VALID_SCORE_KEYS = frozenset({"clarity", "specificity", "structure", "faithfulness", "conciseness"})

    @model_validator(mode="after")
    def _strip_unknown_score_keys(self) -> "PassthroughSaveRequest":
        """Strip unrecognized score dimensions — external LLMs may add extras."""
        if self.scores is not None:
            self.scores = {k: v for k, v in self.scores.items() if k in self._VALID_SCORE_KEYS}
        return self


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

    _prefs = PreferencesService(_cfg.DATA_DIR)
    requested_strategy = body.strategy or _prefs.get("defaults.strategy") or "auto"

    # Unified context enrichment
    context_service = getattr(request.app.state, "context_service", None)
    if not context_service:
        raise HTTPException(status_code=503, detail="Context enrichment service not initialized.")

    from app.config import PROJECT_ROOT as _PT_ROOT
    effective_pt_workspace = body.workspace_path or str(_PT_ROOT)
    enrichment = await context_service.enrich(
        raw_prompt=body.prompt,
        tier="passthrough",
        db=db,
        workspace_path=effective_pt_workspace,
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
        routing_tier="passthrough",
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
    request: Request,
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

    # Validate output length — reject excessively large prompts early
    if len(body.optimized_prompt) > settings.MAX_RAW_PROMPT_CHARS:
        raise HTTPException(
            422,
            f"optimized_prompt too long ({len(body.optimized_prompt)} chars). "
            f"Maximum is {settings.MAX_RAW_PROMPT_CHARS} characters.",
        )

    _prefs = PreferencesService(_cfg.DATA_DIR)
    scoring_enabled = _prefs.get("pipeline.enable_scoring")
    # Default to True if preference is not set (first-run, no preferences file)
    if scoring_enabled is None:
        scoring_enabled = True

    optimized_scores: dict[str, float] | None = None
    original_scores: dict[str, float] | None = None
    deltas: dict[str, float] | None = None
    overall: float | None = None
    scoring_mode = "skipped"
    heuristic_flags: list[str] = []

    # Clean external LLM output — strip preambles, fences, meta-headers,
    # and extract changes summary if the caller didn't provide one.
    # Must run BEFORE heuristic scoring so scores reflect clean text.
    cleaned_prompt, extracted_changes = split_prompt_and_changes(body.optimized_prompt)
    effective_changes = body.changes_summary or extracted_changes

    if scoring_enabled:
        from app.schemas.pipeline_contracts import DimensionScores
        from app.services.score_blender import blend_scores

        # Heuristic baseline — always computed (on cleaned text)
        heur_optimized = HeuristicScorer.score_prompt(
            cleaned_prompt, original=opt.raw_prompt,
        )
        heur_original = HeuristicScorer.score_prompt(opt.raw_prompt)

        # Fetch historical stats for z-score normalization
        historical_stats: dict | None = None
        try:
            from app.services.optimization_service import OptimizationService
            opt_svc = OptimizationService(db)
            historical_stats = await opt_svc.get_score_distribution(
                exclude_scoring_modes=["heuristic", "hybrid_passthrough"],
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
                    {k: max(1.0, min(10.0, float(v))) for k, v in body.scores.items()},
                )
                blended_opt = blend_scores(
                    external_dims, heur_optimized, historical_stats,
                )
                opt_dims = blended_opt.to_dimension_scores()
                scoring_mode = "hybrid_passthrough"
                heuristic_flags = blended_opt.divergence_flags or []
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
    opt.optimized_prompt = cleaned_prompt
    opt.changes_summary = effective_changes or ""
    _raw_task_type = body.task_type or opt.task_type or "general"
    opt.task_type = _raw_task_type if _raw_task_type in VALID_TASK_TYPES else "general"
    opt.strategy_used = effective_strategy
    _domain_resolver = getattr(request.app.state, "domain_resolver", None)
    if _domain_resolver is not None:
        # confidence=1.0: passthrough has no analyzer confidence — domain trusted from external LLM or heuristic
        validated_domain = await _domain_resolver.resolve(body.domain, confidence=1.0)
    else:
        validated_domain = "general"
    opt.domain = validated_domain
    # cluster_id is set asynchronously via optimization_created event → taxonomy hot path
    opt.domain_raw = (body.domain or opt.domain_raw or "general")[:MAX_DOMAIN_RAW_LENGTH]
    opt.intent_label = validate_intent_label(
        title_case_label((body.intent_label or opt.intent_label or "general")[:MAX_INTENT_LABEL_LENGTH]),
        opt.raw_prompt,
    )
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
    opt.heuristic_flags = heuristic_flags if heuristic_flags else None

    # Generate heuristic suggestions (zero-LLM)
    from app.services.heuristic_analyzer import HeuristicAnalyzer
    _analyzer = HeuristicAnalyzer()
    _analysis = await _analyzer.analyze(opt.raw_prompt, db)
    suggestions = generate_heuristic_suggestions(
        dimension_scores=optimized_scores or {},
        weaknesses=_analysis.weaknesses,
        strategy_used=effective_strategy,
    )
    opt.suggestions = suggestions

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
