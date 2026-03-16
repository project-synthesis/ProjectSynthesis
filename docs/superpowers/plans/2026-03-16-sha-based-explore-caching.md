# SHA-Based Explore Caching — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache explore results by `(repo, branch, head_sha, prompt_hash)` so identical prompts against unchanged repos return instantly. SHA-based key = new push auto-invalidates.

**Architecture:** New `ExploreCache` class — in-memory dict with TTL and LRU eviction. The `CodebaseExplorer.explore()` method checks cache before running the pipeline and stores results after. Cache key includes HEAD SHA so pushes automatically miss.

**Tech Stack:** Python 3.12+, hashlib, time, existing CodebaseExplorer

---

## File Structure

| File | Changes |
|------|---------|
| `backend/app/services/explore_cache.py` | New — in-memory TTL cache with LRU |
| `backend/app/services/codebase_explorer.py` | Integrate cache check/store |
| `backend/app/config.py` | Add `EXPLORE_RESULT_CACHE_TTL` setting |
| `backend/tests/test_explore_cache.py` | New — cache behavior tests |
| `backend/tests/test_codebase_explorer.py` | Add cache integration tests |

---

### Task 1: ExploreCache

**Files:**
- Create: `backend/app/services/explore_cache.py`
- Create: `backend/tests/test_explore_cache.py`

- [ ] **Step 1: Write cache tests**

```python
# backend/tests/test_explore_cache.py
"""Tests for in-memory explore result cache."""

import time
import pytest
from app.services.explore_cache import ExploreCache


class TestExploreCache:
    def test_set_and_get(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=10)
        cache.set("repo:main:sha1:abc", "cached context")
        assert cache.get("repo:main:sha1:abc") == "cached context"

    def test_miss_returns_none(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=10)
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self):
        cache = ExploreCache(ttl_seconds=0.1, max_entries=10)  # 100ms TTL
        cache.set("key", "value")
        assert cache.get("key") == "value"
        time.sleep(0.15)
        assert cache.get("key") is None  # expired

    def test_lru_eviction(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=3)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        cache.get("a")  # access 'a' to make it recently used
        cache.set("d", "4")  # should evict 'b' (least recently used)
        assert cache.get("a") == "1"  # still present
        assert cache.get("b") is None  # evicted
        assert cache.get("d") == "4"  # new entry

    def test_sha_change_misses(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=10)
        cache.set("repo:main:sha1:prompt1", "old context")
        # Same repo+branch+prompt but different SHA = miss
        assert cache.get("repo:main:sha2:prompt1") is None

    def test_invalidate_by_repo(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=10)
        cache.set("owner/repo:main:sha1:p1", "ctx1")
        cache.set("owner/repo:main:sha1:p2", "ctx2")
        cache.set("other/repo:main:sha1:p1", "ctx3")
        cache.invalidate("owner/repo")
        assert cache.get("owner/repo:main:sha1:p1") is None
        assert cache.get("owner/repo:main:sha1:p2") is None
        assert cache.get("other/repo:main:sha1:p1") == "ctx3"

    def test_build_key(self):
        key = ExploreCache.build_key("owner/repo", "main", "abc123", "Write a function")
        assert "owner/repo" in key
        assert "main" in key
        assert "abc123" in key
        # prompt is hashed, not plaintext
        assert "Write a function" not in key

    def test_stats(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=10)
        cache.set("a", "1")
        cache.get("a")  # hit
        cache.get("b")  # miss
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
```

- [ ] **Step 2: Implement ExploreCache**

```python
# backend/app/services/explore_cache.py
"""In-memory TTL cache for explore results with LRU eviction.

Keys include HEAD SHA so pushes auto-invalidate.
"""

import hashlib
import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)


class ExploreCache:
    """Thread-safe in-memory cache with TTL and LRU eviction."""

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 100) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def build_key(repo_full_name: str, branch: str, head_sha: str, raw_prompt: str) -> str:
        """Build cache key from repo, branch, SHA, and prompt hash."""
        prompt_hash = hashlib.sha256(raw_prompt.encode()).hexdigest()[:16]
        return f"{repo_full_name}:{branch}:{head_sha}:{prompt_hash}"

    def get(self, key: str) -> str | None:
        """Get cached value, or None on miss/expiry."""
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, timestamp = entry
        if time.monotonic() - timestamp > self._ttl:
            del self._store[key]
            self._misses += 1
            return None
        # Move to end (most recently used)
        self._store.move_to_end(key)
        self._hits += 1
        return value

    def set(self, key: str, value: str) -> None:
        """Store value with current timestamp. Evicts LRU if at capacity."""
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self._max:
            self._store.popitem(last=False)  # evict oldest (LRU)
        self._store[key] = (value, time.monotonic())

    def invalidate(self, repo_full_name: str) -> None:
        """Remove all entries for a given repo."""
        keys_to_remove = [k for k in self._store if k.startswith(f"{repo_full_name}:")]
        for k in keys_to_remove:
            del self._store[k]

    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._store)}
```

- [ ] **Step 3: Run tests, commit**

---

### Task 2: Config + Explorer Integration

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/services/codebase_explorer.py`
- Modify: `backend/tests/test_codebase_explorer.py`

- [ ] **Step 1: Add config setting**

Add to `backend/app/config.py` Settings class:
```python
EXPLORE_RESULT_CACHE_TTL: int = 3600  # seconds (1 hour)
```

- [ ] **Step 2: Integrate cache into CodebaseExplorer**

The explorer should have a class-level cache singleton (shared across instances, like the embedding model):

```python
# At module level in codebase_explorer.py:
from app.services.explore_cache import ExploreCache
_explore_cache = ExploreCache(ttl_seconds=settings.EXPLORE_RESULT_CACHE_TTL)
```

In `_explore_inner()`, after getting `head_sha` but before ranking files:

```python
# Check cache
cache_key = _explore_cache.build_key(repo_full_name, branch, head_sha, raw_prompt)
cached = _explore_cache.get(cache_key)
if cached is not None:
    logger.info("Explore cache hit for %s@%s (SHA=%s)", repo_full_name, branch, head_sha[:8])
    return cached

# ... existing pipeline ...

# After synthesis, before returning:
_explore_cache.set(cache_key, result.context)
return result.context
```

- [ ] **Step 3: Add explorer cache test**

```python
async def test_explore_uses_cache(self, explorer):
    """Second call with same SHA returns cached result without calling provider."""
    # Setup mocks for first call
    explorer._gc.get_branch_head_sha = AsyncMock(return_value="sha1")
    explorer._gc.get_tree = AsyncMock(return_value=[
        {"path": "src/main.py", "type": "blob", "sha": "a1", "size": 100},
    ])
    explorer._gc.get_file_content = AsyncMock(return_value="def main(): pass")
    import numpy as np
    explorer._es.aembed_single = AsyncMock(return_value=np.zeros(384))
    explorer._es.cosine_search = MagicMock(return_value=[(0, 0.9)])
    explorer._provider.complete_parsed = AsyncMock(return_value=ExploreOutput(context="Cached result"))

    # First call — cache miss, runs full pipeline
    result1 = await explorer.explore("Write a function", "owner/repo", "main", "token")
    assert result1 == "Cached result"
    assert explorer._provider.complete_parsed.call_count == 1

    # Second call — same SHA + prompt → cache hit, no provider call
    result2 = await explorer.explore("Write a function", "owner/repo", "main", "token")
    assert result2 == "Cached result"
    assert explorer._provider.complete_parsed.call_count == 1  # still 1, not 2
```

- [ ] **Step 4: Run full suite, commit**
