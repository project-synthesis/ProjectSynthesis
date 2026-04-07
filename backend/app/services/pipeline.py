"""Pipeline orchestrator — async generator yielding SSE PipelineEvents.

Each LLM phase (analyze, optimize, score) is an independent provider call,
not an Agent SDK agent. The orchestrator coordinates them sequentially and
streams status events for the frontend SSE endpoint.
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.models import Optimization
from app.providers.base import LLMProvider, TokenUsage, call_provider_with_retry
from app.schemas.pipeline_contracts import (
    DIMENSION_WEIGHTS,
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    PipelineEvent,
    PipelineResult,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.heuristic_scorer import HeuristicScorer
from app.services.pattern_injection import (
    InjectedPattern,
    auto_inject_patterns,
    format_injected_patterns,
)
from app.services.pipeline_constants import (
    ANALYZE_MAX_TOKENS,
    MAX_DOMAIN_RAW_LENGTH,
    MAX_INTENT_LABEL_LENGTH,
    SCORE_MAX_TOKENS,
    VALID_TASK_TYPES,
    compute_optimize_max_tokens,
    resolve_effective_strategy,
    semantic_check,
    semantic_upgrade_general,
)
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader
from app.services.trace_logger import TraceLogger
from app.utils.text_cleanup import title_case_label, validate_intent_label

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Runs the analyze -> optimize -> score pipeline as an async generator."""

    def __init__(self, prompts_dir: Path) -> None:
        self.prompt_loader = PromptLoader(prompts_dir)
        self.strategy_loader = StrategyLoader(prompts_dir / "strategies")
        self._system_prompt: str | None = None

        # Trace logger — optional; skip if directory cannot be created
        try:
            self.trace_logger: TraceLogger | None = TraceLogger(DATA_DIR / "traces")
        except OSError:
            logger.warning("Could not create traces directory; trace logging disabled")
            self.trace_logger = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = self.prompt_loader.load("agent-guidance.md")
        return self._system_prompt

    @staticmethod
    async def _call_provider(
        provider: LLMProvider,
        *,
        system_prompt: str,
        user_message: str,
        output_format: type,
        model: str,
        effort: str | None = None,
        max_tokens: int = 16384,
        streaming: bool = False,
        cache_ttl: str | None = None,
    ) -> Any:
        """Call provider with smart retry logic.

        Delegates to the shared ``call_provider_with_retry`` utility
        in ``providers.base`` which handles retryable vs non-retryable
        error classification and exponential backoff.

        When ``streaming=True``, dispatches to ``complete_parsed_streaming()``
        to prevent HTTP timeouts on long outputs (e.g. Opus 128K).
        """
        return await call_provider_with_retry(
            provider,
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            output_format=output_format,
            max_tokens=max_tokens,
            effort=effort,
            streaming=streaming,
            cache_ttl=cache_ttl,
        )

    @staticmethod
    def _get_usage(provider: LLMProvider) -> TokenUsage:
        """Return last token usage from provider, or zeros if unavailable."""
        usage = getattr(provider, "last_usage", None)
        if isinstance(usage, TokenUsage):
            return usage
        return TokenUsage()

    # _semantic_check delegated to pipeline_constants.semantic_check()

    # ------------------------------------------------------------------
    # Auto-injection helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _auto_inject_patterns(
        raw_prompt: str,
        taxonomy_engine: Any,
        db: AsyncSession,
        trace_id: str,
        optimization_id: str | None = None,
    ) -> tuple[list[InjectedPattern], list[str]]:
        """Auto-inject cluster meta-patterns based on prompt embedding similarity.

        Delegates to the shared ``pattern_injection.auto_inject_patterns()``
        helper so the same logic is reused by the sampling pipeline.
        """
        return await auto_inject_patterns(
            raw_prompt, taxonomy_engine, db, trace_id,
            optimization_id=optimization_id,
        )

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    async def run(
        self,
        raw_prompt: str,
        provider: LLMProvider,
        db: AsyncSession,
        *,
        strategy_override: str | None = None,
        codebase_guidance: str | None = None,
        codebase_context: str | None = None,
        adaptation_state: str | None = None,
        context_sources: dict[str, bool] | None = None,
        repo_full_name: str | None = None,
        github_token: str | None = None,
        applied_pattern_ids: list[str] | None = None,
        taxonomy_engine: Any | None = None,
        domain_resolver: Any | None = None,
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Execute the full pipeline, yielding SSE events."""
        trace_id = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        start_time = time.monotonic()

        prefs = PreferencesService(DATA_DIR)
        prefs_snapshot = prefs.load()
        optimizer_model = prefs.resolve_model("optimizer", prefs_snapshot)
        analyzer_model = prefs.resolve_model("analyzer", prefs_snapshot)
        scorer_model = prefs.resolve_model("scorer", prefs_snapshot)
        model_ids: dict[str, str] = {
            "analyze": analyzer_model,
            "optimize": optimizer_model,
            "score": scorer_model,
        }

        yield PipelineEvent(event="optimization_start", data={"trace_id": trace_id})

        phase_durations: dict[str, int] = {}

        try:
            # ---------------------------------------------------------------
            # Phase 0: Explore (optional — codebase context injection)
            # ---------------------------------------------------------------
            explore_enabled = prefs.get("pipeline.enable_explore", prefs_snapshot)
            if explore_enabled and repo_full_name and github_token and codebase_context is None:
                yield PipelineEvent(event="status", data={"stage": "explore", "state": "running"})
                try:
                    from app.services.codebase_explorer import CodebaseExplorer
                    from app.services.embedding_service import EmbeddingService
                    from app.services.github_client import GitHubClient

                    phase_start = time.monotonic()
                    explorer = CodebaseExplorer(
                        prompt_loader=self.prompt_loader,
                        github_client=GitHubClient(),
                        embedding_service=EmbeddingService(),
                        provider=provider,
                    )
                    branch = "main"
                    codebase_context = await explorer.explore(
                        raw_prompt=raw_prompt,
                        repo_full_name=repo_full_name,
                        branch=branch,
                        token=github_token,
                    )

                    explore_duration = int((time.monotonic() - phase_start) * 1000)
                    phase_durations["explore_ms"] = explore_duration

                    if codebase_context:
                        logger.info(
                            "Explore context injected (%d chars) trace_id=%s",
                            len(codebase_context), trace_id,
                        )
                    yield PipelineEvent(event="status", data={"stage": "explore", "state": "complete"})
                except Exception as exc:
                    logger.warning(
                        "Explore failed, proceeding without codebase context: %s", exc,
                    )
                    yield PipelineEvent(event="status", data={"stage": "explore", "state": "skipped"})

            # ---------------------------------------------------------------
            # Phase 1: Analyze
            # ---------------------------------------------------------------
            yield PipelineEvent(event="status", data={"stage": "analyze", "state": "running"})

            system_prompt = self._load_system_prompt()

            # Resolve blocked strategies (low approval rate) to filter from analyzer input
            blocked_strategies: set[str] = set()
            adaptation_enabled = prefs.get("pipeline.enable_adaptation", prefs_snapshot)
            if adaptation_enabled and not strategy_override:
                try:
                    from app.services.adaptation_tracker import AdaptationTracker
                    _tracker = AdaptationTracker(db)
                    # Pre-scan: we don't know the task_type yet (analyzer determines it),
                    # so collect blocked strategies across ALL task types to prevent
                    # universally-disliked strategies from being presented.
                    from sqlalchemy import select as sa_select

                    from app.models import StrategyAffinity
                    _result = await db.execute(sa_select(StrategyAffinity))
                    _all_rows = _result.scalars().all()
                    _by_strategy: dict[str, list[float]] = {}
                    _by_strategy_total: dict[str, int] = {}
                    for _row in _all_rows:
                        _total = (_row.thumbs_up or 0) + (_row.thumbs_down or 0)
                        if _total >= AdaptationTracker._MIN_FEEDBACK_FOR_GATE:
                            _by_strategy.setdefault(_row.strategy, []).append(_row.approval_rate)
                            _by_strategy_total[_row.strategy] = (
                                _by_strategy_total.get(_row.strategy, 0) + _total
                            )
                    for _strat, _rates in _by_strategy.items():
                        _avg = sum(_rates) / len(_rates)
                        if _avg < AdaptationTracker._BLOCK_THRESHOLD:
                            blocked_strategies.add(_strat)
                            logger.info(
                                "Strategy '%s' blocked pre-analysis: avg_approval=%.2f across %d task types",
                                _strat, _avg, len(_rates),
                            )
                except Exception as exc:
                    logger.debug("Adaptation pre-filter unavailable: %s", exc)

            available_strategies = self.strategy_loader.format_available(
                blocked=blocked_strategies,
            )

            known_domains = (
                ", ".join(sorted(domain_resolver.domain_labels))
                if domain_resolver is not None and domain_resolver.domain_labels
                else "backend, frontend, database, data, devops, security, fullstack, general"
            )
            analyze_msg = self.prompt_loader.render("analyze.md", {
                "raw_prompt": raw_prompt,
                "available_strategies": available_strategies,
                "known_domains": known_domains,
            })

            phase_start = time.monotonic()
            analysis: AnalysisResult = await self._call_provider(
                provider,
                system_prompt=system_prompt,
                user_message=analyze_msg,
                output_format=AnalysisResult,
                model=analyzer_model,
                effort=prefs.get("pipeline.analyzer_effort", prefs_snapshot) or "low",
                max_tokens=ANALYZE_MAX_TOKENS,
            )

            # Capture actual model ID from provider response
            if isinstance(provider.last_model, str):
                model_ids["analyze"] = provider.last_model
            yield PipelineEvent(
                event="status",
                data={"stage": "analyze", "state": "complete", "model": model_ids["analyze"]},
            )

            analyze_duration = int((time.monotonic() - phase_start) * 1000)
            phase_durations["analyze_ms"] = analyze_duration

            usage = self._get_usage(provider)
            if self.trace_logger:
                self.trace_logger.log_phase(
                    trace_id=trace_id, phase="analyze",
                    duration_ms=analyze_duration,
                    tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
                    model=analyzer_model, provider=provider.name,
                    result={
                        "task_type": analysis.task_type,
                        "strategy": analysis.selected_strategy,
                        "effort": prefs.get("pipeline.analyzer_effort", prefs_snapshot) or "low",
                    },
                )

            # Semantic check + domain confidence gate (shared with sampling pipeline)
            confidence = semantic_check(analysis.task_type, raw_prompt, analysis.confidence)

            # Upgrade "general" to a specific type when strong keywords are present
            effective_task_type = semantic_upgrade_general(analysis.task_type, raw_prompt)
            if effective_task_type != analysis.task_type:
                analysis.task_type = effective_task_type

            logger.info(
                "Domain resolution: raw='%s' confidence=%.2f (analyzer=%.2f) trace_id=%s",
                analysis.domain, confidence, analysis.confidence, trace_id,
            )

            # Resolve domain via domain nodes (replaces hardcoded VALID_DOMAINS whitelist)
            if domain_resolver is not None:
                effective_domain = await domain_resolver.resolve(
                    analysis.domain or "general", confidence, raw_prompt=raw_prompt,
                )
                logger.info(
                    "Domain resolved: '%s' → '%s' trace_id=%s",
                    analysis.domain, effective_domain, trace_id,
                )
            else:
                # Startup race or no domain_resolver — default to "general"
                effective_domain = "general"

            # ---------------------------------------------------------------
            # Phase 1.5: Domain Mapping (Spec Section 4.2)
            # ---------------------------------------------------------------
            domain_raw = (analysis.domain or "general")[:MAX_DOMAIN_RAW_LENGTH]  # pre-gate, truncated
            cluster_id = None
            taxonomy_label = None
            taxonomy_breadcrumb: list[str] = []

            try:
                from app.services.taxonomy import TaxonomyMapping

                if taxonomy_engine is not None:
                    mapping: TaxonomyMapping = await taxonomy_engine.map_domain(
                        domain_raw=domain_raw,
                        db=db,
                        applied_pattern_ids=applied_pattern_ids,
                    )
                    cluster_id = mapping.cluster_id
                    taxonomy_label = mapping.taxonomy_label
                    taxonomy_breadcrumb = mapping.taxonomy_breadcrumb

                    if cluster_id:
                        logger.info(
                            "Domain mapped: '%s' -> node '%s' (%s) trace_id=%s",
                            domain_raw, taxonomy_label,
                            " > ".join(taxonomy_breadcrumb), trace_id,
                        )
                    else:
                        logger.info(
                            "Domain unmapped: '%s' (below alignment floor) trace_id=%s",
                            domain_raw, trace_id,
                        )
                else:
                    logger.debug("Taxonomy engine not available — skipping domain mapping")
            except Exception as exc:
                logger.warning(
                    "Domain mapping failed (non-fatal): %s trace_id=%s",
                    exc, trace_id,
                )

            # Pre-compute prompt embedding once for all downstream consumers
            # (strategy recommendation, pattern injection, few-shot retrieval)
            _prompt_embedding = None
            try:
                from app.services.embedding_service import EmbeddingService as _EmbSvc

                _prompt_embedding = await _EmbSvc().aembed_single(raw_prompt)
            except Exception:
                pass  # consumers will embed independently as fallback

            # Score-informed strategy recommendation from historical data
            data_recommendation = None
            try:
                from app.services.pipeline_constants import recommend_strategy_from_history

                data_recommendation = await recommend_strategy_from_history(
                    raw_prompt=raw_prompt,
                    db=db,
                    available_strategies=self.strategy_loader.list_strategies(),
                    trace_id=trace_id,
                    prompt_embedding=_prompt_embedding,
                )
            except Exception:
                logger.debug("Strategy recommendation unavailable. trace_id=%s", trace_id)

            # Strategy resolution chain (shared with sampling pipeline)
            effective_strategy = resolve_effective_strategy(
                selected_strategy=analysis.selected_strategy,
                available=self.strategy_loader.list_strategies(),
                blocked_strategies=blocked_strategies,
                confidence=confidence,
                strategy_override=strategy_override,
                trace_id=trace_id,
                data_recommendation=data_recommendation,
                task_type=analysis.task_type,
            )

            # ---------------------------------------------------------------
            # Pre-Phase: Auto-inject cluster meta-patterns
            # ---------------------------------------------------------------
            auto_injected_patterns: list[InjectedPattern] = []
            auto_injected_cluster_ids: list[str] = []
            if taxonomy_engine is not None and not applied_pattern_ids:
                try:
                    auto_injected_patterns, auto_injected_cluster_ids = (
                        await self._auto_inject_patterns(
                            raw_prompt=raw_prompt,
                            taxonomy_engine=taxonomy_engine,
                            db=db,
                            trace_id=trace_id,
                            optimization_id=opt_id,
                        )
                    )
                    if auto_injected_patterns:
                        yield PipelineEvent(
                            event="context_injected",
                            data={
                                "clusters": auto_injected_cluster_ids,
                                "patterns": len(auto_injected_patterns),
                            },
                        )
                        if context_sources is None:
                            context_sources = {}
                        context_sources["cluster_injection"] = True
                except Exception as exc:
                    logger.warning("Auto-injection failed: %s", exc)

            # ---------------------------------------------------------------
            # Phase 2: Optimize
            # ---------------------------------------------------------------
            yield PipelineEvent(event="status", data={"stage": "optimize", "state": "running"})

            adaptation_enabled = prefs.get("pipeline.enable_adaptation", prefs_snapshot)
            if not adaptation_enabled:
                adaptation_state = None
            elif adaptation_state is None:
                # Resolve on-demand when not pre-provided (parity with sampling pipeline)
                try:
                    from app.services.adaptation_tracker import AdaptationTracker
                    tracker = AdaptationTracker(db)
                    adaptation_state = await tracker.render_adaptation_state(
                        analysis.task_type,
                    )
                except Exception as exc:
                    logger.debug("Adaptation state unavailable: %s", exc)

            strategy_instructions = self.strategy_loader.load(effective_strategy)
            analysis_summary = (
                f"Task type: {analysis.task_type}\n"
                f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
                f"Strengths: {', '.join(analysis.strengths)}\n"
                f"Strategy: {effective_strategy}\n"
                f"Rationale: {analysis.strategy_rationale}"
            )

            # Resolve applied meta-patterns from knowledge graph
            applied_patterns_text: str | None = None
            if applied_pattern_ids:
                try:
                    from app.models import MetaPattern

                    result = await db.execute(
                        select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
                    )
                    patterns = result.scalars().all()
                    if patterns:
                        lines = [
                            f"- {p.pattern_text}" for p in patterns
                        ]
                        applied_patterns_text = (
                            "The following proven patterns from past optimizations "
                            "should be applied where relevant:\n"
                            + "\n".join(lines)
                        )

                        logger.info(
                            "Injecting %d applied patterns into optimizer context. trace_id=%s",
                            len(patterns), trace_id,
                        )
                except Exception as exc:
                    logger.warning("Failed to resolve applied patterns: %s", exc)

            # Combine explicit applied_patterns_text with auto-injected patterns
            applied_patterns_text = format_injected_patterns(
                auto_injected_patterns, applied_patterns_text,
            )

            # Few-shot example retrieval (show, don't tell)
            few_shot_text: str | None = None
            try:
                from app.services.pattern_injection import (
                    format_few_shot_examples,
                    retrieve_few_shot_examples,
                )

                few_shot_examples = await retrieve_few_shot_examples(
                    raw_prompt=raw_prompt, db=db, trace_id=trace_id,
                    prompt_embedding=_prompt_embedding,
                )
                few_shot_text = format_few_shot_examples(few_shot_examples)
                if few_shot_text:
                    if context_sources is None:
                        context_sources = {}
                    context_sources["few_shot_examples"] = True
            except Exception:
                logger.debug("Few-shot retrieval failed. trace_id=%s", trace_id)

            optimize_msg = self.prompt_loader.render("optimize.md", {
                "raw_prompt": raw_prompt,
                "analysis_summary": analysis_summary,
                "strategy_instructions": strategy_instructions,
                "codebase_guidance": codebase_guidance,
                "codebase_context": codebase_context,
                "adaptation_state": adaptation_state,
                "applied_patterns": applied_patterns_text,
                "few_shot_examples": few_shot_text,
            })

            dynamic_max_tokens = compute_optimize_max_tokens(len(raw_prompt))

            phase_start = time.monotonic()
            optimization: OptimizationResult = await self._call_provider(
                provider,
                system_prompt=system_prompt,
                user_message=optimize_msg,
                output_format=OptimizationResult,
                model=optimizer_model,
                effort=prefs.get("pipeline.optimizer_effort", prefs_snapshot) or "high",
                max_tokens=dynamic_max_tokens,
                streaming=True,
            )

            # Post-cleanup: strip leaked ## Changes / ## Applied Patterns
            # from optimized_prompt — LLMs frequently embed change narratives
            # in the prompt field despite structured output schema separation.
            from app.utils.text_cleanup import sanitize_optimization_result

            _clean_prompt, _clean_changes = sanitize_optimization_result(
                optimization.optimized_prompt, optimization.changes_summary,
            )
            optimization = OptimizationResult(
                optimized_prompt=_clean_prompt,
                changes_summary=_clean_changes,
                strategy_used=optimization.strategy_used,
            )

            # Capture actual model ID from provider response
            if isinstance(provider.last_model, str):
                model_ids["optimize"] = provider.last_model
            yield PipelineEvent(
                event="status",
                data={"stage": "optimize", "state": "complete", "model": model_ids["optimize"]},
            )

            optimize_duration = int((time.monotonic() - phase_start) * 1000)
            phase_durations["optimize_ms"] = optimize_duration

            usage = self._get_usage(provider)
            if self.trace_logger:
                self.trace_logger.log_phase(
                    trace_id=trace_id, phase="optimize",
                    duration_ms=optimize_duration,
                    tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
                    model=optimizer_model, provider=provider.name,
                    result={
                        "strategy_used": effective_strategy,
                        "effort": prefs.get("pipeline.optimizer_effort", prefs_snapshot) or "high",
                    },
                )

            yield PipelineEvent(event="prompt_preview", data={
                "prompt": optimization.optimized_prompt,
                "changes": [optimization.changes_summary],
            })

            # ---------------------------------------------------------------
            # Phase 3: Score
            # ---------------------------------------------------------------
            _divergence_flags: list[str] = []
            if prefs.get("pipeline.enable_scoring", prefs_snapshot):
                yield PipelineEvent(event="status", data={"stage": "score", "state": "running"})

                # Randomize A/B assignment
                original_first = random.choice([True, False])
                if original_first:
                    prompt_a = raw_prompt
                    prompt_b = optimization.optimized_prompt
                    presentation_order = "original_first"
                else:
                    prompt_a = optimization.optimized_prompt
                    prompt_b = raw_prompt
                    presentation_order = "optimized_first"

                logger.info(
                    "Scorer presentation_order=%s trace_id=%s",
                    presentation_order, trace_id,
                )

                scoring_system = self.prompt_loader.load("scoring.md")
                # Use XML tags to prevent prompt content (which may contain ##
                # headers) from corrupting the A/B boundary for the scorer.
                scorer_msg = (
                    f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
                    f"<prompt-b>\n{prompt_b}\n</prompt-b>"
                )

                phase_start = time.monotonic()
                scores: ScoreResult = await self._call_provider(
                    provider,
                    system_prompt=scoring_system,
                    user_message=scorer_msg,
                    output_format=ScoreResult,
                    model=scorer_model,
                    effort=prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low",
                    max_tokens=SCORE_MAX_TOKENS,
                    cache_ttl="1h",
                )

                # Capture actual model ID from provider response
                if isinstance(provider.last_model, str):
                    model_ids["score"] = provider.last_model
                yield PipelineEvent(
                    event="status",
                    data={"stage": "score", "state": "complete", "model": model_ids["score"]},
                )

                score_duration = int((time.monotonic() - phase_start) * 1000)
                phase_durations["score_ms"] = score_duration

                usage = self._get_usage(provider)
                if self.trace_logger:
                    self.trace_logger.log_phase(
                        trace_id=trace_id, phase="score",
                        duration_ms=score_duration,
                        tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
                        model=scorer_model, provider=provider.name,
                        result={"effort": prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low"},
                    )

                # Map A/B scores back to original/optimized
                if original_first:
                    llm_original_scores = scores.prompt_a_scores
                    llm_optimized_scores = scores.prompt_b_scores
                else:
                    llm_original_scores = scores.prompt_b_scores
                    llm_optimized_scores = scores.prompt_a_scores

                # ---------------------------------------------------------------
                # Hybrid scoring: blend LLM + heuristic scores
                # ---------------------------------------------------------------
                heur_original = HeuristicScorer.score_prompt(raw_prompt)
                heur_optimized = HeuristicScorer.score_prompt(
                    optimization.optimized_prompt, original=raw_prompt,
                )

                # Fetch historical stats for z-score normalization (non-fatal)
                historical_stats: dict | None = None
                try:
                    from app.services.optimization_service import OptimizationService
                    opt_svc = OptimizationService(db)
                    historical_stats = await opt_svc.get_score_distribution(
                        exclude_scoring_modes=["heuristic"],
                    )
                except Exception as exc:
                    logger.debug("Historical stats unavailable for normalization: %s", exc)

                blended_original = blend_scores(
                    llm_original_scores, heur_original, historical_stats,
                )
                blended_optimized = blend_scores(
                    llm_optimized_scores, heur_optimized, historical_stats,
                )

                original_scores = blended_original.to_dimension_scores()
                optimized_scores = blended_optimized.to_dimension_scores()

                logger.info(
                    "Hybrid scoring complete: llm_opt=%.1f heur_opt=%s blended_opt=%.1f "
                    "divergence=%s normalized=%s trace_id=%s",
                    llm_optimized_scores.overall,
                    {k: round(v, 1) for k, v in heur_optimized.items()},
                    optimized_scores.overall,
                    blended_optimized.divergence_flags,
                    blended_optimized.normalization_applied,
                    trace_id,
                )

                deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)

                # Log scoring trace for observability
                try:
                    from app.services.taxonomy.event_logger import get_event_logger
                    get_event_logger().log_decision(
                        path="hot", op="score", decision="scored",
                        optimization_id=trace_id,
                        context={
                            "scoring_mode": "hybrid",
                            "overall": optimized_scores.overall,
                            "intent_label": analysis.intent_label,
                            "blended": blended_optimized.as_dict(),
                            "raw_llm": blended_optimized.raw_llm,
                            "raw_heuristic": blended_optimized.raw_heuristic,
                            "deltas": deltas,
                            "divergence": blended_optimized.divergence_flags,
                            "normalization": blended_optimized.normalization_applied,
                            "strategy": effective_strategy,
                            "task_type": analysis.task_type,
                        },
                    )
                except RuntimeError:
                    pass

                if optimized_scores.faithfulness < 6.0:
                    logger.warning(
                        "Low faithfulness score (%.1f) — optimization may have altered intent. trace_id=%s",
                        optimized_scores.faithfulness, trace_id,
                    )

                yield PipelineEvent(event="score_card", data={
                    "original_scores": original_scores.model_dump(),
                    "scores": optimized_scores.model_dump(),
                    "deltas": deltas,
                    "overall_score": optimized_scores.overall,
                })

                # ---------------------------------------------------------------
                # Intent drift gate
                # ---------------------------------------------------------------
                warnings: list[str] = []
                _divergence_flags = blended_optimized.divergence_flags or []
                if _divergence_flags:
                    warnings.append(
                        "Score divergence between LLM and heuristic on: "
                        + ", ".join(_divergence_flags)
                    )

                try:
                    import numpy as np

                    from app.services.embedding_service import EmbeddingService

                    drift_svc = EmbeddingService()
                    orig_vec = await drift_svc.aembed_single(raw_prompt)
                    opt_vec = await drift_svc.aembed_single(optimization.optimized_prompt)
                    similarity = float(
                        np.dot(orig_vec, opt_vec)
                        / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9)
                    )

                    if similarity < 0.5:
                        warnings.append(
                            f"Intent drift detected: semantic similarity {similarity:.2f} "
                            f"between original and optimized prompt is below threshold (0.50)"
                        )
                        logger.warning(
                            "Intent drift detected: similarity=%.2f trace_id=%s",
                            similarity, trace_id,
                        )
                except (ImportError, RuntimeError, ValueError, MemoryError) as exc:
                    logger.debug("Intent drift check skipped: %s", exc)
            else:
                # Scoring disabled — skip Phase 3 entirely
                original_scores = None
                optimized_scores = None
                deltas = None
                warnings = []
                logger.info("Scoring phase skipped per user preferences. trace_id=%s", trace_id)

            # ---------------------------------------------------------------
            # Phase 4: Suggest (when scoring produced results)
            # ---------------------------------------------------------------
            suggestions: list[dict[str, str]] = []
            if optimized_scores and analysis.weaknesses is not None:
                try:
                    yield PipelineEvent(event="status", data={"stage": "suggest", "state": "running"})

                    suggest_msg = self.prompt_loader.render("suggest.md", {
                        "optimized_prompt": optimization.optimized_prompt,
                        "scores": json.dumps(optimized_scores.model_dump(), indent=2),
                        "weaknesses": ", ".join(analysis.weaknesses) if analysis.weaknesses else "none identified",
                        "strategy_used": effective_strategy,
                    })

                    suggest_result: SuggestionsOutput = await self._call_provider(
                        provider,
                        system_prompt=self._load_system_prompt(),
                        user_message=suggest_msg,
                        output_format=SuggestionsOutput,
                        model=settings.MODEL_HAIKU,
                        max_tokens=2048,
                    )
                    suggestions = suggest_result.suggestions

                    yield PipelineEvent(event="suggestions", data={"suggestions": suggestions})
                    yield PipelineEvent(event="status", data={"stage": "suggest", "state": "complete"})

                    logger.info("Suggestions generated: %d items. trace_id=%s", len(suggestions), trace_id)
                except Exception as exc:
                    logger.warning("Suggestion generation failed (non-fatal): %s", exc)

            # ---------------------------------------------------------------
            # Persist to DB
            # ---------------------------------------------------------------
            duration_ms = int((time.monotonic() - start_time) * 1000)

            db_opt = Optimization(
                id=opt_id,
                raw_prompt=raw_prompt,
                optimized_prompt=optimization.optimized_prompt,
                task_type=analysis.task_type if analysis.task_type in VALID_TASK_TYPES else "general",
                intent_label=validate_intent_label(
                    title_case_label(analysis.intent_label or "general"),
                    raw_prompt,
                )[:MAX_INTENT_LABEL_LENGTH],
                domain=effective_domain,
                domain_raw=domain_raw,
                cluster_id=cluster_id,
                strategy_used=effective_strategy,
                changes_summary=optimization.changes_summary,
                score_clarity=optimized_scores.clarity if optimized_scores else None,
                score_specificity=optimized_scores.specificity if optimized_scores else None,
                score_structure=optimized_scores.structure if optimized_scores else None,
                score_faithfulness=optimized_scores.faithfulness if optimized_scores else None,
                score_conciseness=optimized_scores.conciseness if optimized_scores else None,
                overall_score=optimized_scores.overall if optimized_scores else None,
                provider=provider.name,
                routing_tier="internal",
                model_used=model_ids.get("optimize", optimizer_model),
                scoring_mode="hybrid" if optimized_scores else "skipped",
                duration_ms=duration_ms,
                status="completed",
                trace_id=trace_id,
                context_sources=context_sources or {},
                original_scores=original_scores.model_dump() if original_scores else None,
                score_deltas=deltas,
                tokens_by_phase=phase_durations,
                models_by_phase=model_ids,
                heuristic_flags=_divergence_flags or None,
                suggestions=suggestions,
            )
            # Compute weighted improvement score from deltas.
            if deltas:
                _imp = sum(
                    deltas.get(dim, 0) * w
                    for dim, w in DIMENSION_WEIGHTS.items()
                )
                db_opt.improvement_score = round(max(0.0, min(10.0, _imp)), 2)
            db.add(db_opt)

            # Track applied patterns in join table (relationship: "applied")
            applied_cluster_ids: set[str] = set()
            if applied_pattern_ids:
                try:
                    from app.models import OptimizationPattern

                    # Collect unique cluster_ids from applied patterns
                    for pid in applied_pattern_ids:
                        mp_result = await db.execute(
                            select(MetaPattern).where(MetaPattern.id == pid)
                        )
                        mp = mp_result.scalar_one_or_none()
                        if mp:
                            db.add(OptimizationPattern(
                                optimization_id=opt_id,
                                cluster_id=mp.cluster_id,
                                meta_pattern_id=mp.id,
                                relationship="applied",
                            ))
                            applied_cluster_ids.add(mp.cluster_id)

                except Exception as exc:
                    logger.warning("Failed to track applied patterns: %s", exc)

            await db.commit()

            # Include auto-injected cluster IDs in usage propagation
            if auto_injected_cluster_ids:
                applied_cluster_ids.update(auto_injected_cluster_ids)

            # Propagate usage counts AFTER successful commit (Spec 7.8)
            # Use a fresh session — the original db session may be expired post-commit
            if applied_cluster_ids and taxonomy_engine:
                try:
                    from app.database import async_session_factory

                    async with async_session_factory() as usage_db:
                        for fid in applied_cluster_ids:
                            try:
                                await taxonomy_engine.increment_usage(fid, usage_db)
                            except Exception as usage_exc:
                                logger.warning("Usage propagation failed for %s: %s", fid, usage_exc)
                                # Fallback: atomic SQL increment (no tree walk)
                                # Matches sampling_pipeline.py robustness pattern
                                try:
                                    from sqlalchemy import update as sa_upd

                                    from app.models import PromptCluster
                                    await usage_db.execute(
                                        sa_upd(PromptCluster)
                                        .where(PromptCluster.id == fid)
                                        .values(
                                            usage_count=PromptCluster.usage_count + 1,
                                        )
                                    )
                                except Exception:
                                    pass  # truly non-fatal
                        await usage_db.commit()
                except Exception as exc:
                    logger.warning("Post-commit usage propagation failed: %s", exc)

            # Publish real-time event for cross-source notifications
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("optimization_created", {
                    "id": opt_id,
                    "trace_id": trace_id,
                    "task_type": analysis.task_type,
                    "intent_label": analysis.intent_label or "general",
                    "domain": effective_domain,
                    "domain_raw": domain_raw,
                    "strategy_used": effective_strategy,
                    "overall_score": optimized_scores.overall if optimized_scores else None,
                    "provider": provider.name,
                    "status": "completed",
                })
            except Exception:
                logger.debug("Event bus publish failed", exc_info=True)

            # ---------------------------------------------------------------
            # Final event
            # ---------------------------------------------------------------
            result = PipelineResult(
                id=opt_id,
                trace_id=trace_id,
                raw_prompt=raw_prompt,
                optimized_prompt=optimization.optimized_prompt,
                task_type=analysis.task_type,
                strategy_used=effective_strategy,
                changes_summary=optimization.changes_summary,
                optimized_scores=optimized_scores,
                original_scores=original_scores,
                score_deltas=deltas,
                overall_score=optimized_scores.overall if optimized_scores else None,
                provider=provider.name,
                routing_tier="internal",
                model_used=model_ids.get("optimize", optimizer_model),
                models_by_phase=model_ids,
                scoring_mode="hybrid" if optimized_scores else "skipped",
                duration_ms=duration_ms,
                status="completed",
                suggestions=suggestions,
                context_sources=context_sources or {},
                warnings=warnings if warnings else [],
                intent_label=analysis.intent_label,
                domain=effective_domain,
            )

            if optimized_scores:
                logger.info(
                    "Pipeline completed: trace_id=%s duration=%dms strategy=%s overall=%.2f",
                    trace_id, duration_ms, effective_strategy, optimized_scores.overall,
                )
            else:
                logger.info(
                    "Pipeline completed (scoring skipped): trace_id=%s duration=%dms strategy=%s",
                    trace_id, duration_ms, effective_strategy,
                )

            yield PipelineEvent(
                event="optimization_complete",
                data=result.model_dump(mode="json"),
            )

        except Exception as exc:
            logger.exception("Pipeline failed: %s", exc)
            duration_ms = int((time.monotonic() - start_time) * 1000)

            # Persist failed optimization
            try:
                await db.rollback()
                failed_opt = Optimization(
                    id=opt_id,
                    raw_prompt=raw_prompt,
                    status="failed",
                    routing_tier="internal",
                    trace_id=trace_id,
                    duration_ms=duration_ms,
                    provider=provider.name,
                    model_used=optimizer_model,
                    models_by_phase=model_ids,
                )
                db.add(failed_opt)
                await db.commit()
            except Exception as db_exc:
                logger.error("Failed to persist failed optimization: %s", db_exc)

            # Publish failure event for cross-source notifications
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("optimization_failed", {
                    "trace_id": trace_id,
                    "error": str(exc),
                })
            except Exception:
                pass

            yield PipelineEvent(event="error", data={
                "trace_id": trace_id,
                "error": str(exc),
            })
