"""Standalone analyze pipeline for MCP sampling — extracted from ``sampling_pipeline``.

Used by ``synthesis_analyze`` when no local LLM provider is available but the
MCP client supports sampling. Runs a two-phase analyze + baseline score flow
with no optimize phase and no pattern injection.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from mcp.server.fastmcp import Context

from app.config import DATA_DIR, PROMPTS_DIR
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    ScoreResult,
)
from app.services.event_notification import notify_event_bus
from app.services.heuristic_scorer import HeuristicScorer
from app.services.pipeline_constants import (
    MAX_DOMAIN_RAW_LENGTH,
    MAX_INTENT_LABEL_LENGTH,
    semantic_check,
    semantic_upgrade_general,
)
from app.services.prompt_loader import PromptLoader
from app.services.sampling.persistence import fetch_historical_stats
from app.services.sampling.primitives import (
    build_analysis_from_text,
    parse_text_response,
    sampling_request_plain,
    sampling_request_structured,
)
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader

logger = logging.getLogger(__name__)


async def run_sampling_analyze(
    ctx: Context,
    prompt: str,
    *,
    repo_full_name: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Two-phase sampling pipeline: analyze + baseline score.

    Used by ``synthesis_analyze`` when no local LLM provider is available
    but the MCP client supports sampling. Pure diagnostic — no DB row is
    created and no cluster assignment happens. ``repo_full_name`` and
    ``project_id`` are accepted for caller compatibility but are no-ops.
    """
    start = time.monotonic()
    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")

    phase_durations: dict[str, int] = {}
    context_sources: dict[str, Any] = {
        "explore": False,
        "patterns": False,
        "adaptation": False,
        "workspace": False,
    }

    from app.services.trace_logger import TraceLogger

    try:
        trace_logger: TraceLogger | None = TraceLogger(DATA_DIR / "traces")
    except OSError:
        trace_logger = None

    # --- Phase 1: Analyze ---
    phase_t0 = time.monotonic()
    system_prompt = loader.load("agent-guidance.md")
    try:
        from app.tools._shared import get_domain_resolver as _get_dr_analyze
        _analyze_resolver = _get_dr_analyze()
        _analyze_known_domains = (
            ", ".join(sorted(_analyze_resolver.domain_labels))
            if _analyze_resolver.domain_labels
            else "backend, frontend, database, data, devops, security, fullstack, general"
        )
    except Exception:
        _analyze_known_domains = "backend, frontend, database, data, devops, security, fullstack, general"
    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": strategy_loader.format_available(),
        "known_domains": _analyze_known_domains,
    })

    try:
        analysis, _analyze_model = await sampling_request_structured(
            ctx, system_prompt, analyze_msg, AnalysisResult,
        )
    except Exception:
        logger.warning("Structured analysis parsing failed in analyze-only, using fallback")
        try:
            text, _analyze_model = await sampling_request_plain(
                ctx, system_prompt, analyze_msg,
            )
            try:
                analysis = parse_text_response(text, AnalysisResult)
            except Exception:
                analysis = build_analysis_from_text(text, "auto", raw_prompt=prompt)
        except Exception:
            analysis = build_analysis_from_text("", "auto", raw_prompt=prompt)
            _analyze_model = "unknown"

    analyze_ms = int((time.monotonic() - phase_t0) * 1000)
    phase_durations["analyze_ms"] = analyze_ms
    if trace_logger:
        trace_logger.log_phase(
            trace_id="(pending)",
            phase="analyze",
            duration_ms=analyze_ms,
            tokens_in=0, tokens_out=0,
            model=_analyze_model, provider="mcp_sampling",
            result={"task_type": analysis.task_type, "strategy": analysis.selected_strategy},
        )
    logger.info(
        "Sampling analyze Phase 1 complete in %dms: task_type=%s strategy=%s",
        analyze_ms, analysis.task_type, analysis.selected_strategy,
    )

    # Domain resolution via DomainResolver (mirrors pipeline.py)
    _analyze_domain_raw = getattr(analysis, "domain", None) or "general"
    _analyze_confidence = semantic_check(analysis.task_type, prompt, analysis.confidence)

    # Upgrade "general" to a specific type when strong keywords are present
    _upgraded_task_type = semantic_upgrade_general(analysis.task_type, prompt)
    if _upgraded_task_type != analysis.task_type:
        analysis.task_type = _upgraded_task_type  # type: ignore[assignment]

    try:
        from app.tools._shared import get_domain_resolver
        _analyze_resolver = get_domain_resolver()
        effective_domain = await _analyze_resolver.resolve(
            _analyze_domain_raw, _analyze_confidence, raw_prompt=prompt,
        )
    except (ValueError, Exception):
        effective_domain = "general"

    # Domain mapping + DB persistence intentionally removed: sampling analyze
    # is diagnostic (same contract as the internal-tier analyze tool). The
    # domain label still flows through the return payload below; no cluster
    # assignment or Optimization row is created.
    _ = MAX_DOMAIN_RAW_LENGTH  # kept for future callers that re-enable persistence

    # --- Phase 2: Baseline score ---
    phase_t0 = time.monotonic()
    scoring_system = loader.load("scoring.md")
    # For sampling: append explicit JSON output directive (same as main pipeline)
    scoring_system += (
        "\n\nYou MUST output ONLY valid JSON matching the ScoreResult schema. "
        "No markdown, no reasoning text, no commentary outside the JSON structure."
    )
    scorer_msg = (
        f"<prompt-a>\n{prompt}\n</prompt-a>\n\n"
        f"<prompt-b>\n{prompt}\n</prompt-b>"
    )

    heur_scores = HeuristicScorer.score_prompt(prompt)

    _score_model = "unknown"
    try:
        score_result, _score_model = await sampling_request_structured(
            ctx, scoring_system, scorer_msg, ScoreResult,
            max_tokens=1024,
        )
        # Hybrid blend
        historical_stats = await fetch_historical_stats()
        blended = blend_scores(score_result.prompt_a_scores, heur_scores, historical_stats)
        baseline = blended.to_dimension_scores()
    except Exception as exc:
        logger.warning("Sampling baseline score failed, using heuristic-only: %s", exc)
        baseline = DimensionScores(
            clarity=heur_scores.get("clarity", 5.0),
            specificity=heur_scores.get("specificity", 5.0),
            structure=heur_scores.get("structure", 5.0),
            faithfulness=heur_scores.get("faithfulness", 5.0),
            conciseness=heur_scores.get("conciseness", 5.0),
        )
    phase_durations["score_ms"] = int((time.monotonic() - phase_t0) * 1000)
    if trace_logger:
        trace_logger.log_phase(
            trace_id="(pending)",
            phase="score",
            duration_ms=phase_durations["score_ms"],
            tokens_in=0, tokens_out=0,
            model=_score_model, provider="mcp_sampling",
        )

    overall = baseline.overall
    total_ms = int((time.monotonic() - start) * 1000)

    # No DB persistence — see module docstring. The intent label is
    # lightly normalised here so the return payload matches the pipeline
    # contract shape even though nothing is stored.
    _ = MAX_INTENT_LABEL_LENGTH  # kept for future callers that re-enable persistence
    _ = context_sources  # still computed for diagnostic output only

    # --- Notify event bus (diagnostic telemetry only) ---
    await notify_event_bus("optimization_analyzed", {
        "id": None,
        "trace_id": None,
        "task_type": analysis.task_type,
        "strategy": analysis.selected_strategy,
        "overall_score": overall,
        "provider": "mcp_sampling",
        "status": "analyzed",
    })

    # --- Build actionable next steps ---
    dim_scores = {
        "clarity": baseline.clarity,
        "specificity": baseline.specificity,
        "structure": baseline.structure,
        "faithfulness": baseline.faithfulness,
        "conciseness": baseline.conciseness,
    }
    next_steps = [
        "Run `synthesis_optimize(prompt=..., strategy='%s')` to improve this prompt"
        % analysis.selected_strategy,
    ]
    for weakness in analysis.weaknesses[:3]:
        next_steps.append("Address: %s" % weakness)

    weakest_dim = min(dim_scores, key=dim_scores.get)  # type: ignore[arg-type]
    weakest_val = dim_scores[weakest_dim]
    if weakest_val < 7.0:
        next_steps.append(
            "Focus on %s (scored %.1f/10) — this is the biggest opportunity for improvement"
            % (weakest_dim, weakest_val)
        )

    return {
        "optimization_id": None,
        "task_type": analysis.task_type,
        "weaknesses": analysis.weaknesses,
        "strengths": analysis.strengths,
        "selected_strategy": analysis.selected_strategy,
        "strategy_rationale": analysis.strategy_rationale,
        "confidence": analysis.confidence,
        "baseline_scores": dim_scores,
        "overall_score": overall,
        "duration_ms": total_ms,
        "next_steps": next_steps,
        "optimization_ready": {
            "prompt": prompt,
            "strategy": analysis.selected_strategy,
        },
        "intent_label": getattr(analysis, "intent_label", None) or "general",
        "domain": effective_domain,
    }


__all__ = ["run_sampling_analyze"]
