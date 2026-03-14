"""Tests for the result intelligence service."""

from app.schemas.result_assessment import (
    Confidence,
    ResultAssessment,
    Verdict,
)
from app.services.result_intelligence import (
    compute_dimension_insights,
    compute_framework_fit,
    compute_improvement_potential,
    compute_next_actions,
    compute_result_assessment,
    compute_retry_journey,
    compute_verdict,
    detect_trade_offs,
)


class TestComputeVerdict:
    def test_high_score_strong_verdict(self):
        verdict, confidence, headline = compute_verdict(
            overall_score=8.5,
            threshold=6.0,
            framework_avg=7.0,
            user_weights={"clarity_score": 0.3},
            scores={"clarity_score": 9.0},
            gate_triggered="threshold_met",
        )
        assert verdict == Verdict.STRONG
        assert confidence == Confidence.HIGH

    def test_below_threshold_weak_verdict(self):
        verdict, confidence, headline = compute_verdict(
            overall_score=4.0,
            threshold=6.0,
            framework_avg=7.0,
            user_weights=None,
            scores={"clarity_score": 4.0},
            gate_triggered="budget_exhausted",
        )
        assert verdict == Verdict.WEAK

    def test_solid_verdict(self):
        verdict, _, _ = compute_verdict(
            overall_score=6.5,
            threshold=5.0,
            framework_avg=None,
            user_weights=None,
            scores={"clarity_score": 7.0},
            gate_triggered=None,
        )
        assert verdict == Verdict.SOLID

    def test_mixed_verdict(self):
        verdict, _, _ = compute_verdict(
            overall_score=5.0,
            threshold=6.0,
            framework_avg=None,
            user_weights=None,
            scores={"clarity_score": 5.0},
            gate_triggered=None,
        )
        assert verdict == Verdict.MIXED

    def test_headline_contains_score(self):
        _, _, headline = compute_verdict(
            overall_score=8.0,
            threshold=5.0,
            framework_avg=None,
            user_weights=None,
            scores={},
            gate_triggered=None,
        )
        assert "8.0" in headline


class TestComputeDimensionInsights:
    def test_weak_dimensions_flagged(self):
        insights = compute_dimension_insights(
            scores={"clarity_score": 3, "specificity_score": 8},
            user_weights=None,
            threshold=5.0,
            user_history=None,
            framework_perf=None,
            elasticity=None,
            previous_scores=None,
        )
        weak = [i for i in insights if i.is_weak]
        assert len(weak) == 1
        assert weak[0].dimension == "clarity_score"

    def test_strong_dimensions_flagged(self):
        insights = compute_dimension_insights(
            scores={"clarity_score": 9},
            user_weights=None,
            threshold=5.0,
            user_history=None,
            framework_perf=None,
            elasticity=None,
            previous_scores=None,
        )
        strong = [i for i in insights if i.is_strong]
        assert len(strong) == 1

    def test_delta_from_previous(self):
        insights = compute_dimension_insights(
            scores={"clarity_score": 8},
            user_weights=None,
            threshold=5.0,
            user_history=None,
            framework_perf=None,
            elasticity=None,
            previous_scores={"clarity_score": 5},
        )
        assert insights[0].delta_from_previous == 3


class TestDetectTradeOffs:
    def test_no_tradeoffs_single_attempt(self):
        result = detect_trade_offs(
            attempts=[{"clarity_score": 7}],
            user_weights=None,
            framework=None,
        )
        assert result == []

    def test_detects_tradeoff(self):
        attempts = [
            {"clarity_score": 5, "conciseness_score": 8},
            {"clarity_score": 8, "conciseness_score": 5},
        ]
        result = detect_trade_offs(attempts, None, None)
        assert len(result) == 1
        assert result[0].gained_dimension == "clarity_score"
        assert result[0].lost_dimension == "conciseness_score"

    def test_typical_tradeoff_flagged(self):
        attempts = [
            {"structure_score": 5, "conciseness_score": 8},
            {"structure_score": 8, "conciseness_score": 5},
        ]
        result = detect_trade_offs(attempts, None, "chain-of-thought")
        assert len(result) == 1
        assert result[0].is_typical_for_framework is True


class TestComputeRetryJourney:
    def test_single_attempt(self):
        journey = compute_retry_journey(
            attempts=[{"overall_score": 7.0}],
            oracle_diagnostics=None,
        )
        assert journey.total_attempts == 1
        assert journey.momentum_trend == "stable"

    def test_improving_trend(self):
        attempts = [
            {"overall_score": 4.0},
            {"overall_score": 5.0},
            {"overall_score": 7.0},
        ]
        journey = compute_retry_journey(attempts, None)
        assert journey.total_attempts == 3
        assert journey.best_attempt == 3
        assert journey.momentum_trend == "improving"


class TestComputeFrameworkFit:
    def test_returns_none_without_framework(self):
        result = compute_framework_fit(None, "coding", 7.0, None, None)
        assert result is None

    def test_returns_report_with_framework(self):
        result = compute_framework_fit(
            "chain-of-thought", "coding", 8.0, None, None,
        )
        assert result is not None
        assert result.framework == "chain-of-thought"
        assert result.task_type == "coding"


class TestComputeImprovementPotential:
    def test_low_score_high_elasticity_ranked_first(self):
        signals = compute_improvement_potential(
            scores={"clarity_score": 4, "specificity_score": 8},
            elasticity={"clarity_score": 0.8, "specificity_score": 0.3},
            framework=None,
            user_weights=None,
        )
        if signals:
            assert signals[0].dimension == "clarity_score"

    def test_no_signals_when_scores_high(self):
        signals = compute_improvement_potential(
            scores={"clarity_score": 10},
            elasticity={"clarity_score": 0.1},
            framework=None,
            user_weights=None,
        )
        assert len(signals) == 0


class TestComputeNextActions:
    def test_weak_verdict_suggests_refine(self):
        actions = compute_next_actions(
            verdict=Verdict.WEAK,
            confidence=Confidence.LOW,
            weak_dims=["clarity_score"],
            framework_fit=None,
            improvement_signals=[],
            trade_offs=[],
            active_guardrails=None,
        )
        refine_actions = [a for a in actions if a.category == "refine"]
        assert len(refine_actions) >= 1

    def test_strong_verdict_suggests_feedback(self):
        actions = compute_next_actions(
            verdict=Verdict.STRONG,
            confidence=Confidence.HIGH,
            weak_dims=[],
            framework_fit=None,
            improvement_signals=[],
            trade_offs=[],
            active_guardrails=None,
        )
        feedback_actions = [a for a in actions if a.category == "feedback"]
        assert len(feedback_actions) >= 1


class TestComputeResultAssessment:
    def test_full_assessment(self):
        result = compute_result_assessment(
            overall_score=7.5,
            scores={
                "clarity_score": 8,
                "specificity_score": 7,
                "structure_score": 7,
                "faithfulness_score": 8,
                "conciseness_score": 7,
            },
            threshold=5.0,
            framework="chain-of-thought",
            task_type="coding",
        )
        assert isinstance(result, ResultAssessment)
        assert result.verdict in (Verdict.STRONG, Verdict.SOLID)
        assert result.headline
        assert len(result.dimension_insights) == 5

    def test_first_time_fallback(self):
        result = ResultAssessment.first_time_fallback()
        assert result.verdict == Verdict.MIXED
        assert result.confidence == Confidence.LOW
        assert len(result.next_actions) >= 1
        assert result.next_actions[0].category == "feedback"

    def test_weak_result_generates_refine_actions(self):
        result = compute_result_assessment(
            overall_score=3.5,
            scores={
                "clarity_score": 3,
                "specificity_score": 4,
                "structure_score": 3,
                "faithfulness_score": 4,
                "conciseness_score": 4,
            },
        )
        assert result.verdict == Verdict.WEAK
        refine_actions = [a for a in result.next_actions if a.category == "refine"]
        assert len(refine_actions) >= 1
