"""Tests for the adaptation engine — feedback → pipeline parameter tuning."""

import os

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from app.services.adaptation_engine import (
    DEFAULT_WEIGHTS,
    MAX_DAMPING,
    WEIGHT_LOWER_BOUND,
    WEIGHT_UPPER_BOUND,
    adjust_weights_from_deltas,
    compute_override_deltas,
    compute_threshold_from_feedback,
)


class TestDefaultWeights:
    def test_sum_to_one(self):
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_five_dimensions(self):
        assert len(DEFAULT_WEIGHTS) == 5


class TestComputeOverrideDeltas:
    def test_basic_override(self):
        feedbacks = [
            {"dimension_overrides": {"clarity_score": 8}, "scores": {"clarity_score": 6}},
        ]
        deltas = compute_override_deltas(feedbacks)
        assert "clarity_score" in deltas
        assert deltas["clarity_score"] > 0  # user says it's better than validator thought

    def test_no_overrides_returns_empty(self):
        feedbacks = [{"dimension_overrides": None, "scores": {}}]
        deltas = compute_override_deltas(feedbacks)
        assert deltas == {}


class TestAdjustWeights:
    def test_no_deltas_returns_defaults(self):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, {}, damping=0.15, min_samples=3)
        assert weights == DEFAULT_WEIGHTS

    def test_sum_to_one_after_adjustment(self):
        deltas = {"clarity_score": 2.0, "faithfulness_score": -1.5}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_weights_within_bounds(self):
        # Extreme deltas
        deltas = {"clarity_score": 10.0, "specificity_score": -10.0}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        for w in weights.values():
            assert WEIGHT_LOWER_BOUND <= w <= WEIGHT_UPPER_BOUND

    def test_damping_limits_shift(self):
        deltas = {"clarity_score": 10.0}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        shift = abs(weights["clarity_score"] - DEFAULT_WEIGHTS["clarity_score"])
        assert shift <= MAX_DAMPING + 0.01  # small epsilon for float math


class TestComputeThreshold:
    def test_default_with_no_feedback(self):
        t = compute_threshold_from_feedback([], default=5.0, bounds=(3.0, 8.0))
        assert t == 5.0

    def test_bounded_low(self):
        # All negative feedback on high-scoring prompts → lower threshold
        feedbacks = [{"rating": -1, "overall_score": 8.0}] * 10
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert t >= 3.0

    def test_bounded_high(self):
        feedbacks = [{"rating": 1, "overall_score": 3.0}] * 10
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert t <= 8.0


h_settings.register_profile("ci", max_examples=200, deadline=5000)
h_settings.register_profile("dev", max_examples=1000, deadline=10000)
h_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))


class TestAdaptationPropertyBased:
    @given(
        deltas=st.dictionaries(
            keys=st.sampled_from(list(DEFAULT_WEIGHTS.keys())),
            values=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False),
            max_size=5,
        )
    )
    def test_weights_always_sum_to_one(self, deltas):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1)
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    @given(
        deltas=st.dictionaries(
            keys=st.sampled_from(list(DEFAULT_WEIGHTS.keys())),
            values=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False),
            max_size=5,
        )
    )
    def test_all_weights_within_bounds(self, deltas):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1)
        for w in weights.values():
            assert WEIGHT_LOWER_BOUND - 1e-3 <= w <= WEIGHT_UPPER_BOUND + 1e-3

    @given(
        ratings=st.lists(
            st.tuples(
                st.sampled_from([-1, 0, 1]),
                st.floats(min_value=1.0, max_value=10.0, allow_nan=False),
            ),
            min_size=0, max_size=20,
        )
    )
    def test_threshold_always_bounded(self, ratings):
        feedbacks = [{"rating": r, "overall_score": s} for r, s in ratings]
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert 3.0 <= t <= 8.0
