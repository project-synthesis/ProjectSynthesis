"""Generic async cache service backed by Redis with in-memory LRU fallback.

Provides a simple get/set/delete interface. Values are JSON-serialized.
When Redis is unavailable, falls back to an in-memory dict with TTL-based
expiry and lazy cleanup.

Usage::

    from app.services.cache_service import get_cache

    cache = get_cache()
    if cache:
        await cache.set("key", {"data": 1}, ttl_seconds=300)
        result = await cache.get("key")
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Optional

from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# In-memory fallback eviction threshold
_MAX_MEMORY_ENTRIES = 1000


class CacheService:
    """Async cache backed by Redis with in-memory fallback."""

    def __init__(self, redis_service: RedisService) -> None:
        self._redis = redis_service
        # In-memory fallback: {key: (expiry_timestamp, value)}
        self._memory: dict[str, tuple[float, Any]] = {}
        # Fix #4: protect _memory from concurrent over-eviction in async context
        self._memory_lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """Get a value by key. Returns None on miss or expiry.

        Design note: when Redis is healthy, it is authoritative. A Redis miss
        returns None immediately without checking the in-memory layer — even
        though set() dual-writes to both. The in-memory layer is a *failover*
        for when Redis is down or throws, not a second-chance lookup.
        """
        if self._redis.is_ready:
            try:
                raw = await self._redis.client.get(key)
                if raw is not None:
                    return json.loads(raw)
                return None
            except Exception as e:
                logger.debug("Redis cache get failed for %s: %s", key, e)

        # Fallback to in-memory
        entry = self._memory.get(key)
        if entry is None:
            return None
        expiry, value = entry
        if time.time() > expiry:
            del self._memory[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Store a value with TTL. JSON-serializes the value."""
        serialized = json.dumps(value, default=str)

        if self._redis.is_ready:
            try:
                await self._redis.client.set(key, serialized, ex=ttl_seconds)
            except Exception as e:
                logger.debug("Redis cache set failed for %s: %s", key, e)

        # Fix #1/#2: Always write to in-memory regardless of Redis success.
        # This ensures get() can serve from memory if Redis fails transiently,
        # and delete() can fully invalidate across both layers.
        # Fix #4: Lock prevents concurrent set() calls from over-evicting.
        async with self._memory_lock:
            if len(self._memory) >= _MAX_MEMORY_ENTRIES:
                self._cleanup_memory()
            normalized = json.loads(serialized)
            self._memory[key] = (time.time() + ttl_seconds, normalized)

    async def delete(self, key: str) -> None:
        """Delete a key from cache."""
        if self._redis.is_ready:
            try:
                await self._redis.client.delete(key)
            except Exception as e:
                logger.debug("Redis cache delete failed for %s: %s", key, e)

        # Always clean up in-memory too
        self._memory.pop(key, None)

    async def delete_by_substring(self, substring: str) -> int:
        """Delete all keys containing the given substring.

        Scans Redis keys matching ``*substring*`` and removes matching
        in-memory entries.  Returns the number of keys deleted.
        """
        deleted = 0

        # Redis: SCAN with pattern
        if self._redis.is_ready:
            try:
                pattern = f"*{substring}*"
                cursor = 0
                while True:
                    cursor, keys = await self._redis.client.scan(
                        cursor=cursor, match=pattern, count=100,
                    )
                    if keys:
                        await self._redis.client.delete(*keys)
                        deleted += len(keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.debug(
                    "Redis pattern delete failed for *%s*: %s",
                    substring, e,
                )

        # In-memory: iterate and remove matching keys
        matching = [
            k for k in self._memory if substring in k
        ]
        for k in matching:
            del self._memory[k]
            deleted += 1

        return deleted

    def _cleanup_memory(self) -> None:
        """Remove expired entries from in-memory fallback.

        If still over the limit after expiry cleanup, evicts entries
        closest to expiry (soonest to expire) to make room.
        """
        now = time.time()
        expired = [k for k, (exp, _) in self._memory.items() if now > exp]
        for k in expired:
            del self._memory[k]
        # Evict soonest-to-expire entries if still at/over limit
        if len(self._memory) >= _MAX_MEMORY_ENTRIES:
            sorted_keys = sorted(self._memory, key=lambda k: self._memory[k][0])
            to_remove = len(self._memory) - _MAX_MEMORY_ENTRIES + 50  # headroom
            for k in sorted_keys[:to_remove]:
                del self._memory[k]

    @staticmethod
    def make_key(*parts: str) -> str:
        """Build a namespaced cache key: ``synthesis:{part1}:{part2}:...``"""
        return "synthesis:" + ":".join(parts)

    @staticmethod
    def hash_content(content: str) -> str:
        """SHA256 hash truncated to 16 chars, for use in cache keys."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]


# ── Module-level singleton ──────────────────────────────────────────────────

_instance: Optional[CacheService] = None


def init_cache(redis_service: RedisService) -> CacheService:
    """Initialize and return the cache service singleton."""
    global _instance
    _instance = CacheService(redis_service)
    return _instance


def get_cache() -> Optional[CacheService]:
    """Return the cache service singleton, or None if not initialized."""
    return _instance
