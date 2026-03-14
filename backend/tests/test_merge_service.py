"""Tests for merge_service — LLM prompt construction."""

import pytest
from app.services.merge_service import build_merge_system_prompt
from app.schemas.compare_models import (
    CompareResponse, CompareGuidance, ScoreComparison,
    StructuralComparison, EfficiencyComparison, StrategyComparison,
    ContextComparison, ValidationComparison, AdaptationComparison,
)


def _build_mock_compare_response() -> CompareResponse:
    return CompareResponse(
        situation="STRATEGY",
        situation_label="Framework head-to-head",
        insight_headline="CO-STAR vs RISEN — +0.8 overall",
        modifiers=["adapted"],
        a={"id": "a1", "optimized_prompt": "Prompt A text about API security review", "raw_prompt": "review my api"},
        b={"id": "b1", "optimized_prompt": "Prompt B text about API security review", "raw_prompt": "review my api"},
        scores=ScoreComparison(
            dimensions=["clarity", "faithfulness", "specificity", "structure", "conciseness"],
            a_scores={"clarity": 8.5, "faithfulness": 7.5, "specificity": 8.0, "structure": 9.0, "conciseness": 7.0},
            b_scores={"clarity": 6.5, "faithfulness": 8.0, "specificity": 7.0, "structure": 7.5, "conciseness": 7.0},
            deltas={"clarity": 2.0, "faithfulness": -0.5, "specificity": 1.0, "structure": 1.5, "conciseness": 0.0},
            overall_delta=0.8, winner="a", ceilings=[], floors=[],
        ),
        structural=StructuralComparison(
            a_input_words=45, b_input_words=120, a_output_words=144, b_output_words=252,
            a_expansion=3.2, b_expansion=2.1, a_complexity="basic", b_complexity="intermediate",
        ),
        efficiency=EfficiencyComparison(
            a_duration_ms=4100, b_duration_ms=6500, a_tokens=2100, b_tokens=2600,
            a_cost=0.008, b_cost=0.011, a_score_per_token=3.8, b_score_per_token=2.8,
        ),
        strategy=StrategyComparison(
            a_framework="CO-STAR", a_source="llm", a_rationale="Context grounding",
            a_guardrails=["clarity-focus"], b_framework="RISEN", b_source="heuristic",
            b_rationale="Role-based", b_guardrails=[],
        ),
        context=ContextComparison(
            a_repo=None, b_repo="owner/repo", a_has_codebase=False, b_has_codebase=True,
            a_instruction_count=0, b_instruction_count=2,
        ),
        validation=ValidationComparison(
            a_verdict="Strong improvement", b_verdict="Moderate improvement",
            a_issues=[], b_issues=["verbose"], a_changes_made=["added context section"],
            b_changes_made=["added role persona"], a_is_improvement=True, b_is_improvement=True,
        ),
        adaptation=AdaptationComparison(feedbacks_between=3, weight_shifts={"clarity": 0.08}, guardrails_added=["clarity-focus"]),
        top_insights=["Clarity gap is structural", "Both 7.0 conciseness", "Repo context ROI marginal"],
        cross_patterns=[],
        a_is_trashed=False, b_is_trashed=False,
        guidance=CompareGuidance(
            headline="CO-STAR clarity +2.0; RISEN faithfulness +0.5",
            merge_suggestion="Combine CO-STAR context with RISEN role anchoring",
            strengths_a=["clarity", "structure", "specificity"],
            strengths_b=["faithfulness"],
            persistent_weaknesses=["conciseness"],
            actionable=["Clarity gap is structural", "Add word-limit"],
            merge_directives=["Preserve clarity sections", "Inject role definition", "Add format constraint"],
        ),
    )


class TestMergePromptConstruction:
    def test_includes_all_intelligence_sections(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "SITUATION" in prompt
        assert "SCORE INTELLIGENCE" in prompt
        assert "STRUCTURAL INTELLIGENCE" in prompt
        assert "STRATEGY INTELLIGENCE" in prompt
        assert "CONTEXT INTELLIGENCE" in prompt
        assert "ADAPTATION INTELLIGENCE" in prompt
        assert "EFFICIENCY INTELLIGENCE" in prompt
        assert "VALIDATION INTELLIGENCE" in prompt
        assert "MERGE DIRECTIVES" in prompt
        assert "DIMENSION TARGETS" in prompt
        assert "CONSTRAINTS" in prompt

    def test_directives_included(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "Preserve clarity sections" in prompt
        assert "Inject role definition" in prompt

    def test_dimension_targets_present(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "8.5" in prompt  # clarity target from A (winner)
        assert "8.0" in prompt  # faithfulness target from B (winner)

    def test_preamble_present(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "master-class prompt synthesis engine" in prompt.lower() or "prompt synthesis" in prompt.lower()

    def test_constraints_section_present(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "Output ONLY" in prompt or "output only" in prompt.lower()

    def test_scores_table_data(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        # Framework names should appear in strategy section
        assert "CO-STAR" in prompt
        assert "RISEN" in prompt
        # Score deltas should appear
        assert "2.0" in prompt  # clarity delta

    def test_structural_data(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "144" in prompt  # a_output_words
        assert "252" in prompt  # b_output_words
        assert "3.2" in prompt  # a_expansion
        assert "2.1" in prompt  # b_expansion

    def test_efficiency_data(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "4100" in prompt or "4.1" in prompt  # a_duration_ms (raw or seconds)
        assert "2100" in prompt  # a_tokens
        assert "3.8" in prompt  # a_score_per_token

    def test_adaptation_data(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "3" in prompt  # feedbacks_between
        assert "clarity" in prompt.lower()  # weight shift dimension

    def test_validation_data(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "Strong improvement" in prompt
        assert "Moderate improvement" in prompt

    def test_no_guidance_still_works(self):
        compare = _build_mock_compare_response()
        compare.guidance = None
        prompt = build_merge_system_prompt(compare)
        # Should still include all sections, just with empty/fallback directives
        assert "SITUATION" in prompt
        assert "MERGE DIRECTIVES" in prompt
        assert "CONSTRAINTS" in prompt

    def test_situation_classification_present(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "STRATEGY" in prompt
        assert "Framework head-to-head" in prompt

    def test_context_intelligence_repo_info(self):
        compare = _build_mock_compare_response()
        prompt = build_merge_system_prompt(compare)
        assert "owner/repo" in prompt
