"""Tests for pipeline.py context propagation (P-prop).

These tests verify that file_contexts, url_fetched_contexts, and instructions
are forwarded from run_pipeline() to run_optimize(), not just to run_analyze().
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# P-prop-1: file/url/instruction contexts reach run_optimize
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_forwards_file_contexts_to_run_optimize():
    """run_pipeline must pass file_contexts, url_fetched_contexts, and
    instructions to run_optimize — not just to run_analyze.

    These three context types are attached by the user and must be available
    to the optimizer so it can produce grounded output.
    """
    from app.services.pipeline import run_pipeline

    file_contexts = [{"name": "schema.py", "content": "class User: ..."}]
    url_contexts = [{"url": "https://docs.example.com", "content": "API reference"}]
    instructions = ["Use Python typing", "Keep responses concise"]

    captured: dict = {}

    async def mock_analyze(*args, **kwargs):
        yield ("analysis", {
            "task_type": "coding",
            "complexity": "moderate",
            "weaknesses": [],
            "strengths": [],
            "recommended_frameworks": [],
        })

    async def mock_strategy(*args, **kwargs):
        yield ("strategy", {
            "primary_framework": "CO-STAR",
            "secondary_frameworks": [],
            "rationale": "test",
            "approach_notes": "",
        })

    async def mock_optimize(*args, **kwargs):
        captured.update(kwargs)
        yield ("optimization", {
            "optimized_prompt": "improved prompt",
            "changes_made": [],
            "framework_applied": "CO-STAR",
            "optimization_notes": "",
        })

    async def mock_validate(*args, **kwargs):
        yield ("validation", {
            "scores": {
                "clarity_score": 8,
                "specificity_score": 8,
                "structure_score": 8,
                "faithfulness_score": 8,
                "conciseness_score": 8,
                "overall_score": 8,
            },
            "is_improvement": True,
            "verdict": "improved",
            "issues": [],
        })

    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate):
        async for _ in run_pipeline(
            provider=MagicMock(),
            raw_prompt="Optimize this prompt for a Python service",
            optimization_id="test-opt-id",
            file_contexts=file_contexts,
            url_fetched_contexts=url_contexts,
            instructions=instructions,
        ):
            pass

    assert captured.get("file_contexts") == file_contexts, \
        "run_pipeline must pass file_contexts to run_optimize"
    assert captured.get("url_fetched_contexts") == url_contexts, \
        "run_pipeline must pass url_fetched_contexts to run_optimize"
    assert captured.get("instructions") == instructions, \
        "run_pipeline must pass instructions to run_optimize"


# ---------------------------------------------------------------------------
# P-prop-2: context propagation survives the retry path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_retry_also_forwards_file_contexts():
    """The low-score retry path must also forward file_contexts to run_optimize.

    When overall_score < LOW_SCORE_THRESHOLD the pipeline re-runs optimize.
    That retry call must also receive the user's attached context.
    """
    from app.services.pipeline import run_pipeline

    file_contexts = [{"name": "model.py", "content": "class Item: ..."}]
    captured_calls: list[dict] = []

    async def mock_analyze(*args, **kwargs):
        yield ("analysis", {
            "task_type": "coding",
            "complexity": "moderate",
            "weaknesses": [],
            "strengths": [],
            "recommended_frameworks": [],
        })

    async def mock_strategy(*args, **kwargs):
        yield ("strategy", {
            "primary_framework": "CO-STAR",
            "secondary_frameworks": [],
            "rationale": "test",
            "approach_notes": "",
        })

    async def mock_optimize(*args, **kwargs):
        captured_calls.append(dict(kwargs))
        yield ("optimization", {
            "optimized_prompt": "low quality",
            "changes_made": [],
            "framework_applied": "CO-STAR",
            "optimization_notes": "",
        })

    call_count = 0

    async def mock_validate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call: low score to trigger retry; second: high score to stop
        score = 3 if call_count == 1 else 8
        yield ("validation", {
            "scores": {
                "clarity_score": score,
                "specificity_score": score,
                "structure_score": score,
                "faithfulness_score": score,
                "conciseness_score": score,
                "overall_score": score,
            },
            "is_improvement": False if call_count == 1 else True,
            "verdict": "needs work" if call_count == 1 else "improved",
            "issues": ["too vague"] if call_count == 1 else [],
        })

    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.settings") as mock_settings:
        mock_settings.MAX_PIPELINE_RETRIES = 1
        mock_settings.ANALYZE_TIMEOUT_SECONDS = 30
        mock_settings.STRATEGY_TIMEOUT_SECONDS = 30

        async for _ in run_pipeline(
            provider=MagicMock(),
            raw_prompt="Test prompt",
            optimization_id="retry-test-id",
            file_contexts=file_contexts,
        ):
            pass

    # Both the initial and retry calls should have file_contexts
    assert len(captured_calls) == 2, "Expected optimize to be called twice (initial + retry)"
    for i, call_kwargs in enumerate(captured_calls):
        assert call_kwargs.get("file_contexts") == file_contexts, \
            f"Optimize call #{i + 1} must receive file_contexts"


# ---------------------------------------------------------------------------
# P-sentinel-1: optimizer failure sentinel causes validate skip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_optimizer_failure_skips_validate():
    """When optimizer emits optimization_failed=True, validate stage must be skipped."""
    from app.services.pipeline import run_pipeline

    provider = MagicMock()

    async def mock_analyze(*args, **kwargs):
        yield ("analysis", {
            "task_type": "general",
            "complexity": "moderate",
            "weaknesses": [],
            "strengths": [],
            "recommended_frameworks": ["CO-STAR"],
            "codebase_informed": False,
        })

    async def mock_strategy(*args, **kwargs):
        yield ("strategy", {
            "primary_framework": "CO-STAR",
            "secondary_frameworks": [],
            "rationale": "test",
            "approach_notes": "test",
        })

    async def mock_optimize(*args, **kwargs):
        yield ("optimization", {
            "optimized_prompt": "",
            "changes_made": [],
            "framework_applied": "CO-STAR",
            "optimization_notes": "",
            "optimization_failed": True,
        })

    events = []
    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize):
        async for event_type, event_data in run_pipeline(
            provider=provider,
            raw_prompt="test prompt",
            optimization_id="test-123",
        ):
            events.append((event_type, event_data))

    # validate stage event must be present with status="skipped"
    stage_events = [(et, ed) for et, ed in events if et == "stage"]
    validate_events = [(et, ed) for et, ed in stage_events if ed.get("stage") == "validate"]
    assert len(validate_events) == 1
    assert validate_events[0][1]["status"] == "skipped"

    # error event should be emitted for the optimize stage with recoverable=False
    error_events = [(et, ed) for et, ed in events if et == "error"]
    assert len(error_events) >= 1
    opt_error = next((ed for et, ed in error_events if ed.get("stage") == "optimize"), None)
    assert opt_error is not None
    assert opt_error["recoverable"] is False


# ---------------------------------------------------------------------------
# P-default-1: empty analysis gets default task_type and complexity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_analysis_defaults_to_general():
    """When run_analyze yields {} (no task_type), pipeline defaults to 'general'."""
    from app.services.pipeline import run_pipeline

    provider = MagicMock()

    async def mock_analyze_empty(*args, **kwargs):
        yield ("analysis", {})

    received_analysis: dict = {}

    async def mock_strategy(provider, raw_prompt, analysis, **kwargs):
        received_analysis.update(analysis)
        yield ("strategy", {
            "primary_framework": "CO-STAR",
            "secondary_frameworks": [],
            "rationale": "test",
            "approach_notes": "test",
        })

    async def mock_optimize(*args, **kwargs):
        yield ("optimization", {
            "optimized_prompt": "optimized",
            "changes_made": [],
            "framework_applied": "CO-STAR",
            "optimization_notes": "",
            "optimization_failed": False,
        })

    async def mock_validate(*args, **kwargs):
        yield ("validation", {
            "scores": {
                "clarity_score": 8,
                "specificity_score": 8,
                "structure_score": 8,
                "faithfulness_score": 8,
                "conciseness_score": 8,
                "overall_score": 8,
            },
            "overall_score": 8,
            "is_improvement": True,
            "verdict": "Good",
            "issues": [],
        })

    events = []
    with patch("app.services.pipeline.run_analyze", mock_analyze_empty), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate):
        async for event_type, event_data in run_pipeline(
            provider=provider,
            raw_prompt="test prompt",
            optimization_id="test-456",
        ):
            events.append((event_type, event_data))

    # The analysis event emitted by the pipeline should have the defaulted fields
    analysis_events = [(et, ed) for et, ed in events if et == "analysis"]
    assert len(analysis_events) == 1
    assert analysis_events[0][1]["task_type"] == "general"
    assert analysis_events[0][1]["complexity"] == "moderate"

    # Strategy should have received the defaulted analysis
    assert received_analysis.get("task_type") == "general"
