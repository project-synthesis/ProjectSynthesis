"""Tests for the RateLimit FastAPI dependency.

Run: cd backend && source .venv/bin/activate && pytest tests/test_rate_limit.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ── Test: allows within limit ─────────────────────────────────────────────


async def test_allows_within_limit():
    """Requests under the rate limit should pass without raising."""
    from app.dependencies.rate_limit import RateLimit

    rl = RateLimit(lambda: "60/minute")

    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.url = MagicMock()
    mock_request.url.path = "/api/test"

    mock_limiter = MagicMock()
    mock_limiter.hit.return_value = True

    with patch("app.dependencies.rate_limit._limiter", mock_limiter), \
         patch("app.dependencies.rate_limit._is_async", False):
        # Should not raise
        await rl(mock_request)


# ── Test: raises 429 when exceeded ────────────────────────────────────────


async def test_raises_429_when_exceeded():
    """Should raise HTTPException(429) when rate limit is exceeded."""
    from app.dependencies.rate_limit import RateLimit

    rl = RateLimit(lambda: "1/minute")

    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "127.0.0.1"
    mock_request.url = MagicMock()
    mock_request.url.path = "/api/test"

    mock_limiter = MagicMock()
    mock_limiter.hit.return_value = False  # exceeded

    with patch("app.dependencies.rate_limit._limiter", mock_limiter), \
         patch("app.dependencies.rate_limit._is_async", False):
        with pytest.raises(HTTPException) as exc_info:
            await rl(mock_request)

        assert exc_info.value.status_code == 429
        assert exc_info.value.detail["code"] == "RATE_LIMIT_EXCEEDED"


# ── Test: memory fallback when Redis unavailable ──────────────────────────


async def test_memory_fallback_when_redis_unavailable():
    """init_rate_limiter should fall back to MemoryStorage when Redis is down."""
    from app.dependencies.rate_limit import init_rate_limiter

    mock_redis = MagicMock()
    mock_redis.is_available = False

    with patch("app.dependencies.rate_limit._storage", None), \
         patch("app.dependencies.rate_limit._limiter", None), \
         patch("app.dependencies.rate_limit._is_async", False):
        await init_rate_limiter(mock_redis)

    # After init, the module-level state should be set
    import app.dependencies.rate_limit as rl_mod
    assert rl_mod._limiter is not None
    assert rl_mod._is_async is False


# ── Test: parses config string ────────────────────────────────────────────


def test_parses_config_string():
    """The limits library should parse our config format without error."""
    from limits import parse as limits_parse

    result = limits_parse("60/minute")
    assert result is not None

    result2 = limits_parse("20/minute")
    assert result2 is not None

    result3 = limits_parse("10/minute")
    assert result3 is not None


# ── Test: X-Forwarded-For extraction ──────────────────────────────────────


def test_x_forwarded_for_extraction():
    """Should extract the first IP from X-Forwarded-For header."""
    from app.dependencies.rate_limit import _get_client_ip

    mock_request = MagicMock()
    mock_request.headers = {"X-Forwarded-For": "203.0.113.1, 198.51.100.2, 192.0.2.3"}
    mock_request.client = MagicMock()
    mock_request.client.host = "10.0.0.1"

    ip = _get_client_ip(mock_request)
    assert ip == "203.0.113.1"


def test_direct_client_ip_when_no_forwarded():
    """Should use request.client.host when no X-Forwarded-For is present."""
    from app.dependencies.rate_limit import _get_client_ip

    mock_request = MagicMock()
    mock_request.headers = {}
    mock_request.client = MagicMock()
    mock_request.client.host = "10.0.0.1"

    ip = _get_client_ip(mock_request)
    assert ip == "10.0.0.1"
