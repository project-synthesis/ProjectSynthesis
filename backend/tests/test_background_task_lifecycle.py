"""Regression test for asyncio background task GC in GitHub repo indexing.

Root cause recap: `asyncio.create_task(coro)` without holding a strong
reference lets the event loop's weak-ref set drop the task mid-flight.
When the reindex's `_bg_index()` task was GC'd during a long Haiku
synthesis call, `synthesis_status` was stranded at `'running'` and no
SSE `index_phase_changed` event ever fired to flip the UI off
``indexing``. These tests lock the fix: a module-level strong-ref set
plus a `_spawn_bg_task()` helper that adds the task and registers a
``done_callback`` to remove it.
"""

from __future__ import annotations

import asyncio
import gc

import pytest


@pytest.mark.asyncio
async def test_spawn_bg_task_holds_reference_until_done() -> None:
    """A task started via `_spawn_bg_task` must not be GC'd while awaiting."""
    from app.routers import github_repos

    started = asyncio.Event()
    can_finish = asyncio.Event()

    async def _work() -> None:
        started.set()
        await can_finish.wait()

    task = github_repos._spawn_bg_task(_work())

    # Wait for the coroutine to actually start.
    await asyncio.wait_for(started.wait(), timeout=1.0)

    # Drop the local reference and aggressively GC. The module-level
    # strong-ref set must keep the task alive.
    del task
    for _ in range(3):
        gc.collect()
        await asyncio.sleep(0)

    assert len(github_repos._background_tasks) == 1, (
        "Task was dropped from the strong-ref set before completion — "
        "this is the exact failure mode that stranded synthesis_status=running."
    )

    can_finish.set()
    # Allow the done_callback to run.
    for _ in range(5):
        await asyncio.sleep(0)

    assert len(github_repos._background_tasks) == 0, (
        "Task completed but was not removed from the strong-ref set — "
        "would leak memory over time."
    )


@pytest.mark.asyncio
async def test_spawn_bg_task_returns_task_instance() -> None:
    """The helper returns the Task so callers may await/cancel if they want."""
    from app.routers import github_repos

    async def _noop() -> str:
        return "done"

    task = github_repos._spawn_bg_task(_noop())
    assert isinstance(task, asyncio.Task)
    assert await task == "done"


@pytest.mark.asyncio
async def test_spawn_bg_task_removes_on_exception() -> None:
    """Tasks that raise still get removed from the strong-ref set."""
    from app.routers import github_repos

    async def _fail() -> None:
        raise RuntimeError("boom")

    task = github_repos._spawn_bg_task(_fail())
    with pytest.raises(RuntimeError, match="boom"):
        await task

    # Callback runs after the task completes (success or failure).
    for _ in range(5):
        await asyncio.sleep(0)

    assert task not in github_repos._background_tasks, (
        "Failed task leaked into the strong-ref set — callback should "
        "run on exception too."
    )
