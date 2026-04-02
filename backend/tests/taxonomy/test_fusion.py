"""Unit tests for composite embedding fusion module.

Covers:
1. PhaseWeights sum to 1.0
2. PhaseWeights floor enforcement (no weight below WEIGHT_FLOOR)
3. Default profiles exist for each phase
4. CompositeQuery.fuse() produces unit vector
5. Zero signals degrade gracefully
6. adapt_weights converges toward successful profile
7. decay_toward_defaults drifts back to defaults
8. PhaseWeights.from_dict / to_dict round-trip
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.taxonomy.fusion import (
    ADAPTATION_ALPHA,
    DECAY_RATE,
    WEIGHT_FLOOR,
    CompositeQuery,
    PhaseWeights,
    adapt_weights,
    build_composite_query,
    decay_toward_defaults,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DIM = 384


def _rand_unit(dim: int = DIM, seed: int = 42) -> np.ndarray:
    rng = np.random.RandomState(seed)
    v = rng.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _zero(dim: int = DIM) -> np.ndarray:
    return np.zeros(dim, dtype=np.float32)


# ---------------------------------------------------------------------------
# PhaseWeights: sum and normalization
# ---------------------------------------------------------------------------


class TestPhaseWeightsSum:
    """PhaseWeights.total and normalization invariants."""

    def test_default_profiles_sum_to_one(self):
        """All default phase profiles should sum to 1.0."""
        for phase in ("analysis", "optimization", "pattern_injection", "scoring"):
            pw = PhaseWeights.for_phase(phase)
            assert abs(pw.total - 1.0) < 1e-6, f"{phase}: total={pw.total}"

    def test_custom_weights_total(self):
        pw = PhaseWeights(0.1, 0.2, 0.3, 0.4)
        assert abs(pw.total - 1.0) < 1e-6

    def test_non_normalized_total(self):
        pw = PhaseWeights(0.5, 0.5, 0.5, 0.5)
        assert abs(pw.total - 2.0) < 1e-6


# ---------------------------------------------------------------------------
# PhaseWeights: floor enforcement
# ---------------------------------------------------------------------------


class TestPhaseWeightsFloor:
    """enforce_floor guarantees no weight below WEIGHT_FLOOR and sum=1."""

    def test_already_above_floor(self):
        pw = PhaseWeights.for_phase("analysis")
        enforced = pw.enforce_floor()
        assert abs(enforced.total - 1.0) < 1e-6
        for w in (enforced.w_topic, enforced.w_transform, enforced.w_output, enforced.w_pattern):
            assert w >= WEIGHT_FLOOR - 1e-9

    def test_one_weight_below_floor(self):
        pw = PhaseWeights(0.90, 0.01, 0.05, 0.04)
        enforced = pw.enforce_floor()
        assert abs(enforced.total - 1.0) < 1e-6
        assert enforced.w_transform >= WEIGHT_FLOOR - 1e-9
        assert enforced.w_pattern >= WEIGHT_FLOOR - 1e-9

    def test_all_weights_zero(self):
        pw = PhaseWeights(0.0, 0.0, 0.0, 0.0)
        enforced = pw.enforce_floor()
        # Should fall back to equal split
        assert abs(enforced.total - 1.0) < 1e-6

    def test_negative_weights_clamped(self):
        pw = PhaseWeights(-0.5, 0.8, 0.5, 0.2)
        enforced = pw.enforce_floor()
        assert abs(enforced.total - 1.0) < 1e-6
        for w in (enforced.w_topic, enforced.w_transform, enforced.w_output, enforced.w_pattern):
            assert w >= WEIGHT_FLOOR - 1e-9


# ---------------------------------------------------------------------------
# PhaseWeights: default profiles
# ---------------------------------------------------------------------------


class TestPhaseWeightsDefaults:
    """Default profiles exist for all known phases."""

    @pytest.mark.parametrize("phase", ["analysis", "optimization", "pattern_injection", "scoring"])
    def test_known_phase(self, phase: str):
        pw = PhaseWeights.for_phase(phase)
        assert abs(pw.total - 1.0) < 1e-6

    def test_unknown_phase_falls_back(self):
        pw = PhaseWeights.for_phase("nonexistent")
        expected = PhaseWeights.for_phase("optimization")
        assert pw.w_topic == expected.w_topic
        assert pw.w_transform == expected.w_transform


# ---------------------------------------------------------------------------
# PhaseWeights: serialization
# ---------------------------------------------------------------------------


class TestPhaseWeightsSerialization:
    """from_dict / to_dict round-trip."""

    def test_round_trip(self):
        original = PhaseWeights(0.30, 0.25, 0.20, 0.25)
        d = original.to_dict()
        restored = PhaseWeights.from_dict(d)
        assert abs(restored.w_topic - original.w_topic) < 1e-9
        assert abs(restored.w_transform - original.w_transform) < 1e-9
        assert abs(restored.w_output - original.w_output) < 1e-9
        assert abs(restored.w_pattern - original.w_pattern) < 1e-9

    def test_to_dict_keys(self):
        pw = PhaseWeights.for_phase("analysis")
        d = pw.to_dict()
        assert set(d.keys()) == {"w_topic", "w_transform", "w_output", "w_pattern"}

    def test_from_dict_with_floats(self):
        d = {"w_topic": 0.1, "w_transform": 0.2, "w_output": 0.3, "w_pattern": 0.4}
        pw = PhaseWeights.from_dict(d)
        assert abs(pw.total - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# CompositeQuery.fuse: unit vector output
# ---------------------------------------------------------------------------


class TestCompositeQueryFuse:
    """fuse() produces a unit vector from weighted signals."""

    def test_all_signals_present(self):
        cq = CompositeQuery(
            topic=_rand_unit(seed=1),
            transformation=_rand_unit(seed=2),
            output=_rand_unit(seed=3),
            pattern=_rand_unit(seed=4),
        )
        weights = PhaseWeights.for_phase("optimization")
        fused = cq.fuse(weights)
        norm = float(np.linalg.norm(fused))
        assert abs(norm - 1.0) < 1e-5, f"Expected unit vector, got norm={norm}"

    def test_single_signal_returns_unit(self):
        topic = _rand_unit(seed=10)
        cq = CompositeQuery(
            topic=topic,
            transformation=_zero(),
            output=_zero(),
            pattern=_zero(),
        )
        weights = PhaseWeights.for_phase("analysis")
        fused = cq.fuse(weights)
        norm = float(np.linalg.norm(fused))
        assert abs(norm - 1.0) < 1e-5
        # Should be identical to the input topic (only signal present)
        cosine = float(np.dot(fused, topic))
        assert cosine > 0.999

    def test_dtype_is_float32(self):
        cq = CompositeQuery(
            topic=_rand_unit(seed=1),
            transformation=_rand_unit(seed=2),
            output=_zero(),
            pattern=_zero(),
        )
        fused = cq.fuse(PhaseWeights.for_phase("optimization"))
        assert fused.dtype == np.float32


# ---------------------------------------------------------------------------
# CompositeQuery.fuse: zero-signal graceful degradation
# ---------------------------------------------------------------------------


class TestCompositeQueryZeroDegradation:
    """Zero signals drop out gracefully from the fusion."""

    def test_all_zero_returns_zero(self):
        cq = CompositeQuery(
            topic=_zero(),
            transformation=_zero(),
            output=_zero(),
            pattern=_zero(),
        )
        fused = cq.fuse(PhaseWeights.for_phase("analysis"))
        assert float(np.linalg.norm(fused)) < 1e-9

    def test_two_signals_dominate(self):
        topic = _rand_unit(seed=1)
        pattern = _rand_unit(seed=4)
        cq = CompositeQuery(
            topic=topic,
            transformation=_zero(),
            output=_zero(),
            pattern=pattern,
        )
        weights = PhaseWeights(0.25, 0.25, 0.25, 0.25)
        fused = cq.fuse(weights)
        norm = float(np.linalg.norm(fused))
        assert abs(norm - 1.0) < 1e-5
        # Should be a blend of topic and pattern only (equal weight)
        # The fused vector should have positive cosine with both inputs
        assert float(np.dot(fused, topic)) > 0
        assert float(np.dot(fused, pattern)) > 0

    def test_weight_redistribution(self):
        """When one signal is zero, its weight shifts to the remaining signals."""
        topic = _rand_unit(seed=1)
        # Only topic present — regardless of initial weight distribution,
        # the fused result should be the topic itself.
        cq = CompositeQuery(
            topic=topic,
            transformation=_zero(),
            output=_zero(),
            pattern=_zero(),
        )
        # Give topic only 10% weight — but since others are zero,
        # topic should absorb 100%.
        weights = PhaseWeights(0.10, 0.30, 0.30, 0.30)
        fused = cq.fuse(weights)
        cosine = float(np.dot(fused, topic))
        assert cosine > 0.999


# ---------------------------------------------------------------------------
# adapt_weights: convergence toward successful profile
# ---------------------------------------------------------------------------


class TestAdaptWeights:
    """adapt_weights EMA toward a successful profile."""

    def test_single_step_moves_toward_target(self):
        current = PhaseWeights.for_phase("analysis")
        successful = PhaseWeights.for_phase("scoring")
        adapted = adapt_weights(current, successful)

        # Each weight should have moved toward the successful profile
        # w_topic: 0.60 should decrease toward 0.15
        assert adapted.w_topic < current.w_topic
        # w_output: 0.10 should increase toward 0.45
        assert adapted.w_output > current.w_output

    def test_convergence_over_many_steps(self):
        """After many EMA steps, weights should approximately match the target."""
        current = PhaseWeights.for_phase("analysis")
        target = PhaseWeights.for_phase("scoring")

        for _ in range(500):
            current = adapt_weights(current, target, alpha=0.05)

        assert abs(current.w_topic - target.w_topic) < 0.02
        assert abs(current.w_transform - target.w_transform) < 0.02
        assert abs(current.w_output - target.w_output) < 0.02
        assert abs(current.w_pattern - target.w_pattern) < 0.02

    def test_result_sums_to_one(self):
        current = PhaseWeights(0.4, 0.3, 0.2, 0.1)
        successful = PhaseWeights(0.1, 0.2, 0.3, 0.4)
        adapted = adapt_weights(current, successful)
        assert abs(adapted.total - 1.0) < 1e-6

    def test_result_respects_floor(self):
        current = PhaseWeights(0.97, 0.01, 0.01, 0.01)
        successful = PhaseWeights(0.97, 0.01, 0.01, 0.01)
        adapted = adapt_weights(current, successful)
        for w in (adapted.w_topic, adapted.w_transform, adapted.w_output, adapted.w_pattern):
            assert w >= WEIGHT_FLOOR - 1e-9


# ---------------------------------------------------------------------------
# decay_toward_defaults: drift back
# ---------------------------------------------------------------------------


class TestDecayTowardDefaults:
    """decay_toward_defaults drifts weights back to phase defaults."""

    def test_single_step_moves_toward_defaults(self):
        current = PhaseWeights(0.25, 0.25, 0.25, 0.25)  # uniform, not default (analysis default is 0.60/0.15/0.10/0.15)
        decayed = decay_toward_defaults(current, "analysis")

        # w_topic should increase toward 0.60
        assert decayed.w_topic > current.w_topic
        # w_transform should decrease toward 0.15
        # (might not decrease if floor enforcement re-normalizes; check direction)

    def test_convergence_over_many_steps(self):
        """After many decay steps, weights should approximately match defaults."""
        current = PhaseWeights(0.25, 0.25, 0.25, 0.25)
        defaults = PhaseWeights.for_phase("optimization")

        for _ in range(1000):
            current = decay_toward_defaults(current, "optimization", rate=0.01)

        assert abs(current.w_topic - defaults.w_topic) < 0.02
        assert abs(current.w_transform - defaults.w_transform) < 0.02
        assert abs(current.w_output - defaults.w_output) < 0.02
        assert abs(current.w_pattern - defaults.w_pattern) < 0.02

    def test_result_sums_to_one(self):
        current = PhaseWeights(0.1, 0.2, 0.3, 0.4)
        decayed = decay_toward_defaults(current, "scoring")
        assert abs(decayed.total - 1.0) < 1e-6

    def test_at_defaults_stays_at_defaults(self):
        defaults = PhaseWeights.for_phase("analysis")
        decayed = decay_toward_defaults(defaults, "analysis")
        assert abs(decayed.w_topic - defaults.w_topic) < 0.01
        assert abs(decayed.w_transform - defaults.w_transform) < 0.01


# ---------------------------------------------------------------------------
# build_composite_query (async integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_composite_query_all_zeros():
    """With empty taxonomy, all signals except topic should be zero."""
    from unittest.mock import AsyncMock, MagicMock, PropertyMock

    topic_vec = _rand_unit(seed=99)

    embedding_svc = MagicMock()
    embedding_svc.aembed_single = AsyncMock(return_value=topic_vec)
    type(embedding_svc).dimension = PropertyMock(return_value=DIM)

    # Empty taxonomy engine
    engine = MagicMock()
    emb_idx = MagicMock()
    emb_idx.size = 0
    engine.embedding_index = emb_idx
    engine._transformation_index = MagicMock()

    # Mock DB session
    db = AsyncMock()

    cq = await build_composite_query("test prompt", embedding_svc, engine, db)
    assert float(np.linalg.norm(cq.topic)) > 0.9
    assert float(np.linalg.norm(cq.transformation)) < 1e-9
    assert float(np.linalg.norm(cq.output)) < 1e-9
    assert float(np.linalg.norm(cq.pattern)) < 1e-9


@pytest.mark.asyncio
async def test_build_composite_query_with_topic_embedding():
    """Pre-computed topic_embedding should be used instead of re-embedding."""
    from unittest.mock import AsyncMock, MagicMock, PropertyMock

    topic_vec = _rand_unit(seed=77)

    embedding_svc = MagicMock()
    embedding_svc.aembed_single = AsyncMock(side_effect=AssertionError("should not be called for topic"))
    type(embedding_svc).dimension = PropertyMock(return_value=DIM)

    engine = MagicMock()
    emb_idx = MagicMock()
    emb_idx.size = 0
    engine.embedding_index = emb_idx
    engine._transformation_index = MagicMock()

    db = AsyncMock()

    cq = await build_composite_query(
        "test prompt", embedding_svc, engine, db, topic_embedding=topic_vec,
    )
    cosine = float(np.dot(cq.topic, topic_vec))
    assert cosine > 0.999


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants have expected values."""

    def test_weight_floor(self):
        assert WEIGHT_FLOOR == 0.05

    def test_adaptation_alpha(self):
        assert ADAPTATION_ALPHA == 0.05

    def test_decay_rate(self):
        assert DECAY_RATE == 0.01

    def test_score_adaptation_min_samples(self):
        from app.services.taxonomy.fusion import SCORE_ADAPTATION_MIN_SAMPLES

        assert SCORE_ADAPTATION_MIN_SAMPLES == 10


# ---------------------------------------------------------------------------
# Score-correlated target computation
# ---------------------------------------------------------------------------


class TestScoreCorrelatedTarget:
    """compute_score_correlated_target: score-weighted optimal profile."""

    def _default_profile(self) -> dict[str, dict[str, float]]:
        return {
            "analysis": {"w_topic": 0.60, "w_transform": 0.15, "w_output": 0.10, "w_pattern": 0.15},
            "optimization": {"w_topic": 0.20, "w_transform": 0.35, "w_output": 0.25, "w_pattern": 0.20},
            "pattern_injection": {"w_topic": 0.25, "w_transform": 0.25, "w_output": 0.20, "w_pattern": 0.30},
            "scoring": {"w_topic": 0.15, "w_transform": 0.20, "w_output": 0.45, "w_pattern": 0.20},
        }

    def test_below_min_samples_returns_none(self):
        """Returns None when fewer than min_samples profiles."""
        from app.services.taxonomy.fusion import compute_score_correlated_target

        profiles = [(7.0, self._default_profile())] * 5
        assert compute_score_correlated_target(profiles, min_samples=10) is None

    def test_empty_list_returns_none(self):
        from app.services.taxonomy.fusion import compute_score_correlated_target

        assert compute_score_correlated_target([]) is None

    def test_uniform_scores_returns_average(self):
        """When all scores are identical, target = unweighted average."""
        from app.services.taxonomy.fusion import compute_score_correlated_target

        profiles = [(5.0, self._default_profile())] * 15
        result = compute_score_correlated_target(profiles)
        assert result is not None
        # With identical profiles, target should match input
        pw = result["analysis"]
        assert abs(pw.w_topic - 0.60) < 0.02
        assert abs(pw.total - 1.0) < 1e-6

    def test_high_scorer_dominates(self):
        """Optimization with highest score should dominate the target."""
        from app.services.taxonomy.fusion import compute_score_correlated_target

        default = self._default_profile()
        alt = {
            "analysis": {"w_topic": 0.20, "w_transform": 0.40, "w_output": 0.20, "w_pattern": 0.20},
            "optimization": {"w_topic": 0.20, "w_transform": 0.35, "w_output": 0.25, "w_pattern": 0.20},
            "pattern_injection": {"w_topic": 0.25, "w_transform": 0.25, "w_output": 0.20, "w_pattern": 0.30},
            "scoring": {"w_topic": 0.15, "w_transform": 0.20, "w_output": 0.45, "w_pattern": 0.20},
        }
        # 9 mediocre with defaults, 1 excellent with alt profile
        profiles = [(3.0, default)] * 9 + [(10.0, alt)]
        result = compute_score_correlated_target(profiles)
        assert result is not None
        # The 10.0 scorer's w_topic=0.20 should pull analysis target below 0.60
        assert result["analysis"].w_topic < 0.55
        # The 10.0 scorer's w_transform=0.40 should pull analysis target above 0.15
        assert result["analysis"].w_transform > 0.20

    def test_multiple_phases_present(self):
        """All phases in input appear in output."""
        from app.services.taxonomy.fusion import compute_score_correlated_target

        profiles = [(7.0, self._default_profile())] * 12
        result = compute_score_correlated_target(profiles)
        assert result is not None
        assert "analysis" in result
        assert "optimization" in result
        assert "pattern_injection" in result
        assert "scoring" in result

    def test_result_sums_to_one(self):
        """Target weights sum to 1.0 for each phase."""
        from app.services.taxonomy.fusion import compute_score_correlated_target

        profiles = [(8.0, self._default_profile())] * 10
        result = compute_score_correlated_target(profiles)
        assert result is not None
        for phase, pw in result.items():
            assert abs(pw.total - 1.0) < 1e-6, f"{phase}: total={pw.total}"

    def test_respects_weight_floor(self):
        """Target weights respect WEIGHT_FLOOR."""
        from app.services.taxonomy.fusion import WEIGHT_FLOOR, compute_score_correlated_target

        # Extreme profile with near-zero weights
        extreme = {
            "analysis": {"w_topic": 0.97, "w_transform": 0.01, "w_output": 0.01, "w_pattern": 0.01},
        }
        profiles = [(9.0, extreme)] * 15
        result = compute_score_correlated_target(profiles)
        assert result is not None
        pw = result["analysis"]
        assert pw.w_transform >= WEIGHT_FLOOR - 1e-9
        assert pw.w_output >= WEIGHT_FLOOR - 1e-9
        assert pw.w_pattern >= WEIGHT_FLOOR - 1e-9

    def test_below_median_excluded(self):
        """Below-median optimizations should not influence the target."""
        from app.services.taxonomy.fusion import compute_score_correlated_target

        low_profile = {
            "analysis": {"w_topic": 0.10, "w_transform": 0.70, "w_output": 0.10, "w_pattern": 0.10},
        }
        high_profile = {
            "analysis": {"w_topic": 0.60, "w_transform": 0.15, "w_output": 0.10, "w_pattern": 0.15},
        }
        # 5 low scorers with extreme profile, 5 high scorers with default
        profiles = [(2.0, low_profile)] * 5 + [(9.0, high_profile)] * 5
        result = compute_score_correlated_target(profiles)
        assert result is not None
        # Target should be dominated by the high-scoring default-like profile,
        # not pulled toward the low-scoring extreme profile
        assert result["analysis"].w_topic > 0.45

    def test_missing_phase_in_some_profiles(self):
        """Profiles with missing phases contribute only to phases they contain."""
        from app.services.taxonomy.fusion import compute_score_correlated_target

        full = self._default_profile()
        partial = {"analysis": {"w_topic": 0.30, "w_transform": 0.30, "w_output": 0.20, "w_pattern": 0.20}}
        # Mix of full and partial profiles at the same score
        profiles = [(7.0, full)] * 6 + [(7.0, partial)] * 6
        result = compute_score_correlated_target(profiles)
        assert result is not None
        assert "analysis" in result
        # optimization appears (from the 6 full profiles that have it)
        assert "optimization" in result
        # analysis target should blend both full and partial contributions
        # (full has w_topic=0.60, partial has w_topic=0.30)
        assert result["analysis"].w_topic < 0.55  # pulled down by partial
