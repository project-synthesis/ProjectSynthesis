"""Tests for pipeline Pydantic contracts (Section 12)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.pipeline_contracts import (
    AnalysisResult,
    AnalyzerInput,
    DimensionScores,
    OptimizerInput,
    OptimizationResult,
    PipelineEvent,
    PipelineResult,
    ResolvedContext,
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
        # mean of [8.0, 6.0, 7.0, 9.0, 5.0] = 35/5 = 7.0
        assert scores.overall == 7.0

    def test_overall_rounded_to_2_decimals(self):
        scores = DimensionScores(
            clarity=7.0,
            specificity=8.0,
            structure=6.0,
            faithfulness=9.0,
            conciseness=5.0,
        )
        # mean = 35/5 = 7.0 — exact; use a case that needs rounding
        scores2 = DimensionScores(
            clarity=7.1,
            specificity=8.2,
            structure=6.3,
            faithfulness=9.4,
            conciseness=5.0,
        )
        # 7.1 + 8.2 + 6.3 + 9.4 + 5.0 = 36.0 / 5 = 7.2
        assert scores2.overall == 7.2

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
            task_type="code_generation",
            weaknesses=["vague scope"],
            strengths=["clear goal"],
            selected_strategy="chain_of_thought",
            strategy_rationale="Requires step-by-step reasoning.",
            confidence=0.85,
        )
        assert result.task_type == "code_generation"
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
            strategy_used="chain_of_thought",
        )
        assert result.strategy_used == "chain_of_thought"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            OptimizationResult(
                optimized_prompt="x",
                changes_summary="x",
                strategy_used="x",
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
            available_strategies=["chain_of_thought", "few_shot"],
        )
        assert inp.strategy_override is None

    def test_valid_with_override(self):
        inp = AnalyzerInput(
            raw_prompt="Summarise this document.",
            strategy_override="few_shot",
            available_strategies=["chain_of_thought", "few_shot"],
        )
        assert inp.strategy_override == "few_shot"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AnalyzerInput(
                raw_prompt="x",
                available_strategies=[],
                bad_field=True,
            )


# ---------------------------------------------------------------------------
# OptimizerInput
# ---------------------------------------------------------------------------


class TestOptimizerInput:
    def _make_analysis(self) -> AnalysisResult:
        return AnalysisResult(
            task_type="summarisation",
            weaknesses=[],
            strengths=["concise"],
            selected_strategy="few_shot",
            strategy_rationale="Examples help.",
            confidence=0.9,
        )

    def test_valid_minimal(self):
        inp = OptimizerInput(
            raw_prompt="Do something.",
            analysis=self._make_analysis(),
            analysis_summary="Short summary.",
            strategy_instructions="Use few-shot.",
        )
        assert inp.codebase_guidance is None
        assert inp.codebase_context is None
        assert inp.adaptation_state is None

    def test_valid_with_optionals(self):
        inp = OptimizerInput(
            raw_prompt="Do something.",
            analysis=self._make_analysis(),
            analysis_summary="Short summary.",
            strategy_instructions="Use few-shot.",
            codebase_guidance="Focus on X.",
            codebase_context="Context here.",
            adaptation_state="state blob",
        )
        assert inp.codebase_guidance == "Focus on X."

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            OptimizerInput(
                raw_prompt="x",
                analysis=self._make_analysis(),
                analysis_summary="x",
                strategy_instructions="x",
                unknown="nope",
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
# ResolvedContext
# ---------------------------------------------------------------------------


class TestResolvedContext:
    def test_minimal(self):
        ctx = ResolvedContext(
            raw_prompt="Hello world.",
            trace_id="trace-123",
        )
        assert ctx.strategy_override is None
        assert ctx.codebase_guidance is None
        assert ctx.codebase_context is None
        assert ctx.adaptation_state is None
        assert ctx.context_sources == {}

    def test_extra_fields_accepted(self):
        # ResolvedContext does NOT use extra="forbid"
        ctx = ResolvedContext(
            raw_prompt="Hello.",
            trace_id="t1",
            future_field="allowed",
        )
        assert ctx.raw_prompt == "Hello."


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
            task_type="code_generation",
            strategy_used="chain_of_thought",
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
