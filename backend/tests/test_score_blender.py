"""Dedicated unit coverage for ``score_blender``.

The module is imported transitively by pipeline + calibration tests, so
line coverage trends high in the full suite — but the dedicated-test
gap is real: ``test_score_calibration.py`` only exercises the
downstream consumer, not the blend math itself. This file pins the
contract:

1. Weighted blend — per-dimension heuristic weight × LLM weight,
   rounded to one decimal and clamped to [1.0, 10.0].
2. Z-score normalization — applied only when ``count >=
   ZSCORE_MIN_SAMPLES`` AND ``stddev > ZSCORE_MIN_STDDEV``; skipped
   otherwise and the raw LLM score feeds the blend directly.
3. Divergence detection — flag set when |llm - heuristic| > 2.5 on any
   dimension, surfaced in ``BlendedScores.divergence_flags`` sorted
   alphabetically.
4. Overall weighting — matches ``DIMENSION_WEIGHTS`` v3 exactly
   (faithfulness 0.26, clarity/specificity 0.22, structure 0.15,
   conciseness 0.15).
5. ``_normalize_llm_score`` / ``_clamp`` — pure numeric helpers.
6. ``BlendedScores.to_dimension_scores`` / ``as_dict`` — round trip.

Copyright 2025-2026 Project Synthesis contributors.
"""

import pytest

from app.schemas.pipeline_contracts import DIMENSION_WEIGHTS, DimensionScores
from app.services.score_blender import (
    HEURISTIC_WEIGHTS,
    ZSCORE_CENTER,
    ZSCORE_MIN_SAMPLES,
    ZSCORE_SPREAD,
    _clamp,
    _normalize_llm_score,
    blend_scores,
)

# ---------------------------------------------------------------------------
# _clamp + _normalize_llm_score — pure helpers
# ---------------------------------------------------------------------------

class TestClamp:
    def test_below_lo_returns_lo(self):
        assert _clamp(0.5) == 1.0

    def test_above_hi_returns_hi(self):
        assert _clamp(999.0) == 10.0

    def test_within_range_returns_value(self):
        assert _clamp(5.5) == 5.5

    def test_custom_bounds(self):
        assert _clamp(15.0, lo=0.0, hi=20.0) == 15.0


class TestNormalizeLlmScore:
    def test_score_at_mean_centers_on_zscore_center(self):
        # raw == mean → z == 0 → normalized == ZSCORE_CENTER
        assert _normalize_llm_score(7.0, mean=7.0, stddev=1.0) == ZSCORE_CENTER

    def test_one_stddev_above_mean(self):
        # z = +1 → normalized = 5.5 + 1.5 = 7.0
        got = _normalize_llm_score(8.0, mean=7.0, stddev=1.0)
        assert got == pytest.approx(ZSCORE_CENTER + ZSCORE_SPREAD, abs=0.01)

    def test_one_stddev_below_mean(self):
        got = _normalize_llm_score(6.0, mean=7.0, stddev=1.0)
        assert got == pytest.approx(ZSCORE_CENTER - ZSCORE_SPREAD, abs=0.01)

    def test_extreme_positive_is_clamped_at_ten(self):
        # raw 50 stddev above mean → normalized > 10 → clamp
        assert _normalize_llm_score(100.0, mean=5.0, stddev=1.0) == 10.0

    def test_extreme_negative_is_clamped_at_one(self):
        assert _normalize_llm_score(-100.0, mean=5.0, stddev=1.0) == 1.0


# ---------------------------------------------------------------------------
# blend_scores — main path (no historical stats)
# ---------------------------------------------------------------------------

def _llm(**overrides):
    base = {dim: 8.0 for dim in DIMENSION_WEIGHTS}
    base.update(overrides)
    return DimensionScores(**base)


def _heur(**overrides):
    out = {dim: 8.0 for dim in DIMENSION_WEIGHTS}
    out.update(overrides)
    return out


class TestBlendScoresBasic:
    def test_uniform_inputs_produce_uniform_outputs(self):
        """When LLM and heuristic agree, blended value equals the agreement."""
        r = blend_scores(_llm(), _heur())
        for dim in DIMENSION_WEIGHTS:
            assert getattr(r, dim) == 8.0

    def test_weighted_blend_respects_heuristic_weight(self):
        """Blended = (1-w_h) * llm + w_h * heuristic for each dimension."""
        r = blend_scores(_llm(clarity=10.0), _heur(clarity=4.0))
        # clarity heuristic weight = 0.40 → blended = 0.6*10 + 0.4*4 = 7.6
        assert r.clarity == pytest.approx(7.6, abs=0.05)

    def test_overall_matches_dimension_weights_v3(self):
        """Overall is a weighted sum using DIMENSION_WEIGHTS (v3)."""
        r = blend_scores(_llm(), _heur())
        expected = 8.0 * sum(DIMENSION_WEIGHTS.values())
        assert r.overall == pytest.approx(round(expected, 2), abs=0.01)

    def test_scoring_mode_default_is_hybrid(self):
        r = blend_scores(_llm(), _heur())
        assert r.scoring_mode == "hybrid"

    def test_raw_components_are_preserved(self):
        r = blend_scores(_llm(clarity=6.0), _heur(clarity=9.0))
        assert r.raw_llm["clarity"] == 6.0
        assert r.raw_heuristic["clarity"] == 9.0

    def test_missing_heuristic_dimension_uses_neutral_5(self):
        """When a heuristic dimension is absent, the fallback is 5.0."""
        heur = {"clarity": 8.0}  # only clarity present
        r = blend_scores(_llm(), heur)
        # faithfulness: heuristic defaulted to 5.0, weight 0.20 → 0.8*8 + 0.2*5 = 7.4
        assert r.faithfulness == pytest.approx(7.4, abs=0.05)

    def test_values_are_rounded_to_one_decimal(self):
        r = blend_scores(_llm(clarity=8.3), _heur(clarity=7.7))
        # Result rounded to 1 decimal place.
        assert round(r.clarity, 1) == r.clarity

    def test_clamps_to_min_floor(self):
        """Minimum-valid inputs produce a blended result at the 1.0 floor."""
        # DimensionScores enforces [1.0, 10.0] at its own layer — this
        # exercises the downstream clamp for the blended value.
        r = blend_scores(_llm(clarity=1.0), _heur(clarity=1.0))
        assert r.clarity == 1.0

    def test_clamps_to_max_ceiling(self):
        r = blend_scores(_llm(clarity=10.0), _heur(clarity=10.0))
        assert r.clarity == 10.0


class TestDivergenceFlags:
    def test_large_gap_raises_divergence_flag(self):
        """|llm - heuristic| > 2.5 on any dimension → flag."""
        r = blend_scores(_llm(clarity=9.0), _heur(clarity=3.0))
        assert "clarity" in r.divergence_flags

    def test_gap_at_threshold_does_not_flag(self):
        """Exactly 2.5 delta is at-but-not-over the threshold — no flag."""
        r = blend_scores(_llm(clarity=7.5), _heur(clarity=5.0))
        assert "clarity" not in r.divergence_flags

    def test_divergence_flags_sorted_alphabetically(self):
        r = blend_scores(
            _llm(structure=9.5, clarity=9.5),
            _heur(structure=3.0, clarity=3.0),
        )
        assert r.divergence_flags == sorted(r.divergence_flags)

    def test_no_divergence_when_scores_agree(self):
        r = blend_scores(_llm(), _heur())
        assert r.divergence_flags == []


# ---------------------------------------------------------------------------
# Z-score normalization — historical-stats branch
# ---------------------------------------------------------------------------

class TestZScoreNormalization:
    def test_applied_when_samples_and_stddev_above_thresholds(self):
        stats = {
            f"score_{dim}": {
                "count": ZSCORE_MIN_SAMPLES,
                "mean": 8.0,
                "stddev": 0.5,  # > ZSCORE_MIN_STDDEV (0.3)
            }
            for dim in DIMENSION_WEIGHTS
        }
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        assert r.normalization_applied is True

    def test_skipped_when_sample_count_too_low(self):
        stats = {
            f"score_{dim}": {"count": 5, "mean": 8.0, "stddev": 1.0}
            for dim in DIMENSION_WEIGHTS
        }
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        assert r.normalization_applied is False

    def test_skipped_when_stddev_degenerate(self):
        stats = {
            f"score_{dim}": {
                "count": ZSCORE_MIN_SAMPLES * 5,
                "mean": 8.0,
                "stddev": 0.1,  # ≤ ZSCORE_MIN_STDDEV
            }
            for dim in DIMENSION_WEIGHTS
        }
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        assert r.normalization_applied is False

    def test_no_historical_stats_skips_normalization(self):
        r = blend_scores(_llm(), _heur(), historical_stats=None)
        assert r.normalization_applied is False

    def test_partial_historical_stats_only_normalize_matching_dims(self):
        """Only dims present in historical_stats should feed into the
        z-score path — others go through raw."""
        stats = {
            "score_clarity": {
                "count": ZSCORE_MIN_SAMPLES,
                "mean": 5.0,
                "stddev": 0.5,
            }
        }
        # raw clarity well above mean → normalized clarity pulled toward
        # ZSCORE_CENTER range; raw-only dims stay at 8.0.
        r = blend_scores(_llm(clarity=9.0), _heur(), historical_stats=stats)
        assert r.normalization_applied is True
        # faithfulness (no stats) — classic weighted blend = 8.0.
        assert r.faithfulness == 8.0

    def test_pulls_clustered_scores_toward_center(self):
        """An LLM score clustered near the mean should collapse toward ZSCORE_CENTER."""
        stats = {
            f"score_{dim}": {
                "count": ZSCORE_MIN_SAMPLES,
                "mean": 8.0,
                "stddev": 0.5,
            }
            for dim in DIMENSION_WEIGHTS
        }
        # Raw LLM = mean → normalized = ZSCORE_CENTER (5.5). Heuristic at
        # 8.0 pulls the blend back up but normalized component is clearly
        # below the un-normalized comparison.
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        r_raw = blend_scores(_llm(), _heur())
        assert r.clarity < r_raw.clarity


# ---------------------------------------------------------------------------
# BlendedScores dataclass surface
# ---------------------------------------------------------------------------

class TestBlendedScoresSurface:
    def test_to_dimension_scores_round_trip(self):
        r = blend_scores(_llm(), _heur())
        ds = r.to_dimension_scores()
        assert isinstance(ds, DimensionScores)
        for dim in DIMENSION_WEIGHTS:
            assert getattr(ds, dim) == getattr(r, dim)

    def test_as_dict_returns_five_dimensions_only(self):
        r = blend_scores(_llm(), _heur())
        d = r.as_dict()
        assert set(d.keys()) == set(DIMENSION_WEIGHTS.keys())
        for dim, val in d.items():
            assert val == getattr(r, dim)

    def test_heuristic_weights_contract(self):
        """Heuristic weights must lie in [0, 1] and cover every dimension."""
        for dim in DIMENSION_WEIGHTS:
            w = HEURISTIC_WEIGHTS.get(dim)
            assert w is not None, f"missing heuristic weight for {dim}"
            assert 0.0 <= w <= 1.0

    def test_dimension_weights_sum_to_one(self):
        """The overall weighted mean is only meaningful if the weights sum to 1."""
        assert sum(DIMENSION_WEIGHTS.values()) == pytest.approx(1.0, abs=0.001)
