"""Tests for PipelineResult validation edge cases."""

from types import MappingProxyType

from app.schemas.pipeline_contracts import PipelineResult


def _base_kwargs() -> dict:
    """Minimum required fields for PipelineResult."""
    return dict(
        id="test-id",
        trace_id="trace-1",
        raw_prompt="test prompt",
        optimized_prompt="optimized prompt",
        task_type="coding",
        strategy_used="auto",
        changes_summary="improved clarity",
        provider="mock",
        model_used="test-model",
        scoring_mode="skipped",
        duration_ms=1000,
        status="completed",
        context_sources={},
    )


def test_pipeline_result_with_bool_context_sources():
    """Pure boolean flags are accepted (current 4-key schema)."""
    result = PipelineResult(
        **{**_base_kwargs(), "context_sources": {
            "codebase_context": True,
            "strategy_intelligence": False,
            "applied_patterns": True,
            "heuristic_analysis": True,
        }},
    )
    assert result.context_sources["codebase_context"] is True
    assert result.context_sources["strategy_intelligence"] is False


def test_pipeline_result_with_mixed_context_sources():
    """Reproduces the bug: enrichment_meta is a nested dict inside context_sources."""
    sources = {
        "codebase_context": True,
        "strategy_intelligence": True,
        "heuristic_analysis": True,
        "applied_patterns": False,
        "enrichment_meta": {
            "enrichment_profile": "code_aware",
            "repo_full_name": "owner/repo",
            "repo_branch": "main",
            "was_truncated": False,
        },
    }
    result = PipelineResult(**{**_base_kwargs(), "context_sources": sources})
    assert result.context_sources["enrichment_meta"]["repo_full_name"] == "owner/repo"
    assert result.context_sources["enrichment_meta"]["enrichment_profile"] == "code_aware"


def test_pipeline_result_with_none_context_sources():
    """None is coerced to empty dict by the field_validator."""
    result = PipelineResult(**{**_base_kwargs(), "context_sources": None})
    assert result.context_sources == {}


def test_pipeline_result_with_empty_context_sources():
    """Empty dict is accepted."""
    result = PipelineResult(**{**_base_kwargs(), "context_sources": {}})
    assert result.context_sources == {}


def test_pipeline_result_with_mapping_proxy():
    """MappingProxyType is coerced to plain dict."""
    sources = MappingProxyType({
        "codebase_context": True,
        "strategy_intelligence": False,
        "applied_patterns": True,
        "heuristic_analysis": True,
    })
    result = PipelineResult(**{**_base_kwargs(), "context_sources": sources})
    assert isinstance(result.context_sources, dict)
    assert result.context_sources["codebase_context"] is True
