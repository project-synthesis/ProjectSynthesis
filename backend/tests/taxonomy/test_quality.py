"""Tests for taxonomy quality module — Q_system, weights, thresholds."""

import math

import pytest

from app.services.taxonomy.quality import (
    COLD_PATH_EPSILON,
    NodeMetrics,
    QHealthResult,
    QWeights,
    adaptive_threshold,
    compute_q_health,
    compute_q_system,
    epsilon_tolerance,
    is_cold_path_non_regressive,
    is_non_regressive,
    suggestion_threshold,
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
    """Spec Section 2.5 — edge cases and invariants.

    A5 N-guard: Q_system is undefined when fewer than 2 active nodes
    exist. Single-cluster taxonomies have no separation to measure
    and no siblings to compare coherence against, so Q=1.0 is a
    meaningless artifact. Return None so the UI can show "—".
    """

    def test_empty_returns_none(self):
        """Empty node list: no active clusters, Q undefined."""
        assert compute_q_system([], QWeights.from_ramp(0.0)) is None

    def test_single_node_returns_none(self):
        """Single active node: separation has no siblings, Q undefined."""
        from app.services.taxonomy.quality import NodeMetrics

        node = NodeMetrics(coherence=1.0, separation=1.0, state="active")
        assert (
            compute_q_system([node], QWeights.from_ramp(0.0), coverage=1.0)
            is None
        )

    def test_two_active_nodes_returns_float(self):
        """Two active nodes: Q is well-defined."""
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="active"),
            NodeMetrics(coherence=0.9, separation=0.6, state="active"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.0), coverage=1.0)
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_result_bounded_zero_one(self):
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="active"),
            NodeMetrics(coherence=0.9, separation=0.6, state="active"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.5), coverage=0.95)
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_nan_replaced_with_zero(self):
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=float("nan"), separation=0.5, state="active"),
            NodeMetrics(coherence=0.5, separation=0.5, state="active"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.0), coverage=1.0)
        assert score is not None
        assert math.isfinite(score)

    def test_single_active_with_archived_returns_none(self):
        """Retired nodes excluded — only 1 active remains → None."""
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="active"),
            NodeMetrics(coherence=0.0, separation=0.0, state="archived"),
        ]
        assert (
            compute_q_system(nodes, QWeights.from_ramp(0.0), coverage=1.0)
            is None
        )

    def test_multi_active_with_archived_excludes_retired(self):
        """Retired node should not drag score down (when ≥2 active)."""
        from app.services.taxonomy.quality import NodeMetrics

        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="active"),
            NodeMetrics(coherence=0.9, separation=0.6, state="active"),
            NodeMetrics(coherence=0.0, separation=0.0, state="archived"),
        ]
        score = compute_q_system(nodes, QWeights.from_ramp(0.0), coverage=1.0)
        assert score is not None
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

    def test_clamped_at_one(self):
        """Very large populations must not push threshold above 1.0."""
        t = adaptive_threshold(base=0.78, population=1_000_000)
        assert t == 1.0


class TestEpsilonTolerance:
    """Spec Section 2.5 — non-regression epsilon decays with age."""

    def test_young_taxonomy_larger_epsilon(self):
        eps = epsilon_tolerance(warm_path_age=0)
        assert eps == pytest.approx(0.006)

    def test_mature_taxonomy_smaller_epsilon(self):
        eps_young = epsilon_tolerance(warm_path_age=10)
        eps_old = epsilon_tolerance(warm_path_age=100)
        assert eps_old < eps_young

    def test_minimum_floor(self):
        eps = epsilon_tolerance(warm_path_age=10000)
        assert eps >= 0.001


class TestIsNonRegressive:
    """Spec Section 2.5 — Q_after >= Q_before - epsilon."""

    def test_improvement_passes(self):
        assert is_non_regressive(q_before=0.8, q_after=0.85, warm_path_age=10)

    def test_equal_passes(self):
        assert is_non_regressive(q_before=0.8, q_after=0.8, warm_path_age=10)

    def test_small_regression_within_tolerance(self):
        # Young taxonomy has ~0.01 epsilon
        assert is_non_regressive(q_before=0.8, q_after=0.795, warm_path_age=0)

    def test_large_regression_fails(self):
        assert not is_non_regressive(q_before=0.8, q_after=0.7, warm_path_age=50)


class TestNonRegressiveNoneTolerance:
    """A5: Q-gate semantics when either side is ``None`` (<2 active clusters).

      * ``None → defined``: crossing the N=2 threshold is growth — accept.
      * ``defined → None``: a valid taxonomy became degenerate — reject.
      * ``None → None``: no measurable progress — reject.
    """

    def test_warm_gate_accepts_growth_to_defined(self):
        """None baseline → defined Q is growth (bootstrapping past N=2)."""
        assert is_non_regressive(q_before=None, q_after=0.8, warm_path_age=0)

    def test_warm_gate_rejects_destruction_to_none(self):
        """Defined baseline → None means active set was dismantled."""
        assert not is_non_regressive(q_before=0.8, q_after=None, warm_path_age=0)

    def test_warm_gate_rejects_none_to_none(self):
        """No measurable change from degenerate to degenerate."""
        assert not is_non_regressive(q_before=None, q_after=None, warm_path_age=0)

    def test_cold_gate_accepts_growth_to_defined(self):
        assert is_cold_path_non_regressive(q_before=None, q_after=0.8)

    def test_cold_gate_rejects_destruction_to_none(self):
        assert not is_cold_path_non_regressive(q_before=0.8, q_after=None)

    def test_cold_gate_rejects_none_to_none(self):
        assert not is_cold_path_non_regressive(q_before=None, q_after=None)


class TestIsColdPathNonRegressive:
    """Cold-path quality gate — flat COLD_PATH_EPSILON tolerance."""

    def test_constant_value(self):
        assert COLD_PATH_EPSILON == pytest.approx(0.05)

    def test_improvement_passes(self):
        assert is_cold_path_non_regressive(0.6, 0.65)

    def test_equal_passes(self):
        assert is_cold_path_non_regressive(0.6, 0.6)

    def test_within_epsilon_passes(self):
        # 0.6 - 0.55 = 0.05, which is < 0.08 tolerance
        assert is_cold_path_non_regressive(0.6, 0.55)

    def test_exactly_at_boundary_passes(self):
        # 0.6 - 0.05 = 0.55, exactly on the boundary
        assert is_cold_path_non_regressive(0.6, 0.55)

    def test_beyond_epsilon_fails(self):
        # 0.6 - 0.50 = 0.10, which exceeds 0.08 tolerance
        assert not is_cold_path_non_regressive(0.6, 0.50)

    def test_large_regression_fails(self):
        assert not is_cold_path_non_regressive(0.9, 0.0)

    def test_zero_before_zero_after(self):
        assert is_cold_path_non_regressive(0.0, 0.0)

    def test_cold_epsilon_wider_than_warm(self):
        """Cold epsilon must be wider than warm minimum (0.001)."""
        assert COLD_PATH_EPSILON > epsilon_tolerance(warm_path_age=10000)


class TestComputeQSystemStateMembership:
    """Verify which node states contribute to Q computation."""

    def _weights(self):
        return QWeights.from_ramp(0.0)

    def test_active_nodes_included(self):
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="active"),
            NodeMetrics(coherence=0.7, separation=0.6, state="active"),
        ]
        score = compute_q_system(nodes, self._weights())
        assert score is not None and score > 0.0

    def test_mature_nodes_included(self):
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="mature"),
            NodeMetrics(coherence=0.7, separation=0.6, state="mature"),
        ]
        score = compute_q_system(nodes, self._weights())
        assert score is not None and score > 0.0

    def test_template_nodes_included(self):
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="template"),
            NodeMetrics(coherence=0.7, separation=0.6, state="template"),
        ]
        score = compute_q_system(nodes, self._weights())
        assert score is not None and score > 0.0

    def test_candidate_nodes_included(self):
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="candidate"),
            NodeMetrics(coherence=0.7, separation=0.6, state="candidate"),
        ]
        score = compute_q_system(nodes, self._weights())
        assert score is not None and score > 0.0

    def test_domain_nodes_excluded(self):
        """2 domain nodes → 0 active → Q undefined (None)."""
        nodes = [
            NodeMetrics(coherence=0.99, separation=0.99, state="domain"),
            NodeMetrics(coherence=0.99, separation=0.99, state="domain"),
        ]
        assert compute_q_system(nodes, self._weights()) is None

    def test_archived_nodes_excluded(self):
        """2 archived nodes → 0 active → Q undefined (None)."""
        nodes = [
            NodeMetrics(coherence=0.99, separation=0.99, state="archived"),
            NodeMetrics(coherence=0.99, separation=0.99, state="archived"),
        ]
        assert compute_q_system(nodes, self._weights()) is None

    def test_mixed_states_only_non_excluded_contribute(self):
        """mature/template should contribute; domain/archived should not."""
        # All contributing nodes have high quality
        contributing = [
            NodeMetrics(coherence=0.9, separation=0.9, state="active"),
            NodeMetrics(coherence=0.9, separation=0.9, state="mature"),
            NodeMetrics(coherence=0.9, separation=0.9, state="template"),
        ]
        # Excluded nodes have terrible quality — if they contributed the score
        # would be dragged below 0.5
        excluded = [
            NodeMetrics(coherence=0.0, separation=0.0, state="domain"),
            NodeMetrics(coherence=0.0, separation=0.0, state="archived"),
        ]
        score = compute_q_system(contributing + excluded, self._weights(), coverage=1.0)
        assert score is not None and score > 0.5

    def test_only_excluded_states_returns_none(self):
        """Excluded-only → 0 active → None (no metrics to compute)."""
        nodes = [
            NodeMetrics(coherence=0.9, separation=0.9, state="domain"),
            NodeMetrics(coherence=0.9, separation=0.9, state="archived"),
        ]
        assert compute_q_system(nodes, self._weights()) is None


class TestSuggestionThreshold:
    """Spec Section 7.9 — adaptive threshold based on coherence."""

    def test_high_coherence_near_base(self):
        t = suggestion_threshold(base=0.72, coherence=1.0)
        assert t == pytest.approx(0.72)

    def test_low_coherence_higher_threshold(self):
        t = suggestion_threshold(base=0.72, coherence=0.0)
        assert t == pytest.approx(0.72 + 0.15)

    def test_mid_coherence(self):
        t = suggestion_threshold(base=0.72, coherence=0.5)
        assert t == pytest.approx(0.72 + 0.15 * 0.5)


class TestCoherenceThreshold:
    def test_domain_node_gets_lower_floor(self):
        from app.models import PromptCluster
        from app.services.taxonomy.quality import coherence_threshold
        domain = PromptCluster(label="backend", state="domain")
        assert coherence_threshold(domain) == pytest.approx(0.3)

    def test_regular_cluster_gets_standard_floor(self):
        from app.models import PromptCluster
        from app.services.taxonomy.quality import coherence_threshold
        cluster = PromptCluster(label="test", state="active")
        assert coherence_threshold(cluster) == pytest.approx(0.6)


class TestQSystemWithSilhouette:
    """Verify DBCV slot activates when silhouette is provided."""

    def test_silhouette_increases_q_when_ramped(self):
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.6, state="active"),
            NodeMetrics(coherence=0.7, separation=0.5, state="active"),
        ] * 5  # 10 nodes — ramp_progress = (10-5)/20 = 0.25

        # Without silhouette (old behavior)
        w_no_sil = QWeights.from_ramp(0.0)
        q_no_sil = compute_q_system(nodes, w_no_sil, coverage=1.0, dbcv=0.0)

        # With silhouette and ramp
        w_sil = QWeights.from_ramp(0.25)
        q_sil = compute_q_system(nodes, w_sil, coverage=1.0, dbcv=0.9)

        # High silhouette should improve Q
        assert q_sil > q_no_sil

    def test_silhouette_no_effect_below_5_nodes(self):
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.6, state="active"),
        ] * 3  # 3 nodes — ramp_progress = 0.0

        w = QWeights.from_ramp(0.0)
        q = compute_q_system(nodes, w, coverage=1.0, dbcv=0.9)

        # DBCV weight is 0 when ramp is 0, so dbcv=0.9 has no effect
        w2 = QWeights.from_ramp(0.0)
        q2 = compute_q_system(nodes, w2, coverage=1.0, dbcv=0.0)
        assert q == pytest.approx(q2)

    def test_zero_silhouette_no_dead_weight(self):
        """Uninitialized silhouette (0.0) must not create dead weight.

        With 34 active nodes the ramp would be 1.0, giving 15% weight to
        DBCV. If silhouette is 0.0 (no cold path has run), that 15% is
        dead weight pulling Q_system down by ~15%. The ramp guard must
        keep ramp=0.0 when silhouette=0.0 regardless of node count.
        """
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.6, state="active"),
        ] * 34  # 34 nodes — would be ramp=1.0 without guard

        # With zero silhouette, ramp should stay 0 → weights identical to no-DBCV
        w_guarded = QWeights.from_ramp(0.0)  # ramp=0 because silhouette=0
        q_guarded = compute_q_system(nodes, w_guarded, coverage=1.0, dbcv=0.0)

        # Without guard, ramp=1.0 → 15% dead weight
        w_unguarded = QWeights.from_ramp(1.0)
        q_unguarded = compute_q_system(nodes, w_unguarded, coverage=1.0, dbcv=0.0)

        # Guarded Q must be higher (no dead weight)
        assert q_guarded > q_unguarded
        # The difference should be ~15% of Q
        assert q_guarded - q_unguarded > 0.05


class TestComputeQHealth:
    """Tests for the member-weighted q_health metric."""

    def _weights(self) -> QWeights:
        return QWeights.from_ramp(0.0)  # No DBCV, standard weights

    def test_singletons_dont_dominate(self):
        """40 singletons at coh=1.0 + 10 clusters with 6 members at coh=0.42.
        q_health should be significantly lower than q_system."""
        nodes = []
        for _ in range(40):
            nodes.append(NodeMetrics(coherence=1.0, separation=1.0, state="active", member_count=1))
        for _ in range(10):
            nodes.append(NodeMetrics(coherence=0.42, separation=0.6, state="active", member_count=6))

        w = self._weights()
        q_sys = compute_q_system(nodes, w)
        q_health = compute_q_health(nodes, w)

        assert q_health.q_health < q_sys, f"q_health ({q_health.q_health}) should be < q_system ({q_sys})"
        # The 60 members in low-coherence clusters (10*6=60) vs 40 singletons
        # should drag weighted coherence well below arithmetic mean
        assert q_health.coherence_weighted < 0.80

    def test_member_weighted_coherence_correct(self):
        """Manual verification of weighted mean calculation."""
        nodes = [
            NodeMetrics(coherence=0.5, separation=0.8, state="active", member_count=10),
            NodeMetrics(coherence=1.0, separation=1.0, state="active", member_count=2),
        ]
        w = self._weights()
        result = compute_q_health(nodes, w)

        # Expected weighted coherence: (0.5*10 + 1.0*2) / 12 = 7.0/12 = 0.5833
        expected_coh = (0.5 * 10 + 1.0 * 2) / 12
        assert abs(result.coherence_weighted - round(expected_coh, 4)) < 0.001

    def test_all_singletons_equals_arithmetic(self):
        """When all clusters have member_count=1, q_health should equal q_system."""
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.7, state="active", member_count=1),
            NodeMetrics(coherence=0.6, separation=0.9, state="active", member_count=1),
            NodeMetrics(coherence=0.4, separation=0.5, state="active", member_count=1),
        ]
        w = self._weights()
        q_sys = compute_q_system(nodes, w)
        q_health = compute_q_health(nodes, w)

        assert abs(q_health.q_health - q_sys) < 0.001

    def test_returns_full_breakdown(self):
        """QHealthResult contains all expected fields."""
        nodes = [
            NodeMetrics(coherence=0.7, separation=0.8, state="active", member_count=5),
            NodeMetrics(coherence=0.6, separation=0.7, state="active", member_count=3),
        ]
        result = compute_q_health(nodes, self._weights())

        assert isinstance(result, QHealthResult)
        assert isinstance(result.q_health, float)
        assert isinstance(result.coherence_weighted, float)
        assert isinstance(result.separation_weighted, float)
        assert isinstance(result.coverage, float)
        assert isinstance(result.dbcv, float)
        assert isinstance(result.weights, dict)
        assert "w_c" in result.weights
        assert "w_s" in result.weights
        assert isinstance(result.total_members, int)
        assert isinstance(result.cluster_count, int)

    def test_single_active_returns_none(self):
        """A5: q_health undefined below 2 active clusters (symmetric with q_system)."""
        nodes = [
            NodeMetrics(coherence=0.7, separation=0.8, state="active", member_count=5),
        ]
        result = compute_q_health(nodes, self._weights())
        assert result.q_health is None
        # Breakdown fields still populated for observability
        assert result.cluster_count == 1
        assert result.total_members == 5

    def test_zero_members_fallback(self):
        """When all member_counts are 0, falls back to equal weighting."""
        nodes = [
            NodeMetrics(coherence=0.5, separation=0.7, state="active", member_count=0),
            NodeMetrics(coherence=0.9, separation=0.3, state="active", member_count=0),
        ]
        result = compute_q_health(nodes, self._weights())

        # Should not crash, should produce a valid result with equal weighting
        assert 0.0 <= result.q_health <= 1.0
        # total_members reports the actual count (0), not the fallback
        assert result.total_members == 0
        assert result.cluster_count == 2

    def test_empty_returns_none(self):
        """A5: Empty node list → q_health undefined (None)."""
        result = compute_q_health([], self._weights())
        assert result.q_health is None
        assert result.total_members == 0
        assert result.cluster_count == 0

    def test_excludes_domain_and_archived(self):
        """Domain and archived nodes should be excluded from computation.

        Only 1 active remains after exclusion → q_health=None (A5 N-guard).
        """
        nodes = [
            NodeMetrics(coherence=0.5, separation=0.7, state="active", member_count=10),
            NodeMetrics(coherence=0.0, separation=0.0, state="domain", member_count=100),
            NodeMetrics(coherence=0.0, separation=0.0, state="archived", member_count=50),
        ]
        result = compute_q_health(nodes, self._weights())

        assert result.cluster_count == 1  # Only the active node
        assert result.total_members == 10  # Only the active node's members
        assert result.q_health is None  # <2 active → undefined

    def test_negative_member_count_clamped(self):
        """Negative member_count is treated as 0 (clamped via max(n, 0))."""
        nodes = [
            NodeMetrics(coherence=0.8, separation=0.6, state="active", member_count=-5),
            NodeMetrics(coherence=0.4, separation=0.9, state="active", member_count=10),
        ]
        result = compute_q_health(nodes, self._weights())

        # Negative member clamped to 0, so only the 10-member cluster contributes
        assert result.total_members == 10
        assert abs(result.coherence_weighted - 0.4) < 0.01

    def test_nan_coherence_excluded_from_weighted_mean(self):
        """Nodes with NaN coherence don't dilute the weighted mean."""
        nodes = [
            NodeMetrics(coherence=float("nan"), separation=0.8, state="active", member_count=10),
            NodeMetrics(coherence=0.6, separation=0.5, state="active", member_count=10),
        ]
        result = compute_q_health(nodes, self._weights())

        # The NaN node should be excluded from coherence weighting
        # so coherence_weighted should reflect only the 0.6 node
        assert abs(result.coherence_weighted - 0.6) < 0.01
        # Separation includes both (both finite)
        assert abs(result.separation_weighted - 0.65) < 0.01


class TestComputeIntentLabelCoherence:
    """Tests for compute_intent_label_coherence (Tier 5b)."""

    def test_identical_labels_return_one(self):
        """All identical labels → perfect coherence."""
        from app.services.taxonomy.quality import compute_intent_label_coherence

        assert compute_intent_label_coherence(["Build REST API", "Build REST API", "Build REST API"]) == 1.0

    def test_disjoint_labels_return_zero(self):
        """Completely disjoint labels → zero coherence."""
        from app.services.taxonomy.quality import compute_intent_label_coherence

        result = compute_intent_label_coherence(["Build REST API", "Deploy Docker Container"])
        assert result < 0.15  # no token overlap after stop-word removal

    def test_partial_overlap_intermediate(self):
        """Partial token overlap → intermediate coherence."""
        from app.services.taxonomy.quality import compute_intent_label_coherence

        result = compute_intent_label_coherence(["Build REST API", "Build REST Service"])
        assert 0.2 < result < 0.8

    def test_empty_list_returns_one(self):
        """Empty or single-label list → trivially coherent."""
        from app.services.taxonomy.quality import compute_intent_label_coherence

        assert compute_intent_label_coherence([]) == 1.0
        assert compute_intent_label_coherence(["Single Label"]) == 1.0

    def test_empty_string_labels_skipped(self):
        """Empty strings are filtered out."""
        from app.services.taxonomy.quality import compute_intent_label_coherence

        assert compute_intent_label_coherence(["", "", ""]) == 1.0

    def test_stop_words_filtered(self):
        """Labels differing only in stop words should be highly coherent."""
        from app.services.taxonomy.quality import compute_intent_label_coherence

        # "Build" vs "Build" after stop words removed → same token set
        result = compute_intent_label_coherence(["Build the API", "Build an API"])
        assert result >= 0.8
