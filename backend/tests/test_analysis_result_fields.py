"""Tests for intent_label and domain fields on AnalysisResult (Task 2)."""

import pytest
from pydantic import ValidationError

from app.schemas.pipeline_contracts import AnalysisResult


def test_analysis_result_with_intent_label_and_domain():
    """AnalysisResult accepts and exposes intent_label and domain when provided."""
    result = AnalysisResult(
        task_type="coding",
        weaknesses=["no output format specified"],
        strengths=["clear context provided"],
        selected_strategy="chain-of-thought",
        strategy_rationale="Step-by-step reasoning helps with coding tasks.",
        confidence=0.9,
        intent_label="dependency injection refactoring",
        domain="backend",
    )
    assert result.intent_label == "dependency injection refactoring"
    assert result.domain == "backend"


def test_analysis_result_defaults_without_new_fields():
    """AnalysisResult defaults intent_label and domain to 'general' when omitted."""
    result = AnalysisResult(
        task_type="general",
        weaknesses=[],
        strengths=["concise"],
        selected_strategy="auto",
        strategy_rationale="No strong signal; auto is safest.",
        confidence=0.5,
    )
    assert result.intent_label == "general"
    assert result.domain == "general"


def test_analysis_result_rejects_unknown_fields():
    """AnalysisResult raises ValidationError for unexpected fields (extra='forbid')."""
    with pytest.raises(ValidationError):
        AnalysisResult(
            task_type="coding",
            weaknesses=[],
            strengths=[],
            selected_strategy="auto",
            strategy_rationale="Fine.",
            confidence=0.8,
            unknown_field="should_fail",
        )
