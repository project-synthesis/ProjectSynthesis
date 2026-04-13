"""Tests for the sub-domain discovery cluster-count trigger.

Validates that the OR-logic trigger fires sub-domain discovery when a domain
has many child clusters (structural diversity) even if per-cluster coherence
is high. Also verifies backward compatibility with the original coherence path.
"""

from __future__ import annotations

from app.services.taxonomy._constants import (
    SUB_DOMAIN_CLUSTER_PATH_MIN_MEMBERS,
    SUB_DOMAIN_COHERENCE_CEILING,
    SUB_DOMAIN_MIN_CLUSTERS,
    SUB_DOMAIN_MIN_MEMBERS,
)


def _evaluate_trigger(
    total_members: int,
    cluster_count: int,
    mean_coherence: float | None,
) -> tuple[bool, str]:
    """Replicate the trigger logic from engine._propose_sub_domains().

    Returns (would_trigger, trigger_path).
    """
    coherences_present = mean_coherence is not None
    passes_members = total_members >= SUB_DOMAIN_MIN_MEMBERS
    passes_coherence = (
        coherences_present and mean_coherence < SUB_DOMAIN_COHERENCE_CEILING
    )
    passes_clusters = (
        cluster_count >= SUB_DOMAIN_MIN_CLUSTERS
        and total_members >= SUB_DOMAIN_CLUSTER_PATH_MIN_MEMBERS
    )
    would_trigger = (passes_members and passes_coherence) or passes_clusters
    trigger_path = (
        "both" if (passes_members and passes_coherence) and passes_clusters
        else "cluster_count" if passes_clusters
        else "coherence"
    )
    return would_trigger, trigger_path


class TestSubDomainClusterCountTrigger:
    """Tests for the dual-path sub-domain trigger logic."""

    def test_cluster_count_trigger_fires_high_coherence(self):
        """SaaS scenario: 15 clusters, 60 members, coherence 0.85 -> triggers via cluster-count."""
        fires, path = _evaluate_trigger(
            total_members=60,
            cluster_count=15,
            mean_coherence=0.85,
        )
        assert fires is True
        assert path == "cluster_count"

    def test_skips_below_min_clusters(self):
        """10 clusters (below 12), high coherence -> does NOT trigger."""
        fires, _path = _evaluate_trigger(
            total_members=40,
            cluster_count=10,
            mean_coherence=0.85,
        )
        assert fires is False

    def test_skips_below_cluster_path_min_members(self):
        """15 clusters but only 20 members (below 30) -> cluster path skips."""
        fires, _path = _evaluate_trigger(
            total_members=20,
            cluster_count=15,
            mean_coherence=0.85,
        )
        assert fires is False

    def test_coherence_path_still_works(self):
        """Original path: 5 clusters, 25 members, coherence 0.35 -> triggers via coherence."""
        fires, path = _evaluate_trigger(
            total_members=25,
            cluster_count=5,
            mean_coherence=0.35,
        )
        assert fires is True
        assert path == "coherence"

    def test_both_paths_fire(self):
        """Both conditions met: 14 clusters, 50 members, coherence 0.40 -> trigger_path='both'."""
        fires, path = _evaluate_trigger(
            total_members=50,
            cluster_count=14,
            mean_coherence=0.40,
        )
        assert fires is True
        assert path == "both"

    def test_coherence_path_requires_coherence_data(self):
        """No coherence data (None) -> coherence path cannot fire."""
        fires, _path = _evaluate_trigger(
            total_members=25,
            cluster_count=5,
            mean_coherence=None,
        )
        assert fires is False

    def test_constants_have_expected_values(self):
        """Verify constants are set to planned values."""
        assert SUB_DOMAIN_MIN_CLUSTERS == 12
        assert SUB_DOMAIN_CLUSTER_PATH_MIN_MEMBERS == 30
        assert SUB_DOMAIN_MIN_MEMBERS == 20
        assert SUB_DOMAIN_COHERENCE_CEILING == 0.50
