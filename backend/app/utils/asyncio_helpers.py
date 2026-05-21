"""Shared asyncio helpers consumed by multiple routers.

Currently houses ``_swallow_task_exception``, lifted from
``backend/app/routers/probes.py`` (Foundation P3 Cycle 11 / v0.4.18) in
v0.4.34 so the new SSE branch on ``POST /api/seed`` can reuse it without
duplicating the helper across two routers.

Single-responsibility leaf module — no inter-app imports beyond stdlib.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _swallow_task_exception(task: "asyncio.Task[Any]") -> None:
    """Drain any exception from a fire-and-forget background task so
    asyncio doesn't log ``"Task exception was never retrieved"``.

    Used by the SSE branches on ``POST /api/probes`` and ``POST /api/seed``
    — the SSE consumer doesn't ``await`` the orchestrator task itself; the
    orchestrator persists any failure to the row and emits a terminal
    event on the bus (``probe_failed`` / ``seed_failed``), so the SSE
    stream surfaces the failure to the client. This callback exists only
    to silence the asyncio warning when the bare task object is GC'd
    without its exception being retrieved.
    """
    try:
        task.result()
    except BaseException:  # noqa: BLE001
        # Catches ``asyncio.CancelledError`` plus any other exception; the
        # SSE branches deliberately swallow all of them since the
        # orchestrator already persisted state + emitted a terminal event.
        pass


__all__ = ["_swallow_task_exception"]
