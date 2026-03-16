"""In-memory TTL cache for explore results with LRU eviction."""

import hashlib
import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)


class ExploreCache:
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 100) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def build_key(repo_full_name: str, branch: str, head_sha: str, raw_prompt: str) -> str:
        prompt_hash = hashlib.sha256(raw_prompt.encode()).hexdigest()[:16]
        return f"{repo_full_name}:{branch}:{head_sha}:{prompt_hash}"

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            logger.debug("Explore cache miss: %s (total misses=%d)", key[:60], self._misses)
            return None
        value, timestamp = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._store[key]
            self._misses += 1
            logger.debug("Explore cache TTL expired: %s", key[:60])
            return None
        self._store.move_to_end(key)
        self._hits += 1
        logger.debug("Explore cache hit: %s (total hits=%d)", key[:60], self._hits)
        return value

    def set(self, key: str, value: str) -> None:
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self._max:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug("Explore cache LRU eviction: %s (size was %d)", evicted_key[:60], self._max)
        self._store[key] = (value, time.monotonic())
        logger.debug("Explore cache set: %s (%d chars, size=%d)", key[:60], len(value), len(self._store))

    def invalidate(self, repo_full_name: str) -> None:
        keys_to_remove = [k for k in self._store if k.startswith(f"{repo_full_name}:")]
        for k in keys_to_remove:
            del self._store[k]
        if keys_to_remove:
            logger.info("Explore cache invalidated %d entries for %s", len(keys_to_remove), repo_full_name)

    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}
