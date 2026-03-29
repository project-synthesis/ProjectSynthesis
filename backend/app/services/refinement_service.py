"""Refinement service — version history, branching, and suggestion generation.

Each refinement turn is a fresh pipeline invocation (analyze -> refine -> score
-> suggest), not multi-turn conversation. The service orchestrates its own flow
using refine.md instead of optimize.md.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.models import Optimization, RefinementBranch, RefinementTurn
from app.providers.base import LLMProvider, call_provider_with_retry
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    PipelineEvent,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.heuristic_scorer import HeuristicScorer
from app.services.pipeline_constants import ANALYZE_MAX_TOKENS, SCORE_MAX_TOKENS, compute_optimize_max_tokens
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RefinementService:
    """Manages refinement sessions with version history, branching, and
    suggestion generation."""

    def __init__(
        self,
        db: AsyncSession,
        provider: LLMProvider,
        prompts_dir: Path,
    ) -> None:
        self.db = db
        self.provider = provider
        self.prompt_loader = PromptLoader(prompts_dir)
        self.strategy_loader = StrategyLoader(prompts_dir / "strategies")

    # ------------------------------------------------------------------
    # Provider call with retry
    # ------------------------------------------------------------------

    async def _call_provider(
        self,
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

        Delegates to the shared ``call_provider_with_retry`` utility.
        When ``streaming=True``, uses ``complete_parsed_streaming()`` to
        prevent HTTP timeouts on long outputs (e.g. Opus refine phase).
        """
        return await call_provider_with_retry(
            self.provider,
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            output_format=output_format,
            max_tokens=max_tokens,
            effort=effort,
            streaming=streaming,
            cache_ttl=cache_ttl,
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def create_initial_turn(
        self,
        optimization_id: str,
        prompt: str,
        scores_dict: dict[str, Any],
        strategy_used: str,
    ) -> RefinementTurn:
        """Create the first branch and version-1 turn for an optimization.

        Args:
            optimization_id: The parent optimization ID.
            prompt: The optimized prompt text.
            scores_dict: Score dimensions as a dict.
            strategy_used: Strategy name used for the optimization.

        Returns:
            The newly created RefinementTurn (version=1).
        """
        branch = RefinementBranch(
            id=str(uuid.uuid4()),
            optimization_id=optimization_id,
            parent_branch_id=None,
            forked_at_version=None,
        )
        self.db.add(branch)

        turn = RefinementTurn(
            id=str(uuid.uuid4()),
            optimization_id=optimization_id,
            version=1,
            branch_id=branch.id,
            parent_version=None,
            refinement_request=None,
            prompt=prompt,
            scores=scores_dict,
            deltas=None,
            deltas_from_original=None,
            strategy_used=strategy_used,
            suggestions=None,
            trace_id=None,
        )
        self.db.add(turn)
        await self.db.commit()

        logger.info(
            "Initial refinement turn created: optimization_id=%s branch_id=%s",
            optimization_id, branch.id,
        )
        return turn

    async def create_refinement_turn(
        self,
        optimization_id: str,
        branch_id: str,
        refinement_request: str,
        codebase_guidance: str | None = None,
        codebase_context: str | None = None,
        adaptation_state: str | None = None,
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Run a refinement pipeline and yield SSE events.

        Stages: analyzer -> refiner -> scorer -> suggestion generator.

        Args:
            optimization_id: The parent optimization ID.
            branch_id: The branch to append the new turn to.
            refinement_request: User's refinement instruction.

        Yields:
            PipelineEvent objects for each stage.
        """
        _prefs = PreferencesService(DATA_DIR)
        _prefs_snapshot = _prefs.load()

        # Get the latest turn on this branch
        result = await self.db.execute(
            select(RefinementTurn)
            .where(
                RefinementTurn.optimization_id == optimization_id,
                RefinementTurn.branch_id == branch_id,
            )
            .order_by(RefinementTurn.version.desc())
            .limit(1)
        )
        prev_turn = result.scalar_one()

        # Get the original optimization for the raw prompt
        opt_result = await self.db.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        optimization = opt_result.scalar_one()

        current_prompt = prev_turn.prompt
        original_prompt = optimization.raw_prompt
        strategy_name = prev_turn.strategy_used or "auto"

        trace_id = str(uuid.uuid4())

        logger.info(
            "Refinement turn started: optimization_id=%s branch_id=%s prev_version=%d trace_id=%s",
            optimization_id, branch_id, prev_turn.version, trace_id,
        )

        # ---------------------------------------------------------------
        # Stage 1: Analyze
        # ---------------------------------------------------------------
        yield PipelineEvent(event="status", data={"stage": "analyze", "state": "running"})

        system_prompt = self.prompt_loader.load("agent-guidance.md")
        available_strategies = self.strategy_loader.format_available()

        # Resolve dynamic domain list for analyzer prompt
        try:
            from app.services.domain_resolver import get_domain_resolver
            _resolver = get_domain_resolver()
            _known_domains = ", ".join(sorted(_resolver.domain_labels))
        except (ValueError, ImportError):
            _known_domains = "backend, frontend, database, devops, security, fullstack, general"

        analyze_msg = self.prompt_loader.render("analyze.md", {
            "raw_prompt": current_prompt,
            "available_strategies": available_strategies,
            "known_domains": _known_domains,
        })

        analysis: AnalysisResult = await self._call_provider(
            system_prompt=system_prompt,
            user_message=analyze_msg,
            output_format=AnalysisResult,
            model=_prefs.resolve_model("analyzer", _prefs_snapshot),
            effort=_prefs.get("pipeline.analyzer_effort", _prefs_snapshot) or "low",
            max_tokens=ANALYZE_MAX_TOKENS,
        )

        yield PipelineEvent(event="status", data={"stage": "analyze", "state": "complete"})

        # ---------------------------------------------------------------
        # Stage 2: Refine
        # ---------------------------------------------------------------
        yield PipelineEvent(event="status", data={"stage": "refine", "state": "running"})

        strategy_instructions = self.strategy_loader.load(strategy_name)

        refine_msg = self.prompt_loader.render("refine.md", {
            "current_prompt": current_prompt,
            "refinement_request": refinement_request,
            "original_prompt": original_prompt,
            "strategy_instructions": strategy_instructions,
            "codebase_guidance": codebase_guidance,
            "codebase_context": codebase_context,
            "adaptation_state": adaptation_state,
        })

        # Dynamic output budget matching the main pipeline (128K cap with streaming)
        dynamic_max_tokens = compute_optimize_max_tokens(len(current_prompt))

        refined: OptimizationResult = await self._call_provider(
            system_prompt=system_prompt,
            user_message=refine_msg,
            output_format=OptimizationResult,
            model=_prefs.resolve_model("optimizer", _prefs_snapshot),
            effort=_prefs.get("pipeline.optimizer_effort", _prefs_snapshot) or "high",
            max_tokens=dynamic_max_tokens,
            streaming=True,
        )

        yield PipelineEvent(event="prompt_preview", data={
            "prompt": refined.optimized_prompt,
            "changes": [refined.changes_summary],
        })

        yield PipelineEvent(event="status", data={"stage": "refine", "state": "complete"})

        # ---------------------------------------------------------------
        # Stage 3: Score
        # ---------------------------------------------------------------
        scoring_enabled = _prefs.get("pipeline.enable_scoring", _prefs_snapshot)
        if scoring_enabled is None:
            scoring_enabled = True

        optimized_scores = None
        original_scores = None
        deltas = None
        deltas_from_prev = None
        suggestions_list: list = []

        if scoring_enabled:
            yield PipelineEvent(event="status", data={"stage": "score", "state": "running"})

            # Randomize A/B assignment to prevent position bias
            original_first = random.choice([True, False])
            if original_first:
                prompt_a = original_prompt
                prompt_b = refined.optimized_prompt
            else:
                prompt_a = refined.optimized_prompt
                prompt_b = original_prompt
            scoring_system = self.prompt_loader.load("scoring.md")
            # XML tags prevent prompt content with ## headers from corrupting
            # the A/B boundary for the scorer
            scorer_msg = (
                f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
                f"<prompt-b>\n{prompt_b}\n</prompt-b>"
            )

            scores: ScoreResult = await self._call_provider(
                system_prompt=scoring_system,
                user_message=scorer_msg,
                output_format=ScoreResult,
                model=_prefs.resolve_model("scorer", _prefs_snapshot),
                effort=_prefs.get("pipeline.scorer_effort", _prefs_snapshot) or "low",
                max_tokens=SCORE_MAX_TOKENS,
                cache_ttl="1h",
            )

            # Map A/B scores back to original/optimized
            if original_first:
                llm_original = scores.prompt_a_scores
                llm_optimized = scores.prompt_b_scores
            else:
                llm_original = scores.prompt_b_scores
                llm_optimized = scores.prompt_a_scores

            # Hybrid scoring: blend LLM + heuristic (same as main pipeline)
            heur_original = HeuristicScorer.score_prompt(original_prompt)
            heur_optimized = HeuristicScorer.score_prompt(
                refined.optimized_prompt, original=original_prompt,
            )

            # Fetch historical stats for z-score normalization (non-fatal)
            historical_stats: dict | None = None
            try:
                from app.services.optimization_service import OptimizationService
                opt_svc = OptimizationService(self.db)
                historical_stats = await opt_svc.get_score_distribution(
                    exclude_scoring_modes=["heuristic"],
                )
            except Exception:
                pass

            blended_original = blend_scores(llm_original, heur_original, historical_stats)
            blended_optimized = blend_scores(llm_optimized, heur_optimized, historical_stats)
            original_scores = blended_original.to_dimension_scores()
            optimized_scores = blended_optimized.to_dimension_scores()

            # Compute deltas (current refinement vs original)
            deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)

            # Compute deltas from previous turn
            if prev_turn.scores:
                prev_scores = prev_turn.scores
                deltas_from_prev = {}
                for dim in ("clarity", "specificity", "structure", "faithfulness", "conciseness"):
                    opt_val = getattr(optimized_scores, dim)
                    prev_val = prev_scores.get(dim)
                    if prev_val is not None:
                        deltas_from_prev[dim] = round(opt_val - prev_val, 2)

            yield PipelineEvent(event="score_card", data={
                "original_scores": original_scores.model_dump(),
                "scores": optimized_scores.model_dump(),
                "deltas": deltas,
                "overall_score": optimized_scores.overall,
            })

            yield PipelineEvent(event="status", data={"stage": "score", "state": "complete"})

            # ---------------------------------------------------------------
            # Stage 4: Suggest (only when scoring is enabled)
            # ---------------------------------------------------------------
            yield PipelineEvent(event="status", data={"stage": "suggest", "state": "running"})

            suggestions_list = await self._generate_suggestions(
                optimized_prompt=refined.optimized_prompt,
                scores=optimized_scores.model_dump(),
                weaknesses=analysis.weaknesses,
                strategy=refined.strategy_used,
            )

            yield PipelineEvent(event="suggestions", data={"suggestions": suggestions_list})
            yield PipelineEvent(event="status", data={"stage": "suggest", "state": "complete"})
        else:
            yield PipelineEvent(event="status", data={"stage": "score", "state": "skipped"})
            logger.info(
                "Scoring phase skipped per user preferences. trace_id=%s",
                trace_id,
            )

        # ---------------------------------------------------------------
        # Persist new turn
        # ---------------------------------------------------------------
        scores_dict = optimized_scores.model_dump() if optimized_scores else None
        if scores_dict and optimized_scores:
            scores_dict["overall"] = optimized_scores.overall

        new_turn = RefinementTurn(
            id=str(uuid.uuid4()),
            optimization_id=optimization_id,
            version=prev_turn.version + 1,
            branch_id=branch_id,
            parent_version=prev_turn.version,
            refinement_request=refinement_request,
            prompt=refined.optimized_prompt,
            scores=scores_dict,
            deltas=deltas_from_prev,
            deltas_from_original=deltas,
            strategy_used=refined.strategy_used,
            suggestions=suggestions_list,
            trace_id=trace_id,
        )
        self.db.add(new_turn)
        await self.db.commit()

        # Publish real-time event for cross-source notifications
        _overall = optimized_scores.overall if optimized_scores else None
        try:
            from app.services.event_bus import event_bus
            event_bus.publish("refinement_turn", {
                "optimization_id": optimization_id,
                "version": new_turn.version,
                "overall_score": _overall,
                "branch_id": branch_id,
            })
        except Exception:
            logger.debug("Event bus publish failed for refinement turn — ignoring")

        logger.info(
            "Refinement turn completed: optimization_id=%s version=%d overall=%s trace_id=%s",
            optimization_id, new_turn.version, _overall, trace_id,
        )

    async def get_versions(
        self,
        optimization_id: str,
        branch_id: str | None = None,
    ) -> list[RefinementTurn]:
        """Get refinement turns for an optimization, optionally filtered by branch.

        Args:
            optimization_id: The parent optimization ID.
            branch_id: If given, filter to this branch only.

        Returns:
            List of RefinementTurn objects ordered by version ascending.
        """
        stmt = (
            select(RefinementTurn)
            .where(RefinementTurn.optimization_id == optimization_id)
            .order_by(RefinementTurn.version.asc())
        )

        if branch_id is not None:
            stmt = stmt.where(RefinementTurn.branch_id == branch_id)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def rollback(
        self,
        optimization_id: str,
        to_version: int,
    ) -> RefinementBranch:
        """Create a new branch forked from a specific version.

        Args:
            optimization_id: The parent optimization ID.
            to_version: The version number to fork from.

        Returns:
            The newly created RefinementBranch.
        """
        # Find the turn at the requested version
        result = await self.db.execute(
            select(RefinementTurn).where(
                RefinementTurn.optimization_id == optimization_id,
                RefinementTurn.version == to_version,
            )
        )
        source_turn = result.scalar_one()

        new_branch = RefinementBranch(
            id=str(uuid.uuid4()),
            optimization_id=optimization_id,
            parent_branch_id=source_turn.branch_id,
            forked_at_version=to_version,
        )
        self.db.add(new_branch)
        await self.db.commit()

        logger.info(
            "Rollback branch created: optimization_id=%s from_version=%d new_branch_id=%s",
            optimization_id, to_version, new_branch.id,
        )

        return new_branch

    async def get_branches(
        self,
        optimization_id: str,
    ) -> list[RefinementBranch]:
        """Get all branches for an optimization.

        Args:
            optimization_id: The parent optimization ID.

        Returns:
            List of RefinementBranch objects.
        """
        result = await self.db.execute(
            select(RefinementBranch)
            .where(RefinementBranch.optimization_id == optimization_id)
            .order_by(RefinementBranch.created_at.asc())
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _generate_suggestions(
        self,
        optimized_prompt: str,
        scores: dict[str, Any],
        weaknesses: list[str],
        strategy: str,
    ) -> list[dict[str, str]]:
        """Generate 3 actionable refinement suggestions via Haiku.

        Args:
            optimized_prompt: The current optimized prompt.
            scores: Score dimensions as a dict.
            weaknesses: Weaknesses from the analyzer.
            strategy: Strategy name used.

        Returns:
            List of 3 suggestion dicts: [{text: str, source: str}].
        """
        suggest_msg = self.prompt_loader.render("suggest.md", {
            "optimized_prompt": optimized_prompt,
            "scores": json.dumps(scores, indent=2),
            "weaknesses": ", ".join(weaknesses) if weaknesses else "none identified",
            "strategy_used": strategy,
        })

        system_prompt = self.prompt_loader.load("agent-guidance.md")

        result: SuggestionsOutput = await self._call_provider(
            system_prompt=system_prompt,
            user_message=suggest_msg,
            output_format=SuggestionsOutput,
            model=settings.MODEL_HAIKU,
            max_tokens=2048,
        )

        return result.suggestions

