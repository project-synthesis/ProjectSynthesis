"""Tests for pipeline Pydantic contracts (Section 12)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.pipeline_contracts import (
    AnalysisResult,
    AnalyzerInput,
    DimensionScores,
    OptimizationResult,
    PipelineEvent,
    PipelineResult,
    ScoreResult,
    ScorerInput,
)

# ---------------------------------------------------------------------------
# DimensionScores
# ---------------------------------------------------------------------------


class TestDimensionScores:
    def test_valid(self):
        scores = DimensionScores(
            clarity=8.0,
            specificity=7.5,
            structure=6.0,
            faithfulness=9.0,
            conciseness=5.5,
        )
        assert scores.clarity == 8.0
        assert scores.conciseness == 5.5

    def test_below_range_rejected(self):
        with pytest.raises(ValidationError):
            DimensionScores(
                clarity=0.9,
                specificity=5.0,
                structure=5.0,
                faithfulness=5.0,
                conciseness=5.0,
            )

    def test_above_range_rejected(self):
        with pytest.raises(ValidationError):
            DimensionScores(
                clarity=10.1,
                specificity=5.0,
                structure=5.0,
                faithfulness=5.0,
                conciseness=5.0,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            DimensionScores(
                clarity=5.0,
                specificity=5.0,
                structure=5.0,
                faithfulness=5.0,
                conciseness=5.0,
                unknown_field=1.0,
            )

    def test_overall_computation(self):
        scores = DimensionScores(
            clarity=8.0,
            specificity=6.0,
            structure=7.0,
            faithfulness=9.0,
            conciseness=5.0,
        )
        # v3 weights: 8*0.22 + 6*0.22 + 7*0.15 + 9*0.26 + 5*0.15 = 7.22
        assert scores.overall == 7.22

    def test_overall_rounded_to_2_decimals(self):
        _scores = DimensionScores(
            clarity=7.0,
            specificity=8.0,
            structure=6.0,
            faithfulness=9.0,
            conciseness=5.0,
        )
        # weighted mean — use a case that needs rounding
        scores2 = DimensionScores(
            clarity=7.1,
            specificity=8.2,
            structure=6.3,
            faithfulness=9.4,
            conciseness=5.0,
        )
        # v3 weights: 7.1*0.22 + 8.2*0.22 + 6.3*0.15 + 9.4*0.26 + 5.0*0.15 = 7.50
        assert scores2.overall == 7.5

    def test_boundary_values_accepted(self):
        scores = DimensionScores(
            clarity=1.0,
            specificity=10.0,
            structure=5.5,
            faithfulness=1.0,
            conciseness=10.0,
        )
        assert scores.clarity == 1.0
        assert scores.specificity == 10.0


# ---------------------------------------------------------------------------
# AnalysisResult
# ---------------------------------------------------------------------------


class TestAnalysisResult:
    def test_valid(self):
        result = AnalysisResult(
            task_type="coding",
            weaknesses=["vague scope"],
            strengths=["clear goal"],
            selected_strategy="chain-of-thought",
            strategy_rationale="Requires step-by-step reasoning.",
            confidence=0.85,
        )
        assert result.task_type == "coding"
        assert result.confidence == 0.85

    def test_confidence_below_range_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisResult(
                task_type="x",
                weaknesses=[],
                strengths=[],
                selected_strategy="x",
                strategy_rationale="x",
                confidence=-0.1,
            )

    def test_confidence_above_range_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisResult(
                task_type="x",
                weaknesses=[],
                strengths=[],
                selected_strategy="x",
                strategy_rationale="x",
                confidence=1.1,
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AnalysisResult(
                task_type="x",
                weaknesses=[],
                strengths=[],
                selected_strategy="x",
                strategy_rationale="x",
                confidence=0.5,
                extra_key="nope",
            )


# ---------------------------------------------------------------------------
# OptimizationResult
# ---------------------------------------------------------------------------


class TestOptimizationResult:
    def test_valid(self):
        result = OptimizationResult(
            optimized_prompt="Write a Python function that...",
            changes_summary="Added context and constraints.",
        )
        assert result.optimized_prompt == "Write a Python function that..."

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            OptimizationResult(
                optimized_prompt="x",
                changes_summary="x",
                sneaky="field",
            )


# ---------------------------------------------------------------------------
# ScoreResult
# ---------------------------------------------------------------------------


class TestScoreResult:
    def _make_scores(self) -> DimensionScores:
        return DimensionScores(
            clarity=7.0,
            specificity=7.0,
            structure=7.0,
            faithfulness=7.0,
            conciseness=7.0,
        )

    def test_valid(self):
        result = ScoreResult(
            prompt_a_scores=self._make_scores(),
            prompt_b_scores=self._make_scores(),
        )
        assert result.prompt_a_scores.clarity == 7.0
        assert result.prompt_b_scores.overall == 7.0

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ScoreResult(
                prompt_a_scores=self._make_scores(),
                prompt_b_scores=self._make_scores(),
                extra="oops",
            )


# ---------------------------------------------------------------------------
# AnalyzerInput
# ---------------------------------------------------------------------------


class TestAnalyzerInput:
    def test_valid_no_override(self):
        inp = AnalyzerInput(
            raw_prompt="Summarise this document.",
            available_strategies=["chain-of-thought", "few-shot"],
        )
        assert inp.strategy_override is None

    def test_valid_with_override(self):
        inp = AnalyzerInput(
            raw_prompt="Summarise this document.",
            strategy_override="few-shot",
            available_strategies=["chain-of-thought", "few-shot"],
        )
        assert inp.strategy_override == "few-shot"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AnalyzerInput(
                raw_prompt="x",
                available_strategies=[],
                bad_field=True,
            )


# ---------------------------------------------------------------------------
# ScorerInput
# ---------------------------------------------------------------------------


class TestScorerInput:
    def test_valid(self):
        inp = ScorerInput(
            prompt_a="Original prompt.",
            prompt_b="Optimized prompt.",
            presentation_order="ab",
        )
        assert inp.presentation_order == "ab"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ScorerInput(
                prompt_a="x",
                prompt_b="y",
                presentation_order="ab",
                extra_data="bad",
            )


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


def _make_dim_scores(score: float = 7.0) -> DimensionScores:
    return DimensionScores(
        clarity=score,
        specificity=score,
        structure=score,
        faithfulness=score,
        conciseness=score,
    )


class TestPipelineResult:
    def test_valid(self):
        result = PipelineResult(
            id="result-1",
            trace_id="trace-abc",
            raw_prompt="Original.",
            optimized_prompt="Optimized.",
            task_type="coding",
            strategy_used="chain-of-thought",
            changes_summary="Added structure.",
            optimized_scores=_make_dim_scores(8.0),
            original_scores=_make_dim_scores(6.0),
            score_deltas={"clarity": 2.0, "specificity": 2.0},
            overall_score=8.0,
            provider="claude_cli",
            model_used="claude-3-5-haiku-20241022",
            scoring_mode="ab",
            duration_ms=1500,
            status="success",
            context_sources={"codebase": True},
        )
        assert result.tokens_total == 0
        assert result.tokens_by_phase == {}
        assert result.repo_full_name is None
        assert result.codebase_context_snapshot is None
        assert isinstance(result.created_at, datetime)

    def test_accepts_extra_fields(self):
        # PipelineResult does NOT use extra="forbid"
        result = PipelineResult(
            id="r2",
            trace_id="t2",
            raw_prompt="x",
            optimized_prompt="y",
            task_type="x",
            strategy_used="x",
            changes_summary="x",
            optimized_scores=_make_dim_scores(),
            original_scores=_make_dim_scores(),
            score_deltas={},
            overall_score=7.0,
            provider="x",
            model_used="x",
            scoring_mode="x",
            duration_ms=100,
            status="success",
            context_sources={},
            future_extension="allowed",
        )
        assert result.id == "r2"

    def test_created_at_defaults_to_utc_now(self):
        before = datetime.now(timezone.utc)
        result = PipelineResult(
            id="r3",
            trace_id="t3",
            raw_prompt="x",
            optimized_prompt="y",
            task_type="x",
            strategy_used="x",
            changes_summary="x",
            optimized_scores=_make_dim_scores(),
            original_scores=_make_dim_scores(),
            score_deltas={},
            overall_score=7.0,
            provider="x",
            model_used="x",
            scoring_mode="x",
            duration_ms=100,
            status="success",
            context_sources={},
        )
        after = datetime.now(timezone.utc)
        assert before <= result.created_at <= after


# ---------------------------------------------------------------------------
# PipelineEvent
# ---------------------------------------------------------------------------


class TestPipelineEvent:
    def test_valid(self):
        event = PipelineEvent(event="stage_complete", data={"stage": "analyze", "duration_ms": 300})
        assert event.event == "stage_complete"
        assert event.data["stage"] == "analyze"

    def test_empty_data(self):
        event = PipelineEvent(event="heartbeat", data={})
        assert event.data == {}
