"""Tests for the CacheService.

Run: cd backend && source .venv/bin/activate && pytest tests/test_cache_service.py -v
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from app.services.cache_service import CacheService


def _make_mock_redis(available: bool = False) -> MagicMock:
    """Create a mock RedisService."""
    mock = MagicMock()
    mock.is_available = available
    mock.client = None
    return mock


# ── Test: set and get round trip ──────────────────────────────────────────


async def test_set_and_get_round_trip():
    """Should store and retrieve a value from in-memory fallback."""
    redis_mock = _make_mock_redis(available=False)
    cache = CacheService(redis_mock)

    await cache.set("test:key", {"data": 42}, ttl_seconds=300)
    result = await cache.get("test:key")

    assert result == {"data": 42}


# ── Test: miss returns None ───────────────────────────────────────────────


async def test_miss_returns_none():
    """Should return None for a key that doesn't exist."""
    redis_mock = _make_mock_redis(available=False)
    cache = CacheService(redis_mock)

    result = await cache.get("nonexistent:key")
    assert result is None


# ── Test: TTL expiry ──────────────────────────────────────────────────────


async def test_ttl_expiry():
    """Should return None after TTL expires (in-memory mode)."""
    redis_mock = _make_mock_redis(available=False)
    cache = CacheService(redis_mock)

    # Set with 1-second TTL
    await cache.set("expire:key", "value", ttl_seconds=1)
    result_before = await cache.get("expire:key")
    assert result_before == "value"

    # Manually expire by backdating the entry
    cache._memory["expire:key"] = (time.time() - 1, "value")
    result_after = await cache.get("expire:key")
    assert result_after is None


# ── Test: delete invalidation ─────────────────────────────────────────────


async def test_delete_invalidation():
    """Should remove a key from cache on delete."""
    redis_mock = _make_mock_redis(available=False)
    cache = CacheService(redis_mock)

    await cache.set("delete:key", "value", ttl_seconds=300)
    assert await cache.get("delete:key") == "value"

    await cache.delete("delete:key")
    assert await cache.get("delete:key") is None


# ── Test: fallback to memory ─────────────────────────────────────────────


async def test_fallback_to_memory():
    """When Redis is unavailable, should use in-memory storage."""
    redis_mock = _make_mock_redis(available=False)
    cache = CacheService(redis_mock)

    await cache.set("memory:key", [1, 2, 3], ttl_seconds=600)
    result = await cache.get("memory:key")
    assert result == [1, 2, 3]
    assert "memory:key" in cache._memory


# ── Test: make_key ────────────────────────────────────────────────────────


def test_make_key():
    """make_key should produce synthesis: namespaced keys."""
    key = CacheService.make_key("strategy", "code_generation", "high")
    assert key == "synthesis:strategy:code_generation:high"


# ── Test: hash_content ────────────────────────────────────────────────────


def test_hash_content_deterministic():
    """hash_content should produce the same hash for the same input."""
    h1 = CacheService.hash_content("test content")
    h2 = CacheService.hash_content("test content")
    assert h1 == h2
    assert len(h1) == 16


def test_hash_content_different_for_different_input():
    """hash_content should produce different hashes for different inputs."""
    h1 = CacheService.hash_content("content a")
    h2 = CacheService.hash_content("content b")
    assert h1 != h2
