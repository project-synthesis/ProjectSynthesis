"""Tests for MCP server tools."""

from unittest.mock import AsyncMock, patch

import pytest

from app.mcp_server import (
    synthesis_optimize,
    synthesis_prepare_optimization,
    synthesis_save_result,
)

# ---------------------------------------------------------------------------
# synthesis_prepare_optimization
# ---------------------------------------------------------------------------


async def test_prepare_returns_assembled_prompt(db_session) -> None:
    """synthesis_prepare_optimization assembles a passthrough prompt with all expected fields."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_prepare_optimization(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    assert "trace_id" in result
    assert len(result["trace_id"]) == 36  # UUID format
    assert "assembled_prompt" in result
    assert len(result["assembled_prompt"]) > 0
    assert "context_size_tokens" in result
    assert result["context_size_tokens"] > 0
    assert "strategy_requested" in result
    assert result["strategy_requested"] == "auto"  # default when none specified


async def test_prepare_with_explicit_strategy(db_session) -> None:
    """Explicit strategy name is reflected in the returned strategy_requested."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_prepare_optimization(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
            strategy="chain-of-thought",
        )

    assert result["strategy_requested"] == "chain-of-thought"
    # Strategy instructions should be embedded in the assembled prompt
    assert len(result["assembled_prompt"]) > 100


async def test_prepare_falls_back_to_auto_for_unknown_strategy(db_session) -> None:
    """Unknown strategy falls back to auto without raising."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_prepare_optimization(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
            strategy="nonexistent-strategy-xyz",
        )

    assert result["strategy_requested"] == "auto"


async def test_prepare_rejects_short_prompt() -> None:
    """Prompts under 20 characters are rejected."""
    with pytest.raises(ValueError, match="too short"):
        await synthesis_prepare_optimization(prompt="short")


# ---------------------------------------------------------------------------
# synthesis_save_result
# ---------------------------------------------------------------------------


async def test_save_result_applies_bias_correction(db_session) -> None:
    """Bias-corrected scores should be lower than raw input (15% discount at default factor)."""
    raw_scores = {
        "clarity": 8.0,
        "specificity": 8.0,
        "structure": 8.0,
        "faithfulness": 8.0,
        "conciseness": 8.0,
    }

    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_save_result(
            trace_id="test-trace-id",
            optimized_prompt=(
                "## Task\nWrite a well-structured Python function.\n\n"
                "## Requirements\n- Must validate input\n- Must return bool"
            ),
            changes_summary="Added structure and constraints",
            task_type="coding",
            strategy_used="structured-output",
            scores=raw_scores,
            model="claude-opus-4",
        )

    assert "optimization_id" in result
    assert result["scoring_mode"] == "hybrid_passthrough"
    assert result["strategy_compliance"] == "matched"
    assert result["overall_score"] is not None

    # Hybrid blending combines bias-corrected IDE scores with heuristics
    # Scores should be lower than raw input (bias correction + heuristic blending)
    for dim, score_value in result["scores"].items():
        assert score_value < raw_scores[dim], (
            f"{dim}: blended {score_value} should be < raw {raw_scores[dim]}"
        )


async def test_save_result_without_scores(db_session) -> None:
    """Saving without IDE scores falls back to heuristic scoring."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_save_result(
            trace_id="test-trace-no-scores",
            optimized_prompt="A simple prompt without scores provided by the caller.",
        )

    # Falls back to heuristic scoring when no IDE scores are provided
    assert result["scoring_mode"] == "heuristic"
    assert result["scores"]  # non-empty — heuristic scores computed
    assert result["overall_score"] is not None
    assert result["heuristic_flags"] == []
    assert result["strategy_compliance"] == "unknown"


# ---------------------------------------------------------------------------
# synthesis_optimize
# ---------------------------------------------------------------------------


async def test_optimize_validates_prompt_too_short() -> None:
    """synthesis_optimize rejects prompts under 20 characters."""
    with pytest.raises(ValueError, match="too short"):
        await synthesis_optimize(prompt="hi")


async def test_optimize_validates_prompt_too_long() -> None:
    """synthesis_optimize rejects prompts over 200000 characters."""
    with pytest.raises(ValueError, match="too long"):
        await synthesis_optimize(prompt="x" * 200001)


# ---------------------------------------------------------------------------
# synthesis_save_result — codebase_context
# ---------------------------------------------------------------------------


async def test_save_result_stores_codebase_context(db_session) -> None:
    """Passing codebase_context persists it (truncated) on the optimization record."""
    context_text = "Project uses FastAPI + SvelteKit. Key patterns: async, runes."

    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_save_result(
            trace_id="test-trace-with-context",
            optimized_prompt="A well-structured prompt with clear requirements.",
            strategy_used="auto",
            codebase_context=context_text,
        )

    assert "optimization_id" in result

    # Verify the record was persisted with codebase_context_snapshot
    from sqlalchemy import select

    from app.models import Optimization

    row = await db_session.execute(
        select(Optimization).where(Optimization.trace_id == "test-trace-with-context")
    )
    opt = row.scalar_one()
    assert opt.codebase_context_snapshot == context_text


# ---------------------------------------------------------------------------
# synthesis_optimize — passthrough mode (no provider)
# ---------------------------------------------------------------------------


async def test_optimize_passthrough_when_no_provider(db_session) -> None:
    """When no provider is available, synthesis_optimize returns pending_external."""
    with (
        patch("app.mcp_server._provider", None),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    assert result["status"] == "pending_external"
    assert "trace_id" in result
    assert len(result["trace_id"]) == 36  # UUID
    assert "assembled_prompt" in result
    assert len(result["assembled_prompt"]) > 100
    assert "instructions" in result
    assert "synthesis_save_result" in result["instructions"]
    assert result["strategy_used"] == "auto"


async def test_optimize_passthrough_includes_strategy(db_session) -> None:
    """Passthrough mode includes the requested strategy in the assembled prompt."""
    with (
        patch("app.mcp_server._provider", None),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
            strategy="chain-of-thought",
        )

    assert result["status"] == "pending_external"
    assert result["strategy_used"] == "chain-of-thought"


async def test_optimize_passthrough_then_save_full_flow(db_session) -> None:
    """Full passthrough flow: optimize (pending) → save_result (completed)."""
    with (
        patch("app.mcp_server._provider", None),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Step 1: Get assembled prompt
        pending = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    assert pending["status"] == "pending_external"
    trace_id = pending["trace_id"]

    # Step 2: Save the external LLM's result
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        saved = await synthesis_save_result(
            trace_id=trace_id,
            optimized_prompt=(
                "## Task\nValidate email addresses using RFC 5322.\n\n"
                "## Requirements\n- Use re module\n- Return bool\n- Handle None input"
            ),
            changes_summary="Added structure, constraints, edge cases",
            task_type="coding",
            strategy_used="auto",
            scores={
                "clarity": 8.5,
                "specificity": 8.0,
                "structure": 9.0,
                "faithfulness": 8.5,
                "conciseness": 7.5,
            },
        )

    assert "optimization_id" in saved
    assert saved["scoring_mode"] == "hybrid_passthrough"
    assert saved["overall_score"] is not None
    assert saved["scores"]  # non-empty
    assert saved["strategy_compliance"] == "matched"


async def test_optimize_passthrough_save_without_scores(db_session) -> None:
    """Passthrough save without IDE scores falls back to heuristic."""
    with (
        patch("app.mcp_server._provider", None),
        patch("app.mcp_server.async_session_factory") as mock_factory,
    ):
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        pending = await synthesis_optimize(
            prompt="Write a Python function that validates email addresses using RFC 5322 regex.",
        )

    trace_id = pending["trace_id"]

    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        saved = await synthesis_save_result(
            trace_id=trace_id,
            optimized_prompt="A well-structured prompt without IDE scores.",
        )

    assert saved["scoring_mode"] == "heuristic"
    assert saved["overall_score"] is not None
    assert saved["scores"]
