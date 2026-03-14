"""Tests for corrected issues -> dimension weight integration."""

from app.services.adaptation_engine import apply_issue_signals


class TestIssueSignals:
    def test_no_issues_returns_base_deltas(self):
        base = {"clarity_score": 0.1, "structure_score": -0.05}
        result = apply_issue_signals(base, {}, total_feedbacks=5)
        assert result == base

    def test_lost_key_terms_boosts_faithfulness(self):
        result = apply_issue_signals(
            {"faithfulness_score": 0.0},
            {"lost_key_terms": 3},
            total_feedbacks=10,
        )
        assert result["faithfulness_score"] > 0

    def test_multiple_issues_accumulate(self):
        result = apply_issue_signals(
            {"faithfulness_score": 0.0, "specificity_score": 0.0},
            {"lost_key_terms": 3, "too_vague": 2},
            total_feedbacks=10,
        )
        assert result["faithfulness_score"] > 0
        assert result["specificity_score"] > 0

    def test_unknown_issue_ignored(self):
        base = {"clarity_score": 0.1}
        result = apply_issue_signals(base, {"unknown_issue": 5}, total_feedbacks=10)
        assert result == base

    def test_zero_total_feedbacks_safe(self):
        result = apply_issue_signals({}, {"lost_key_terms": 1}, total_feedbacks=0)
        assert isinstance(result, dict)
