"""In-memory batch optimization pipeline.

Runs N prompts through analyze → optimize → score → embed in parallel
with zero DB writes. Results accumulate as PendingOptimization objects.
Bulk persist writes everything in a single transaction.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader

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
    session_factory: Any | None = None,
    taxonomy_engine: Any | None = None,
    domain_resolver: Any | None = None,
    tier: str = "internal",
    context_service: Any | None = None,
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

    Returns a PendingOptimization with all fields populated.
    On any phase failure, returns a PendingOptimization with error set
    and status="failed". Never raises — errors are captured in the result.
    """
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    t0 = time.monotonic()

    try:
        from app.config import DATA_DIR
        from app.providers.base import call_provider_with_retry
        from app.schemas.pipeline_contracts import (
            AnalysisResult,
            DimensionScores,
            OptimizationResult,
            ScoreResult,
        )
        from app.services.heuristic_scorer import HeuristicScorer
        from app.services.pipeline_constants import (
            ANALYZE_MAX_TOKENS,
            SCORE_MAX_TOKENS,
            VALID_TASK_TYPES,
            compute_optimize_max_tokens,
            resolve_effective_strategy,
            semantic_upgrade_general,
        )
        from app.services.preferences import PreferencesService
        from app.services.score_blender import blend_scores
        from app.services.strategy_loader import StrategyLoader
        from app.utils.text_cleanup import sanitize_optimization_result, title_case_label, validate_intent_label

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
            effort=prefs.get("pipeline.analyzer_effort", prefs_snapshot) or "low",
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
                async with session_factory() as _enrich_db:
                    enrichment = await context_service.enrich(
                        raw_prompt=raw_prompt,
                        tier=tier,
                        db=_enrich_db,
                        workspace_path=None,
                        repo_full_name=repo_full_name,
                        applied_pattern_ids=None,
                        preferences_snapshot=prefs_snapshot,
                    )
                applied_patterns_text = enrichment.applied_patterns
                adaptation_text = enrichment.strategy_intelligence
                enriched_codebase_context = enrichment.codebase_context
                divergence_alerts_text = enrichment.divergence_alerts
                # Merge enrichment's layer flags + enrichment_meta for persistence
                enrichment_sources = dict(enrichment.context_sources)
                if enrichment.enrichment_meta:
                    enrichment_sources["enrichment_meta"] = dict(enrichment.enrichment_meta)
                if applied_patterns_text:
                    context_flags["cluster_injection"] = True
                if adaptation_text:
                    context_flags["strategy_intelligence"] = True
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
                    from app.services.pattern_injection import (
                        auto_inject_patterns,
                        format_injected_patterns,
                    )
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
                    from app.services.context_enrichment import resolve_strategy_intelligence
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
                from app.services.pattern_injection import (
                    format_few_shot_examples,
                    retrieve_few_shot_examples,
                )
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
                optimization.optimized_prompt, original=raw_prompt,
            )

            # Fetch historical stats for z-score normalization (matches pipeline.py)
            historical_stats = None
            if session_factory is not None:
                try:
                    from app.services.optimization_service import OptimizationService
                    async with session_factory() as _stats_db:
                        svc = OptimizationService(_stats_db)
                        historical_stats = await svc.get_score_distribution(
                            exclude_scoring_modes=["heuristic"],
                        )
                except Exception as _hs_exc:
                    logger.debug("Historical stats fetch failed: %s", _hs_exc)

            blended_original = blend_scores(llm_original, heur_original, historical_stats)
            blended_optimized = blend_scores(llm_optimized, heur_optimized, historical_stats)

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
            from app.schemas.pipeline_contracts import DIMENSION_WEIGHTS
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

                from app.schemas.pipeline_contracts import SuggestionsOutput
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


async def run_batch(
    prompts: list[str],
    provider: LLMProvider,
    prompt_loader: PromptLoader,
    embedding_service: EmbeddingService,
    *,
    max_parallel: int = 10,
    codebase_context: str | None = None,
    repo_full_name: str | None = None,
    batch_id: str | None = None,
    on_progress: Any | None = None,
    session_factory: Any | None = None,
    taxonomy_engine: Any | None = None,
    domain_resolver: Any | None = None,
    tier: str = "internal",
    context_service: Any | None = None,
) -> list[PendingOptimization]:
    """Run N prompts through the pipeline in parallel.

    Args:
        prompts: Raw prompt strings to optimize.
        provider: LLM provider for all phases.
        max_parallel: Concurrency limit (10 internal, 5 API, 2 sampling).
        on_progress: Callback fired after each prompt completes.
        session_factory: Async DB session factory for enrichment queries
            (pattern injection, few-shot retrieval, adaptation state, score stats).
        taxonomy_engine: TaxonomyEngine singleton for pattern injection.
        domain_resolver: DomainResolver singleton for domain resolution.

    Returns:
        List of PendingOptimization results (some may have status="failed").
    """
    batch_id = batch_id or str(uuid.uuid4())
    semaphore = asyncio.Semaphore(max_parallel)
    results: list[PendingOptimization] = [None] * len(prompts)  # type: ignore

    async def _run_with_semaphore(index: int, prompt: str) -> None:
        # Rate limit (429) backoff: reduce semaphore by half on first 429, retry once
        _rate_limited = False

        async def _attempt() -> PendingOptimization:
            nonlocal _rate_limited
            result = await run_single_prompt(
                raw_prompt=prompt,
                provider=provider,
                prompt_loader=prompt_loader,
                embedding_service=embedding_service,
                codebase_context=codebase_context,
                repo_full_name=repo_full_name,
                batch_id=batch_id,
                prompt_index=index,
                total_prompts=len(prompts),
                session_factory=session_factory,
                taxonomy_engine=taxonomy_engine,
                domain_resolver=domain_resolver,
                tier=tier,
                context_service=context_service,
            )
            # Check for rate limit error in result
            if (
                result.status == "failed"
                and result.error
                and ("429" in result.error or "rate_limit" in result.error.lower())
                and not _rate_limited
            ):
                _rate_limited = True
                logger.warning(
                    "Rate limit hit on prompt %d — reducing concurrency and retrying", index
                )
                # Reduce effective parallelism by acquiring an extra slot
                await semaphore.acquire()
                try:
                    await asyncio.sleep(5)
                    retry = await run_single_prompt(
                        raw_prompt=prompt,
                        provider=provider,
                        prompt_loader=prompt_loader,
                        embedding_service=embedding_service,
                        codebase_context=codebase_context,
                        repo_full_name=repo_full_name,
                        batch_id=batch_id,
                        prompt_index=index,
                        total_prompts=len(prompts),
                        session_factory=session_factory,
                        taxonomy_engine=taxonomy_engine,
                        domain_resolver=domain_resolver,
                        tier=tier,
                        context_service=context_service,
                    )
                    return retry
                finally:
                    semaphore.release()
            return result

        async with semaphore:
            result = await _attempt()
            results[index] = result

            # Log per-prompt event
            try:
                from app.services.taxonomy.event_logger import get_event_logger
                decision = "seed_prompt_scored" if result.status == "completed" else "seed_prompt_failed"
                ctx: dict[str, Any] = {
                    "batch_id": batch_id,
                    "prompt_index": index,
                    "total": len(prompts),
                    "overall_score": result.overall_score,
                    "improvement_score": result.improvement_score,
                    "task_type": result.task_type,
                    "strategy_used": result.strategy_used,
                    "intent_label": result.intent_label,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }
                if result.status == "failed":
                    ctx["recovery"] = "skipped"
                get_event_logger().log_decision(
                    path="hot", op="seed", decision=decision,
                    optimization_id=result.trace_id,
                    context=ctx,
                )
            except RuntimeError:
                pass

            # Publish seed_batch_progress to event bus for SSE frontend
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("seed_batch_progress", {
                    "batch_id": batch_id,
                    "phase": "optimize",
                    "completed": sum(1 for r in results if r is not None),
                    "total": len(prompts),
                    "current_prompt": (
                        result.intent_label or result.raw_prompt[:60]
                        if result.status == "completed"
                        else result.raw_prompt[:60]
                    ),
                    "failed": sum(
                        1 for r in results if r is not None and r.status == "failed"
                    ),
                })
            except Exception as _bus_exc:
                logger.debug("seed_batch_progress publish failed: %s", _bus_exc)

            if on_progress:
                on_progress(index, len(prompts), result)

    await asyncio.gather(
        *[_run_with_semaphore(i, p) for i, p in enumerate(prompts)],
        return_exceptions=True,
    )

    # Stamp project_id on all completed results (resolve once, not per-prompt)
    from app.services.project_service import resolve_repo_project
    _, _batch_project_id = await resolve_repo_project(repo_full_name)
    if _batch_project_id:
        for r in results:
            if r is not None and r.status == "completed":
                r.project_id = _batch_project_id

    return [r for r in results if r is not None]


async def bulk_persist(
    results: list[PendingOptimization],
    session_factory: Any,
    batch_id: str,
) -> int:
    """Persist all completed optimizations in a single transaction.

    Returns count of rows inserted. Skips failed optimizations.
    Idempotent: skips prompts already persisted for this batch_id.
    Includes retry logic — one retry after 5s on transient failures.
    """
    t0 = time.monotonic()
    # Quality gate: filter out low-quality seeds before persisting.
    # Seeds with overall_score < 5.0 or improvement_score <= 0.0 add noise
    # to the taxonomy and few-shot pool without providing value.
    seed_min_score = 5.0
    completed_raw = [r for r in results if r.status == "completed"]
    completed = []
    quality_rejected = 0
    for r in completed_raw:
        if r.overall_score is not None and r.overall_score < seed_min_score:
            quality_rejected += 1
            logger.info(
                "Seed quality gate: rejected %s (score=%.2f, improvement=%.2f)",
                r.id[:8], r.overall_score, r.improvement_score or 0.0,
            )
            continue
        completed.append(r)
    if quality_rejected:
        logger.info("Seed quality gate: %d/%d rejected (min_score=%.1f)",
                     quality_rejected, len(completed_raw), seed_min_score)

    if not completed:
        return 0

    inserted = 0
    inserted_pendings: list[PendingOptimization] = []
    for attempt in range(2):
        try:
            async with session_factory() as db:
                from sqlalchemy import select as sa_select

                from app.models import Optimization

                # Idempotency check: find already-persisted IDs for this batch
                existing_ids_result = await db.execute(
                    sa_select(Optimization.id).where(
                        Optimization.context_sources.op("->>")(
                            "batch_id"
                        ) == batch_id
                    )
                )
                existing_ids: set[str] = {row[0] for row in existing_ids_result}
                inserted = 0  # Reset for retry
                inserted_pendings = []  # Reset for retry
                for pending in completed:
                    if pending.id in existing_ids:
                        logger.debug(
                            "Skipping already-persisted optimization %s (batch_id=%s)",
                            pending.id[:8], batch_id,
                        )
                        continue

                    db_opt = Optimization(
                        id=pending.id,
                        trace_id=pending.trace_id,
                        raw_prompt=pending.raw_prompt,
                        optimized_prompt=pending.optimized_prompt,
                        task_type=pending.task_type,
                        strategy_used=pending.strategy_used,
                        changes_summary=pending.changes_summary,
                        score_clarity=pending.score_clarity,
                        score_specificity=pending.score_specificity,
                        score_structure=pending.score_structure,
                        score_faithfulness=pending.score_faithfulness,
                        score_conciseness=pending.score_conciseness,
                        overall_score=pending.overall_score,
                        improvement_score=pending.improvement_score,
                        scoring_mode=pending.scoring_mode,
                        intent_label=pending.intent_label,
                        domain=pending.domain,
                        domain_raw=pending.domain_raw,
                        embedding=pending.embedding,
                        optimized_embedding=pending.optimized_embedding,
                        transformation_embedding=pending.transformation_embedding,
                        models_by_phase=pending.models_by_phase,
                        original_scores=pending.original_scores,
                        score_deltas=pending.score_deltas,
                        duration_ms=pending.duration_ms,
                        status=pending.status,
                        provider=pending.provider,
                        model_used=pending.model_used,
                        routing_tier=pending.routing_tier,
                        heuristic_flags=pending.heuristic_flags,
                        suggestions=pending.suggestions,
                        repo_full_name=pending.repo_full_name,
                        project_id=pending.project_id,
                        context_sources=pending.context_sources,
                    )
                    db.add(db_opt)
                    inserted += 1
                    inserted_pendings.append(pending)

                await db.commit()
            break  # success
        except Exception as exc:
            if attempt == 0:
                logger.warning("Bulk persist failed, retrying in 5s: %s", exc)
                await asyncio.sleep(5)
            else:
                raise

    # Per-prompt event emission — parallels the regular pipeline contract so
    # frontend history refresh and cross-process MCP bridge fire reliably.
    # `source="batch_seed"` lets consumers distinguish seed-originated rows
    # from text-editor optimizations while batch-level `seed_*` events still
    # stream the coarser batch progress view.
    if inserted_pendings:
        try:
            from app.services.event_bus import event_bus
            for pending in inserted_pendings:
                event_bus.publish("optimization_created", {
                    "id": pending.id,
                    "trace_id": pending.trace_id,
                    "task_type": pending.task_type,
                    "intent_label": pending.intent_label or "general",
                    "domain": pending.domain,
                    "domain_raw": pending.domain_raw,
                    "strategy_used": pending.strategy_used,
                    "overall_score": pending.overall_score,
                    "provider": pending.provider,
                    "status": pending.status,
                    "routing_tier": pending.routing_tier,
                    "source": "batch_seed",
                    "batch_id": batch_id,
                })
        except Exception:
            logger.debug("Event bus publish failed", exc_info=True)

    duration_ms = int((time.monotonic() - t0) * 1000)

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_persist_complete",
            context={
                "batch_id": batch_id,
                "rows_inserted": inserted,
                "rows_skipped_idempotent": len(completed) - inserted,
                "transaction_ms": duration_ms,
            },
        )
    except RuntimeError:
        pass

    logger.info("Bulk persist: %d rows in %dms", inserted, duration_ms)
    return inserted


async def batch_taxonomy_assign(
    results: list[PendingOptimization],
    session_factory: Any,
    batch_id: str,
) -> dict[str, Any]:
    """Assign clusters for all persisted optimizations in one transaction.

    Pattern extraction is deferred (pattern_stale=True) — the warm path
    handles it after the batch completes.

    Returns summary dict with clusters_assigned, clusters_created, domains_touched.
    """
    t0 = time.monotonic()
    completed = [r for r in results if r.status == "completed" and r.embedding]
    clusters_created = 0
    domains_touched: set[str] = set()

    if not completed:
        return {"clusters_assigned": 0, "clusters_created": 0, "domains_touched": []}

    from sqlalchemy import select as sa_select

    from app.models import Optimization, OptimizationPattern
    from app.services.taxonomy import get_engine
    from app.services.taxonomy.cluster_meta import write_meta
    from app.services.taxonomy.family_ops import assign_cluster

    engine = get_engine()
    assigned = 0

    async with session_factory() as db:
        for pending in completed:
            try:
                embedding = np.frombuffer(pending.embedding, dtype=np.float32)  # type: ignore[arg-type]
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=pending.intent_label or "general",
                    domain=pending.domain or "general",
                    task_type=pending.task_type or "general",
                    overall_score=pending.overall_score,
                    embedding_index=engine._embedding_index,
                )

                # Write cluster_id back to the Optimization row (matches engine.py hot path)
                opt_row = await db.execute(
                    sa_select(Optimization).where(Optimization.id == pending.id)
                )
                opt = opt_row.scalar_one_or_none()
                if opt is not None:
                    opt.cluster_id = cluster.id

                # Create OptimizationPattern join record so downstream consumers
                # (history, detail view, lifecycle, pattern injection) can find this
                # optimization's cluster. Matches engine.py hot path step 5.
                db.add(OptimizationPattern(
                    optimization_id=pending.id,
                    cluster_id=cluster.id,
                    relationship="source",
                ))

                # Track what was created
                if cluster.member_count == 1:
                    clusters_created += 1
                domains_touched.add(pending.domain or "general")
                assigned += 1

                # Defer pattern extraction to warm path
                cluster.cluster_metadata = write_meta(
                    cluster.cluster_metadata, pattern_stale=True,
                )

            except Exception as exc:
                logger.warning(
                    "Taxonomy assign failed for %s: %s",
                    pending.id[:8], exc,
                )

        await db.commit()

    duration_ms = int((time.monotonic() - t0) * 1000)
    domains_list = sorted(domains_touched)

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_taxonomy_complete",
            context={
                "batch_id": batch_id,
                "clusters_assigned": assigned,
                "clusters_created": clusters_created,
                "domains_touched": domains_list,
                "transaction_ms": duration_ms,
            },
        )
    except RuntimeError:
        pass

    # Trigger warm path (single event — debounce handles the rest)
    try:
        from app.services.event_bus import event_bus
        event_bus.publish("taxonomy_changed", {
            "trigger": "batch_seed",
            "batch_id": batch_id,
            "clusters_created": clusters_created,
        })
    except Exception as _bus_exc:
        logger.warning("taxonomy_changed publish failed after batch seed: %s", _bus_exc)

    logger.info(
        "Taxonomy assign: %d clusters (%d new), domains=%s (%dms)",
        assigned, clusters_created, domains_list, duration_ms,
    )

    return {
        "clusters_assigned": assigned,
        "clusters_created": clusters_created,
        "domains_touched": domains_list,
    }


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
