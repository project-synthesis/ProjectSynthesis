"""Pipeline orchestrator — async generator yielding SSE PipelineEvents.

Each LLM phase (analyze, optimize, score) is an independent provider call,
not an Agent SDK agent. The orchestrator coordinates them sequentially and
streams status events for the frontend SSE endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.models import Optimization
from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    PipelineEvent,
    PipelineResult,
    ScoreResult,
)
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader
from app.services.trace_logger import TraceLogger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIDENCE_GATE = 0.7
RETRY_DELAY_SECONDS = 2
MAX_RETRIES = 1

CODING_KEYWORDS: set[str] = {
    "function", "class", "api", "code", "program",
    "script", "endpoint", "database", "module", "import",
}



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

    async def _call_provider(
        self,
        provider: LLMProvider,
        *,
        system_prompt: str,
        user_message: str,
        output_format: type,
        model: str,
        effort: str | None = None,
        max_tokens: int = 16384,
    ) -> Any:
        """Call provider with retry logic (1 retry after 2s delay)."""
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                return await provider.complete_parsed(
                    model=model,
                    system_prompt=system_prompt,
                    user_message=user_message,
                    output_format=output_format,
                    max_tokens=max_tokens,
                    effort=effort,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Provider call failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, MAX_RETRIES + 1, RETRY_DELAY_SECONDS, exc,
                    )
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _semantic_check(task_type: str, raw_prompt: str, confidence: float) -> float:
        """If task_type is 'coding' but no coding keywords found, reduce confidence."""
        if task_type == "coding":
            words = set(raw_prompt.lower().split())
            if not words & CODING_KEYWORDS:
                logger.warning(
                    "Semantic check: task_type='coding' but no coding keywords in prompt"
                )
                confidence = max(0.0, confidence - 0.2)
        return confidence

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
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Execute the full pipeline, yielding SSE events."""
        trace_id = str(uuid.uuid4())
        opt_id = str(uuid.uuid4())
        start_time = time.monotonic()

        yield PipelineEvent(event="optimization_start", data={"trace_id": trace_id})

        phase_durations: dict[str, int] = {}

        try:
            # ---------------------------------------------------------------
            # Phase 0: Explore (optional — codebase context injection)
            # ---------------------------------------------------------------
            if repo_full_name and github_token and codebase_context is None:
                try:
                    from app.services.codebase_explorer import CodebaseExplorer
                    from app.services.embedding_service import EmbeddingService
                    from app.services.github_client import GitHubClient

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
                    if codebase_context:
                        logger.info(
                            "Explore context injected (%d chars) trace_id=%s",
                            len(codebase_context), trace_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "Explore failed, proceeding without codebase context: %s", exc,
                    )

            # ---------------------------------------------------------------
            # Phase 1: Analyze
            # ---------------------------------------------------------------
            yield PipelineEvent(event="status", data={"stage": "analyze", "state": "running"})

            system_prompt = self._load_system_prompt()
            available_strategies = self.strategy_loader.format_available()

            analyze_msg = self.prompt_loader.render("analyze.md", {
                "raw_prompt": raw_prompt,
                "available_strategies": available_strategies,
            })

            phase_start = time.monotonic()
            analysis: AnalysisResult = await self._call_provider(
                provider,
                system_prompt=system_prompt,
                user_message=analyze_msg,
                output_format=AnalysisResult,
                model=settings.MODEL_SONNET,
                effort="medium",
            )

            yield PipelineEvent(event="status", data={"stage": "analyze", "state": "complete"})

            analyze_duration = int((time.monotonic() - phase_start) * 1000)
            phase_durations["analyze_ms"] = analyze_duration

            if self.trace_logger:
                self.trace_logger.log_phase(
                    trace_id=trace_id, phase="analyze",
                    duration_ms=analyze_duration,
                    tokens_in=0, tokens_out=0,
                    model=settings.MODEL_SONNET, provider=provider.name,
                    result={"task_type": analysis.task_type, "strategy": analysis.selected_strategy},
                )

            # Semantic check
            confidence = self._semantic_check(analysis.task_type, raw_prompt, analysis.confidence)

            # Confidence gate
            effective_strategy = analysis.selected_strategy
            if confidence < CONFIDENCE_GATE and not strategy_override:
                logger.info(
                    "Confidence gate triggered (%.2f < %.2f), overriding strategy to 'auto'",
                    confidence, CONFIDENCE_GATE,
                )
                effective_strategy = "auto"

            if strategy_override:
                effective_strategy = strategy_override

            # ---------------------------------------------------------------
            # Phase 2: Optimize
            # ---------------------------------------------------------------
            yield PipelineEvent(event="status", data={"stage": "optimize", "state": "running"})

            strategy_instructions = self.strategy_loader.load(effective_strategy)
            analysis_summary = (
                f"Task type: {analysis.task_type}\n"
                f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
                f"Strengths: {', '.join(analysis.strengths)}\n"
                f"Strategy: {effective_strategy}\n"
                f"Rationale: {analysis.strategy_rationale}"
            )

            optimize_msg = self.prompt_loader.render("optimize.md", {
                "raw_prompt": raw_prompt,
                "analysis_summary": analysis_summary,
                "strategy_instructions": strategy_instructions,
                "codebase_guidance": codebase_guidance,
                "codebase_context": codebase_context,
                "adaptation_state": adaptation_state,
            })

            dynamic_max_tokens = max(16384, len(raw_prompt) // 4 * 2)

            phase_start = time.monotonic()
            optimization: OptimizationResult = await self._call_provider(
                provider,
                system_prompt=system_prompt,
                user_message=optimize_msg,
                output_format=OptimizationResult,
                model=settings.MODEL_OPUS,
                effort="high",
                max_tokens=dynamic_max_tokens,
            )

            yield PipelineEvent(event="status", data={"stage": "optimize", "state": "complete"})

            optimize_duration = int((time.monotonic() - phase_start) * 1000)
            phase_durations["optimize_ms"] = optimize_duration

            if self.trace_logger:
                self.trace_logger.log_phase(
                    trace_id=trace_id, phase="optimize",
                    duration_ms=optimize_duration,
                    tokens_in=0, tokens_out=0,
                    model=settings.MODEL_OPUS, provider=provider.name,
                    result={"strategy_used": optimization.strategy_used},
                )

            yield PipelineEvent(event="prompt_preview", data={
                "prompt": optimization.optimized_prompt,
                "changes": [optimization.changes_summary],
            })

            # ---------------------------------------------------------------
            # Phase 3: Score
            # ---------------------------------------------------------------
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
            scorer_msg = f"## Prompt A\n\n{prompt_a}\n\n## Prompt B\n\n{prompt_b}"

            phase_start = time.monotonic()
            scores: ScoreResult = await self._call_provider(
                provider,
                system_prompt=scoring_system,
                user_message=scorer_msg,
                output_format=ScoreResult,
                model=settings.MODEL_SONNET,
                effort="medium",
            )

            yield PipelineEvent(event="status", data={"stage": "score", "state": "complete"})

            score_duration = int((time.monotonic() - phase_start) * 1000)
            phase_durations["score_ms"] = score_duration

            if self.trace_logger:
                self.trace_logger.log_phase(
                    trace_id=trace_id, phase="score",
                    duration_ms=score_duration,
                    tokens_in=0, tokens_out=0,
                    model=settings.MODEL_SONNET, provider=provider.name,
                )

            # Map A/B scores back to original/optimized
            if original_first:
                original_scores = scores.prompt_a_scores
                optimized_scores = scores.prompt_b_scores
            else:
                original_scores = scores.prompt_b_scores
                optimized_scores = scores.prompt_a_scores

            deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)

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
            except Exception as exc:
                logger.debug("Intent drift check skipped: %s", exc)

            # ---------------------------------------------------------------
            # Persist to DB
            # ---------------------------------------------------------------
            duration_ms = int((time.monotonic() - start_time) * 1000)

            db_opt = Optimization(
                id=opt_id,
                raw_prompt=raw_prompt,
                optimized_prompt=optimization.optimized_prompt,
                task_type=analysis.task_type,
                strategy_used=optimization.strategy_used,
                changes_summary=optimization.changes_summary,
                score_clarity=optimized_scores.clarity,
                score_specificity=optimized_scores.specificity,
                score_structure=optimized_scores.structure,
                score_faithfulness=optimized_scores.faithfulness,
                score_conciseness=optimized_scores.conciseness,
                overall_score=optimized_scores.overall,
                provider=provider.name,
                model_used=settings.MODEL_OPUS,
                scoring_mode="independent",
                duration_ms=duration_ms,
                status="completed",
                trace_id=trace_id,
                context_sources=context_sources or {},
                original_scores=original_scores.model_dump(),
                score_deltas=deltas,
                tokens_by_phase=phase_durations,
            )
            db.add(db_opt)
            await db.commit()

            # ---------------------------------------------------------------
            # Final event
            # ---------------------------------------------------------------
            result = PipelineResult(
                id=opt_id,
                trace_id=trace_id,
                raw_prompt=raw_prompt,
                optimized_prompt=optimization.optimized_prompt,
                task_type=analysis.task_type,
                strategy_used=optimization.strategy_used,
                changes_summary=optimization.changes_summary,
                optimized_scores=optimized_scores,
                original_scores=original_scores,
                score_deltas=deltas,
                overall_score=optimized_scores.overall,
                provider=provider.name,
                model_used=settings.MODEL_OPUS,
                scoring_mode="independent",
                duration_ms=duration_ms,
                status="completed",
                context_sources=context_sources or {},
                warnings=warnings if warnings else [],
            )

            logger.info(
                "Pipeline completed: trace_id=%s duration=%dms strategy=%s overall=%.2f",
                trace_id, duration_ms, optimization.strategy_used, optimized_scores.overall,
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
                    trace_id=trace_id,
                    duration_ms=duration_ms,
                    provider=provider.name,
                )
                db.add(failed_opt)
                await db.commit()
            except Exception as db_exc:
                logger.error("Failed to persist failed optimization: %s", db_exc)

            yield PipelineEvent(event="error", data={
                "trace_id": trace_id,
                "error": str(exc),
            })
