"""Tests for the 7-gate adaptive RetryOracle."""

import pytest
from app.services.retry_oracle import RetryOracle, RetryDecision


class TestOracleInit:
    def test_default_threshold(self):
        oracle = RetryOracle(max_retries=3)
        assert oracle.threshold == 5.0

    def test_custom_threshold(self):
        oracle = RetryOracle(max_retries=3, threshold=6.5)
        assert oracle.threshold == 6.5

    def test_threshold_clamped_low(self):
        oracle = RetryOracle(max_retries=3, threshold=1.0)
        assert oracle.threshold == 3.0

    def test_threshold_clamped_high(self):
        oracle = RetryOracle(max_retries=3, threshold=9.5)
        assert oracle.threshold == 8.0


class TestRecordAttempt:
    def test_first_attempt_recorded(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"clarity_score": 6, "specificity_score": 5, "structure_score": 7,
                    "faithfulness_score": 4, "conciseness_score": 8, "overall_score": 5.8},
            prompt="Test prompt",
            focus_areas=[],
        )
        assert oracle.attempt_count == 1

    def test_multiple_attempts(self):
        oracle = RetryOracle(max_retries=3)
        for i in range(3):
            oracle.record_attempt(
                scores={"overall_score": 4.0 + i},
                prompt=f"Prompt version {i}",
                focus_areas=[],
            )
        assert oracle.attempt_count == 3


class TestGate1ScoreAboveThreshold:
    def test_accept_when_score_above_threshold(self):
        oracle = RetryOracle(max_retries=3, threshold=5.0)
        oracle.record_attempt(
            scores={"overall_score": 7.0, "clarity_score": 7, "specificity_score": 7,
                    "structure_score": 7, "faithfulness_score": 7, "conciseness_score": 7},
            prompt="Good prompt", focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action == "accept"
        assert "threshold" in decision.reason.lower()


class TestGate2BudgetExhausted:
    def test_accept_best_when_max_retries_reached(self):
        oracle = RetryOracle(max_retries=1, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="V1", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V2", focus_areas=["clarity_score"])
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "budget" in decision.reason.lower()


class TestGate3CycleDetected:
    def test_accept_best_on_cycle(self):
        oracle = RetryOracle(max_retries=5, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="same prompt", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 4.5}, prompt="same prompt", focus_areas=[])
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "cycle" in decision.reason.lower()


class TestGate4CreativeExhaustion:
    def test_accept_best_on_low_entropy(self):
        oracle = RetryOracle(max_retries=5, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="same prompt here", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 4.5}, prompt="same prompt here.", focus_areas=[])
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "exhaustion" in decision.reason.lower() or "cycle" in decision.reason.lower()


class TestGate5NegativeMomentum:
    def test_accept_best_on_declining_scores(self):
        oracle = RetryOracle(max_retries=5, threshold=8.0)
        oracle.record_attempt(scores={"overall_score": 6.0}, prompt="V1 prompt text", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V2 very different prompt text", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 4.0}, prompt="V3 completely new approach here", focus_areas=[])
        decision = oracle.should_retry()
        assert decision.action == "accept_best"


class TestGate6ZeroSumTrap:
    def test_accept_best_on_consecutive_regressions(self):
        oracle = RetryOracle(max_retries=10, threshold=8.0)
        oracle.record_attempt(
            scores={"overall_score": 5.0, "clarity_score": 5, "specificity_score": 5,
                    "structure_score": 5, "faithfulness_score": 5, "conciseness_score": 5},
            prompt="V1 original prompt", focus_areas=[],
        )
        oracle.record_attempt(
            scores={"overall_score": 5.1, "clarity_score": 7, "specificity_score": 3,
                    "structure_score": 3, "faithfulness_score": 7, "conciseness_score": 3},
            prompt="V2 different approach", focus_areas=[],
        )
        oracle.record_attempt(
            scores={"overall_score": 5.0, "clarity_score": 4, "specificity_score": 6,
                    "structure_score": 6, "faithfulness_score": 3, "conciseness_score": 6},
            prompt="V3 yet another approach", focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action == "accept_best"


class TestGate7DiminishingReturns:
    def test_accept_best_when_expected_gain_too_low(self):
        oracle = RetryOracle(max_retries=10, threshold=8.0)
        for i in range(5):
            oracle.record_attempt(
                scores={"overall_score": 4.0 + i * 0.1},
                prompt=f"Version {i} with unique content to avoid cycle detection number {i}",
                focus_areas=[],
            )
        decision = oracle.should_retry()
        assert decision.action == "accept_best"
        assert "diminishing" in decision.reason.lower()


class TestBestOfNSelection:
    def test_best_attempt_is_highest_score(self):
        oracle = RetryOracle(max_retries=5)
        oracle.record_attempt(scores={"overall_score": 6.0}, prompt="V1", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 7.5}, prompt="V2 unique", focus_areas=[])
        oracle.record_attempt(scores={"overall_score": 5.0}, prompt="V3 unique different", focus_areas=[])
        assert oracle.best_attempt_index == 1

    def test_best_attempt_with_user_weights(self):
        # Heavy clarity weight (0.60) makes V1 (high clarity, low others) win
        # V1: 9*0.60 + 3*0.10*4 = 5.40 + 1.20 = 6.60
        # V2: 5*0.60 + 6*0.10*4 = 3.00 + 2.40 = 5.40
        weights = {"clarity_score": 0.60, "specificity_score": 0.10, "structure_score": 0.10,
                   "faithfulness_score": 0.10, "conciseness_score": 0.10}
        oracle = RetryOracle(max_retries=5, user_weights=weights)
        oracle.record_attempt(
            scores={"overall_score": 5.0, "clarity_score": 9, "specificity_score": 3,
                    "structure_score": 3, "faithfulness_score": 3, "conciseness_score": 3},
            prompt="V1", focus_areas=[],
        )
        oracle.record_attempt(
            scores={"overall_score": 6.0, "clarity_score": 5, "specificity_score": 6,
                    "structure_score": 6, "faithfulness_score": 6, "conciseness_score": 6},
            prompt="V2 unique", focus_areas=[],
        )
        assert oracle.best_attempt_index == 0


class TestFocusSelection:
    def test_focus_returns_lowest_elastic_dimensions(self):
        oracle = RetryOracle(max_retries=5)
        oracle.record_attempt(
            scores={"overall_score": 4.0, "clarity_score": 3, "specificity_score": 7,
                    "structure_score": 5, "faithfulness_score": 4, "conciseness_score": 6},
            prompt="V1", focus_areas=[],
        )
        decision = oracle.should_retry()
        assert decision.action == "retry"
        assert "clarity_score" in decision.focus_areas


class TestDiagnosticMessage:
    def test_builds_message_string(self):
        oracle = RetryOracle(max_retries=3)
        oracle.record_attempt(
            scores={"overall_score": 4.0, "clarity_score": 3, "faithfulness_score": 4},
            prompt="V1", focus_areas=[],
        )
        msg = oracle.build_diagnostic_message(["clarity_score", "faithfulness_score"])
        assert "clarity" in msg.lower()
        assert "faithfulness" in msg.lower()
