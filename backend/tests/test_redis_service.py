"""Tests for the RedisService.

Run: cd backend && source .venv/bin/activate && pytest tests/test_redis_service.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.redis_service import RedisService


# ── Test: graceful degradation on connect failure ─────────────────────────


async def test_graceful_degradation_on_connect_failure():
    """RedisService.connect() should return False and not crash when Redis is down."""
    svc = RedisService(host="nonexistent-host", port=9999, db=0, password="")

    # Mock the redis.asyncio module to simulate connection failure
    with patch("app.services.redis_service.aioredis") as mock_aioredis:
        mock_pool = AsyncMock()
        mock_aioredis.ConnectionPool.return_value = mock_pool

        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_aioredis.Redis.return_value = mock_client

        result = await svc.connect()

    assert result is False
    assert svc.is_available is False


# ── Test: health check when disconnected ──────────────────────────────────


async def test_health_check_when_disconnected():
    """health_check should return False when Redis is not connected."""
    svc = RedisService(host="localhost", port=6379, db=0, password="")
    # Never connected — _available is False by default

    result = await svc.health_check()
    assert result is False


# ── Test: is_available property ───────────────────────────────────────────


def test_is_available_false_by_default():
    """is_available should be False before connect() is called."""
    svc = RedisService()
    assert svc.is_available is False


def test_client_returns_none_when_unavailable():
    """client property should return None when Redis is unavailable."""
    svc = RedisService()
    assert svc.client is None


# ── Test: close is safe when not connected ────────────────────────────────


async def test_close_safe_when_not_connected():
    """close() should not raise when called without a prior connect()."""
    svc = RedisService()
    # Should not raise
    await svc.close()
    assert svc.is_available is False
