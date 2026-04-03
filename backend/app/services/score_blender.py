"""Hybrid score blending — combines LLM scores with heuristic scores.

Addresses same-family LLM bias (Wataoka et al. 2024) by weighting
model-independent heuristic signals alongside LLM judgments. Applies
z-score normalization when historical stats are available to prevent
score clustering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.schemas.pipeline_contracts import DimensionScores

logger = logging.getLogger(__name__)

# Dimension-specific heuristic weights.
# Higher weight = more trust in the heuristic for that dimension.
HEURISTIC_WEIGHTS: dict[str, float] = {
    "structure": 0.50,      # Heuristic is very reliable (regex detects headers/lists/tags)
    "conciseness": 0.40,    # TTR + filler detection is solid
    "specificity": 0.40,    # Constraint counting works well
    "clarity": 0.30,        # Flesch is limited; LLM judges ambiguity better
    "faithfulness": 0.20,   # Embedding similarity is rough; LLM evaluates intent better
}

DIMENSIONS = ("clarity", "specificity", "structure", "faithfulness", "conciseness")

DIMENSION_WEIGHTS: dict[str, float] = {
    "clarity": 0.25,
    "specificity": 0.25,
    "structure": 0.20,
    "faithfulness": 0.20,
    "conciseness": 0.10,
}

# Z-score normalization parameters
ZSCORE_MIN_SAMPLES = 999999   # Disabled — re-enable after rubric recalibration baseline
ZSCORE_MIN_STDDEV = 0.3       # Skip normalization if stddev is tiny (degenerate data)
ZSCORE_CENTER = 5.5           # Re-center normalized scores around midpoint
ZSCORE_SPREAD = 1.5           # Map 1 stddev to ±1.5 on the 1-10 scale


@dataclass
class BlendedScores:
    """Result of hybrid score blending."""

    clarity: float  # Blended clarity score (1.0-10.0)
    specificity: float  # Blended specificity score (1.0-10.0)
    structure: float  # Blended structure score (1.0-10.0)
    faithfulness: float  # Blended faithfulness score (1.0-10.0)
    conciseness: float  # Blended conciseness score (1.0-10.0)
    overall: float  # Weighted overall score (1.0-10.0)
    scoring_mode: str = "hybrid"  # Scoring method: 'hybrid', 'llm_only', or 'heuristic_only'
    divergence_flags: list[str] = field(default_factory=list)  # Dimensions where LLM and heuristic disagree by >2.5
    raw_llm: dict[str, float] = field(default_factory=dict)  # Raw LLM scores before blending
    raw_heuristic: dict[str, float] = field(default_factory=dict)  # Raw heuristic scores before blending
    normalization_applied: bool = False  # Whether z-score normalization was applied

    def to_dimension_scores(self) -> DimensionScores:
        """Convert to pipeline-compatible DimensionScores model."""
        return DimensionScores(
            clarity=self.clarity,
            specificity=self.specificity,
            structure=self.structure,
            faithfulness=self.faithfulness,
            conciseness=self.conciseness,
        )

    def as_dict(self) -> dict[str, float]:
        """Return just the five dimension scores as a dict."""
        return {
            "clarity": self.clarity,
            "specificity": self.specificity,
            "structure": self.structure,
            "faithfulness": self.faithfulness,
            "conciseness": self.conciseness,
        }


def _clamp(value: float, lo: float = 1.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def _normalize_llm_score(
    raw: float,
    mean: float,
    stddev: float,
) -> float:
    """Z-score normalization to break LLM score clustering.

    Re-centers the score around ZSCORE_CENTER (5.5) with a spread factor
    so that:
      - A score 1 stddev above mean → ~7.0 (not inflated to 9.0)
      - A score at mean → 5.5 (not clustered at 8.0)
      - A score 1 stddev below mean → ~4.0 (actually reflects weakness)
    """
    z = (raw - mean) / stddev
    normalized = ZSCORE_CENTER + z * ZSCORE_SPREAD
    return _clamp(normalized)


def blend_scores(
    llm_scores: DimensionScores,
    heuristic_scores: dict[str, float],
    historical_stats: dict[str, dict[str, float]] | None = None,
) -> BlendedScores:
    """Blend LLM and heuristic scores with optional z-score normalization.

    Args:
        llm_scores: Scores from the LLM judge (DimensionScores model).
        heuristic_scores: Scores from HeuristicScorer.score_prompt().
        historical_stats: Optional per-dimension stats from
            optimization_service.get_score_distribution(). Expected shape:
            ``{"score_clarity": {"count": N, "mean": M, "stddev": S}, ...}``

    Returns:
        BlendedScores with per-dimension blended values, divergence flags,
        and raw scores for transparency.
    """
    blended: dict[str, float] = {}
    raw_llm: dict[str, float] = {}
    raw_heur: dict[str, float] = {}
    normalization_applied = False
    divergence_flags: list[str] = []

    for dim in DIMENSIONS:
        llm_raw = getattr(llm_scores, dim)
        heur_raw = heuristic_scores.get(dim, 5.0)  # neutral fallback

        raw_llm[dim] = llm_raw
        raw_heur[dim] = heur_raw

        # Z-score normalization of LLM component
        llm_component = llm_raw
        stat_key = f"score_{dim}"
        if historical_stats and stat_key in historical_stats:
            stats = historical_stats[stat_key]
            count = stats.get("count", 0)
            mean = stats.get("mean", 0.0)
            stddev = stats.get("stddev", 0.0)

            if count >= ZSCORE_MIN_SAMPLES and stddev > ZSCORE_MIN_STDDEV:
                llm_component = _normalize_llm_score(llm_raw, mean, stddev)
                normalization_applied = True
                logger.debug(
                    "Z-score normalization for %s: raw=%.1f mean=%.1f std=%.1f → normalized=%.1f",
                    dim, llm_raw, mean, stddev, llm_component,
                )

        # Weighted blend
        w_h = HEURISTIC_WEIGHTS.get(dim, 0.30)
        w_l = 1.0 - w_h
        blended_val = w_l * llm_component + w_h * heur_raw
        blended[dim] = round(_clamp(blended_val), 1)

        # Divergence detection
        if abs(llm_raw - heur_raw) > 2.5:
            divergence_flags.append(dim)
            logger.info(
                "Score divergence on %s: llm=%.1f heuristic=%.1f (delta=%.1f)",
                dim, llm_raw, heur_raw, abs(llm_raw - heur_raw),
            )

    # Overall: weighted mean — conciseness downweighted to prevent compression
    overall = round(
        sum(blended[dim] * DIMENSION_WEIGHTS[dim] for dim in DIMENSIONS),
        2,
    )

    if divergence_flags:
        logger.warning(
            "Hybrid scoring divergence on %d dimension(s): %s",
            len(divergence_flags), divergence_flags,
        )

    return BlendedScores(
        clarity=blended["clarity"],
        specificity=blended["specificity"],
        structure=blended["structure"],
        faithfulness=blended["faithfulness"],
        conciseness=blended["conciseness"],
        overall=overall,
        scoring_mode="hybrid",
        divergence_flags=sorted(divergence_flags),
        raw_llm=raw_llm,
        raw_heuristic=raw_heur,
        normalization_applied=normalization_applied,
    )
