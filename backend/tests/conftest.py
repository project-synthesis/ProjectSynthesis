"""Shared pytest fixtures for backend tests.

Autouse fixtures applied to ALL tests in this directory.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _bypass_rate_limiter():
    """Disable rate limiting in unit tests.

    Patches the module-level _limiter in rate_limit.py so that every
    call to RateLimit.__call__ sees a limiter that always allows requests.
    """
    mock_limiter = AsyncMock()
    mock_limiter.hit.return_value = True       # sync fallback (MemoryStorage)
    mock_limiter.ahit = AsyncMock(return_value=True)  # async (RedisStorage)

    with patch("app.dependencies.rate_limit._limiter", mock_limiter):
        yield
