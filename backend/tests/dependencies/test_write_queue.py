"""Tests for ``app.dependencies.write_queue.get_write_queue`` (cycle 7a).

The dependency centralizes ``app.state.write_queue`` lookup; the contract is:

  * RuntimeError when ``app.state.write_queue`` is unset (lifespan ordering bug).
  * Returns the singleton when set.

Both branches are exercised here so the cycle 9 lifespan integration has a
sentinel test that fails loudly if the wiring regresses.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.dependencies.write_queue import get_write_queue


def _make_request(state: SimpleNamespace):
    """Build a minimal ``Request``-shaped object with ``request.app.state``.

    The dependency only touches ``request.app.state``, so we don't need a
    real Starlette ``Request`` -- a plain object with the right shape avoids
    pulling Starlette's request construction into the unit test.
    """
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)


def test_get_write_queue_raises_runtime_error_when_unset():
    """Missing ``app.state.write_queue`` -> RuntimeError with diagnostic."""
    state = SimpleNamespace()  # no ``write_queue`` attribute
    request = _make_request(state)

    with pytest.raises(RuntimeError) as excinfo:
        get_write_queue(request)

    msg = str(excinfo.value)
    assert "write_queue" in msg
    assert "lifespan" in msg


def test_get_write_queue_returns_singleton_when_set():
    """Returns whatever was installed on ``app.state.write_queue`` verbatim."""
    queue = MagicMock(name="WriteQueue")
    state = SimpleNamespace(write_queue=queue)
    request = _make_request(state)

    result = get_write_queue(request)
    assert result is queue


def test_get_write_queue_raises_when_explicitly_none():
    """Explicit ``None`` is rejected the same as missing attr."""
    state = SimpleNamespace(write_queue=None)
    request = _make_request(state)

    with pytest.raises(RuntimeError):
        get_write_queue(request)
