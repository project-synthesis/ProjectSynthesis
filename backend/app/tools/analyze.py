"""Handler for synthesis_analyze MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import time
import uuid

from mcp.server.fastmcp import Context

from app.config import PROMPTS_DIR
from app.database import async_session_factory
from app.models import Optimization
from app.schemas.mcp_models import AnalyzeOutput
from app.schemas.pipeline_contracts import AnalysisResult, ScoreResult
from app.services.event_notification import notify_event_bus
from app.services.heuristic_scorer import HeuristicScorer
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.routing import RoutingContext
from app.services.sampling_pipeline import run_sampling_analyze
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader
from app.tools._shared import DATA_DIR, get_domain_resolver, get_routing, get_taxonomy_engine

logger = logging.getLogger(__name__)


async def handle_analyze(
    prompt: str,
    ctx: Context | None,
) -> AnalyzeOutput:
    """Analyze a prompt and score it."""
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )

    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()
    ctx_routing = RoutingContext(preferences=prefs_snapshot, caller="mcp")
    routing = get_routing()
    decision = routing.resolve(ctx_routing)

    provider = decision.provider

    if decision.tier == "sampling":
        logger.info("synthesis_analyze: tier=sampling prompt_len=%d reason=%r", len(prompt), decision.reason)
        if ctx and hasattr(ctx, "session") and ctx.session:
            try:
                result = await run_sampling_analyze(ctx, prompt)
                return AnalyzeOutput(**result)
            except Exception as exc:
                logger.warning("Sampling analyze failed: %s: %s", type(exc).__name__, exc)
        raise ValueError("No LLM provider available. Set ANTHROPIC_API_KEY or install the Claude CLI.")

    if decision.tier == "passthrough":
        logger.info("synthesis_analyze: tier=passthrough — rejecting (analysis requires provider)")
        raise ValueError("Analysis requires a local provider or MCP sampling capability.")

    start = time.monotonic()
    logger.info(
        "synthesis_analyze: tier=internal provider=%s prompt_len=%d",
        decision.provider_name, len(prompt),
    )

    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")

    # Resolve model IDs once for reuse in calls + persistence
    analyzer_model = prefs.resolve_model("analyzer", prefs_snapshot)
    scorer_model = prefs.resolve_model("scorer", prefs_snapshot)

    # --- Phase 1: Analyze ---
    system_prompt = loader.load("agent-guidance.md")

    # Resolve dynamic domain list for analyzer prompt
    try:
        resolver = get_domain_resolver()
        known_domains = ", ".join(sorted(resolver.domain_labels))
    except ValueError:
        known_domains = "backend, frontend, database, devops, security, fullstack, general"

    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": strategy_loader.format_available(),
        "known_domains": known_domains,
    })

    try:
        analysis: AnalysisResult = await provider.complete_parsed(
            model=analyzer_model,
            system_prompt=system_prompt,
            user_message=analyze_msg,
            output_format=AnalysisResult,
            max_tokens=16384,
            effort="medium",
        )
    except Exception as exc:
        logger.error("synthesis_analyze Phase 1 (analyze) failed: %s", exc)
        raise ValueError("Analysis failed: %s" % exc) from exc

    analyze_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "synthesis_analyze Phase 1 complete in %dms: task_type=%s strategy=%s confidence=%.2f",
        analyze_ms, analysis.task_type, analysis.selected_strategy, analysis.confidence,
    )

    # --- Phase 2: Score original prompt ---
    scoring_system = loader.load("scoring.md")
    scorer_msg = (
        f"<prompt-a>\n{prompt}\n</prompt-a>\n\n"
        f"<prompt-b>\n{prompt}\n</prompt-b>"
    )

    try:
        score_result: ScoreResult = await provider.complete_parsed(
            model=scorer_model,
            system_prompt=scoring_system,
            user_message=scorer_msg,
            output_format=ScoreResult,
            max_tokens=16384,
            effort="medium",
        )
    except Exception as exc:
        logger.error("synthesis_analyze Phase 2 (score) failed: %s", exc)
        raise ValueError("Scoring failed: %s" % exc) from exc

    # Both A and B are the same prompt — use prompt_a_scores as baseline
    heur_scores = HeuristicScorer.score_prompt(prompt)
    blended = blend_scores(score_result.prompt_a_scores, heur_scores)
    baseline = blended.to_dimension_scores()
    overall = baseline.overall

    total_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "synthesis_analyze Phase 2 complete: overall=%.1f total_ms=%d",
        overall, total_ms,
    )

    # --- Phase 2.5: Domain Mapping (Spec 6.7, hot-path only) ---
    domain_raw = getattr(analysis, "domain", None) or "general"
    cluster_id = None
    taxonomy_engine = get_taxonomy_engine()
    if taxonomy_engine is not None:
        try:
            async with async_session_factory() as db_map:
                mapping = await taxonomy_engine.map_domain(
                    domain_raw=domain_raw,
                    db=db_map,
                    applied_pattern_ids=None,
                )
                cluster_id = mapping.cluster_id
        except Exception as exc:
            logger.warning("MCP domain mapping failed (non-fatal): %s", exc)

    # --- Persist to DB ---
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    async with async_session_factory() as db:
        opt = Optimization(
            id=opt_id,
            raw_prompt=prompt,
            optimized_prompt="",
            task_type=analysis.task_type,
            intent_label=getattr(analysis, "intent_label", None) or "general",
            domain=getattr(analysis, "domain", None) or "general",
            strategy_used=analysis.selected_strategy,
            changes_summary="",
            score_clarity=baseline.clarity,
            score_specificity=baseline.specificity,
            score_structure=baseline.structure,
            score_faithfulness=baseline.faithfulness,
            score_conciseness=baseline.conciseness,
            overall_score=overall,
            domain_raw=domain_raw,
            cluster_id=cluster_id,
            provider=provider.name,
            model_used=analyzer_model,
            models_by_phase={
                "analyze": analyzer_model,
                "score": scorer_model,
            },
            scoring_mode="baseline",
            status="analyzed",
            trace_id=trace_id,
            duration_ms=total_ms,
        )
        db.add(opt)
        await db.commit()

    logger.info(
        "synthesis_analyze persisted: optimization_id=%s trace_id=%s cluster_id=%s",
        opt_id, trace_id, cluster_id,
    )

    # --- Notify event bus ---
    await notify_event_bus("optimization_analyzed", {
        "id": opt_id,
        "trace_id": trace_id,
        "task_type": analysis.task_type,
        "strategy": analysis.selected_strategy,
        "overall_score": overall,
        "provider": provider.name,
        "status": "analyzed",
    })

    # --- Build actionable next steps ---
    next_steps = [
        "Run `synthesis_optimize(prompt=..., strategy='%s')` to improve this prompt"
        % analysis.selected_strategy,
    ]
    for weakness in analysis.weaknesses[:3]:
        next_steps.append("Address: %s" % weakness)

    dim_scores = {
        "clarity": baseline.clarity,
        "specificity": baseline.specificity,
        "structure": baseline.structure,
        "faithfulness": baseline.faithfulness,
        "conciseness": baseline.conciseness,
    }
    weakest_dim = min(dim_scores, key=dim_scores.get)  # type: ignore[arg-type]
    weakest_val = dim_scores[weakest_dim]
    if weakest_val < 7.0:
        next_steps.append(
            "Focus on %s (scored %.1f/10) — this is the biggest opportunity for improvement"
            % (weakest_dim, weakest_val)
        )

    return AnalyzeOutput(
        optimization_id=opt_id,
        task_type=analysis.task_type,
        weaknesses=analysis.weaknesses,
        strengths=analysis.strengths,
        selected_strategy=analysis.selected_strategy,
        strategy_rationale=analysis.strategy_rationale,
        confidence=analysis.confidence,
        baseline_scores=dim_scores,
        overall_score=overall,
        duration_ms=total_ms,
        next_steps=next_steps,
        optimization_ready={
            "strategy": analysis.selected_strategy,
        },
        intent_label=getattr(analysis, "intent_label", None) or "general",
        domain=getattr(analysis, "domain", None) or "general",
    )
