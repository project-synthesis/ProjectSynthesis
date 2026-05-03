"""FastAPI dependency for the global WriteQueue singleton.

Centralizes the ``app.state.write_queue`` lookup so routers and any other
FastAPI dependency needing to submit writes through the single-writer queue
worker do not duplicate the resolution logic. Mirrors the pattern used by
``app.dependencies.probes.get_probe_service`` and
``app.dependencies.rate_limit.RateLimit``.

The queue itself is a process-level singleton constructed in the FastAPI
``lifespan`` (cycle 9 wires this; until then the dependency raises a
``RuntimeError`` if reached on a process where the queue has not been
initialized -- a strong signal of a lifespan ordering bug).
"""
from __future__ import annotations

from fastapi import Request

from app.services.write_queue import WriteQueue


def get_write_queue(request: Request) -> WriteQueue:
    """Return the singleton ``WriteQueue`` from ``app.state``.

    Used by routers (cycle 7+) and any FastAPI dependency that needs to
    submit writes through the queue.

    Args:
        request: The incoming FastAPI ``Request``; provides access to
            ``app.state.write_queue`` set up at lifespan startup.

    Returns:
        The process-level ``WriteQueue`` singleton.

    Raises:
        RuntimeError: If ``app.state.write_queue`` is unset. Indicates the
            lifespan never installed the queue (ordering bug or test setup
            without the lifespan running). Callers should let this propagate
            so the misconfiguration is visible at request time rather than
            silently degrading writes back to per-request sessions.
    """
    queue = getattr(request.app.state, "write_queue", None)
    if queue is None:
        raise RuntimeError(
            "write_queue not initialized; check lifespan ordering",
        )
    return queue


__all__ = ["get_write_queue"]
