"""Tests for domain mapping integration in the pipeline."""

import pytest
from app.schemas.pipeline_contracts import AnalysisResult


def test_analysis_result_accepts_freetext_domain():
    """AnalysisResult.domain should accept any string, not just DomainType values."""
    result = AnalysisResult(
        task_type="coding",
        weaknesses=["none"],
        strengths=["good"],
        selected_strategy="auto",
        strategy_rationale="test",
        confidence=0.9,
        intent_label="REST API design",
        domain="REST API design",  # free-text, not one of the old 7 values
    )
    assert result.domain == "REST API design"


def test_analysis_result_still_accepts_legacy_domains():
    """Legacy domain values like 'backend' still work as plain strings."""
    result = AnalysisResult(
        task_type="coding",
        weaknesses=["none"],
        strengths=["good"],
        selected_strategy="auto",
        strategy_rationale="test",
        confidence=0.9,
        domain="backend",
    )
    assert result.domain == "backend"
