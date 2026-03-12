"""Tests for pipeline.py — context propagation, retry logic, settings wiring, and context builders.

P-prop  — context forwarding from run_pipeline to downstream stages
P-sentinel — optimizer failure sentinel gating
P-default — default analysis values
P-retry-err — retry exception error event emission
P-settings — settings wiring (model override, max_retries, default_strategy, streaming)
P-ctx — context_builders edge cases
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _full_settings(**overrides) -> dict:
    """Return a complete settings dict with optional overrides.

    Every test that mocks load_settings should use this so the pipeline's
    top-of-function settings load always gets a valid dict.
    """
    defaults = {
        "default_model": "auto",
        "pipeline_timeout": 300,
        "max_retries": 1,
        "default_strategy": None,
        "auto_validate": True,
        "stream_optimize": True,
    }
    defaults.update(overrides)
    return defaults

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
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.load_settings", return_value=_full_settings()):
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
         patch("app.services.pipeline.load_settings", return_value={"auto_validate": True}), \
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
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.load_settings", return_value=_full_settings()):
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
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.load_settings", return_value=_full_settings()):
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


# ---------------------------------------------------------------------------
# P-retry-err-1: retry exception emits error event (not silent break)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_exception_emits_error_event():
    """When the retry validate stage raises, an error event must be emitted.

    Previously the except handler only logged + broke, leaving the client
    with a dangling 'validate started' stage and no error signal.
    """
    from app.services.pipeline import run_pipeline

    provider = MagicMock()

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
        yield ("optimization", {
            "optimized_prompt": "some prompt",
            "changes_made": [],
            "framework_applied": "CO-STAR",
            "optimization_notes": "",
        })

    validate_call_count = 0

    async def mock_validate(*args, **kwargs):
        nonlocal validate_call_count
        validate_call_count += 1
        if validate_call_count == 1:
            # First call: low score to trigger retry
            yield ("validation", {
                "scores": {
                    "clarity_score": 3,
                    "specificity_score": 3,
                    "structure_score": 3,
                    "faithfulness_score": 3,
                    "conciseness_score": 3,
                    "overall_score": 3,
                },
                "is_improvement": False,
                "verdict": "needs work",
                "issues": ["too vague"],
            })
        else:
            # Second call (retry): raise to simulate provider failure
            raise RuntimeError("Provider connection lost")
            yield  # unreachable — makes this an async generator function

    events = []
    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.load_settings", return_value={"auto_validate": True}), \
         patch("app.services.pipeline.settings") as mock_settings:
        mock_settings.MAX_PIPELINE_RETRIES = 1
        mock_settings.ANALYZE_TIMEOUT_SECONDS = 30
        mock_settings.STRATEGY_TIMEOUT_SECONDS = 30

        async for event_type, event_data in run_pipeline(
            provider=provider,
            raw_prompt="Test prompt",
            optimization_id="retry-err-test",
        ):
            events.append((event_type, event_data))

    # An error event must be emitted for the failed retry
    error_events = [(et, ed) for et, ed in events if et == "error"]
    retry_errors = [ed for _, ed in error_events if ed.get("recoverable") is True]
    assert len(retry_errors) >= 1, \
        "Retry exception must emit a recoverable error event"
    assert "validate" in retry_errors[0]["stage"], \
        "Error event must identify the validate stage"


# ---------------------------------------------------------------------------
# P-skip-validate: auto_validate=False skips Stage 4
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_skips_validate_when_auto_validate_false():
    """When auto_validate is disabled in settings, Stage 4 should be skipped."""
    from app.services.pipeline import run_pipeline

    async def mock_analyze(*args, **kwargs):
        yield ("analysis", {"task_type": "general", "complexity": "simple",
                            "weaknesses": [], "strengths": [], "recommended_frameworks": []})

    async def mock_strategy(*args, **kwargs):
        yield ("strategy", {"primary_framework": "CO-STAR", "secondary_frameworks": [],
                            "rationale": "test", "approach_notes": ""})

    async def mock_optimize(*args, **kwargs):
        yield ("optimization", {"optimized_prompt": "improved", "changes_made": [],
                                "framework_applied": "CO-STAR", "optimization_notes": ""})

    validate_called = False

    async def mock_validate(*args, **kwargs):
        nonlocal validate_called
        validate_called = True
        yield ("validation", {"scores": {"overall_score": 8}, "is_improvement": True,
                              "verdict": "good", "issues": []})

    events = []
    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.load_settings", return_value=_full_settings(auto_validate=False)):
        async for event_type, event_data in run_pipeline(
            provider=MagicMock(),
            raw_prompt="Test prompt",
            optimization_id="skip-validate-test",
        ):
            events.append((event_type, event_data))

    assert not validate_called, "run_validate must not be called when auto_validate is False"

    # Validate stage must be emitted as skipped
    validate_stages = [(et, ed) for et, ed in events
                       if et == "stage" and ed.get("stage") == "validate"]
    assert len(validate_stages) == 1
    assert validate_stages[0][1]["status"] == "skipped"


# ---------------------------------------------------------------------------
# P-ctx-1: format_file_contexts filters None and empty content
# ---------------------------------------------------------------------------

def test_format_file_contexts_filters_none_content():
    """format_file_contexts must skip items with None or empty content."""
    from app.services.context_builders import format_file_contexts

    contexts = [
        {"name": "valid.py", "content": "class Foo: pass"},
        {"name": "none_content.py", "content": None},
        {"name": "empty_content.py", "content": ""},
        {"name": "missing_content.py"},  # no content key at all
    ]
    result = format_file_contexts(contexts)

    # Only the valid item should appear
    assert "valid.py" in result
    assert "class Foo: pass" in result
    assert "none_content.py" not in result
    assert "empty_content.py" not in result
    assert "missing_content.py" not in result


def test_format_file_contexts_empty_input():
    """format_file_contexts returns empty string for None and empty list."""
    from app.services.context_builders import format_file_contexts

    assert format_file_contexts(None) == ""
    assert format_file_contexts([]) == ""


def test_format_file_contexts_all_empty_returns_empty():
    """format_file_contexts returns empty string when all items have empty content."""
    from app.services.context_builders import format_file_contexts

    contexts = [
        {"name": "a.py", "content": ""},
        {"name": "b.py", "content": None},
    ]
    assert format_file_contexts(contexts) == ""


# ---------------------------------------------------------------------------
# P-ctx-2: format_instructions shared helper
# ---------------------------------------------------------------------------

def test_format_instructions_basic():
    """format_instructions produces the expected block."""
    from app.services.context_builders import format_instructions

    result = format_instructions(["Use Python", "Be concise"])
    assert "User-specified output constraints:" in result
    assert "  - Use Python" in result
    assert "  - Be concise" in result


def test_format_instructions_empty():
    """format_instructions returns empty string for None and empty list."""
    from app.services.context_builders import format_instructions

    assert format_instructions(None) == ""
    assert format_instructions([]) == ""


def test_format_instructions_custom_label():
    """format_instructions supports custom label."""
    from app.services.context_builders import format_instructions

    result = format_instructions(["constraint"], label="Custom label")
    assert "Custom label:" in result
    assert "  - constraint" in result


def test_format_instructions_caps_at_10():
    """format_instructions caps items at MAX_INSTRUCTIONS (10)."""
    from app.services.context_builders import format_instructions

    items = [f"item_{i}" for i in range(15)]
    result = format_instructions(items)
    assert "item_9" in result
    assert "item_10" not in result


# ---------------------------------------------------------------------------
# P-ctx-3: context builder edge cases
# ---------------------------------------------------------------------------

def test_build_codebase_summary_none_returns_empty():
    """build_codebase_summary returns empty string for None input."""
    from app.services.context_builders import build_codebase_summary

    assert build_codebase_summary(None) == ""
    assert build_codebase_summary({}) == ""


def test_build_analysis_summary_quality_flags():
    """build_analysis_summary emits correct caveats for quality flags."""
    from app.services.context_builders import build_analysis_summary

    fallback = build_analysis_summary({"analysis_quality": "fallback"})
    assert "fell back to defaults" in fallback

    failed = build_analysis_summary({"analysis_quality": "failed"})
    assert "failed completely" in failed

    # 'full' and 'cached' should NOT produce warnings
    full = build_analysis_summary({"analysis_quality": "full", "task_type": "coding"})
    assert "fell back" not in full
    assert "failed" not in full


def test_build_analysis_summary_codebase_informed():
    """build_analysis_summary emits correct notes for codebase_informed values."""
    from app.services.context_builders import build_analysis_summary

    partial = build_analysis_summary({"codebase_informed": "partial"})
    assert "partially informed" in partial

    failed = build_analysis_summary({"codebase_informed": "failed"})
    assert "no codebase grounding" in failed

    no_cb = build_analysis_summary({"codebase_informed": False})
    assert "no codebase grounding" in no_cb

    # True should NOT produce a warning
    with_cb = build_analysis_summary({"codebase_informed": True, "task_type": "coding"})
    assert "partially" not in with_cb
    assert "grounding" not in with_cb


def test_build_strategy_summary_empty_returns_empty():
    """build_strategy_summary returns empty string for None and empty dict."""
    from app.services.context_builders import build_strategy_summary

    assert build_strategy_summary(None) == ""
    assert build_strategy_summary({}) == ""


# ---------------------------------------------------------------------------
# P-settings-1: model override propagates to all stages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_model_override_propagates_to_stages():
    """When default_model is set to a specific model, all stage calls receive it."""
    from app.services.pipeline import run_pipeline

    captured_kwargs: dict[str, dict] = {}

    async def _capture(stage_name):
        async def _inner(*args, **kwargs):
            captured_kwargs[stage_name] = dict(kwargs)
            if stage_name == "analyze":
                yield ("analysis", {"task_type": "general", "complexity": "simple",
                                    "weaknesses": [], "strengths": [], "recommended_frameworks": []})
            elif stage_name == "strategy":
                yield ("strategy", {"primary_framework": "CO-STAR", "secondary_frameworks": [],
                                    "rationale": "test", "approach_notes": ""})
            elif stage_name == "optimize":
                yield ("optimization", {"optimized_prompt": "better", "changes_made": [],
                                        "framework_applied": "CO-STAR", "optimization_notes": ""})
            elif stage_name == "validate":
                yield ("validation", {"scores": {"clarity_score": 8, "specificity_score": 8,
                                                  "structure_score": 8, "faithfulness_score": 8,
                                                  "conciseness_score": 8, "overall_score": 8},
                                      "is_improvement": True, "verdict": "good", "issues": []})
        return _inner

    with patch("app.services.pipeline.run_analyze", await _capture("analyze")), \
         patch("app.services.pipeline.run_strategy", await _capture("strategy")), \
         patch("app.services.pipeline.run_optimize", await _capture("optimize")), \
         patch("app.services.pipeline.run_validate", await _capture("validate")), \
         patch("app.services.pipeline.load_settings",
               return_value=_full_settings(default_model="claude-haiku-4-5-20251001")):
        async for _ in run_pipeline(
            provider=MagicMock(),
            raw_prompt="Test",
            optimization_id="model-override-test",
        ):
            pass

    for stage in ("analyze", "strategy", "optimize", "validate"):
        assert captured_kwargs[stage].get("model") == "claude-haiku-4-5-20251001", \
            f"{stage} must receive model override"


# ---------------------------------------------------------------------------
# P-settings-2: max_retries=0 disables retry loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_max_retries_zero_disables_retry():
    """When max_retries is 0, no retry should occur even with a low score."""
    from app.services.pipeline import run_pipeline

    optimize_call_count = 0

    async def mock_analyze(*args, **kwargs):
        yield ("analysis", {"task_type": "general", "complexity": "simple",
                            "weaknesses": [], "strengths": [], "recommended_frameworks": []})

    async def mock_strategy(*args, **kwargs):
        yield ("strategy", {"primary_framework": "CO-STAR", "secondary_frameworks": [],
                            "rationale": "test", "approach_notes": ""})

    async def mock_optimize(*args, **kwargs):
        nonlocal optimize_call_count
        optimize_call_count += 1
        yield ("optimization", {"optimized_prompt": "mediocre", "changes_made": [],
                                "framework_applied": "CO-STAR", "optimization_notes": ""})

    async def mock_validate(*args, **kwargs):
        yield ("validation", {
            "scores": {"clarity_score": 3, "specificity_score": 3, "structure_score": 3,
                       "faithfulness_score": 3, "conciseness_score": 3, "overall_score": 3},
            "is_improvement": False, "verdict": "poor", "issues": ["vague"],
        })

    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.load_settings",
               return_value=_full_settings(max_retries=0)):
        async for _ in run_pipeline(
            provider=MagicMock(),
            raw_prompt="Test",
            optimization_id="no-retry-test",
        ):
            pass

    assert optimize_call_count == 1, "optimize must be called exactly once when max_retries=0"


# ---------------------------------------------------------------------------
# P-settings-3: default_strategy used when no strategy_override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_default_strategy_from_settings():
    """When default_strategy is set in settings and no strategy_override is passed,
    the pipeline should use it as an override (skip LLM strategy stage)."""
    from app.services.pipeline import run_pipeline

    strategy_llm_called = False

    async def mock_analyze(*args, **kwargs):
        yield ("analysis", {"task_type": "general", "complexity": "simple",
                            "weaknesses": [], "strengths": [], "recommended_frameworks": []})

    async def mock_strategy(*args, **kwargs):
        nonlocal strategy_llm_called
        strategy_llm_called = True
        yield ("strategy", {"primary_framework": "CO-STAR", "secondary_frameworks": [],
                            "rationale": "test", "approach_notes": ""})

    async def mock_optimize(*args, **kwargs):
        yield ("optimization", {"optimized_prompt": "better", "changes_made": [],
                                "framework_applied": "RISEN", "optimization_notes": ""})

    async def mock_validate(*args, **kwargs):
        yield ("validation", {
            "scores": {"clarity_score": 8, "specificity_score": 8, "structure_score": 8,
                       "faithfulness_score": 8, "conciseness_score": 8, "overall_score": 8},
            "is_improvement": True, "verdict": "good", "issues": [],
        })

    events = []
    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.load_settings",
               return_value=_full_settings(default_strategy="RISEN")):
        async for event_type, event_data in run_pipeline(
            provider=MagicMock(),
            raw_prompt="Test",
            optimization_id="default-strategy-test",
        ):
            events.append((event_type, event_data))

    assert not strategy_llm_called, \
        "LLM strategy stage must not run when default_strategy is set"

    # Strategy event should report the override
    strategy_events = [(et, ed) for et, ed in events if et == "strategy"]
    assert len(strategy_events) == 1
    assert strategy_events[0][1]["primary_framework"] == "RISEN"
    assert strategy_events[0][1]["strategy_source"] == "override"


# ---------------------------------------------------------------------------
# P-settings-4: stream_optimize=False passes streaming=False to optimizer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_stream_optimize_false():
    """When stream_optimize is False, optimizer receives streaming=False."""
    from app.services.pipeline import run_pipeline

    captured: dict = {}

    async def mock_analyze(*args, **kwargs):
        yield ("analysis", {"task_type": "general", "complexity": "simple",
                            "weaknesses": [], "strengths": [], "recommended_frameworks": []})

    async def mock_strategy(*args, **kwargs):
        yield ("strategy", {"primary_framework": "CO-STAR", "secondary_frameworks": [],
                            "rationale": "test", "approach_notes": ""})

    async def mock_optimize(*args, **kwargs):
        captured.update(kwargs)
        yield ("optimization", {"optimized_prompt": "better", "changes_made": [],
                                "framework_applied": "CO-STAR", "optimization_notes": ""})

    async def mock_validate(*args, **kwargs):
        yield ("validation", {
            "scores": {"clarity_score": 8, "specificity_score": 8, "structure_score": 8,
                       "faithfulness_score": 8, "conciseness_score": 8, "overall_score": 8},
            "is_improvement": True, "verdict": "good", "issues": [],
        })

    with patch("app.services.pipeline.run_analyze", mock_analyze), \
         patch("app.services.pipeline.run_strategy", mock_strategy), \
         patch("app.services.pipeline.run_optimize", mock_optimize), \
         patch("app.services.pipeline.run_validate", mock_validate), \
         patch("app.services.pipeline.load_settings",
               return_value=_full_settings(stream_optimize=False)):
        async for _ in run_pipeline(
            provider=MagicMock(),
            raw_prompt="Test",
            optimization_id="no-stream-test",
        ):
            pass

    assert captured.get("streaming") is False, \
        "optimizer must receive streaming=False when stream_optimize is disabled"
