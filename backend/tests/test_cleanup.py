"""Tests for background cleanup service (Task 7)."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.services.cleanup import (
    run_cleanup_cycle,
    sweep_expired_tokens,
    sweep_expired_github_tokens,
    sweep_old_linked_repos,
    sweep_soft_deleted_optimizations,
)


def _make_session_mock():
    """Return an AsyncMock that works as an async context manager session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


async def test_sweep_expired_refresh_tokens():
    """sweep_expired_tokens() should call session.execute and session.commit."""
    cm, session = _make_session_mock()
    with patch("app.services.cleanup.async_session", return_value=cm):
        await sweep_expired_tokens()
    session.execute.assert_called_once()
    session.commit.assert_called_once()


async def test_sweep_expired_github_tokens():
    """sweep_expired_github_tokens() should call session.execute and session.commit."""
    cm, session = _make_session_mock()
    with patch("app.services.cleanup.async_session", return_value=cm):
        await sweep_expired_github_tokens()
    session.execute.assert_called_once()
    session.commit.assert_called_once()


async def test_sweep_old_linked_repos():
    """sweep_old_linked_repos() should call session.execute and session.commit."""
    cm, session = _make_session_mock()
    with patch("app.services.cleanup.async_session", return_value=cm):
        await sweep_old_linked_repos()
    session.execute.assert_called_once()
    session.commit.assert_called_once()


async def test_sweep_soft_deleted_optimizations():
    """sweep_soft_deleted_optimizations() should call session.execute and session.commit."""
    cm, session = _make_session_mock()
    with patch("app.services.cleanup.async_session", return_value=cm):
        await sweep_soft_deleted_optimizations()
    session.execute.assert_called_once()
    session.commit.assert_called_once()


async def test_run_cleanup_isolates_sweep_failures():
    """run_cleanup_cycle() runs all 4 sweeps even if the first one raises."""
    call_count = 0

    async def failing_sweep():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("simulated sweep failure")

    async def ok_sweep():
        nonlocal call_count
        call_count += 1

    with (
        patch("app.services.cleanup.sweep_expired_tokens", side_effect=failing_sweep),
        patch("app.services.cleanup.sweep_expired_github_tokens", side_effect=ok_sweep),
        patch("app.services.cleanup.sweep_old_linked_repos", side_effect=ok_sweep),
        patch("app.services.cleanup.sweep_soft_deleted_optimizations", side_effect=ok_sweep),
    ):
        await run_cleanup_cycle()

    assert call_count == 4, f"Expected 4 sweeps to run, got {call_count}"
