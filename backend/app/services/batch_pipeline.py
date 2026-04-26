"""In-memory batch optimization pipeline — seed-prompt execution.

Runs one seed prompt end-to-end (analyze → optimize → score → embed) without
any DB writes. Results accumulate as ``PendingOptimization`` objects and are
persisted in a single transaction by ``bulk_persist`` after the batch
completes.

Phase 3E split: parallel orchestration and post-batch persistence live in
sibling modules to keep this file focused on per-prompt execution.

* ``PendingOptimization`` — in-memory result dataclass (kept here because all
  three split modules use it; defined in the module that callers import).
* ``run_single_prompt`` — per-prompt execution loop; enrichment via
  ``ContextEnrichmentService.enrich()`` matches the regular ``pipeline.py``
  contract.
* ``estimate_batch_cost`` — USD cost estimator for a batch seed run.
* ``run_batch`` / ``bulk_persist`` / ``batch_taxonomy_assign`` are
  re-exported from ``batch_orchestrator`` and ``batch_persistence`` for
  backward compatibility — external callers (``tools/seed.py``,
  ``tests/test_batch_pipeline.py``) continue to import them from
  ``app.services.batch_pipeline``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config import DATA_DIR
from app.providers.base import LLMProvider, call_provider_with_retry
from app.schemas.pipeline_contracts import (
    DIMENSION_WEIGHTS,
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.batch_orchestrator import run_batch
from app.services.batch_persistence import batch_taxonomy_assign, bulk_persist
from app.services.classification_agreement import get_classification_agreement
from app.services.context_enrichment import resolve_strategy_intelligence
from app.services.embedding_service import EmbeddingService
from app.services.heuristic_scorer import HeuristicScorer
from app.services.optimization_service import OptimizationService
from app.services.pattern_injection import (
    auto_inject_patterns,
    format_few_shot_examples,
    format_injected_patterns,
    retrieve_few_shot_examples,
)
from app.services.pipeline_constants import (
    ANALYZE_MAX_TOKENS,
    SCORE_MAX_TOKENS,
    VALID_TASK_TYPES,
    clamp_analyze_effort,
    compute_optimize_max_tokens,
    resolve_effective_strategy,
    semantic_upgrade_general,
)
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader
from app.utils.text_cleanup import (
    sanitize_optimization_result,
    title_case_label,
    validate_intent_label,
)

if TYPE_CHECKING:
    from collections.abc import AsyncContextManager, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    # Production wires in `async_sessionmaker[AsyncSession]` from `app.database`;
    # tests pass lightweight factories with an `async __aenter__/__aexit__`. Both
    # satisfy `Callable[[], AsyncContextManager[AsyncSession]]` structurally.
    SessionFactory = (
        async_sessionmaker[AsyncSession]
        | Callable[[], AsyncContextManager[AsyncSession]]
    )

# Z-score distribution shape: {dimension: {"count": int, "mean": float, "stddev": float}}
ScoreDistribution = dict[str, dict[str, float | int]]

logger = logging.getLogger(__name__)


@dataclass
class PendingOptimization:
    """In-memory optimization result awaiting bulk persist."""

    id: str
    trace_id: str
    raw_prompt: str
    batch_id: str = ""  # Lineage: which batch produced this row
    optimized_prompt: str | None = None
    task_type: str | None = None
    strategy_used: str | None = None
    changes_summary: str | None = None
    score_clarity: float | None = None
    score_specificity: float | None = None
    score_structure: float | None = None
    score_faithfulness: float | None = None
    score_conciseness: float | None = None
    overall_score: float | None = None
    improvement_score: float | None = None
    scoring_mode: str | None = None
    intent_label: str | None = None
    domain: str | None = None
    domain_raw: str | None = None
    embedding: bytes | None = None
    optimized_embedding: bytes | None = None
    transformation_embedding: bytes | None = None
    models_by_phase: dict | None = None
    original_scores: dict | None = None
    score_deltas: dict | None = None
    duration_ms: int | None = None
    status: str = "completed"
    provider: str | None = None
    model_used: str | None = None
    routing_tier: str | None = None
    heuristic_flags: list | None = None
    suggestions: list | None = None
    repo_full_name: str | None = None
    project_id: str | None = None
    context_sources: dict | None = None
    error: str | None = None  # Non-None if this prompt failed


async def run_single_prompt(
    raw_prompt: str,
    provider: LLMProvider,
    prompt_loader: PromptLoader,
    embedding_service: EmbeddingService,
    *,
    codebase_context: str | None = None,
    repo_full_name: str | None = None,
    batch_id: str = "",
    agent_name: str = "",
    prompt_index: int = 0,
    total_prompts: int = 1,
    session_factory: SessionFactory | None = None,
    taxonomy_engine: Any | None = None,
    domain_resolver: Any | None = None,
    tier: str = "internal",
    context_service: Any | None = None,
    historical_stats: ScoreDistribution | None = None,
    project_id: str | None = None,
) -> PendingOptimization:
    """Run one prompt through analyze → optimize → score → embed in memory.

    IMPORTANT: This function does NOT use PipelineOrchestrator. It makes
    direct provider calls following the same phase logic but without DB
    dependencies.

    ``tier`` is the routing tier resolved upstream (typically via
    ``RoutingManager.resolve()``). It is persisted on the resulting
    ``PendingOptimization.routing_tier`` so downstream analytics and
    cost attribution see the real tier instead of a hardcoded default.

    ``context_service`` — when provided, ``ContextEnrichmentService.enrich()``
    is the single entry for pattern injection, strategy intelligence,
    codebase context, and divergence alerts. This aligns the batch with
    the regular pipeline so seeded prompts go through the same B0 repo
    relevance gate, B1/B2 divergence detection, and enrichment-profile
    selection (code_aware / knowledge_work / cold_start). When ``None``,
    falls back to inline enrichment for backward compatibility.

    ``historical_stats`` — pre-fetched score distribution for z-score
    normalization. ``run_batch`` fetches this once per batch and threads
    it here to avoid the N+1 query pattern (one ``get_score_distribution``
    round-trip per prompt). When ``None``, falls back to a per-prompt
    fetch using ``session_factory`` for backward compat.

    Returns a PendingOptimization with all fields populated.
    On any phase failure, returns a PendingOptimization with error set
    and status="failed". Never raises — errors are captured in the result.
    """
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    t0 = time.monotonic()

    try:
        prefs = PreferencesService(DATA_DIR)
        prefs_snapshot = prefs.load()
        analyzer_model = prefs.resolve_model("analyzer", prefs_snapshot)
        optimizer_model = prefs.resolve_model("optimizer", prefs_snapshot)
        scorer_model = prefs.resolve_model("scorer", prefs_snapshot)

        system_prompt = prompt_loader.load("agent-guidance.md")
        strategy_loader = StrategyLoader(prompt_loader.prompts_dir / "strategies")
        available_strategies = strategy_loader.format_available()

        # --- Phase 1: Analyze ---
        # Use dynamic domain labels from domain resolver when available (matches pipeline.py).
        # Falls back to hardcoded list on cold start before domain resolver is initialized.
        _known_domains = "backend, frontend, database, data, devops, security, fullstack, general"
        if domain_resolver is not None:
            try:
                _labels = domain_resolver.domain_labels
                if _labels:
                    _known_domains = ", ".join(sorted(_labels))
            except Exception as _dr_exc:
                logger.warning(
                    "Domain resolver labels unavailable (falling back to hardcoded): %s",
                    _dr_exc,
                )
        analyze_msg = prompt_loader.render("analyze.md", {
            "raw_prompt": raw_prompt,
            "available_strategies": available_strategies,
            "known_domains": _known_domains,
        })
        analysis: AnalysisResult = await call_provider_with_retry(
            provider,
            model=analyzer_model,
            system_prompt=system_prompt,
            user_message=analyze_msg,
            output_format=AnalysisResult,
            max_tokens=ANALYZE_MAX_TOKENS,
            effort=clamp_analyze_effort(
                prefs.get("pipeline.analyzer_effort", prefs_snapshot)
            ),
        )

        # Semantic upgrade gate (matches pipeline.py)
        effective_task_type = semantic_upgrade_general(analysis.task_type, raw_prompt)
        if effective_task_type != analysis.task_type:
            analysis.task_type = effective_task_type  # type: ignore[assignment]

        # Domain resolution (matches pipeline.py hot path)
        effective_domain = analysis.domain or "general"
        if domain_resolver is not None:
            try:
                effective_domain = await domain_resolver.resolve(
                    analysis.domain, analysis.confidence, raw_prompt,
                )
            except Exception as _dr_exc:
                logger.debug("Domain resolve failed for prompt %d: %s", prompt_index, _dr_exc)

        effective_strategy = resolve_effective_strategy(
            selected_strategy=analysis.selected_strategy,
            available=strategy_loader.list_strategies(),
            blocked_strategies=set(),
            confidence=analysis.confidence,
            strategy_override=None,
            trace_id=trace_id,
            data_recommendation=None,
            task_type=analysis.task_type,
            intent_label=analysis.intent_label,
        )
        strategy_instructions = strategy_loader.load(effective_strategy)
        analysis_summary = (
            f"Task type: {analysis.task_type}\n"
            f"Domain: {effective_domain}\n"
            f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
            f"Strengths: {', '.join(analysis.strengths)}\n"
            f"Strategy: {effective_strategy}\n"
            f"Rationale: {analysis.strategy_rationale}"
        )

        # --- Pre-optimization enrichment (matches pipeline.py Phase 1.5) ---
        # Embed raw prompt BEFORE Phase 2 so it can drive pattern/few-shot search
        prompt_embedding = None
        raw_embedding: bytes | None = None
        try:
            prompt_vec = await embedding_service.aembed_single(raw_prompt)
            prompt_embedding = prompt_vec  # ndarray for search
            raw_embedding = prompt_vec.astype("float32").tobytes()
        except Exception as exc:
            logger.warning("Raw embedding failed for prompt %d: %s", prompt_index, exc)

        # Unified enrichment via ContextEnrichmentService — delivers pattern
        # injection, strategy intelligence, codebase context, and divergence
        # alerts through the same entry point as the regular pipeline. Also
        # gives the seed path the B0 repo relevance gate and B1/B2 tech-stack
        # divergence detection, plus enrichment-profile selection
        # (code_aware / knowledge_work / cold_start).
        applied_patterns_text: str | None = None
        adaptation_text: str | None = None
        enriched_codebase_context: str | None = None
        divergence_alerts_text: str | None = None
        enrichment_sources: dict[str, Any] = {}
        context_flags: dict[str, bool] = {}

        if context_service is not None and session_factory is not None:
            try:
                # Retrieve routing safely via app state or shared MCP accessor
                routing = None
                try:
                    from app.tools._shared import get_routing
                    routing = get_routing()
                except Exception:
                    pass

                async with session_factory() as _enrich_db:
                    enrichment = await context_service.enrich(
                        raw_prompt=raw_prompt,
                        tier=tier,
                        db=_enrich_db,
                        workspace_path=None,
                        repo_full_name=repo_full_name,
                        applied_pattern_ids=None,
                        preferences_snapshot=prefs_snapshot,
                        project_id=project_id,
                        provider=routing.provider if routing else None,
                    )
                applied_patterns_text = enrichment.applied_patterns
                adaptation_text = enrichment.strategy_intelligence
                enriched_codebase_context = enrichment.codebase_context
                divergence_alerts_text = enrichment.divergence_alerts
                # Merge enrichment's layer flags + enrichment_meta for persistence
                enrichment_sources = dict(enrichment.context_sources)
                if enrichment.enrichment_meta:
                    enrichment_sources["enrichment_meta"] = dict(enrichment.enrichment_meta)
                    # A1 follow-up: batch path also runs LLM analysis + domain
                    # resolution. Reconcile signals against the final
                    # effective_domain so seed rows don't ship contradictions.
                    from app.services.context_enrichment import reconcile_domain_signals
                    enrichment_sources["enrichment_meta"] = reconcile_domain_signals(
                        enrichment_sources["enrichment_meta"], effective_domain,
                    )
                if applied_patterns_text:
                    context_flags["cluster_injection"] = True
                if adaptation_text:
                    context_flags["strategy_intelligence"] = True

                # E1: record heuristic-vs-LLM classification agreement.
                # Mirrors pipeline.py so the health endpoint's agreement +
                # strategy-intel-hit-rate counters reflect seed traffic too.
                heuristic = enrichment.analysis
                if heuristic is not None:
                    try:
                        ca = get_classification_agreement()
                        ca.record(
                            heuristic_task_type=heuristic.task_type,
                            heuristic_domain=heuristic.domain or "general",
                            llm_task_type=analysis.task_type,
                            llm_domain=effective_domain,
                            prompt_snippet=raw_prompt[:80],
                        )
                        ca.record_strategy_intel(had_intel=bool(adaptation_text))
                    except Exception:
                        logger.debug(
                            "Classification agreement tracking failed",
                            exc_info=True,
                        )
            except Exception as _enrich_exc:
                logger.warning(
                    "ContextEnrichmentService.enrich() failed for prompt %d: %s — "
                    "falling back to empty enrichment",
                    prompt_index, _enrich_exc,
                )
        else:
            # Legacy inline enrichment path (kept for callers that haven't been
            # migrated to pass a context_service). Matches pre-Fix-2 behavior.
            if taxonomy_engine is not None and session_factory is not None and prompt_embedding is not None:
                try:
                    async with session_factory() as _enrich_db:
                        injected, _cluster_ids = await auto_inject_patterns(
                            raw_prompt=raw_prompt,
                            taxonomy_engine=taxonomy_engine,
                            db=_enrich_db,
                            trace_id=trace_id,
                            optimization_id=opt_id,
                        )
                    if injected:
                        applied_patterns_text = format_injected_patterns(injected)
                        context_flags["cluster_injection"] = True
                except Exception as _pi_exc:
                    logger.debug("Pattern injection failed for prompt %d: %s", prompt_index, _pi_exc)

            if session_factory is not None:
                try:
                    async with session_factory() as _si_db:
                        adaptation_text, _ = await resolve_strategy_intelligence(
                            _si_db,
                            analysis.task_type or "general",
                            analysis.domain or "general",
                        )
                    if adaptation_text:
                        context_flags["strategy_intelligence"] = True
                except Exception as _si_exc:
                    logger.debug("Strategy intelligence failed for prompt %d: %s", prompt_index, _si_exc)

        # Retrieve few-shot examples (input-similar past optimizations).
        # EnrichedContext does not include few-shot, so this runs in both the
        # unified-enrichment path and the legacy path — matching pipeline.py
        # which also retrieves few-shot outside ContextEnrichmentService.
        few_shot_text: str | None = None
        if session_factory is not None and prompt_embedding is not None:
            try:
                async with session_factory() as _fs_db:
                    examples = await retrieve_few_shot_examples(
                        raw_prompt=raw_prompt,
                        db=_fs_db,
                        trace_id=trace_id,
                        prompt_embedding=prompt_embedding,
                    )
                few_shot_text = format_few_shot_examples(examples)
                if few_shot_text:
                    context_flags["few_shot_examples"] = True
            except Exception as _fs_exc:
                logger.debug("Few-shot retrieval failed for prompt %d: %s", prompt_index, _fs_exc)

        # --- Phase 2: Optimize ---
        # Prefer codebase context from enrichment (B0-gated + workspace fallback);
        # explicit `codebase_context` param acts as a caller-supplied override.
        _effective_codebase = codebase_context or enriched_codebase_context
        optimize_msg = prompt_loader.render("optimize.md", {
            "raw_prompt": raw_prompt,
            "analysis_summary": analysis_summary,
            "strategy_instructions": strategy_instructions,
            "codebase_context": _effective_codebase,
            "strategy_intelligence": adaptation_text,
            "applied_patterns": applied_patterns_text,
            "few_shot_examples": few_shot_text,
            "divergence_alerts": divergence_alerts_text,
        })
        dynamic_max_tokens = compute_optimize_max_tokens(len(raw_prompt))
        optimization: OptimizationResult = await call_provider_with_retry(
            provider,
            model=optimizer_model,
            system_prompt=system_prompt,
            user_message=optimize_msg,
            output_format=OptimizationResult,
            max_tokens=dynamic_max_tokens,
            effort=prefs.get("pipeline.optimizer_effort", prefs_snapshot) or "high",
            streaming=True,
        )
        _clean_prompt, _clean_changes = sanitize_optimization_result(
            optimization.optimized_prompt, optimization.changes_summary,
        )
        optimization = OptimizationResult(
            optimized_prompt=_clean_prompt,
            changes_summary=_clean_changes,
            strategy_used=optimization.strategy_used,
        )

        # --- Phase 3: Score ---
        original_scores = None
        optimized_scores = None
        deltas = None
        scoring_mode = "skipped"
        _heuristic_flags: list | None = None
        if prefs.get("pipeline.enable_scoring", prefs_snapshot):
            import random
            original_first = random.choice([True, False])
            prompt_a = raw_prompt if original_first else optimization.optimized_prompt
            prompt_b = optimization.optimized_prompt if original_first else raw_prompt

            scoring_system = prompt_loader.load("scoring.md")
            scorer_msg = (
                f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
                f"<prompt-b>\n{prompt_b}\n</prompt-b>"
            )
            scores: ScoreResult = await call_provider_with_retry(
                provider,
                model=scorer_model,
                system_prompt=scoring_system,
                user_message=scorer_msg,
                output_format=ScoreResult,
                max_tokens=SCORE_MAX_TOKENS,
                effort=prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low",
            )
            llm_original = scores.prompt_a_scores if original_first else scores.prompt_b_scores
            llm_optimized = scores.prompt_b_scores if original_first else scores.prompt_a_scores

            heur_original = HeuristicScorer.score_prompt(raw_prompt)
            heur_optimized = HeuristicScorer.score_prompt(
                optimization.optimized_prompt,
                original=raw_prompt,
            )

            # Historical stats for z-score normalization. Prefer the pre-fetched
            # value threaded from run_batch (one DB round-trip per batch, shared
            # across all prompts) and fall back to a per-prompt fetch only for
            # legacy direct callers of run_single_prompt.
            effective_stats = historical_stats
            if effective_stats is None and session_factory is not None:
                try:
                    async with session_factory() as _stats_db:
                        svc = OptimizationService(_stats_db)
                        effective_stats = await svc.get_score_distribution(
                            exclude_scoring_modes=["heuristic"],
                        )
                except Exception as _hs_exc:
                    logger.debug("Historical stats fetch failed: %s", _hs_exc)

            blended_original = blend_scores(
                llm_original, heur_original, effective_stats,
                prompt_text=raw_prompt,
            )
            blended_optimized = blend_scores(
                llm_optimized, heur_optimized, effective_stats,
                prompt_text=optimization.optimized_prompt,
            )

            original_scores = blended_original.to_dimension_scores()
            optimized_scores = blended_optimized.to_dimension_scores()
            deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)
            scoring_mode = "hybrid"
            # Capture divergence flags (matches pipeline.py)
            if blended_optimized.divergence_flags:
                _heuristic_flags = blended_optimized.divergence_flags
                context_flags["divergence_flags"] = True

        # Improvement score — weights from single source of truth
        improvement_score: float | None = None
        if deltas:
            _imp = sum(
                deltas.get(dim, 0) * w
                for dim, w in DIMENSION_WEIGHTS.items()
            )
            improvement_score = round(max(0.0, min(10.0, _imp)), 2)

        # --- Phase 3.5: Suggest (matches pipeline.py Phase 4) ---
        # Generate 3 actionable suggestions for the optimized prompt.
        # Previously skipped for seeds, breaking refinement UX (empty suggestions panel).
        suggestions_list: list | None = None
        if optimized_scores and analysis.weaknesses is not None:
            try:
                import json as _json
                suggest_msg = prompt_loader.render("suggest.md", {
                    "optimized_prompt": optimization.optimized_prompt,
                    "scores": _json.dumps(optimized_scores.model_dump(), indent=2),
                    "weaknesses": ", ".join(analysis.weaknesses) if analysis.weaknesses else "none identified",
                    "strategy_used": effective_strategy,
                    "score_deltas": "batch seed — no previous deltas",
                    "score_trajectory": "first turn",
                })
                suggest_result: SuggestionsOutput = await call_provider_with_retry(
                    provider,
                    model=scorer_model,  # Haiku — cheap and fast
                    system_prompt=system_prompt,
                    user_message=suggest_msg,
                    output_format=SuggestionsOutput,
                    max_tokens=2048,
                    effort="low",
                )
                suggestions_list = suggest_result.suggestions
            except Exception as _sug_exc:
                logger.debug("Suggestion generation failed for prompt %d: %s", prompt_index, _sug_exc)

        # --- Phase 4: Embed ---
        # Raw embedding already computed before Phase 2 for enrichment queries.
        # Only compute optimized + transformation embeddings here.
        opt_embedding: bytes | None = None
        xfm_embedding: bytes | None = None
        try:
            opt_vec = await embedding_service.aembed_single(optimization.optimized_prompt)
            opt_embedding = opt_vec.astype("float32").tobytes()
        except Exception as exc:
            logger.warning("Optimized embedding failed for prompt %d: %s", prompt_index, exc)
        try:
            diff_text = f"{raw_prompt} → {optimization.optimized_prompt}"
            xfm_vec = await embedding_service.aembed_single(diff_text)
            xfm_embedding = xfm_vec.astype("float32").tobytes()
        except Exception as exc:
            logger.warning("Transformation embedding failed for prompt %d: %s", prompt_index, exc)

        duration_ms = int((time.monotonic() - t0) * 1000)
        task_type = (
            analysis.task_type if analysis.task_type in VALID_TASK_TYPES else "general"
        )

        return PendingOptimization(
            id=opt_id,
            trace_id=trace_id,
            batch_id=batch_id,
            raw_prompt=raw_prompt,
            optimized_prompt=optimization.optimized_prompt,
            task_type=task_type,
            strategy_used=effective_strategy,
            changes_summary=optimization.changes_summary,
            score_clarity=optimized_scores.clarity if optimized_scores else None,
            score_specificity=optimized_scores.specificity if optimized_scores else None,
            score_structure=optimized_scores.structure if optimized_scores else None,
            score_faithfulness=optimized_scores.faithfulness if optimized_scores else None,
            score_conciseness=optimized_scores.conciseness if optimized_scores else None,
            overall_score=optimized_scores.overall if optimized_scores else None,
            improvement_score=improvement_score,
            scoring_mode=scoring_mode,
            intent_label=validate_intent_label(
                title_case_label(analysis.intent_label or "general"),
                raw_prompt,
            ),
            domain=effective_domain,
            domain_raw=(analysis.domain or "general"),
            embedding=raw_embedding,
            optimized_embedding=opt_embedding,
            transformation_embedding=xfm_embedding,
            models_by_phase={"analyze": analyzer_model, "optimize": optimizer_model, "score": scorer_model},
            original_scores=original_scores.model_dump() if original_scores else None,
            score_deltas=deltas,
            duration_ms=duration_ms,
            status="completed",
            provider=provider.name,
            model_used=optimizer_model,
            routing_tier=tier,
            heuristic_flags=_heuristic_flags,
            suggestions=suggestions_list,
            repo_full_name=repo_full_name,
            context_sources={
                "source": "batch_seed",
                "batch_id": batch_id,
                "agent": agent_name,
                **enrichment_sources,
                **context_flags,
            },
        )

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            "Batch prompt %d/%d failed: %s", prompt_index + 1, total_prompts, exc
        )
        return PendingOptimization(
            id=opt_id,
            trace_id=trace_id,
            batch_id=batch_id,
            raw_prompt=raw_prompt,
            status="failed",
            error=str(exc)[:500],
            duration_ms=duration_ms,
            routing_tier=tier,
        )


def estimate_batch_cost(
    prompt_count: int,
    agent_count: int,
    tier: str,
) -> float | None:
    """Estimate USD cost for a batch seed run.

    Returns None for sampling tier (IDE subscription covers it).
    """
    if tier == "sampling":
        return None
    if tier == "passthrough":
        return 0.0

    # Agent generation: N agents × 1 Haiku call
    # ~500 input tokens + ~500 output tokens per agent
    haiku_cost_per_call = 0.001 * 0.5 + 0.005 * 0.5  # $1/$5 per 1M tokens
    agent_cost = agent_count * haiku_cost_per_call

    # Per optimization: analyze (Sonnet) + optimize (Opus) + score (Sonnet)
    # Rough estimates: ~2K tokens in + ~2K out per phase
    sonnet_cost = 0.003 * 2 + 0.015 * 2  # $3/$15 per 1M tokens
    opus_cost = 0.005 * 2 + 0.025 * 2    # $5/$25 per 1M tokens
    per_opt = sonnet_cost + opus_cost + sonnet_cost  # analyze + optimize + score

    return round(agent_cost + prompt_count * per_opt, 2)


__all__ = [
    "PendingOptimization",
    "ScoreDistribution",
    "batch_taxonomy_assign",
    "bulk_persist",
    "estimate_batch_cost",
    "run_batch",
    "run_single_prompt",
]
