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
            return None
        value, timestamp = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._store[key]
            self._misses += 1
            return None
        self._store.move_to_end(key)
        self._hits += 1
        return value

    def set(self, key: str, value: str) -> None:
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self._max:
            self._store.popitem(last=False)
        self._store[key] = (value, time.monotonic())

    def invalidate(self, repo_full_name: str) -> None:
        keys_to_remove = [k for k in self._store if k.startswith(f"{repo_full_name}:")]
        for k in keys_to_remove:
            del self._store[k]

    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}
