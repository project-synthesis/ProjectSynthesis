"""Tests for taxonomy quality module — Q_system, weights, thresholds."""

import math

import pytest

from app.services.taxonomy.quality import (
    QWeights,
    adaptive_threshold,
    compute_q_system,
)


class TestQWeights:
    """Spec Section 2.5 — constant-sum weight normalization."""

    def test_weights_sum_to_one_no_dbcv(self):
        w = QWeights.from_ramp(ramp_progress=0.0)
        total = w.w_c + w.w_s + w.w_v + w.w_d
        assert total == pytest.approx(1.0)
        assert w.w_d == 0.0
        assert w.w_c == pytest.approx(0.4)
        assert w.w_s == pytest.approx(0.35)
        assert w.w_v == pytest.approx(0.25)

    def test_weights_sum_to_one_full_dbcv(self):
        w = QWeights.from_ramp(ramp_progress=1.0)
        total = w.w_c + w.w_s + w.w_v + w.w_d
        assert total == pytest.approx(1.0)
        assert w.w_d == pytest.approx(0.15)
        assert w.w_c == pytest.approx(0.34)  # 0.4 * 0.85
        assert w.w_s == pytest.approx(0.2975)  # 0.35 * 0.85

    def test_weights_sum_to_one_mid_ramp(self):
        w = QWeights.from_ramp(ramp_progress=0.5)
        total = w.w_c + w.w_s + w.w_v + w.w_d
        assert total == pytest.approx(1.0)

    def test_ramp_clamped_above_one(self):
        w = QWeights.from_ramp(ramp_progress=2.0)
        assert w.w_d == pytest.approx(0.15)  # capped at target

    def test_ramp_clamped_below_zero(self):
        w = QWeights.from_ramp(ramp_progress=-1.0)
        assert w.w_d == 0.0


class TestComputeQSystem:
    """Spec Section 2.5 — edge cases and invariants."""

    def test_empty_returns_zero(self):
        assert compute_q_system([], QWeights.from_ramp(0.0)) == 0.0

    def test_single_node(self):
        """Single confirmed node: coherence=1.0, separation=1.0."""
        from app.services.taxonomy.quality import NodeMetrics

        node = NodeMetrics(coherence=1.0, separation=1.0, state="confirmed")
        score = compute_q_system([node], QWeights.from_ramp(0.0), coverage=1.0)
        assert 0.0 <= score <= 1.0
        assert score > 0.5  # should be high with perfect metrics

    def test_result_bounded_zero_one(self):
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="confirmed"),
            NodeMetrics(coherence=0.9, separation=0.6, state="confirmed"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.5), coverage=0.95)
        assert 0.0 <= score <= 1.0

    def test_nan_replaced_with_zero(self):
        from app.services.taxonomy.quality import NodeMetrics

        node = NodeMetrics(coherence=float("nan"), separation=0.5, state="confirmed")
        score = compute_q_system([node], QWeights.from_ramp(0.0), coverage=1.0)
        assert math.isfinite(score)

    def test_retired_nodes_excluded(self):
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="confirmed"),
            NodeMetrics(coherence=0.0, separation=0.0, state="retired"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.0), coverage=1.0)
        # Retired node should not drag score down
        assert score > 0.5


class TestAdaptiveThreshold:
    """Spec Section 2.4 — threshold scales with population."""

    def test_small_population_lenient(self):
        t = adaptive_threshold(base=0.78, population=3)
        assert t == pytest.approx(0.78 * (1 + 0.15 * math.log(1 + 3)), rel=1e-3)
        assert t < 1.0  # must stay reasonable

    def test_large_population_strict(self):
        t_small = adaptive_threshold(base=0.78, population=3)
        t_large = adaptive_threshold(base=0.78, population=100)
        assert t_large > t_small  # larger populations are stricter

    def test_zero_population(self):
        t = adaptive_threshold(base=0.78, population=0)
        assert t == pytest.approx(0.78)  # base value exactly
