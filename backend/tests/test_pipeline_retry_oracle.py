"""Tests for pipeline integration with RetryOracle."""


from app.services.retry_oracle import RetryOracle


class TestPipelineOracleIntegration:
    def test_oracle_replaces_fixed_threshold(self):
        """Oracle should be instantiated with user adaptation threshold."""
        oracle = RetryOracle(max_retries=1, threshold=6.5)
        oracle.record_attempt(
            scores={"overall_score": 7.0},
            prompt="Good prompt",
            focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action == "accept"

    def test_oracle_best_of_n_returns_highest(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(scores={"overall_score": 6.0}, prompt="V1", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 8.0}, prompt="V2 unique", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V3 different", focus_areas=[])
        assert oracle.best_attempt_index == 1

    def test_oracle_diagnostics_structure(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"overall_score": 4.0, "clarity_score": 3},
            prompt="Test",
            focus_areas=[],
        )
        diag = oracle.get_diagnostics()
        assert "attempt" in diag
        assert "momentum" in diag
        assert "best_attempt_index" in diag
        assert "overall_score" in diag
        assert "threshold" in diag
        assert "action" in diag
        assert "gate" in diag
        assert "focus_areas" in diag
