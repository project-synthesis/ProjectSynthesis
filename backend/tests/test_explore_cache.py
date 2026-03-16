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
        cache = ExploreCache(ttl_seconds=0.1, max_entries=10)
        cache.set("key", "value")
        assert cache.get("key") == "value"
        time.sleep(0.15)
        assert cache.get("key") is None

    def test_lru_eviction(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=3)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        cache.get("a")  # touch 'a'
        cache.set("d", "4")  # evicts 'b' (LRU)
        assert cache.get("a") == "1"
        assert cache.get("b") is None
        assert cache.get("d") == "4"

    def test_sha_change_misses(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=10)
        cache.set("repo:main:sha1:p1", "old")
        assert cache.get("repo:main:sha2:p1") is None

    def test_invalidate_by_repo(self):
        cache = ExploreCache(ttl_seconds=60, max_entries=10)
        cache.set("owner/repo:main:sha1:p1", "ctx1")
        cache.set("owner/repo:main:sha1:p2", "ctx2")
        cache.set("other/repo:main:sha1:p1", "ctx3")
        cache.invalidate("owner/repo")
        assert cache.get("owner/repo:main:sha1:p1") is None
        assert cache.get("other/repo:main:sha1:p1") == "ctx3"

    def test_build_key(self):
        key = ExploreCache.build_key("owner/repo", "main", "abc123", "Write a function")
        assert "owner/repo" in key
        assert "main" in key
        assert "abc123" in key
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
