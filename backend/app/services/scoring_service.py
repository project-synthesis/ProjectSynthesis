"""Shared scoring orchestration for passthrough and heuristic-only paths.

Extracts the duplicated scoring sequence from ``tools/save_result.py``
and ``routers/optimize.py`` into a single async function.

**Not used by** ``pipeline.py`` or ``sampling_pipeline.py`` — those have
LLM-scored paths with A/B randomization, intent drift checks, and
taxonomy logging that are structurally different from passthrough scoring.

Documented divergences between internal and passthrough scoring:
  - Historical stats exclusion: internal excludes ["heuristic"],
    passthrough excludes ["heuristic", "hybrid_passthrough"]
  - Scoring mode: internal="hybrid", passthrough="hybrid_passthrough"
  - Error handling: internal propagates (pipeline.py) or falls back
    (sampling_pipeline.py); passthrough always falls back to heuristic

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class PassthroughScoringResult:
    """Result of passthrough scoring orchestration."""

    optimized_scores: dict[str, float]
    original_scores: dict[str, float] | None
    overall: float | None
    deltas: dict[str, float] | None
    scoring_mode: str  # "hybrid_passthrough" | "heuristic" | "skipped"
    divergence_flags: list[str] = field(default_factory=list)


async def score_passthrough(
    *,
    raw_prompt: str | None,
    optimized_prompt: str,
    external_scores: dict[str, float] | None,
    db: AsyncSession,
    scoring_enabled: bool = True,
) -> PassthroughScoringResult:
    """Orchestrate passthrough scoring: heuristic → blend → deltas.

    Shared by ``tools/save_result.py`` and ``routers/optimize.py`` to
    eliminate the 95% duplication between those two call sites.

    Args:
        raw_prompt: Original prompt (for delta computation). None if unavailable.
        optimized_prompt: The optimized prompt text to score.
        external_scores: IDE/external LLM dimension scores (optional).
            Keys: clarity, specificity, structure, faithfulness, conciseness.
            Values clamped to [1.0, 10.0] internally.
        db: Async DB session for historical stats fetch.
        scoring_enabled: From preferences. When False, returns mode="skipped".

    Returns:
        PassthroughScoringResult with all scoring artifacts.
    """
    from app.schemas.pipeline_contracts import DimensionScores
    from app.services.heuristic_scorer import HeuristicScorer
    from app.services.score_blender import blend_scores

    if not scoring_enabled:
        return PassthroughScoringResult(
            optimized_scores={},
            original_scores=None,
            overall=None,
            deltas=None,
            scoring_mode="skipped",
        )

    # 1. Heuristic baseline (always computed)
    heur_optimized = HeuristicScorer.score_prompt(
        optimized_prompt, original=raw_prompt,
    )
    heur_original = HeuristicScorer.score_prompt(raw_prompt) if raw_prompt else {}

    # 2. Fetch historical stats for z-score normalization
    # Exclude heuristic and hybrid_passthrough — passthrough scores are
    # externally provided, not calibrated against internal distribution.
    historical_stats: dict[str, Any] | None = None
    try:
        from app.services.optimization_service import OptimizationService

        opt_svc = OptimizationService(db)
        historical_stats = await opt_svc.get_score_distribution(
            exclude_scoring_modes=["heuristic", "hybrid_passthrough"],
        )
    except Exception as exc:
        logger.debug("Historical stats unavailable for normalization: %s", exc)

    # 3. Blend external scores with heuristic (or use heuristic-only)
    scoring_mode = "heuristic"
    divergence_flags: list[str] = []

    if external_scores:
        # Clamp external scores to valid range
        clean = {k: max(1.0, min(10.0, float(v))) for k, v in external_scores.items()}
        try:
            ide_dims = DimensionScores.from_dict(clean)
            blended_opt = blend_scores(
                ide_dims, heur_optimized, historical_stats,
                prompt_text=optimized_prompt,
            )
            opt_dims = blended_opt.to_dimension_scores()
            scoring_mode = "hybrid_passthrough"
            divergence_flags = blended_opt.divergence_flags or []
        except Exception as exc:
            logger.warning("Hybrid blending failed, falling back to heuristic: %s", exc)
            opt_dims = DimensionScores.from_dict(heur_optimized)
    else:
        # Heuristic-only — no blending, no z-score normalization
        opt_dims = DimensionScores.from_dict(heur_optimized)

    # 4. Original scores (symmetric with optimized path)
    if raw_prompt and heur_original:
        if scoring_mode == "hybrid_passthrough":
            try:
                blended_orig = blend_scores(
                    DimensionScores.from_dict(heur_original),
                    heur_original,
                    historical_stats,
                    prompt_text=raw_prompt,
                )
                orig_dims = blended_orig.to_dimension_scores()
            except Exception:
                orig_dims = DimensionScores.from_dict(heur_original)
        else:
            orig_dims = DimensionScores.from_dict(heur_original)
        original_scores = orig_dims.to_dict()
        deltas = DimensionScores.compute_deltas(orig_dims, opt_dims)
    else:
        original_scores = None
        deltas = None

    return PassthroughScoringResult(
        optimized_scores=opt_dims.to_dict(),
        original_scores=original_scores,
        overall=opt_dims.overall,
        deltas=deltas,
        scoring_mode=scoring_mode,
        divergence_flags=divergence_flags,
    )
