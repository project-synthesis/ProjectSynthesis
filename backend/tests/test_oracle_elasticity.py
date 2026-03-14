"""Tests for framework-aware elasticity in RetryOracle."""
from app.services.retry_oracle import GateName, RetryOracle


class TestElasticityTracking:
    def test_elasticity_updates_all_dimensions(self):
        oracle = RetryOracle(max_retries=3, threshold=7.0, framework="chain-of-thought")
        scores_1 = {"clarity_score": 5.0, "structure_score": 6.0, "conciseness_score": 7.0,
                     "faithfulness_score": 6.0, "specificity_score": 5.5}
        scores_2 = {"clarity_score": 7.0, "structure_score": 6.5, "conciseness_score": 6.5,
                     "faithfulness_score": 7.0, "specificity_score": 5.5}
        oracle.record_attempt(scores_1, "prompt 1", [])
        oracle.record_attempt(scores_2, "prompt 2", [])
        for dim in scores_1:
            assert oracle.get_elasticity("chain-of-thought", dim) is not None

    def test_high_change_produces_high_elasticity(self):
        oracle = RetryOracle(max_retries=3, threshold=7.0, framework="chain-of-thought")
        oracle.record_attempt({"clarity_score": 3.0, "conciseness_score": 8.0}, "p1", [])
        oracle.record_attempt({"clarity_score": 8.0, "conciseness_score": 7.8}, "p2", [])
        clarity_e = oracle.get_elasticity("chain-of-thought", "clarity_score")
        conciseness_e = oracle.get_elasticity("chain-of-thought", "conciseness_score")
        assert clarity_e > conciseness_e

    def test_framework_aware_focus_selection(self):
        oracle = RetryOracle(
            max_retries=3, threshold=7.0, framework="chain-of-thought",
            user_weights={"clarity_score": 0.3, "structure_score": 0.25,
                          "faithfulness_score": 0.2, "specificity_score": 0.15,
                          "conciseness_score": 0.1},
        )
        oracle.record_attempt(
            {"clarity_score": 5.0, "structure_score": 5.0, "conciseness_score": 3.0,
             "faithfulness_score": 5.0, "specificity_score": 5.0},
            "p1", [],
        )
        focus = oracle._select_focus_areas()
        # conciseness should NOT be in focus despite lowest score
        # because chain-of-thought de-emphasizes it
        assert "conciseness_score" not in focus


class TestGateEnum:
    def test_should_retry_returns_gate_name(self):
        oracle = RetryOracle(max_retries=1, threshold=5.0, framework="chain-of-thought")
        oracle.record_attempt(
            {"clarity_score": 8.0, "structure_score": 8.0, "conciseness_score": 8.0,
             "faithfulness_score": 8.0, "specificity_score": 8.0},
            "prompt", [],
        )
        decision = oracle.should_retry()
        assert hasattr(decision, "gate")
        assert decision.gate == GateName.THRESHOLD_MET

    def test_budget_exhausted_gate(self):
        oracle = RetryOracle(max_retries=1, threshold=9.0, framework="chain-of-thought")
        oracle.record_attempt(
            {"clarity_score": 5.0, "structure_score": 5.0, "conciseness_score": 5.0,
             "faithfulness_score": 5.0, "specificity_score": 5.0},
            "prompt 1", [],
        )
        # First should_retry -> RETRY
        d1 = oracle.should_retry()
        assert d1.action == "retry"

        oracle.record_attempt(
            {"clarity_score": 5.5, "structure_score": 5.5, "conciseness_score": 5.5,
             "faithfulness_score": 5.5, "specificity_score": 5.5},
            "prompt 2", [],
        )
        # Second should_retry -> should hit budget with >= fix
        d2 = oracle.should_retry()
        assert d2.gate == GateName.BUDGET_EXHAUSTED


class TestGetDiagnostics:
    def test_diagnostics_uses_gate_enum(self):
        oracle = RetryOracle(max_retries=1, threshold=5.0, framework="chain-of-thought")
        oracle.record_attempt(
            {"clarity_score": 8.0, "structure_score": 8.0, "conciseness_score": 8.0,
             "faithfulness_score": 8.0, "specificity_score": 8.0},
            "prompt", [],
        )
        oracle.should_retry()  # Sets the last decision
        diag = oracle.get_diagnostics()
        assert "gate" in diag
        assert diag["gate"] == "threshold_met"
