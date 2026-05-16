"""Refinement service — version history, branching, and suggestion generation.

Each refinement turn is a fresh pipeline invocation (analyze -> refine -> score
-> suggest), not multi-turn conversation. The service orchestrates its own flow
using refine.md instead of optimize.md.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401  (re-used by Task 4-6 method params)

from app.config import DATA_DIR, settings
from app.models import RefinementBranch, RefinementTurn
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
from app.services.pipeline_constants import (
    ANALYZE_MAX_TOKENS,
    SCORE_MAX_TOKENS,
    clamp_analyze_effort,
    compute_optimize_max_tokens,
)
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader

if TYPE_CHECKING:
    from app.services.refinement_context import (
        RefinementContext,
        RollbackPayload,
        _InitialTurnPayload,
        _OptSnapshot,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RefinementService:
    """Manages refinement sessions with version history, branching, and
    suggestion generation.

    Foundation P4 Cycle 2: constructor is keyword-only and does NOT hold a
    DB session. Each method takes `db: AsyncSession` as a method-level
    parameter where DB access is needed. The LLM-pipeline method
    `invoke_refinement_pipeline(ctx)` takes a `RefinementContext` instead
    of session+ORM rows.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        prompts_dir: Path,
        data_dir: Path | None = None,
    ) -> None:
        """Construct a RefinementService.

        Keyword-only (`*,` separator) because `provider` has a default but
        `prompts_dir` does not — Python's "non-default arg after default"
        rule would raise `SyntaxError` with positional args.

        Args:
            provider: LLM provider for invoke_refinement_pipeline. Optional
                so read-only callers (get_versions, get_branches) and rollback
                (no LLM, but does write) can construct without a provider.
                Methods that require a provider raise `ValueError("provider
                required")` if `self.provider is None`.
            prompts_dir: Required path to the prompts directory.
            data_dir: Optional override for preferences. Defaults to
                config.DATA_DIR. Matches PipelineOrchestrator pattern for
                test isolation.
        """
        self.provider = provider
        self.prompt_loader = PromptLoader(prompts_dir)
        self.strategy_loader = StrategyLoader(prompts_dir / "strategies")
        self._data_dir: Path = data_dir or DATA_DIR

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

    def build_initial_turn_payload(
        self,
        opt_snapshot: "_OptSnapshot",
        initial_scores_dict: dict[str, float],
    ) -> "_InitialTurnPayload":
        """Pure-compute payload builder for the seed branch + initial turn.

        Replaces `create_initial_turn` (Cycle 2 rename). No DB writes — caller
        submits the actual INSERT via `WriteQueue.submit(operation_label=
        "refine_initial_turn")` using the returned kwargs.

        Args:
            opt_snapshot: Frozen snapshot of the Optimization row.
            initial_scores_dict: Pre-computed score dict from
                `build_scores_dict(opt)` inside the read session.

        Returns:
            Frozen `_InitialTurnPayload` with `branch_kwargs` + `turn_kwargs`
            ready to splat into RefinementBranch / RefinementTurn constructors.
        """
        import uuid as _uuid

        from app.services.refinement_context import _InitialTurnPayload

        branch_id = str(_uuid.uuid4())
        turn_id = str(_uuid.uuid4())

        branch_kwargs = {
            "id": branch_id,
            "optimization_id": opt_snapshot.id,
            "parent_branch_id": None,
            "forked_at_version": None,
        }
        # parent_version=None and refinement_request=None for the seed turn —
        # matches the existing `create_initial_turn` defaults at
        # refinement_service.py:141-142.
        turn_kwargs = {
            "id": turn_id,
            "optimization_id": opt_snapshot.id,
            "branch_id": branch_id,
            "version": 1,
            "parent_version": None,
            "prompt": opt_snapshot.optimized_prompt,
            "refinement_request": None,
            "scores": initial_scores_dict,
            "deltas": None,
            "deltas_from_original": None,
            "strategy_used": opt_snapshot.strategy_used or "auto",
            "suggestions": None,
            "trace_id": None,
        }

        return _InitialTurnPayload(
            branch_kwargs=branch_kwargs,
            turn_kwargs=turn_kwargs,
        )

    async def invoke_refinement_pipeline(
        self,
        ctx: "RefinementContext",
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Run the 4-LLM-call refinement pipeline against a frozen context.

        Replaces `create_refinement_turn` (Cycle 2 rename). All ORM-bound
        data flows through `ctx` (the read session has already closed).
        No DB writes inside this method — caller persists via WriteQueue.

        Terminal yield contract (NEW in Cycle 2): the last yielded event
        MUST be `PipelineEvent(event="refinement_complete", data={...})`
        with 6 keys: `optimized_prompt`, `scores`, `deltas_from_prev`,
        `deltas_from_original`, `strategy_used`, `suggestions`. The
        caller's `_persist_refinement_turn` callback consumes these to
        build the RefinementTurn row.

        Args:
            ctx: Frozen RefinementContext with all snapshot data.

        Yields:
            PipelineEvent objects for SSE streaming. Final yield is
            always `refinement_complete` (or `error` on failure).

        Raises:
            ValueError("provider required") on first iteration if
                `self.provider is None`.
        """
        import random as _random

        if self.provider is None:
            raise ValueError("provider required")

        opt_snapshot = ctx.opt_snapshot
        # Avoid local name `latest_turn` — Test 5b forbids that identifier
        # entirely under ast.Name to catch detached-ORM leaks. Use `latest_snap`
        # for the snapshot dataclass binding.
        latest_snap = ctx.latest_turn_snapshot
        if latest_snap is None:
            raise ValueError(
                "invoke_refinement_pipeline requires latest_turn_snapshot; "
                "caller must seed via build_initial_turn_payload + queue submit"
            )

        prev_prompt = latest_snap.prompt
        prev_scores = latest_snap.scores or {}
        prev_strategy = latest_snap.strategy_used or "auto"
        original_prompt = opt_snapshot.raw_prompt

        # Resolve preferences (no DB — uses self._data_dir snapshot)
        prefs = PreferencesService(self._data_dir)
        prefs_snapshot = prefs.load()
        analyze_effort = clamp_analyze_effort(
            prefs.get("pipeline.analyzer_effort", prefs_snapshot)
        )
        scoring_enabled = prefs.get("pipeline.enable_scoring", prefs_snapshot)
        if scoring_enabled is None:
            scoring_enabled = True

        strategy_name = prev_strategy

        # ----- Phase 1: Analyze -----
        yield PipelineEvent(event="status", data={"stage": "analyze", "state": "running"})

        # Resolve dynamic domain list for analyzer prompt (mirrors existing
        # refinement_service.py:223-228 block).
        try:
            from app.services.domain_resolver import get_domain_resolver
            _resolver = get_domain_resolver()
            _known_domains = ", ".join(sorted(_resolver.domain_labels))
        except (ValueError, ImportError):
            _known_domains = "backend, frontend, database, data, devops, security, fullstack, general"

        available_strategies = self.strategy_loader.format_available()
        system_prompt = self.prompt_loader.load("agent-guidance.md")

        analyze_msg = self.prompt_loader.render("analyze.md", {
            "raw_prompt": prev_prompt,
            "available_strategies": available_strategies,
            "known_domains": _known_domains,
        })

        analysis: AnalysisResult = await self._call_provider(
            system_prompt=system_prompt,
            user_message=analyze_msg,
            output_format=AnalysisResult,
            model=prefs.resolve_model("analyzer", prefs_snapshot),
            effort=analyze_effort,
            max_tokens=ANALYZE_MAX_TOKENS,
        )

        strategy_name = analysis.selected_strategy or strategy_name

        yield PipelineEvent(event="status", data={"stage": "analyze", "state": "complete"})

        # ----- Phase 2: Optimize (refine) -----
        yield PipelineEvent(event="status", data={"stage": "refine", "state": "running"})

        strategy_instructions = self.strategy_loader.load(strategy_name)

        # Build prev-scores summary string + top-2 dimensions (mirrors
        # refinement_service.py:257-272).
        scores_str = ", ".join(
            f"{dim}: {prev_scores.get(dim, '?')}" for dim in
            ("clarity", "specificity", "structure", "faithfulness", "conciseness")
        ) if prev_scores else "not yet scored"
        if prev_scores:
            sorted_dims = sorted(
                ((d, v) for d, v in prev_scores.items() if d != "overall"),
                key=lambda x: x[1], reverse=True,
            )
            strongest_str = ", ".join(f"{d} ({v})" for d, v in sorted_dims[:2])
        else:
            strongest_str = "unknown"

        # ``refine.md`` template — variable names match manifest.json + the
        # pre-restructure call site at refinement_service.py:274.
        refine_msg = self.prompt_loader.render("refine.md", {
            "current_prompt": prev_prompt,
            "refinement_request": ctx.refinement_request,
            "original_prompt": original_prompt,
            "strategy_instructions": strategy_instructions,
            "codebase_context": ctx.enrichment.codebase_context,
            "strategy_intelligence": ctx.enrichment.strategy_intelligence,
            "current_scores": scores_str,
            "strongest_dimensions": strongest_str,
            "divergence_alerts": ctx.enrichment.divergence_alerts,
            "applied_patterns": ctx.enrichment.applied_patterns,
        })

        dynamic_max_tokens = compute_optimize_max_tokens(len(prev_prompt))

        refined: OptimizationResult = await self._call_provider(
            system_prompt=system_prompt,
            user_message=refine_msg,
            output_format=OptimizationResult,
            model=prefs.resolve_model("optimizer", prefs_snapshot),
            effort=prefs.get("pipeline.optimizer_effort", prefs_snapshot) or "high",
            max_tokens=dynamic_max_tokens,
            streaming=True,
        )

        optimized_prompt = refined.optimized_prompt

        yield PipelineEvent(event="prompt_preview", data={
            "prompt": optimized_prompt,
            "changes": [refined.changes_summary],
        })

        yield PipelineEvent(event="status", data={"stage": "refine", "state": "complete"})

        # ----- Phase 3: Score -----
        scores_dict: dict[str, float] | None = None
        deltas_from_prev: dict[str, float] | None = None
        deltas_from_original: dict[str, float] | None = None
        optimized_scores = None

        if scoring_enabled:
            yield PipelineEvent(event="status", data={"stage": "score", "state": "running"})

            # A/B randomized presentation (mirrors refinement_service.py:323-347).
            original_first = _random.choice([True, False])
            if original_first:
                prompt_a = original_prompt
                prompt_b = optimized_prompt
            else:
                prompt_a = optimized_prompt
                prompt_b = original_prompt
            scoring_system = self.prompt_loader.load("scoring.md")
            scorer_msg = (
                f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
                f"<prompt-b>\n{prompt_b}\n</prompt-b>"
            )

            score_result: ScoreResult = await self._call_provider(
                system_prompt=scoring_system,
                user_message=scorer_msg,
                output_format=ScoreResult,
                model=prefs.resolve_model("scorer", prefs_snapshot),
                effort=prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low",
                max_tokens=SCORE_MAX_TOKENS,
                cache_ttl="1h",
            )

            # Map A/B scores back to original/optimized
            if original_first:
                llm_original = score_result.prompt_a_scores
                llm_optimized = score_result.prompt_b_scores
            else:
                llm_original = score_result.prompt_b_scores
                llm_optimized = score_result.prompt_a_scores

            # HeuristicScorer.score_prompt is a @classmethod returning dict
            heur_original = HeuristicScorer.score_prompt(original_prompt)
            heur_optimized = HeuristicScorer.score_prompt(
                optimized_prompt,
                original=original_prompt,
            )

            blended_original = blend_scores(
                llm_original,
                heur_original,
                historical_stats=ctx.historical_stats,
                prompt_text=original_prompt,
                task_type=analysis.task_type,
            )
            blended_optimized = blend_scores(
                llm_optimized,
                heur_optimized,
                historical_stats=ctx.historical_stats,
                prompt_text=optimized_prompt,
                task_type=analysis.task_type,
            )
            original_scores = blended_original.to_dimension_scores()
            optimized_scores = blended_optimized.to_dimension_scores()

            # Compute deltas (current refinement vs original)
            deltas_from_original = DimensionScores.compute_deltas(
                original_scores, optimized_scores,
            )

            # Compute deltas from previous turn
            if prev_scores:
                deltas_from_prev = {}
                for dim in ("clarity", "specificity", "structure", "faithfulness", "conciseness"):
                    opt_val = getattr(optimized_scores, dim)
                    prev_val = prev_scores.get(dim)
                    if prev_val is not None:
                        deltas_from_prev[dim] = round(opt_val - prev_val, 2)

            scores_dict = optimized_scores.model_dump()
            scores_dict["overall"] = optimized_scores.overall

            yield PipelineEvent(event="score_card", data={
                "original_scores": original_scores.model_dump(),
                "scores": optimized_scores.model_dump(),
                "deltas": deltas_from_prev,
                "overall_score": optimized_scores.overall,
            })

            yield PipelineEvent(event="status", data={"stage": "score", "state": "complete"})

        # ----- Phase 4: Suggest -----
        suggestions: list[dict] | None = None
        if scoring_enabled and optimized_scores is not None:
            yield PipelineEvent(event="status", data={"stage": "suggest", "state": "running"})

            # Compute trajectory for suggestion generator (mirrors
            # refinement_service.py:414-422).
            _neg_count = sum(1 for v in (deltas_from_prev or {}).values() if v < -0.3)
            if not deltas_from_prev:
                _trajectory = "first turn"
            elif _neg_count >= 3:
                _trajectory = "degrading"
            elif _neg_count >= 2:
                _trajectory = "oscillating"
            else:
                _trajectory = "improving"

            suggestions = await self._generate_suggestions(
                optimized_prompt=optimized_prompt,
                scores=optimized_scores.model_dump(),
                weaknesses=analysis.weaknesses,
                strategy=strategy_name,
                score_deltas=deltas_from_prev,
                score_trajectory=_trajectory,
            )

            yield PipelineEvent(event="suggestions", data={"suggestions": suggestions})
            yield PipelineEvent(event="status", data={"stage": "suggest", "state": "complete"})
        else:
            yield PipelineEvent(event="status", data={"stage": "score", "state": "skipped"})

        # ----- Terminal yield: refinement_complete -----
        # Cycle 2 NEW behavior. Caller's _persist_refinement_turn callback
        # consumes these 6 keys to build the RefinementTurn row.
        yield PipelineEvent(
            event="refinement_complete",
            data={
                "optimized_prompt": optimized_prompt,
                "scores": scores_dict,
                "deltas_from_prev": deltas_from_prev,
                "deltas_from_original": deltas_from_original,
                "strategy_used": strategy_name,
                "suggestions": suggestions,
            },
        )

    async def get_versions(
        self,
        db: AsyncSession,
        optimization_id: str,
        branch_id: str | None = None,
    ) -> list[RefinementTurn]:
        """Return all turns for an optimization, ordered by version ASC.

        Cycle 2: takes `db` as method-level parameter (drops `self.db`).
        Pure read; no writes.
        """
        stmt = select(RefinementTurn).where(
            RefinementTurn.optimization_id == optimization_id,
        )
        if branch_id is not None:
            stmt = stmt.where(RefinementTurn.branch_id == branch_id)
        stmt = stmt.order_by(RefinementTurn.version.asc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def rollback(
        self,
        db: AsyncSession,
        optimization_id: str,
        to_version: int,
    ) -> "RollbackPayload":
        """Build a new branch forked at `to_version` AND seed it with an
        initial turn carrying the content of the rolled-back-from version.
        Returns an un-attached ``RollbackPayload`` — caller persists both
        rows atomically via ``WriteQueue.submit()``.

        Cycle 2: drops inline `db.commit()` AND drops `self.db` (db is now
        a method param). Pure read + payload construction. The actual
        INSERTs run inside the caller's queue callback.

        v0.4.22 soak-gate Day 1 fix (refine-after-rollback UX defect): in
        addition to the new branch kwargs, this method now also returns
        ``seed_turn_kwargs`` for an initial ``RefinementTurn`` on the new
        branch. The seed turn copies the content of ``target_turn``
        (the version being rolled back to) so that subsequent
        ``POST /api/refine`` without an explicit ``branch_id`` works
        seamlessly — the latest-branch lookup defaults to the rollback
        branch, the latest-turn lookup finds the seed turn, and refinement
        proceeds from the rolled-back state.

        Args:
            db: Read session (for verifying the to_version exists).
            optimization_id: Target optimization.
            to_version: Version to fork from.

        Returns:
            Frozen RollbackPayload with ``branch_kwargs`` + ``seed_turn_kwargs``
            ready to splat into RefinementBranch + RefinementTurn constructors.

        Raises:
            ValueError if `to_version` does not exist for `optimization_id`.
        """
        import uuid as _uuid

        from app.services.refinement_context import RollbackPayload

        # Verify the to_version exists
        verify_stmt = select(RefinementTurn).where(
            RefinementTurn.optimization_id == optimization_id,
            RefinementTurn.version == to_version,
        ).limit(1)
        result = await db.execute(verify_stmt)
        target_turn = result.scalar_one_or_none()
        if target_turn is None:
            raise ValueError(
                f"Cannot rollback: version {to_version} not found for "
                f"optimization {optimization_id}"
            )

        # Build kwargs for the new branch (parent_branch_id is the current
        # latest branch at to_version)
        parent_branch_stmt = select(RefinementBranch).where(
            RefinementBranch.id == target_turn.branch_id,
        )
        parent_result = await db.execute(parent_branch_stmt)
        parent_branch = parent_result.scalar_one_or_none()
        if parent_branch is None:
            raise ValueError(
                f"Cannot rollback: parent branch {target_turn.branch_id} not found"
            )

        new_branch_id = str(_uuid.uuid4())
        branch_kwargs = {
            "id": new_branch_id,
            "optimization_id": optimization_id,
            "parent_branch_id": parent_branch.id,
            "forked_at_version": to_version,
        }

        # v0.4.22 fix: seed an initial turn on the new branch with the
        # rolled-back content. Snapshot the fields synchronously from the
        # attached ``target_turn`` ORM row (we're still inside the read
        # session here). The new branch's seed turn is version 1 — branches
        # have independent version sequences (RefinementTurn unique key is
        # branch_id + version, NOT optimization_id + version).
        seed_turn_kwargs = {
            "id": str(_uuid.uuid4()),
            "optimization_id": optimization_id,
            "branch_id": new_branch_id,
            "version": 1,
            "parent_version": None,
            # Use a clean user-facing label — version=1 falls through the
            # ``RefinementTurnCard.svelte`` "version > 1" gate so this only
            # appears in detail views, but we still keep it concise and
            # avoid leaking the parent branch UUID into the UI.
            "refinement_request": f"Rollback to v{to_version}",
            "prompt": target_turn.prompt,
            "scores": target_turn.scores,
            "deltas": target_turn.deltas,
            "deltas_from_original": target_turn.deltas_from_original,
            "strategy_used": target_turn.strategy_used,
            "suggestions": target_turn.suggestions,
            "trace_id": target_turn.trace_id,
        }

        return RollbackPayload(
            branch_kwargs=branch_kwargs,
            seed_turn_kwargs=seed_turn_kwargs,
        )

    async def get_branches(
        self,
        db: AsyncSession,
        optimization_id: str,
    ) -> list[RefinementBranch]:
        """Return all branches for an optimization, ordered by created_at ASC.

        Cycle 2: takes `db` as method-level parameter (drops `self.db`).
        Pure read; no writes.
        """
        stmt = select(RefinementBranch).where(
            RefinementBranch.optimization_id == optimization_id,
        ).order_by(RefinementBranch.created_at.asc())
        result = await db.execute(stmt)
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
        score_deltas: dict[str, float] | None = None,
        score_trajectory: str = "first turn",
    ) -> list[dict[str, str]]:
        """Generate 3 actionable refinement suggestions via Haiku.

        Args:
            optimized_prompt: The current optimized prompt.
            scores: Score dimensions as a dict.
            weaknesses: Weaknesses from the analyzer.
            strategy: Strategy name used.
            score_deltas: Deltas from previous turn (if any).
            score_trajectory: "improving", "degrading", "oscillating", or "first turn".

        Returns:
            List of 3 suggestion dicts: [{text: str, source: str}].
        """
        deltas_str = "first turn — no previous deltas"
        if score_deltas:
            deltas_str = ", ".join(
                f"{dim}: {v:+.1f}" for dim, v in score_deltas.items()
            )

        suggest_msg = self.prompt_loader.render("suggest.md", {
            "optimized_prompt": optimized_prompt,
            "scores": json.dumps(scores, indent=2),
            "weaknesses": ", ".join(weaknesses) if weaknesses else "none identified",
            "strategy_used": strategy,
            "score_deltas": deltas_str,
            "score_trajectory": score_trajectory,
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

