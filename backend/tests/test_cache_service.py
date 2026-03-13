"""Tests for the CacheService.

Run: cd backend && source .venv/bin/activate && pytest tests/test_cache_service.py -v
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from app.services.cache_service import CacheService


def _make_mock_redis(ready: bool = False) -> MagicMock:
    """Create a mock RedisService with correct is_ready property.

    Fix #3: use is_ready (not is_available) to match what CacheService checks.
    """
    mock = MagicMock()
    type(mock).is_ready = PropertyMock(return_value=ready)
    mock.client = None
    return mock


# ── Test: set and get round trip ──────────────────────────────────────────


async def test_set_and_get_round_trip():
    """Should store and retrieve a value from in-memory fallback."""
    redis_mock = _make_mock_redis(ready=False)
    cache = CacheService(redis_mock)

    await cache.set("test:key", {"data": 42}, ttl_seconds=300)
    result = await cache.get("test:key")

    assert result == {"data": 42}


# ── Test: miss returns None ───────────────────────────────────────────────


async def test_miss_returns_none():
    """Should return None for a key that doesn't exist."""
    redis_mock = _make_mock_redis(ready=False)
    cache = CacheService(redis_mock)

    result = await cache.get("nonexistent:key")
    assert result is None


# ── Test: TTL expiry ──────────────────────────────────────────────────────


async def test_ttl_expiry():
    """Should return None after TTL expires (in-memory mode)."""
    redis_mock = _make_mock_redis(ready=False)
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
    redis_mock = _make_mock_redis(ready=False)
    cache = CacheService(redis_mock)

    await cache.set("delete:key", "value", ttl_seconds=300)
    assert await cache.get("delete:key") == "value"

    await cache.delete("delete:key")
    assert await cache.get("delete:key") is None


# ── Test: fallback to memory ─────────────────────────────────────────────


async def test_fallback_to_memory():
    """When Redis is unavailable, should use in-memory storage."""
    redis_mock = _make_mock_redis(ready=False)
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


# ── Test: JSON normalization in memory fallback ──────────────────────


async def test_set_normalizes_data_types_in_memory():
    """In-memory fallback should store JSON-normalized values, not raw Python objects.

    This ensures that the in-memory path returns the same types as the Redis
    path (e.g., tuples become lists, non-serializable objects become strings).
    """
    redis_mock = _make_mock_redis(ready=False)
    cache = CacheService(redis_mock)

    # Tuples should become lists after JSON normalization
    await cache.set("norm:key", {"items": (1, 2, 3)}, ttl_seconds=300)
    result = await cache.get("norm:key")
    assert result == {"items": [1, 2, 3]}
    assert isinstance(result["items"], list)


# ── Test: memory eviction when over limit ────────────────────────────


async def test_memory_eviction_when_over_limit():
    """Entries should be evicted when in-memory cache exceeds the limit."""
    from app.services.cache_service import _MAX_MEMORY_ENTRIES

    redis_mock = _make_mock_redis(ready=False)
    cache = CacheService(redis_mock)

    # Fill cache past the limit with long-lived entries
    for i in range(_MAX_MEMORY_ENTRIES + 100):
        cache._memory[f"key:{i}"] = (time.time() + 86400, f"value-{i}")

    # Trigger cleanup by adding one more entry via set()
    await cache.set("trigger:key", "new-value", ttl_seconds=300)

    # Should be back under the limit (with 50 headroom evicted)
    assert len(cache._memory) <= _MAX_MEMORY_ENTRIES


# ── Test: Fix #1/#2 — set() always writes in-memory (dual-write) ─────


async def test_set_writes_to_memory_even_when_redis_succeeds():
    """Fix #1/#2: set() must write in-memory even on Redis success so get()
    can serve from memory if Redis fails transiently on a later read.
    """
    redis_mock = _make_mock_redis(ready=True)
    redis_mock.client = AsyncMock()
    cache = CacheService(redis_mock)

    await cache.set("dual:key", {"x": 1}, ttl_seconds=300)

    # Value should be in both Redis AND in-memory
    redis_mock.client.set.assert_called_once()
    assert "dual:key" in cache._memory
    expiry, val = cache._memory["dual:key"]
    assert val == {"x": 1}


async def test_get_falls_back_to_memory_on_redis_read_failure():
    """Fix #1: if Redis GET raises, the in-memory fallback should serve the value."""
    redis_mock = _make_mock_redis(ready=True)
    redis_mock.client = AsyncMock()
    # set() succeeds on Redis
    redis_mock.client.set = AsyncMock()
    cache = CacheService(redis_mock)

    await cache.set("fb:key", "hello", ttl_seconds=300)

    # Now make Redis GET fail
    redis_mock.client.get = AsyncMock(side_effect=ConnectionError("gone"))

    result = await cache.get("fb:key")
    assert result == "hello"


async def test_delete_removes_from_both_layers():
    """Fix #2: delete() must clear both Redis and in-memory to prevent resurrection."""
    redis_mock = _make_mock_redis(ready=True)
    redis_mock.client = AsyncMock()
    cache = CacheService(redis_mock)

    await cache.set("del:key", "val", ttl_seconds=300)
    assert "del:key" in cache._memory

    await cache.delete("del:key")
    assert "del:key" not in cache._memory
    redis_mock.client.delete.assert_called_once_with("del:key")


# ── Test: Fix #4 — concurrent set() calls don't over-evict ──────────


@pytest.mark.asyncio
async def test_concurrent_set_does_not_over_evict():
    """Fix #4: concurrent set() calls should not evict far below the limit."""
    from app.services.cache_service import _MAX_MEMORY_ENTRIES

    redis_mock = _make_mock_redis(ready=False)
    cache = CacheService(redis_mock)

    # Fill to exactly the limit
    for i in range(_MAX_MEMORY_ENTRIES):
        cache._memory[f"pre:{i}"] = (time.time() + 86400, f"val-{i}")

    # Fire 20 concurrent set() calls
    tasks = [
        cache.set(f"concurrent:{i}", f"data-{i}", ttl_seconds=300)
        for i in range(20)
    ]
    await asyncio.gather(*tasks)

    # Memory should not have been evicted far below the headroom target.
    # Without the lock, each concurrent call would independently evict 50 entries.
    # With the lock, cleanup runs once then subsequent calls just add entries.
    assert len(cache._memory) >= _MAX_MEMORY_ENTRIES - 50


# ── Test: key collision isolation across components ──────────────────


def test_make_key_different_components_no_collision():
    """Cache keys for different components must not collide."""
    repos_key = CacheService.make_key("repos", "session-123")
    strategy_key = CacheService.make_key("strategy_v3", "session-123")
    analyze_key = CacheService.make_key("analyze_v3", "session-123")
    explore_key = CacheService.make_key("explore", "owner/repo", "main", "sha", "session-123")

    keys = {repos_key, strategy_key, analyze_key, explore_key}
    assert len(keys) == 4, "Cache keys must be unique across components"


# ── Test: SessionCacheMiddleware ─────────────────────────────────────


@pytest.mark.asyncio
async def test_session_cache_middleware_writes_to_redis():
    """SessionCacheMiddleware should write session data to Redis after response."""
    from app.middleware.session_cache import SessionCacheMiddleware

    redis_mock = MagicMock()
    type(redis_mock).is_ready = PropertyMock(return_value=True)
    redis_mock.client = AsyncMock()

    app_mock = MagicMock()
    app_mock.state.redis = redis_mock

    session_data = {"session_id": "sess-abc", "user": "test"}

    inner_app = AsyncMock()

    middleware = SessionCacheMiddleware(inner_app)

    scope = {
        "type": "http",
        "app": app_mock,
        "session": session_data,
    }
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(scope, receive, send)

    # Should have written to Redis with the session key
    redis_mock.client.set.assert_called_once()
    call_args = redis_mock.client.set.call_args
    assert call_args[0][0] == "synthesis:session:sess-abc"
    assert call_args[1]["ex"] == 7 * 86400


@pytest.mark.asyncio
async def test_session_cache_middleware_noop_when_redis_unavailable():
    """SessionCacheMiddleware should be a no-op when Redis is not ready."""
    from app.middleware.session_cache import SessionCacheMiddleware

    redis_mock = MagicMock()
    type(redis_mock).is_ready = PropertyMock(return_value=False)

    app_mock = MagicMock()
    app_mock.state.redis = redis_mock

    inner_app = AsyncMock()
    middleware = SessionCacheMiddleware(inner_app)

    scope = {
        "type": "http",
        "app": app_mock,
        "session": {"session_id": "sess-abc"},
    }

    await middleware(scope, AsyncMock(), AsyncMock())

    # Redis client should not have been accessed
    assert not hasattr(redis_mock.client, "set") or not redis_mock.client.set.called


@pytest.mark.asyncio
async def test_session_cache_middleware_handles_redis_write_failure():
    """Fix #5/#6: Redis write failure should not break the response."""
    from app.middleware.session_cache import SessionCacheMiddleware

    redis_mock = MagicMock()
    type(redis_mock).is_ready = PropertyMock(return_value=True)
    redis_mock.client = AsyncMock()
    redis_mock.client.set = AsyncMock(side_effect=ConnectionError("Redis down"))

    app_mock = MagicMock()
    app_mock.state.redis = redis_mock

    inner_app = AsyncMock()
    middleware = SessionCacheMiddleware(inner_app)

    scope = {
        "type": "http",
        "app": app_mock,
        "session": {"session_id": "sess-abc", "data": "test"},
    }

    # Should not raise
    await middleware(scope, AsyncMock(), AsyncMock())


@pytest.mark.asyncio
async def test_session_cache_middleware_skips_non_http():
    """SessionCacheMiddleware should pass through non-HTTP scopes."""
    from app.middleware.session_cache import SessionCacheMiddleware

    inner_app = AsyncMock()
    middleware = SessionCacheMiddleware(inner_app)

    scope = {"type": "websocket"}
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(scope, receive, send)
    inner_app.assert_called_once_with(scope, receive, send)


@pytest.mark.asyncio
async def test_session_cache_middleware_skips_missing_session_id():
    """SessionCacheMiddleware should skip when session has no session_id."""
    from app.middleware.session_cache import SessionCacheMiddleware

    redis_mock = MagicMock()
    type(redis_mock).is_ready = PropertyMock(return_value=True)
    redis_mock.client = AsyncMock()

    app_mock = MagicMock()
    app_mock.state.redis = redis_mock

    inner_app = AsyncMock()
    middleware = SessionCacheMiddleware(inner_app)

    scope = {
        "type": "http",
        "app": app_mock,
        "session": {"some_other_key": "value"},  # no session_id
    }

    await middleware(scope, AsyncMock(), AsyncMock())

    # Should not have written to Redis
    redis_mock.client.set.assert_not_called()


# ── Test: Strategy and Analyze cache hit paths ───────────────────────


@pytest.mark.asyncio
async def test_analyze_cache_hit_returns_cached_result():
    """Fix #17: Analyze stage should return cached result on cache hit."""
    from unittest.mock import patch

    from app.services.analyzer import run_analyze

    cached_analysis = {
        "task_type": "code_generation",
        "complexity": "moderate",
        "weaknesses": ["lacks specificity"],
        "strengths": ["clear intent"],
        "recommended_frameworks": ["chain_of_thought"],
    }

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(return_value=cached_analysis)
    mock_cache.hash_content = CacheService.hash_content
    mock_cache.make_key = CacheService.make_key

    mock_provider = MagicMock()

    with patch("app.services.analyzer.get_cache", return_value=mock_cache):
        events = []
        async for event in run_analyze(
            provider=mock_provider,
            raw_prompt="test prompt",
        ):
            events.append(event)

    assert len(events) == 1
    assert events[0][0] == "analysis"
    assert events[0][1]["analysis_quality"] == "cached"
    # LLM should NOT have been called
    mock_provider.complete.assert_not_called()


@pytest.mark.asyncio
async def test_strategy_cache_hit_returns_cached_result():
    """Fix #17: Strategy stage should return cached result on cache hit."""
    from unittest.mock import patch

    from app.services.strategy import run_strategy

    cached_strategy = {
        "primary_framework": "chain_of_thought",
        "secondary_frameworks": [],
        "rationale": "Best for code gen",
        "approach_notes": "Use step-by-step",
    }

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(return_value=cached_strategy)
    mock_cache.hash_content = CacheService.hash_content
    mock_cache.make_key = CacheService.make_key

    mock_provider = MagicMock()
    analysis = {"task_type": "code_generation", "complexity": "moderate"}

    with patch("app.services.strategy.get_cache", return_value=mock_cache):
        events = []
        async for event in run_strategy(
            provider=mock_provider,
            raw_prompt="test prompt",
            analysis=analysis,
        ):
            events.append(event)

    assert len(events) == 1
    assert events[0][0] == "strategy"
    assert events[0][1]["strategy_source"] == "cached"
    mock_provider.complete.assert_not_called()


# ── Test: Explore cache skip on SHA failure (Fix #11/#12) ────────────


@pytest.mark.asyncio
async def test_explore_does_not_cache_when_sha_is_none():
    """Fix #11/#12: explore should not cache results when HEAD SHA fetch failed."""
    from unittest.mock import patch

    from app.services.codebase_explorer import run_explore

    mock_provider = MagicMock()
    mock_provider.complete_json = AsyncMock(return_value={
        "tech_stack": ["Python"],
        "key_files_read": ["main.py"],
        "relevant_code_snippets": [],
        "codebase_observations": ["obs"],
        "prompt_grounding_notes": ["note"],
    })

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(return_value=None)  # cache miss
    mock_cache.set = AsyncMock()
    mock_cache.hash_content = CacheService.hash_content
    mock_cache.make_key = CacheService.make_key

    mock_tree = [
        {"path": "README.md", "sha": "abc", "size_bytes": 500},
        {"path": "main.py", "sha": "def", "size_bytes": 200},
    ]
    from app.services.repo_index_service import IndexStatus

    mock_idx_svc = MagicMock()
    mock_idx_svc.get_index_status = AsyncMock(
        return_value=IndexStatus(status="none")
    )

    with (
        patch("anyio.to_thread.run_sync", new_callable=AsyncMock),
        patch("app.services.codebase_explorer.get_cache", return_value=mock_cache),
        patch(
            "app.services.codebase_explorer.get_branch_head_sha",
            new_callable=AsyncMock,
            return_value=None,  # SHA fetch failed
        ),
        patch(
            "app.services.codebase_explorer.get_repo_tree",
            new_callable=AsyncMock,
            return_value=mock_tree,
        ),
        patch(
            "app.services.codebase_explorer.get_repo_index_service",
            return_value=mock_idx_svc,
        ),
        patch(
            "app.services.codebase_explorer.read_file_content",
            new_callable=AsyncMock,
            return_value="# content\nline2",
        ),
    ):
        events = []
        async for event in run_explore(
            provider=mock_provider,
            raw_prompt="test prompt",
            repo_full_name="owner/repo",
            repo_branch="main",
            github_token="fake-token",
        ):
            events.append(event)

    # Should have produced a result
    result_events = [e for e in events if e[0] == "explore_result"]
    assert len(result_events) == 1

    # cache.set should NOT have been called (SHA is None)
    mock_cache.set.assert_not_called()


# ── Test: Fix #9 — Analyze cache key varies with context content ─────


@pytest.mark.asyncio
async def test_analyze_cache_key_varies_with_context_content():
    """Fix #9: Different codebase contexts with the same prompt should produce
    different cache keys (not just bool(context) flags).
    """
    from unittest.mock import patch

    from app.services.analyzer import run_analyze

    mock_cache = AsyncMock()
    mock_cache.get = AsyncMock(return_value=None)  # always miss
    mock_cache.set = AsyncMock()
    mock_cache.hash_content = CacheService.hash_content
    mock_cache.make_key = CacheService.make_key

    mock_provider = MagicMock()
    analyze_json = (
        '{"task_type":"general","complexity":"simple",'
        '"weaknesses":[],"strengths":[],"recommended_frameworks":[]}'
    )
    mock_provider.complete = AsyncMock(return_value=analyze_json)

    cache_keys_used = []

    async def capture_key(key):
        cache_keys_used.append(key)
        return None

    mock_cache.get = capture_key

    # Run 1: with codebase context from repo A
    with patch("app.services.analyzer.get_cache", return_value=mock_cache):
        async for _ in run_analyze(
            provider=mock_provider,
            raw_prompt="test prompt",
            codebase_context={"repo": "owner/repoA", "branch": "main"},
        ):
            pass

    # Run 2: same prompt, different repo
    with patch("app.services.analyzer.get_cache", return_value=mock_cache):
        async for _ in run_analyze(
            provider=mock_provider,
            raw_prompt="test prompt",
            codebase_context={"repo": "owner/repoB", "branch": "main"},
        ):
            pass

    assert len(cache_keys_used) == 2
    assert cache_keys_used[0] != cache_keys_used[1], (
        "Same prompt with different repos must produce different cache keys"
    )


@pytest.mark.asyncio
async def test_analyze_cache_key_varies_with_observations():
    """Same repo+branch but different observations (e.g. after code push) must
    produce different cache keys — not just repo identity.
    """
    from unittest.mock import patch

    from app.services.analyzer import run_analyze

    mock_cache = AsyncMock()
    mock_cache.set = AsyncMock()
    mock_cache.hash_content = CacheService.hash_content
    mock_cache.make_key = CacheService.make_key

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(
        return_value='{"task_type":"general","complexity":"simple","weaknesses":[],"strengths":[],"recommended_frameworks":[]}'
    )

    cache_keys_used = []

    async def capture_key(key):
        cache_keys_used.append(key)
        return None

    mock_cache.get = capture_key

    # Run 1: repo A@main with observation set X
    with patch("app.services.analyzer.get_cache", return_value=mock_cache):
        async for _ in run_analyze(
            provider=mock_provider,
            raw_prompt="test prompt",
            codebase_context={
                "repo": "owner/repo", "branch": "main",
                "observations": ["Uses FastAPI with SQLAlchemy"],
            },
        ):
            pass

    # Run 2: same repo+branch, different observations (code was pushed)
    with patch("app.services.analyzer.get_cache", return_value=mock_cache):
        async for _ in run_analyze(
            provider=mock_provider,
            raw_prompt="test prompt",
            codebase_context={
                "repo": "owner/repo", "branch": "main",
                "observations": ["Uses Django with Postgres"],
            },
        ):
            pass

    assert len(cache_keys_used) == 2
    assert cache_keys_used[0] != cache_keys_used[1], (
        "Same repo but different observations must produce different cache keys"
    )
