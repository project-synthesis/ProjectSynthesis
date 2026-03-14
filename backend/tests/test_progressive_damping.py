"""Tests for progressive damping — confidence-weighted adaptation."""

from app.services.adaptation_engine import compute_effective_damping


def _make_feedbacks(ratings):
    return [type("F", (), {"rating": r})() for r in ratings]


class TestProgressiveDamping:
    def test_single_feedback_very_low_damping(self):
        d = compute_effective_damping(_make_feedbacks([1]))
        assert 0.02 < d < 0.08

    def test_three_feedbacks_moderate_damping(self):
        d = compute_effective_damping(_make_feedbacks([1, 1, 1]))
        assert 0.07 < d < 0.14

    def test_ten_feedbacks_near_max(self):
        d = compute_effective_damping(_make_feedbacks([1] * 10))
        assert d >= 0.12

    def test_inconsistent_feedback_lowers_damping(self):
        consistent = compute_effective_damping(_make_feedbacks([1, 1, 1, 1, 1]))
        inconsistent = compute_effective_damping(_make_feedbacks([1, -1, 1, -1, 1]))
        assert consistent > inconsistent

    def test_zero_feedbacks_returns_zero(self):
        assert compute_effective_damping([]) == 0.0

    def test_damping_never_exceeds_ceiling(self):
        d = compute_effective_damping(_make_feedbacks([1] * 100))
        assert d <= 0.18

    def test_recent_consistency_weighted_more(self):
        old_noisy = _make_feedbacks([-1, 1, -1, 1, -1, 1, 1, 1, 1, 1])
        recent_noisy = _make_feedbacks([1, 1, 1, 1, 1, -1, 1, -1, 1, -1])
        assert compute_effective_damping(old_noisy) > compute_effective_damping(
            recent_noisy
        )
