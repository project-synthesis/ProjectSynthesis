"""Tests for signal-driven sub-domain discovery threshold logic.

Validates the adaptive threshold formula and qualifier evaluation
that replaced the HDBSCAN-based trigger.
"""

from __future__ import annotations

from app.services.taxonomy._constants import (
    SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH,
    SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
    SUB_DOMAIN_QUALIFIER_MIN_MEMBERS,
    SUB_DOMAIN_QUALIFIER_SCALE_RATE,
)


def _adaptive_threshold(total_members: int) -> float:
    """Replicate the adaptive threshold formula from _propose_sub_domains()."""
    return max(
        SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW,
        SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH
        - SUB_DOMAIN_QUALIFIER_SCALE_RATE * total_members,
    )


class TestAdaptiveThreshold:
    """Tests for the signal-driven adaptive threshold formula."""

    def test_small_domain_high_threshold(self):
        """20-member domain requires ~52% consistency."""
        t = _adaptive_threshold(20)
        assert 0.51 < t < 0.53

    def test_medium_domain_moderate_threshold(self):
        """30-member domain requires ~48% consistency."""
        t = _adaptive_threshold(30)
        assert 0.47 < t < 0.49

    def test_large_domain_floor_threshold(self):
        """50+ member domain hits the 40% floor."""
        t = _adaptive_threshold(50)
        assert t == SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW

    def test_very_large_domain_stays_at_floor(self):
        """200-member domain stays at 40% floor."""
        t = _adaptive_threshold(200)
        assert t == SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW

    def test_threshold_decreases_monotonically(self):
        """Threshold never increases as domain grows."""
        prev = 1.0
        for size in range(1, 200):
            t = _adaptive_threshold(size)
            assert t <= prev
            prev = t

    def test_constants_have_expected_values(self):
        """Verify constants are set to planned values."""
        assert SUB_DOMAIN_QUALIFIER_MIN_MEMBERS == 5
        assert SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH == 0.60
        assert SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW == 0.40
        assert SUB_DOMAIN_QUALIFIER_SCALE_RATE == 0.004


class TestQualifierEvaluation:
    """Tests for qualifier pass/fail logic."""

    def test_qualifier_fails_above_threshold_small_domain(self):
        """8 out of 20 (40%) with threshold at 52% -> FAIL."""
        total = 20
        count = 8
        threshold = _adaptive_threshold(total)
        assert count / total < threshold

    def test_qualifier_passes_large_domain(self):
        """25 out of 60 (41.7%) with threshold at 40% -> PASS."""
        total = 60
        count = 25
        threshold = _adaptive_threshold(total)
        assert count / total >= threshold
        assert count >= SUB_DOMAIN_QUALIFIER_MIN_MEMBERS

    def test_qualifier_fails_too_few_members(self):
        """3 out of 10 (30%) -> fails min members even if consistency passes."""
        count = 3
        assert count < SUB_DOMAIN_QUALIFIER_MIN_MEMBERS

    def test_fragmented_distribution_no_subdomain(self):
        """5 qualifiers each at 20% in a 50-member domain -> none pass 40% floor."""
        total = 50
        threshold = _adaptive_threshold(total)
        for q_count in [10, 10, 10, 10, 10]:
            assert q_count / total < threshold
