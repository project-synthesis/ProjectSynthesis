"""Tests for synthesis_save_result edge cases.

Comprehensive save_result tests (with real DB) live in test_mcp_tools.py.
These tests verify specific mocking scenarios.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.mcp_server import synthesis_save_result

pytestmark = pytest.mark.asyncio


async def test_synthesis_save_result(db_session):
    """Save result with scores: persists and returns SaveResultOutput."""
    scores = {
        "clarity": 5.0,
        "specificity": 5.0,
        "structure": 5.0,
        "faithfulness": 5.0,
        "conciseness": 5.0,
    }

    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await synthesis_save_result(
            trace_id="tr_save_test",
            optimized_prompt="Hello again, this is a properly optimized prompt.",
            task_type="coding",
            strategy_used="auto",
            scores=scores,
            model="ide_llm",
        )

    assert result.optimization_id
    assert result.overall_score is not None
    # With IDE scores provided → hybrid_passthrough mode
    assert result.scoring_mode == "hybrid_passthrough"


async def test_synthesis_save_result_standalone(db_session):
    """Save result without prior prepare: creates standalone record (no error)."""
    with patch("app.mcp_server.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # No matching trace_id from prepare → creates standalone record
        result = await synthesis_save_result(
            trace_id="tr_nonexistent_prepare",
            optimized_prompt="A standalone prompt that was not prepared first.",
        )

    # Should succeed — creates new record with heuristic scoring
    assert result.optimization_id
    assert result.scoring_mode == "heuristic"
    assert result.overall_score is not None
