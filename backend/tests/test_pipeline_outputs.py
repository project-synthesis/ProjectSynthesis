"""Tests for Pydantic pipeline output models (L8).

Validates schema generation, field constraints, default values,
round-trip serialization, and extra-field rejection.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.pipeline_outputs import (
    AnalyzeOutput,
    ExploreSynthesisOutput,
    IntentClassificationOutput,
    OptimizeFallbackOutput,
    StrategyOutput,
    ValidateOutput,
)

# ---------------------------------------------------------------------------
# L8-schema — JSON Schema generation with additionalProperties: false
# ---------------------------------------------------------------------------


def test_intent_classification_schema_has_additional_properties_false():
    schema = IntentClassificationOutput.model_json_schema()
    assert schema.get("additionalProperties") is False
    assert "intent_category" in schema["properties"]


def test_explore_synthesis_schema_has_additional_properties_false():
    schema = ExploreSynthesisOutput.model_json_schema()
    assert schema.get("additionalProperties") is False


def test_analyze_schema_has_additional_properties_false():
    schema = AnalyzeOutput.model_json_schema()
    assert schema.get("additionalProperties") is False


def test_strategy_schema_has_additional_properties_false():
    schema = StrategyOutput.model_json_schema()
    assert schema.get("additionalProperties") is False


def test_validate_schema_has_additional_properties_false():
    schema = ValidateOutput.model_json_schema()
    assert schema.get("additionalProperties") is False


def test_optimize_fallback_schema_has_additional_properties_false():
    schema = OptimizeFallbackOutput.model_json_schema()
    assert schema.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# L8-nested — CodeSnippet nested in ExploreSynthesisOutput uses $defs/$ref
# ---------------------------------------------------------------------------


def test_explore_schema_uses_defs_for_code_snippet():
    schema = ExploreSynthesisOutput.model_json_schema()
    # Pydantic generates $defs for nested models
    assert "$defs" in schema
    assert "CodeSnippet" in schema["$defs"]


# ---------------------------------------------------------------------------
# L8-validate-scores — ValidateOutput rejects scores outside 1-10
# ---------------------------------------------------------------------------


def test_validate_output_rejects_score_below_minimum():
    with pytest.raises(ValidationError) as exc_info:
        ValidateOutput(clarity_score=0)
    assert "clarity_score" in str(exc_info.value)


def test_validate_output_rejects_score_above_maximum():
    with pytest.raises(ValidationError) as exc_info:
        ValidateOutput(specificity_score=11)
    assert "specificity_score" in str(exc_info.value)


def test_validate_output_accepts_boundary_scores():
    v = ValidateOutput(clarity_score=1, specificity_score=10)
    assert v.clarity_score == 1
    assert v.specificity_score == 10


# ---------------------------------------------------------------------------
# L8-extra-forbid — extra="forbid" rejects unknown fields
# ---------------------------------------------------------------------------


def test_analyze_output_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AnalyzeOutput(task_type="general", unknown_field="oops")


def test_validate_output_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ValidateOutput(clarity_score=5, overall_score=7.5)  # overall_score not in model


def test_strategy_output_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        StrategyOutput(primary_framework="CRISPE", extra_thing=True)


# ---------------------------------------------------------------------------
# L8-defaults — Default values populate correctly
# ---------------------------------------------------------------------------


def test_analyze_output_defaults():
    a = AnalyzeOutput()
    assert a.task_type == "general"
    assert a.complexity == "moderate"
    assert a.weaknesses == []
    assert a.strengths == []
    assert a.recommended_frameworks == []


def test_validate_output_defaults():
    v = ValidateOutput()
    assert v.clarity_score == 5
    assert v.specificity_score == 5
    assert v.structure_score == 5
    assert v.faithfulness_score == 5
    assert v.conciseness_score == 5
    assert v.is_improvement is False
    assert v.verdict == ""
    assert v.issues == []


def test_strategy_output_defaults():
    s = StrategyOutput(primary_framework="CRISPE")
    assert s.secondary_frameworks == []
    assert s.rationale == ""
    assert s.approach_notes == ""


def test_explore_synthesis_defaults():
    e = ExploreSynthesisOutput(
        tech_stack=["Python"],
        key_files_read=["main.py"],
        codebase_observations=["uses FastAPI"],
        prompt_grounding_notes=["entry point is main.py"],
    )
    assert e.relevant_code_snippets == []
    assert e.coverage_pct is None


# ---------------------------------------------------------------------------
# L8-roundtrip — model_dump() round-trips correctly
# ---------------------------------------------------------------------------


def test_analyze_output_roundtrip():
    data = {
        "task_type": "instruction",
        "complexity": "simple",
        "weaknesses": ["no examples"],
        "strengths": ["clear intent"],
        "recommended_frameworks": ["CRISPE"],
    }
    a = AnalyzeOutput.model_validate(data)
    dumped = a.model_dump()
    assert dumped == data
    # Re-validate from dump
    a2 = AnalyzeOutput.model_validate(dumped)
    assert a2 == a


def test_validate_output_roundtrip():
    data = {
        "clarity_score": 8,
        "specificity_score": 7,
        "structure_score": 6,
        "faithfulness_score": 9,
        "conciseness_score": 7,
        "is_improvement": True,
        "verdict": "Good job",
        "issues": [],
    }
    v = ValidateOutput.model_validate(data)
    dumped = v.model_dump()
    assert dumped == data


def test_explore_synthesis_roundtrip_with_snippets():
    data = {
        "tech_stack": ["Python", "FastAPI"],
        "key_files_read": ["main.py", "config.py"],
        "relevant_code_snippets": [
            {"file": "main.py", "lines": "1-10", "context": "entry point"},
        ],
        "codebase_observations": ["uses async"],
        "prompt_grounding_notes": ["main.py is the entry"],
        "coverage_pct": 42,
    }
    e = ExploreSynthesisOutput.model_validate(data)
    dumped = e.model_dump()
    assert dumped == data


def test_optimize_fallback_roundtrip():
    data = {
        "optimized_prompt": "Be specific and clear.",
        "changes_made": ["Added role context"],
        "framework_applied": "CRISPE",
        "optimization_notes": "Applied instruction framework",
    }
    o = OptimizeFallbackOutput.model_validate(data)
    dumped = o.model_dump()
    assert dumped == data
