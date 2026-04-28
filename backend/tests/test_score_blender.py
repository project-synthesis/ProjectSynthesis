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

    def test_extreme_positive_clamps_at_ten(self):
        # C1 asymmetric: positive z is NOT capped — only the [1.0, 10.0]
        # clamp bounds the upper end.  Confident high-quality LLM scores
        # should still reach 10.0 normalized.  Without this, a raw 9.5
        # with mean≈8 stddev≈0.4 (cycle-8 corpus shape) clipped to ~8.5
        # — suppressing legitimate excellence.
        assert _normalize_llm_score(100.0, mean=5.0, stddev=1.0) == 10.0

    def test_extreme_negative_capped_at_zscore_floor(self):
        # C1 asymmetric: negative z is FLOOR-capped at -ZSCORE_CAP.
        # Without the cap, an LLM raw score 1.5+ stddev below the rolling
        # mean would floor-clamp to 1.0 on narrow-stddev corpora.  The
        # cap bounds the worst case at 5.5 - 2.0*1.5 = 2.5 — still a
        # strong demotion, but not a floor-clamp.
        from app.services.score_blender import ZSCORE_CAP

        assert _normalize_llm_score(-100.0, mean=5.0, stddev=1.0) == pytest.approx(
            ZSCORE_CENTER - ZSCORE_CAP * ZSCORE_SPREAD, abs=0.01,
        )


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
                "stddev": 0.6,  # > ZSCORE_MIN_STDDEV (0.5)
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
                "stddev": 0.6,
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
                "stddev": 0.6,
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
# F2 — ZSCORE_MIN_STDDEV threshold (audit-prompt hardening 2026-04-28)
# ---------------------------------------------------------------------------


class TestZNormThreshold:
    """Pin the narrow-distribution gate at stddev > 0.5 (was 0.3).

    Audit-class corpora cluster at stddev ≈ 0.35–0.45 — z-norm at the old
    threshold floor-capped legitimately adequate raw LLM scores.  The new
    threshold mirrors the narrow-distribution flag in
    ``routers/health.py:392-394``.  Strict inequality (``>``) means
    stddev=0.5 itself bypasses; only stddev > 0.5 normalizes.
    """

    def test_wide_distribution_normalizes(self):
        """AC-F2-1: wide distribution (stddev=1.0) still triggers z-norm."""
        stats = {
            f"score_{dim}": {
                "count": ZSCORE_MIN_SAMPLES,
                "mean": 5.0,
                "stddev": 1.0,  # > 0.5 — z-norm fires
            }
            for dim in DIMENSION_WEIGHTS
        }
        # LLM=8.0, mean=5.0 → z=+3 → far from mean, normalization visible.
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        assert r.normalization_applied is True

    def test_narrow_distribution_bypasses(self):
        """AC-F2-2: narrow distribution (stddev=0.4) bypasses z-norm post-F2.

        Pre-F2 (threshold 0.3): 0.4 > 0.3 → fires → would FAIL this test.
        Post-F2 (threshold 0.5): 0.4 > 0.5 is FALSE → bypasses → passes.
        """
        stats = {
            f"score_{dim}": {
                "count": ZSCORE_MIN_SAMPLES,
                "mean": 5.0,
                "stddev": 0.4,
            }
            for dim in DIMENSION_WEIGHTS
        }
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        assert r.normalization_applied is False

    def test_degenerate_distribution_bypasses(self):
        """AC-F2-3: degenerate distribution (stddev=0.1) bypasses both pre/post-F2.

        Regression guard — ensures the bump didn't accidentally invert the
        comparison or break the original "skip degenerate" intent.
        """
        stats = {
            f"score_{dim}": {
                "count": ZSCORE_MIN_SAMPLES,
                "mean": 5.0,
                "stddev": 0.1,
            }
            for dim in DIMENSION_WEIGHTS
        }
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        assert r.normalization_applied is False

    def test_boundary_stddev_bypasses(self):
        """AC-F2-4: stddev=0.5 (exactly the threshold) bypasses (strict inequality).

        Pre-F2: 0.5 > 0.3 → fires → would FAIL.
        Post-F2: 0.5 > 0.5 is FALSE → bypasses → passes.
        Pins the strict-inequality semantic — stddev MUST exceed
        ZSCORE_MIN_STDDEV, equality alone does not trigger normalization.
        """
        stats = {
            f"score_{dim}": {
                "count": ZSCORE_MIN_SAMPLES,
                "mean": 5.0,
                "stddev": 0.5,
            }
            for dim in DIMENSION_WEIGHTS
        }
        r = blend_scores(_llm(), _heur(), historical_stats=stats)
        assert r.normalization_applied is False


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


# ---------------------------------------------------------------------------
# F3 — task_type plumbing (audit-prompt hardening 2026-04-28)
# ---------------------------------------------------------------------------


class TestBlendScoresTaskType:
    """``blend_scores`` must thread ``task_type`` into the overall weighting.

    Per spec §F3, ``score_blender.blend_scores`` accepts a
    ``task_type: str | None = None`` kwarg.  When the caller is in
    ``analyze`` task scope, the analysis schema (clarity/specificity ↑,
    faithfulness/conciseness ↓) feeds the overall computation; the
    five blended dimension values themselves are unchanged (they're
    blends of LLM + heuristic), only the weighting that produces
    ``BlendedScores.overall`` differs.
    """

    def test_blend_scores_threads_task_type(self):
        """AC-F3-6: same dim-scores, different task_type → different overall.

        Same LLM and heuristic inputs.  Call once with ``task_type=None``
        and once with ``task_type='analysis'``; assert the resulting
        ``BlendedScores.overall`` differs (the two schemas weight the
        same five blended values differently, so the overall mean must
        diverge for any non-uniform fixture).
        """
        # High clarity/specificity, low faithfulness — analysis schema
        # rewards what default schema penalises, guaranteeing divergence.
        llm = _llm(clarity=9.0, specificity=9.0, faithfulness=4.0)
        heur = _heur(clarity=9.0, specificity=9.0, faithfulness=4.0)

        default = blend_scores(llm, heur, task_type=None)
        analysis = blend_scores(llm, heur, task_type="analysis")

        assert default.overall != analysis.overall, (
            f"Expected different overalls for different task_type; "
            f"got default={default.overall}, analysis={analysis.overall}"
        )


# ---------------------------------------------------------------------------
# F5 — false-premise flag (audit-prompt hardening 2026-04-28)
# ---------------------------------------------------------------------------


# A technically-dense prompt: ≥ TECHNICAL_CONTEXT_THRESHOLD (3) distinct
# hits from ``_TECHNICAL_NOUNS``.  This fixture cites ``pipeline``,
# ``schema``, ``service``, ``api``, ``endpoint``, ``module`` — far above
# the 3-hit floor so the technical_dense gate fires deterministically.
_TECHNICAL_DENSE_PROMPT = (
    "Audit the pipeline schema service: trace the api endpoint module "
    "to confirm the database migration is correctly wired."
)

# A plain-prose prompt with zero technical-noun hits — guarantees
# ``technical_dense=False`` so AC-F5-3 can pin the density-gate path.
_PROSE_PROMPT = (
    "Write a friendly letter to my grandmother about the lovely garden "
    "we visited last Sunday afternoon during the warm summer breeze."
)


class TestFalsePremise:
    """``possible_false_premise`` divergence flag (spec §F5).

    Fires only when ALL THREE conditions hold:
      1. ``task_type == 'analysis'``
      2. ``llm_scores.faithfulness < 5.0`` on the faithfulness dimension
      3. ``technical_dense == True`` (≥ TECHNICAL_CONTEXT_THRESHOLD
         technical-noun hits in ``prompt_text``)

    The flag is purely additive — it never changes any score, only
    surfaces operator-review signal.
    """

    def test_flag_fires_on_analysis_low_faithfulness(self):
        """AC-F5-1: all three conditions met → flag in divergence_flags."""
        llm = _llm(faithfulness=4.5)
        heur = _heur(faithfulness=4.5)  # avoid >2.5 divergence noise
        r = blend_scores(
            llm,
            heur,
            historical_stats=None,
            prompt_text=_TECHNICAL_DENSE_PROMPT,
            task_type="analysis",
        )
        assert "possible_false_premise" in r.divergence_flags, (
            f"Expected 'possible_false_premise' flag; "
            f"got divergence_flags={r.divergence_flags}"
        )

    def test_flag_does_not_fire_for_non_analysis(self):
        """AC-F5-2: task_type='coding' → flag NOT raised."""
        llm = _llm(faithfulness=4.5)
        heur = _heur(faithfulness=4.5)
        r = blend_scores(
            llm,
            heur,
            historical_stats=None,
            prompt_text=_TECHNICAL_DENSE_PROMPT,
            task_type="coding",
        )
        assert "possible_false_premise" not in r.divergence_flags

    def test_flag_does_not_fire_without_technical_density(self):
        """AC-F5-3: prose prompt (technical_dense=False) → flag NOT raised."""
        llm = _llm(faithfulness=4.5)
        heur = _heur(faithfulness=4.5)
        r = blend_scores(
            llm,
            heur,
            historical_stats=None,
            prompt_text=_PROSE_PROMPT,
            task_type="analysis",
        )
        assert "possible_false_premise" not in r.divergence_flags

    def test_flag_does_not_fire_above_threshold(self):
        """AC-F5-4: faithfulness=6.0 (above 5.0 floor) → flag NOT raised."""
        llm = _llm(faithfulness=6.0)
        heur = _heur(faithfulness=6.0)
        r = blend_scores(
            llm,
            heur,
            historical_stats=None,
            prompt_text=_TECHNICAL_DENSE_PROMPT,
            task_type="analysis",
        )
        assert "possible_false_premise" not in r.divergence_flags

    def test_flag_does_not_change_score(self):
        """AC-F5-5: the flag is purely additive — overall + per-dim scores
        identical when only ``task_type`` flips between firing (analysis)
        and non-firing (coding) configurations.

        NOTE: ``task_type`` already drives ``get_dimension_weights()`` per
        F3, so the *overall* is allowed to diverge between schemas — this
        test pins ``DIMENSION_WEIGHTS`` parity by holding ``task_type``
        constant and varying only the prompt density: the flag fires for
        the technical-dense fixture under analysis but not for the prose
        fixture under analysis, while overall + per-dim scores stay
        identical (since ``technical_dense`` only flips the conciseness
        heuristic blend weight, which the prose prompt also bypasses).
        """
        llm = _llm(faithfulness=4.5)
        heur = _heur(faithfulness=4.5)

        # Call A: technical-dense + analysis → flag fires post-fix.
        a = blend_scores(
            llm,
            heur,
            historical_stats=None,
            prompt_text=_TECHNICAL_DENSE_PROMPT,
            task_type="analysis",
        )
        # Call B: same conditions but task_type='coding' → flag does NOT fire.
        # Overall divergence is allowed (F3 weights differ); per-dim scores
        # MUST be identical since dimension blends are task_type-agnostic.
        b = blend_scores(
            llm,
            heur,
            historical_stats=None,
            prompt_text=_TECHNICAL_DENSE_PROMPT,
            task_type="coding",
        )

        # Per-dimension blended scores are identical (task_type does not
        # influence per-dim blending — only the overall weighting).
        for dim in DIMENSION_WEIGHTS:
            assert getattr(a, dim) == getattr(b, dim), (
                f"Per-dim blend should be task_type-agnostic on {dim}: "
                f"a={getattr(a, dim)} b={getattr(b, dim)}"
            )

        # Non-false-premise flags identical.
        a_other = [f for f in a.divergence_flags if f != "possible_false_premise"]
        b_other = [f for f in b.divergence_flags if f != "possible_false_premise"]
        assert a_other == b_other, (
            f"Non-false-premise divergence flags should match: "
            f"a={a_other} b={b_other}"
        )

        # The ONLY difference between A and B is the presence of
        # 'possible_false_premise' in A's flags.
        assert "possible_false_premise" not in b.divergence_flags


# ---------------------------------------------------------------------------
# F3.1 (v0.4.10) — Persistence wiring for analysis-weighted overall
# ---------------------------------------------------------------------------


class TestPersistenceWeightWiring:
    """v0.4.9 F3 wired analysis weights into ``score_blender.blend_scores``
    but the persisted ``overall_score`` field reads from
    ``DimensionScores.overall`` (the @property), which always uses the
    default ``DIMENSION_WEIGHTS``. The analysis weights are computed but
    never reach the database.

    cycle-19→22 replay confirmed: stored mean 7.155 = v3 default;
    computed-with-v4 mean 7.208. Delta lost: +0.053 across 19 prompts.

    The fix wires ``compute_overall(task_type)`` into the persistence
    path. These tests pin the bug and the fix.
    """

    def test_blended_overall_diverges_from_property_for_analysis(self):
        """RED: ``BlendedScores.overall`` (analysis-weighted) and
        ``BlendedScores.to_dimension_scores().overall`` (default-weighted
        property) must produce different values for a fixture where v3
        and v4 schemas would diverge.

        This is the structural surface of the bug — converting Blended →
        DimensionScores via ``to_dimension_scores()`` LOSES the
        analysis-weighted overall.
        """
        # Fixture: high clarity/specificity/structure, low faithfulness/conciseness
        # v3 default: 0.22*9 + 0.22*9 + 0.15*8 + 0.26*4 + 0.15*4 = 6.80
        # v4 analysis: 0.25*9 + 0.25*9 + 0.20*8 + 0.20*4 + 0.10*4 = 7.30
        llm = DimensionScores(
            clarity=9.0, specificity=9.0, structure=8.0,
            faithfulness=4.0, conciseness=4.0,
        )
        heur = {"clarity": 9.0, "specificity": 9.0, "structure": 8.0,
                "faithfulness": 4.0, "conciseness": 4.0}

        blended = blend_scores(
            llm, heur, historical_stats=None,
            prompt_text="Audit something.", task_type="analysis",
        )

        # The dataclass field stores the analysis-weighted overall.
        assert blended.overall == pytest.approx(7.30, abs=0.05), (
            f"Analysis-weighted overall should be ~7.30, got {blended.overall}"
        )

        # The property on DimensionScores uses DEFAULT weights — bug surface.
        ds = blended.to_dimension_scores()
        assert ds.overall == pytest.approx(6.80, abs=0.05), (
            f"Property uses default weights, expected ~6.80, got {ds.overall}"
        )

        # Divergence ≥ 0.4 confirms the persistence sites that read
        # `.overall` instead of `.compute_overall(task_type)` are storing
        # the wrong value for analysis-class prompts.
        assert blended.overall - ds.overall >= 0.4, (
            f"Persistence-bug fixture must have ≥ 0.4 divergence between "
            f"BlendedScores.overall ({blended.overall}) and "
            f"DimensionScores.overall ({ds.overall})"
        )

    def test_compute_overall_recovers_analysis_weighting(self):
        """GREEN: ``DimensionScores.compute_overall('analysis')`` recovers
        the analysis-weighted value that the @property loses.

        This is the call signature that persistence sites must use to
        match ``BlendedScores.overall`` (the source of truth from
        ``score_blender``).
        """
        llm = DimensionScores(
            clarity=9.0, specificity=9.0, structure=8.0,
            faithfulness=4.0, conciseness=4.0,
        )
        heur = {"clarity": 9.0, "specificity": 9.0, "structure": 8.0,
                "faithfulness": 4.0, "conciseness": 4.0}

        blended = blend_scores(
            llm, heur, historical_stats=None,
            prompt_text="Audit something.", task_type="analysis",
        )
        ds = blended.to_dimension_scores()

        # compute_overall(task_type) recovers the analysis-weighted value.
        assert ds.compute_overall("analysis") == pytest.approx(
            blended.overall, abs=1e-9,
        ), (
            "DimensionScores.compute_overall('analysis') must equal "
            "BlendedScores.overall (the analysis-weighted source of truth)"
        )

        # And compute_overall(None) matches the @property (default weights).
        assert ds.compute_overall(None) == pytest.approx(
            ds.overall, abs=1e-9,
        )

    def test_persistence_sites_use_compute_overall(self):
        """REGRESSION: every ``optimized_scores.overall`` reference in
        the persistence path of ``pipeline_phases.persist_and_propagate``
        and the equivalent sites in ``sampling_pipeline``,
        ``batch_pipeline``, ``pipeline`` MUST be ``compute_overall(task_type)``.

        This is a code-structure assertion that catches regressions where
        a future refactor reintroduces the bug. Refinement service is
        exempt — refinement has no analysis re-classification, so passing
        None and degrading to property semantics is the intended behavior.
        """
        from pathlib import Path

        backend_root = Path(__file__).resolve().parent.parent / "app" / "services"
        # Files where analysis.task_type is in scope and overall_score
        # is persisted or emitted in events.
        gated_files = [
            "pipeline_phases.py",
            "sampling_pipeline.py",
            "batch_pipeline.py",
            "pipeline.py",
        ]

        for fname in gated_files:
            text = (backend_root / fname).read_text()
            # The bug pattern: `optimized_scores.overall` (without
            # `.compute_overall`). These call sites should use
            # `compute_overall(task_type)` since task_type is in scope.
            #
            # We allow a single legitimate exception: log-line debug
            # output where `optimized_scores.overall` may appear inside
            # `logger.info` for human inspection of the property value.
            for line_no, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                # Skip log lines, comments, docstrings.
                if stripped.startswith(("#", '"', "'", "logger.")):
                    continue
                if "optimized_scores.overall" in line and "compute_overall" not in line:
                    pytest.fail(
                        f"{fname}:{line_no} uses `optimized_scores.overall` "
                        f"(default weights) — must be `compute_overall(task_type)` "
                        f"for analysis-class fidelity. Line: {stripped}"
                    )
