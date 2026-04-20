"""Pipeline orchestrator — async generator yielding SSE PipelineEvents.

Each LLM phase (analyze, optimize, score) is an independent provider call,
not an Agent SDK agent.  The orchestrator coordinates them sequentially
and streams status events for the frontend SSE endpoint.

The heavy lifting for each phase lives in :mod:`app.services.pipeline_phases`
— this module is the thin async-generator shell that wires the phase
helpers together and yields SSE events at the boundaries.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR
from app.providers.base import LLMProvider, TokenUsage, call_provider_with_retry
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    OptimizationResult,
    PipelineEvent,
)
from app.services.pattern_injection import (
    InjectedPattern,
    auto_inject_patterns,
)
from app.services.pipeline_constants import ANALYZE_MAX_TOKENS
from app.services.pipeline_phases import (
    PersistenceInputs,
    build_optimize_context,
    build_pipeline_result,
    persist_and_propagate,
    persist_failed_optimization,
    resolve_blocked_strategies,
    resolve_post_analyze_state,
    run_hybrid_scoring,
    run_suggestion_phase,
)
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader
from app.services.trace_logger import TraceLogger

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Runs the analyze -> optimize -> score pipeline as an async generator."""

    def __init__(self, prompts_dir: Path, data_dir: Path | None = None) -> None:
        self.prompt_loader = PromptLoader(prompts_dir)
        self.strategy_loader = StrategyLoader(prompts_dir / "strategies")
        self._system_prompt: str | None = None

        # data_dir overrides the global DATA_DIR for preferences + traces.
        # Injecting it here (instead of reading DATA_DIR inline) lets tests
        # isolate per-run state — each PipelineOrchestrator reads/writes
        # against its own data directory instead of bleeding into the
        # shared production preferences.json.
        self._data_dir: Path = data_dir or DATA_DIR

        try:
            self.trace_logger: TraceLogger | None = TraceLogger(self._data_dir / "traces")
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
        error classification and exponential backoff.  When
        ``streaming=True``, dispatches to ``complete_parsed_streaming()``
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

    @staticmethod
    async def _auto_inject_patterns(
        raw_prompt: str,
        taxonomy_engine: Any,
        db: AsyncSession,
        trace_id: str,
        optimization_id: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[InjectedPattern], list[str]]:
        """Auto-inject cluster meta-patterns based on prompt embedding similarity.

        Delegates to the shared ``pattern_injection.auto_inject_patterns()``
        helper so the same logic is reused by the sampling pipeline.
        """
        return await auto_inject_patterns(
            raw_prompt, taxonomy_engine, db, trace_id,
            optimization_id=optimization_id,
            project_id=project_id,
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
        codebase_context: str | None = None,
        strategy_intelligence: str | None = None,
        context_sources: dict[str, Any] | None = None,
        repo_full_name: str | None = None,
        project_id: str | None = None,
        github_token: str | None = None,
        applied_pattern_ids: list[str] | None = None,
        taxonomy_engine: Any | None = None,
        domain_resolver: Any | None = None,
        heuristic_task_type: str | None = None,
        heuristic_domain: str | None = None,
        divergence_alerts: str | None = None,
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Execute the full pipeline, yielding SSE events.

        ``project_id`` is frozen at entry by the caller (router / MCP tool) —
        no persist-time resolution happens inside the pipeline.  This
        eliminates the race where a repo link committed mid-pipeline would
        non-deterministically flip a prompt from Legacy to the new project.
        Pass ``None`` only when the caller has no way to resolve it; the
        row will land with ``project_id=NULL`` and the next
        ``_backfill_project_ids`` sweep at startup will repair it.
        """
        trace_id = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        start_time = time.monotonic()

        prefs = PreferencesService(self._data_dir)
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
        effective_strategy: str | None = None

        try:
            # ---------------------------------------------------------------
            # Enrichment trace — log what context was provided (no LLM call)
            if self.trace_logger and context_sources:
                self.trace_logger.log_phase(
                    trace_id=trace_id, phase="enrichment",
                    duration_ms=0,
                    tokens_in=0, tokens_out=0,
                    model="none", provider=provider.name,
                    result={
                        "has_codebase_context": codebase_context is not None,
                        "context_chars": len(codebase_context) if codebase_context else 0,
                        "repo_full_name": (
                            repo_full_name
                            or (context_sources or {}).get("enrichment_meta", {}).get("repo_full_name")
                        ),
                        "context_sources": context_sources,
                    },
                )

            # ---------------------------------------------------------------
            # Phase 1: Analyze
            # ---------------------------------------------------------------
            yield PipelineEvent(
                event="status",
                data={"stage": "analyze", "state": "running", "model": model_ids["analyze"]},
            )

            system_prompt = self._load_system_prompt()

            strategy_intel_enabled = prefs.get(
                "pipeline.enable_strategy_intelligence", prefs_snapshot,
            )
            blocked_strategies = await resolve_blocked_strategies(
                db, enabled=bool(strategy_intel_enabled),
                strategy_override=strategy_override,
            )
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

            # ---------------------------------------------------------------
            # Phase 1.5: Post-analyze state resolution
            # ---------------------------------------------------------------
            post_analyze = await resolve_post_analyze_state(
                raw_prompt=raw_prompt,
                analysis=analysis,
                db=db,
                strategy_loader=self.strategy_loader,
                domain_resolver=domain_resolver,
                taxonomy_engine=taxonomy_engine,
                strategy_override=strategy_override,
                blocked_strategies=blocked_strategies,
                heuristic_task_type=heuristic_task_type,
                heuristic_domain=heuristic_domain,
                applied_pattern_ids=applied_pattern_ids,
                trace_id=trace_id,
            )
            effective_strategy = post_analyze.effective_strategy

            # ---------------------------------------------------------------
            # Pre-Phase: Auto-inject cluster meta-patterns
            # ---------------------------------------------------------------
            auto_injected_patterns: list[InjectedPattern] = []
            auto_injected_cluster_ids: list[str] = []
            if taxonomy_engine is not None:
                try:
                    auto_injected_patterns, auto_injected_cluster_ids = (
                        await self._auto_inject_patterns(
                            raw_prompt=raw_prompt,
                            taxonomy_engine=taxonomy_engine,
                            db=db,
                            trace_id=trace_id,
                            optimization_id=opt_id,
                            project_id=project_id,
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

                        # Store pattern texts for UI attribution (ForgeArtifact)
                        em = context_sources.get("enrichment_meta")
                        if isinstance(em, dict):
                            em["applied_pattern_texts"] = [
                                {
                                    "text": ip.pattern_text,
                                    "source": ip.source or "cluster",
                                    "cluster_label": ip.cluster_label or "",
                                    "similarity": round(ip.similarity, 3) if ip.similarity else None,
                                }
                                for ip in auto_injected_patterns
                            ]
                except Exception as exc:
                    logger.warning("Auto-injection failed: %s", exc)

            # ---------------------------------------------------------------
            # Phase 2: Optimize
            # ---------------------------------------------------------------
            yield PipelineEvent(
                event="status",
                data={"stage": "optimize", "state": "running", "model": model_ids["optimize"]},
            )

            enable_si = prefs.get(
                "pipeline.enable_strategy_intelligence", prefs_snapshot,
            )
            if not enable_si:
                strategy_intelligence = None
            elif strategy_intelligence is None:
                try:
                    from app.services.context_enrichment import resolve_strategy_intelligence
                    strategy_intelligence, _ = await resolve_strategy_intelligence(
                        db, analysis.task_type, analysis.domain or "general",
                    )
                except Exception as exc:
                    logger.debug("Strategy intelligence unavailable: %s", exc)

            optimize_bundle = await build_optimize_context(
                raw_prompt=raw_prompt,
                analysis=analysis,
                effective_strategy=effective_strategy,
                effective_domain=post_analyze.effective_domain,
                prompt_loader=self.prompt_loader,
                strategy_loader=self.strategy_loader,
                db=db,
                applied_pattern_ids=applied_pattern_ids,
                auto_injected_patterns=auto_injected_patterns,
                codebase_context=codebase_context,
                strategy_intelligence=strategy_intelligence,
                divergence_alerts=divergence_alerts,
                prompt_embedding=post_analyze.prompt_embedding,
                trace_id=trace_id,
            )

            if optimize_bundle.context_updates:
                if context_sources is None:
                    context_sources = {}
                context_sources.update(optimize_bundle.context_updates)

            phase_start = time.monotonic()
            optimization: OptimizationResult = await self._call_provider(
                provider,
                system_prompt=system_prompt,
                user_message=optimize_bundle.optimize_msg,
                output_format=OptimizationResult,
                model=optimizer_model,
                effort=prefs.get("pipeline.optimizer_effort", prefs_snapshot) or "high",
                max_tokens=optimize_bundle.dynamic_max_tokens,
                streaming=True,
            )

            # Post-cleanup: strip leaked ## Changes / ## Applied Patterns
            from app.utils.text_cleanup import sanitize_optimization_result

            clean_prompt, clean_changes = sanitize_optimization_result(
                optimization.optimized_prompt, optimization.changes_summary,
            )
            optimization = OptimizationResult(
                optimized_prompt=clean_prompt,
                changes_summary=clean_changes,
                strategy_used=optimization.strategy_used,
            )

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
                        "patterns_injected": len(auto_injected_patterns),
                        "injection_clusters": len(auto_injected_cluster_ids),
                        "has_explicit_patterns": bool(applied_pattern_ids),
                    },
                )

            yield PipelineEvent(event="prompt_preview", data={
                "prompt": optimization.optimized_prompt,
                "changes": [optimization.changes_summary],
            })

            # ---------------------------------------------------------------
            # Phase 3: Score (optional)
            # ---------------------------------------------------------------
            scoring = None
            if prefs.get("pipeline.enable_scoring", prefs_snapshot):
                yield PipelineEvent(
                    event="status",
                    data={"stage": "score", "state": "running", "model": model_ids["score"]},
                )

                scoring = await run_hybrid_scoring(
                    raw_prompt=raw_prompt,
                    optimization=optimization,
                    analysis=analysis,
                    effective_strategy=effective_strategy,
                    provider=provider,
                    prompt_loader=self.prompt_loader,
                    trace_logger=self.trace_logger,
                    prefs=prefs,
                    prefs_snapshot=prefs_snapshot,
                    scorer_model=scorer_model,
                    trace_id=trace_id,
                    db=db,
                    call_provider=self._call_provider,
                )

                model_ids["score"] = scoring.score_model
                yield PipelineEvent(
                    event="status",
                    data={"stage": "score", "state": "complete", "model": model_ids["score"]},
                )
                phase_durations["score_ms"] = scoring.score_duration_ms

                yield PipelineEvent(event="score_card", data={
                    "original_scores": scoring.original_scores.model_dump(),
                    "scores": scoring.optimized_scores.model_dump(),
                    "deltas": scoring.deltas,
                    "overall_score": scoring.optimized_scores.overall,
                })
            else:
                logger.info("Scoring phase skipped per user preferences. trace_id=%s", trace_id)

            # ---------------------------------------------------------------
            # Phase 4: Suggest (when scoring produced results)
            # ---------------------------------------------------------------
            suggestions: list[dict[str, str]] = []
            if scoring and scoring.optimized_scores and analysis.weaknesses is not None:
                yield PipelineEvent(
                    event="status",
                    data={"stage": "suggest", "state": "running", "model": "claude-haiku-4-5"},
                )
                suggestions = await run_suggestion_phase(
                    optimization=optimization,
                    optimized_scores=scoring.optimized_scores,
                    analysis=analysis,
                    effective_strategy=effective_strategy,
                    prompt_loader=self.prompt_loader,
                    system_prompt=self._load_system_prompt(),
                    provider=provider,
                    trace_id=trace_id,
                    call_provider=self._call_provider,
                )
                yield PipelineEvent(event="suggestions", data={"suggestions": suggestions})
                yield PipelineEvent(event="status", data={"stage": "suggest", "state": "complete"})

            # ---------------------------------------------------------------
            # Persist to DB + propagate usage
            # ---------------------------------------------------------------
            duration_ms = int((time.monotonic() - start_time) * 1000)

            persist_inputs = PersistenceInputs(
                opt_id=opt_id,
                raw_prompt=raw_prompt,
                analysis=analysis,
                optimization=optimization,
                effective_strategy=effective_strategy,
                effective_domain=post_analyze.effective_domain,
                domain_raw=post_analyze.domain_raw,
                cluster_id=post_analyze.cluster_id,
                scoring=scoring,
                suggestions=suggestions,
                phase_durations=phase_durations,
                model_ids=model_ids,
                optimizer_model=optimizer_model,
                provider_name=provider.name,
                repo_full_name=repo_full_name,
                project_id=project_id,
                context_sources=context_sources,
                trace_id=trace_id,
                duration_ms=duration_ms,
                applied_pattern_ids=applied_pattern_ids,
                auto_injected_cluster_ids=auto_injected_cluster_ids,
                taxonomy_engine=taxonomy_engine,
                divergence_flags=scoring.divergence_flags if scoring else [],
            )
            await persist_and_propagate(db, persist_inputs)

            # ---------------------------------------------------------------
            # Final event
            # ---------------------------------------------------------------
            result = build_pipeline_result(persist_inputs)

            if scoring and scoring.optimized_scores:
                logger.info(
                    "Pipeline completed: trace_id=%s duration=%dms strategy=%s overall=%.2f",
                    trace_id, duration_ms, effective_strategy, scoring.optimized_scores.overall,
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

            # Structured error log
            try:
                import traceback as _tb

                from app.services.error_logger import get_error_logger

                get_error_logger().log_error(
                    service="pipeline",
                    level="error",
                    module="app.services.pipeline",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    traceback=_tb.format_exc(),
                    request_context={
                        "trace_id": trace_id,
                        "strategy": effective_strategy,
                        "provider": provider.name if provider else None,
                    },
                )
            except Exception:
                pass

            await persist_failed_optimization(
                db,
                opt_id=opt_id,
                raw_prompt=raw_prompt,
                trace_id=trace_id,
                duration_ms=duration_ms,
                provider=provider,
                optimizer_model=optimizer_model,
                model_ids=model_ids,
            )

            yield PipelineEvent(event="error", data={
                "trace_id": trace_id,
                "error": str(exc),
            })


__all__ = ["PipelineOrchestrator"]
