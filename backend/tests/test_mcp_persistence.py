"""Tests for MCP tool DB persistence (Tasks 14-15)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_session_mock():
    """Return an AsyncMock that works as an async context manager session."""
    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.merge = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


# ── Task 14 tests ─────────────────────────────────────────────────────────────

def test_accumulate_event_analysis():
    """_accumulate_event maps analysis event fields onto the ORM object."""
    from app.mcp_server import _accumulate_event

    opt = MagicMock()
    _accumulate_event(opt, "analysis", {
        "task_type": "coding",
        "complexity": "medium",
        "weaknesses": ["unclear scope"],
        "strengths": ["concise"],
        "model": "claude-opus-4-6",
    })

    assert opt.task_type == "coding"
    assert opt.model_analyze == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_run_and_persist_commits_twice():
    """_run_and_persist creates and finalises the Optimization — commits at least twice."""
    from app.mcp_server import _run_and_persist

    cm, session = _make_session_mock()

    async def _fake_pipeline(**kwargs):
        yield "analysis", {"task_type": "coding", "model": "test-model"}

    mock_provider = MagicMock()
    mock_provider.name = "test-provider"

    with (
        patch("app.mcp_server.async_session", return_value=cm),
        patch("app.services.pipeline.run_pipeline", side_effect=_fake_pipeline),
    ):
        results, opt = await _run_and_persist(
            mock_provider,
            "test prompt",
            opt_id="test-id-001",
        )

    assert session.commit.call_count >= 2


# ── Task 15 tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_and_persist_sets_retry_of():
    """_run_and_persist passes retry_of through to the Optimization constructor."""
    from app.mcp_server import _run_and_persist

    cm, session = _make_session_mock()

    async def _fake_pipeline(**kwargs):
        yield "analysis", {"task_type": "coding", "model": "test-model"}
        return

    mock_provider = MagicMock()
    mock_provider.name = "test-provider"

    with (
        patch("app.mcp_server.async_session", return_value=cm),
        patch("app.services.pipeline.run_pipeline", side_effect=_fake_pipeline),
        patch("app.mcp_server.Optimization") as MockOpt,
    ):
        MockOpt.return_value = MagicMock()
        await _run_and_persist(
            mock_provider,
            "test prompt",
            opt_id="new-id-002",
            retry_of="orig-id",
        )

    MockOpt.assert_called_once()
    _, kwargs = MockOpt.call_args
    assert kwargs.get("retry_of") == "orig-id"
