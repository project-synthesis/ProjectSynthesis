"""Tests for the sampling pipeline service (sampling_pipeline.py).

Verifies structured output via tool calling, text fallback, and end-to-end
pipeline execution with mocked MCP sampling.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from app.schemas.pipeline_contracts import AnalysisResult, DimensionScores, ScoreResult
from app.services.pipeline_constants import CODING_KEYWORDS, CONFIDENCE_GATE
from app.services.sampling_pipeline import (
    _parse_text_response,
    _pydantic_to_mcp_tool,
    _sampling_request_plain,
    _sampling_request_structured,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SimpleModel(BaseModel):
    """Minimal model for testing parsing."""

    name: str
    value: int


def _make_text_result(text: str, model: str = "claude-sonnet-4-6"):
    """Build a mock CreateMessageResult with text content."""
    content = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=content, model=model)


def _make_tool_use_result(tool_input: dict, model: str = "claude-sonnet-4-6"):
    """Build a mock CreateMessageResult with tool_use content."""
    block = SimpleNamespace(type="tool_use", input=tool_input)
    return SimpleNamespace(content=[block], model=model)


def _make_ctx(create_message_return=None, side_effect=None):
    """Build a mock MCP Context with a create_message mock."""
    ctx = MagicMock()
    ctx.session = MagicMock()
    if side_effect:
        ctx.session.create_message = AsyncMock(side_effect=side_effect)
    else:
        ctx.session.create_message = AsyncMock(return_value=create_message_return)
    return ctx


# ---------------------------------------------------------------------------
# _pydantic_to_mcp_tool
# ---------------------------------------------------------------------------


def test_pydantic_to_mcp_tool():
    """Schema conversion produces a valid Tool with expected fields."""
    tool = _pydantic_to_mcp_tool(_SimpleModel, "test_tool", "A test tool")

    assert tool.name == "test_tool"
    assert tool.description == "A test tool"
    assert "properties" in tool.inputSchema
    assert "name" in tool.inputSchema["properties"]
    assert "value" in tool.inputSchema["properties"]


# ---------------------------------------------------------------------------
# _parse_text_response
# ---------------------------------------------------------------------------


def test_parse_text_response_json():
    """Direct JSON string is parsed correctly."""
    text = '{"name": "hello", "value": 42}'
    result = _parse_text_response(text, _SimpleModel)

    assert result.name == "hello"
    assert result.value == 42


def test_parse_text_response_codeblock():
    """JSON inside a markdown code block is extracted and parsed."""
    text = 'Some text before\n```json\n{"name": "world", "value": 7}\n```\nSome text after'
    result = _parse_text_response(text, _SimpleModel)

    assert result.name == "world"
    assert result.value == 7


def test_parse_text_response_invalid():
    """Non-JSON text raises ValueError."""
    with pytest.raises(ValueError, match="Cannot parse"):
        _parse_text_response("just plain text", _SimpleModel)


# ---------------------------------------------------------------------------
# No model_preferences in sampling requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sampling_request_plain_sends_no_model_preferences():
    """Verify _sampling_request_plain does not include model_preferences in kwargs."""
    ctx = _make_ctx(create_message_return=_make_text_result("hello"))
    text, model_id = await _sampling_request_plain(ctx, "system", "user")
    call_kwargs = ctx.session.create_message.call_args
    assert "model_preferences" not in call_kwargs.kwargs
    assert text == "hello"


@pytest.mark.asyncio
async def test_sampling_request_structured_sends_no_model_preferences():
    """Verify _sampling_request_structured does not include model_preferences."""
    ctx = _make_ctx(create_message_return=_make_tool_use_result({"name": "x", "value": 1}))
    result, model_id = await _sampling_request_structured(
        ctx, "system", "user", _SimpleModel,
    )
    call_kwargs = ctx.session.create_message.call_args
    assert "model_preferences" not in call_kwargs.kwargs
    assert result.name == "x"


# ---------------------------------------------------------------------------
# _sampling_request_structured — tool_use parsing
# ---------------------------------------------------------------------------


async def test_sampling_structured_tool_use():
    """Structured request parses tool_use content from response."""
    tool_input = {"name": "parsed", "value": 99}
    mock_result = _make_tool_use_result(tool_input, model="claude-opus-4-6")
    ctx = _make_ctx(create_message_return=mock_result)

    parsed, model_id = await _sampling_request_structured(
        ctx, "system", "user", _SimpleModel,
    )

    assert parsed.name == "parsed"
    assert parsed.value == 99
    assert model_id == "claude-opus-4-6"


async def test_sampling_structured_text_fallback():
    """When response is text (no tool_use), falls back to text parsing."""
    text_result = _make_text_result('{"name": "fallback", "value": 1}')
    ctx = _make_ctx(create_message_return=text_result)

    parsed, model_id = await _sampling_request_structured(
        ctx, "system", "user", _SimpleModel,
    )

    assert parsed.name == "fallback"
    assert parsed.value == 1


async def test_sampling_structured_client_no_tools_support():
    """Falls back to plain text when client raises TypeError (no tools support)."""
    ctx = _make_ctx()

    # First call (with tools) raises TypeError, second call (plain) returns text
    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if "tools" in kwargs:
            raise TypeError("create_message() got an unexpected keyword argument 'tools'")
        return _make_text_result('{"name": "plain", "value": 5}')

    ctx.session.create_message = AsyncMock(side_effect=side_effect)

    parsed, model_id = await _sampling_request_structured(
        ctx, "system", "user", _SimpleModel,
    )

    assert parsed.name == "plain"
    assert parsed.value == 5


# ---------------------------------------------------------------------------
# _sampling_request_plain
# ---------------------------------------------------------------------------


async def test_sampling_request_plain():
    """Plain text request returns text and model ID."""
    mock_result = _make_text_result("Hello world", model="claude-haiku-4-5")
    ctx = _make_ctx(create_message_return=mock_result)

    text, model_id = await _sampling_request_plain(ctx, "system", "user")

    assert text == "Hello world"
    assert model_id == "claude-haiku-4-5"



# ---------------------------------------------------------------------------
# run_sampling_pipeline — end-to-end
# ---------------------------------------------------------------------------


async def test_run_sampling_pipeline_full():
    """End-to-end sampling pipeline with mocked create_message calls."""
    from app.services.sampling_pipeline import run_sampling_pipeline

    # Build mock responses for each phase
    analysis_json = AnalysisResult(
        task_type="coding",
        weaknesses=["vague requirements"],
        strengths=["good intent"],
        selected_strategy="chain-of-thought",
        strategy_rationale="Coding task benefits from step-by-step",
        confidence=0.85,
        intent_label="code review",
        domain="backend",
    ).model_dump_json()

    optimize_json = (
        '{"optimized_prompt": "Improved prompt text", '
        '"changes_summary": "Added structure", '
        '"strategy_used": "chain-of-thought"}'
    )

    score_json = ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=7.0, specificity=7.0, structure=7.0,
            faithfulness=7.0, conciseness=7.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=8.0, specificity=8.0, structure=8.0,
            faithfulness=8.0, conciseness=8.0,
        ),
    ).model_dump_json()

    # Each call gets a different response based on call order
    call_count = 0
    responses = [
        _make_text_result(analysis_json, model="claude-sonnet-4-6"),
        _make_text_result(optimize_json, model="claude-opus-4-6"),
        _make_text_result(score_json, model="claude-sonnet-4-6"),
    ]

    async def mock_create_message(**kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    ctx = _make_ctx()
    ctx.session.create_message = AsyncMock(side_effect=mock_create_message)

    # Set up a DomainResolver with known domain labels so the sampling
    # pipeline can resolve "backend" without hitting a real DB.
    from app.services.domain_resolver import DomainResolver
    _test_resolver = DomainResolver()
    _test_resolver._domain_labels = {"backend", "frontend", "database", "devops", "security", "fullstack", "general"}

    with patch("app.services.sampling_pipeline.async_session_factory") as mock_factory, \
         patch("app.services.domain_resolver._instance", _test_resolver):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_sampling_pipeline(
            ctx, "Write a Python function that validates email addresses using regex patterns.",
            None,  # strategy_override
            None,  # codebase_guidance
        )

    assert result["pipeline_mode"] == "sampling"
    assert result["optimization_id"]
    assert result["trace_id"]  # Issue 3: trace_id must be in result
    assert result["optimized_prompt"] == "Improved prompt text"
    assert result["task_type"] == "coding"
    assert result["strategy_used"] == "chain-of-thought"
    assert result["model_used"] == "claude-opus-4-6"
    assert result["intent_label"] == "code review"
    assert result["domain"] == "backend"


# ---------------------------------------------------------------------------
# run_sampling_analyze
# ---------------------------------------------------------------------------


async def test_run_sampling_analyze():
    """Analyze via sampling: two-phase (analyze + baseline score)."""
    from app.services.sampling_pipeline import run_sampling_analyze

    analysis_json = AnalysisResult(
        task_type="writing",
        weaknesses=["lacks detail"],
        strengths=["clear intent"],
        selected_strategy="structured-output",
        strategy_rationale="Writing benefits from structure",
        confidence=0.9,
        intent_label="blog post",
        domain="general",
    ).model_dump_json()

    score_json = ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=6.5, specificity=6.0, structure=7.0,
            faithfulness=7.5, conciseness=6.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=6.5, specificity=6.0, structure=7.0,
            faithfulness=7.5, conciseness=6.0,
        ),
    ).model_dump_json()

    call_count = 0
    responses = [
        _make_text_result(analysis_json),
        _make_text_result(score_json),
    ]

    async def mock_create_message(**kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    ctx = _make_ctx()
    ctx.session.create_message = AsyncMock(side_effect=mock_create_message)

    with patch("app.services.sampling_pipeline.async_session_factory") as mock_factory:
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_sampling_analyze(
            ctx, "Write a comprehensive blog post about sustainable energy trends.",
        )

    assert result["task_type"] == "writing"
    assert result["selected_strategy"] == "structured-output"
    assert result["overall_score"] > 0
    assert result["optimization_id"]
    assert result["baseline_scores"]
    assert result["intent_label"] == "blog post"
    assert result["domain"] == "general"
    assert len(result["next_steps"]) >= 1


# ---------------------------------------------------------------------------
# Confidence gate + semantic check (parity with pipeline.py)
# ---------------------------------------------------------------------------


def test_confidence_gate_constant():
    """CONFIDENCE_GATE is 0.7, matching pipeline.py."""
    assert CONFIDENCE_GATE == 0.7


def test_coding_keywords_present():
    """CODING_KEYWORDS set is non-empty and contains expected terms."""
    assert len(CODING_KEYWORDS) > 0
    assert "function" in CODING_KEYWORDS
    assert "class" in CODING_KEYWORDS
    assert "api" in CODING_KEYWORDS


async def test_confidence_gate_overrides_strategy():
    """When confidence < 0.7 and no override, strategy is set to 'auto'."""
    from app.services.sampling_pipeline import run_sampling_pipeline

    # Analysis with confidence=0.5 (below gate) and task_type != coding
    analysis_json = AnalysisResult(
        task_type="writing",
        weaknesses=["unclear"],
        strengths=["concise"],
        selected_strategy="chain-of-thought",
        strategy_rationale="Reasoning helps",
        confidence=0.5,
        intent_label="essay",
        domain="general",
    ).model_dump_json()

    optimize_json = (
        '{"optimized_prompt": "Better text", '
        '"changes_summary": "Improved", '
        '"strategy_used": "auto"}'
    )

    score_json = ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=7.0, specificity=7.0, structure=7.0,
            faithfulness=7.0, conciseness=7.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=8.0, specificity=8.0, structure=8.0,
            faithfulness=8.0, conciseness=8.0,
        ),
    ).model_dump_json()

    call_count = 0
    responses = [
        _make_text_result(analysis_json),
        _make_text_result(optimize_json),
        _make_text_result(score_json),
    ]

    async def mock_create_message(**kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    ctx = _make_ctx()
    ctx.session.create_message = AsyncMock(side_effect=mock_create_message)

    with patch("app.services.sampling_pipeline.async_session_factory") as mock_factory:
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_sampling_pipeline(
            ctx,
            "Write a comprehensive essay about the history of philosophy and ethics.",
            None,  # strategy_override
            None,  # codebase_guidance
        )

    # Confidence 0.5 < CONFIDENCE_GATE 0.7 → strategy overridden from analyzer pick.
    # "auto" resolves to a task-type-appropriate strategy (writing → role-playing).
    assert result["strategy_used"] != "chain-of-thought"  # analyzer's pick was overridden


async def test_semantic_check_reduces_confidence():
    """When task_type='coding' but no coding keywords, strategy overridden to 'auto'."""
    from app.services.sampling_pipeline import run_sampling_pipeline

    # Analysis says coding with confidence 0.8, but prompt has no coding keywords
    # Semantic check reduces confidence by 0.2 → 0.6 < 0.7 → gate triggers
    analysis_json = AnalysisResult(
        task_type="coding",
        weaknesses=["unclear"],
        strengths=["intent"],
        selected_strategy="chain-of-thought",
        strategy_rationale="Coding benefits from step-by-step",
        confidence=0.8,
        intent_label="general task",
        domain="general",
    ).model_dump_json()

    optimize_json = (
        '{"optimized_prompt": "Better text", '
        '"changes_summary": "Improved", '
        '"strategy_used": "auto"}'
    )

    score_json = ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=7.0, specificity=7.0, structure=7.0,
            faithfulness=7.0, conciseness=7.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=8.0, specificity=8.0, structure=8.0,
            faithfulness=8.0, conciseness=8.0,
        ),
    ).model_dump_json()

    call_count = 0
    responses = [
        _make_text_result(analysis_json),
        _make_text_result(optimize_json),
        _make_text_result(score_json),
    ]

    async def mock_create_message(**kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    ctx = _make_ctx()
    ctx.session.create_message = AsyncMock(side_effect=mock_create_message)

    with patch("app.services.sampling_pipeline.async_session_factory") as mock_factory:
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_sampling_pipeline(
            ctx,
            # Prompt has NO coding keywords — only general language
            "Help me think about how to better organize my daily tasks and routines.",
            None,  # strategy_override
            None,  # codebase_guidance
        )

    # Semantic check drops 0.8 → 0.6, below CONFIDENCE_GATE 0.7 → overrides
    # analyzer's "chain-of-thought" pick. Auto resolves to task-type default.
    assert result["strategy_used"] != "chain-of-thought"


async def test_strategy_override_bypasses_confidence_gate():
    """When strategy_override is provided, confidence gate does not trigger."""
    from app.services.sampling_pipeline import run_sampling_pipeline

    # Low confidence but explicit strategy override
    analysis_json = AnalysisResult(
        task_type="writing",
        weaknesses=["unclear"],
        strengths=["concise"],
        selected_strategy="chain-of-thought",
        strategy_rationale="Reasoning helps",
        confidence=0.3,  # Very low
        intent_label="essay",
        domain="general",
    ).model_dump_json()

    optimize_json = (
        '{"optimized_prompt": "Better text", '
        '"changes_summary": "Improved", '
        '"strategy_used": "few-shot"}'
    )

    score_json = ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=7.0, specificity=7.0, structure=7.0,
            faithfulness=7.0, conciseness=7.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=8.0, specificity=8.0, structure=8.0,
            faithfulness=8.0, conciseness=8.0,
        ),
    ).model_dump_json()

    call_count = 0
    responses = [
        _make_text_result(analysis_json),
        _make_text_result(optimize_json),
        _make_text_result(score_json),
    ]

    async def mock_create_message(**kwargs):
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        call_count += 1
        return responses[idx]

    ctx = _make_ctx()
    ctx.session.create_message = AsyncMock(side_effect=mock_create_message)

    with patch("app.services.sampling_pipeline.async_session_factory") as mock_factory:
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await run_sampling_pipeline(
            ctx,
            "Write a comprehensive essay about the history of philosophy and ethics.",
            "few-shot",  # Explicit strategy override
            None,  # codebase_guidance
        )

    # Even with confidence 0.3, explicit override wins
    assert result["strategy_used"] == "few-shot"
