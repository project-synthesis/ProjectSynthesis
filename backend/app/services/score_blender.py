"""Hybrid score blending — combines LLM scores with heuristic scores.

Addresses same-family LLM bias (Wataoka et al. 2024) by weighting
model-independent heuristic signals alongside LLM judgments. Applies
z-score normalization when historical stats are available to prevent
score clustering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.schemas.pipeline_contracts import (
    DIMENSION_WEIGHTS,
    DimensionScores,
    get_dimension_weights,
)

logger = logging.getLogger(__name__)

# Dimension-specific heuristic weights.
# Higher weight = more trust in the heuristic for that dimension.
HEURISTIC_WEIGHTS: dict[str, float] = {
    "structure": 0.50,      # Heuristic is very reliable (regex detects headers/lists/tags)
    "conciseness": 0.20,    # TTR penalizes domain-term repetition in structured prompts; LLM judges density better
    "specificity": 0.40,    # Constraint counting works well
    "clarity": 0.40,        # Precision signals + ambiguity detection now reliable
    "faithfulness": 0.20,   # Embedding similarity is rough; LLM evaluates intent better
}

DIMENSIONS = tuple(DIMENSION_WEIGHTS.keys())

# Z-score normalization parameters
ZSCORE_MIN_SAMPLES = 30       # Minimum sample count for z-score stability (CLT threshold)
ZSCORE_MIN_STDDEV = 0.5       # Skip normalization on narrow distributions (audits cluster
                              # at stddev≈0.35–0.45 — z-norm there floor-caps adequate
                              # raw scores). Mirrors the narrow-distribution gate at
                              # routers/health.py:392-394.
ZSCORE_CENTER = 5.5           # Re-center normalized scores around midpoint
ZSCORE_SPREAD = 1.5           # Map 1 stddev to ±1.5 on the 1-10 scale
ZSCORE_CAP = 2.0              # |z| ceiling — guards against narrow-stddev amplification (C1)
                              # On a corpus with stddev=0.37 and historical mean=8.58, an LLM
                              # score of 7.1 produces z=-3.97, normalized to 1.0 — flooring the
                              # blended dimension even though 7.1 is "above average" by any
                              # absolute standard.  Capping |z| at 2.0 keeps narrow-distribution
                              # demotions bounded (worst-case dimension floor 5.5-2.0*1.5 = 2.5)
                              # while still letting moderately-bad scores demote meaningfully.
                              # The cap matters more as more cycles complete; once stddev widens
                              # past ~1.0 the cap rarely fires.


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

    C1 (asymmetric): z is FLOOR-capped at ``-ZSCORE_CAP`` (=-2.0) but
    NOT ceiling-capped.  Original C1 capped both ends and accidentally
    suppressed legitimate high-quality outputs — a raw 9.5 with mean≈8
    stddev≈0.4 produces z=3.75, which the symmetric cap clipped at 2.0
    (normalized 8.5) when it should have been 5.5+5.625=11 → clamped to
    10.  The asymmetric cap keeps the original C1 protection (a raw 7.1
    from a "below-average but not catastrophic" LLM call no longer
    floor-clamps to 1.0; it's bounded at 5.5-3.0=2.5) while restoring
    upside on confident high-quality outputs.  Final 1.0–10.0 clamp is
    still applied after ``ZSCORE_CENTER + z*ZSCORE_SPREAD``, so
    extreme positive z values land at 10.0 rather than going unbounded.
    """
    z = (raw - mean) / stddev
    if z < 0:
        z = max(-ZSCORE_CAP, z)
    # Positive z left uncapped (final clamp to [1.0, 10.0] handles overflow).
    normalized = ZSCORE_CENTER + z * ZSCORE_SPREAD
    return _clamp(normalized)


def blend_scores(
    llm_scores: DimensionScores,
    heuristic_scores: dict[str, float],
    historical_stats: dict[str, dict[str, float]] | None = None,
    prompt_text: str | None = None,
    task_type: str | None = None,
) -> BlendedScores:
    """Blend LLM and heuristic scores with optional z-score normalization.

    Args:
        llm_scores: Scores from the LLM judge (DimensionScores model).
        heuristic_scores: Scores from HeuristicScorer.score_prompt().
        historical_stats: Optional per-dimension stats from
            optimization_service.get_score_distribution(). Expected shape:
            ``{"score_clarity": {"count": N, "mean": M, "stddev": S}, ...}``
        prompt_text: Optional optimized-prompt text used by C3 to detect
            technical-prompt density and bump the heuristic conciseness
            weight on prompts where LLM and heuristic systematically
            disagree about info density.

    Returns:
        BlendedScores with per-dimension blended values, divergence flags,
        and raw scores for transparency.
    """
    blended: dict[str, float] = {}
    raw_llm: dict[str, float] = {}
    raw_heur: dict[str, float] = {}
    normalization_applied = False
    divergence_flags: list[str] = []

    # C3: detect technical-prompt density once.  Heuristic conciseness
    # already grants a TTR multiplier when ``_count_technical_nouns(prompt)
    # >= TECHNICAL_CONTEXT_THRESHOLD`` (heuristic_scorer.py:177).  The same
    # gate widens the heuristic's blend weight on conciseness here, on the
    # observation that LLM-as-judge consistently mis-reads structurally
    # dense technical specs as verbose (e.g. cycle 6 prompt 1: heur=8.3
    # but LLM raw≈7.1, blended dropped to 3.9 after z-amplification).
    # Trusting the heuristic more on technical prompts dampens that drift.
    technical_dense = False
    if prompt_text:
        try:
            from app.services.heuristic_scorer import (
                TECHNICAL_CONTEXT_THRESHOLD,
                _count_technical_nouns,
            )
            technical_dense = (
                _count_technical_nouns(prompt_text) >= TECHNICAL_CONTEXT_THRESHOLD
            )
        except Exception:
            logger.debug(
                "C3 technical-density detection failed, using default weights",
                exc_info=True,
            )

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
        # C3: bump heuristic weight on conciseness for technical-dense prompts.
        # 0.20 → 0.35 lifts the heuristic's structural-density signal from
        # "minor adjustment" to "substantial pull" without overwhelming the
        # LLM-as-judge component.  Other dimensions stay at default.
        if dim == "conciseness" and technical_dense:
            w_h = 0.35
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

        # F5: false-premise flag — fires for audit-class prompts where the LLM
        # scorer (with codebase context) rated faithfulness below 5.0 on a
        # technical-dense prompt. The combination signals: surface symbol
        # density may be masking a wrong premise the LLM detected against
        # ground truth.  Purely additive — does not change the score.
        if (
            task_type == "analysis"
            and dim == "faithfulness"
            and llm_raw < 5.0
            and technical_dense
        ):
            divergence_flags.append("possible_false_premise")

    # Overall: weighted mean (faithfulness 0.25, structure 0.15, rest 0.20)
    overall = round(
        sum(blended[dim] * w for dim, w in get_dimension_weights(task_type).items()),
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
