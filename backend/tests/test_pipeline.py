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
