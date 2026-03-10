"""Tests for repo caching in github_repos router (migrated from in-memory to CacheService)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def test_evict_repo_cache_calls_cache_delete():
    """evict_repo_cache should call cache.delete with the correct key."""
    from app.routers.github_repos import evict_repo_cache
    from app.services.cache_service import CacheService

    mock_cache = AsyncMock(spec=CacheService)
    mock_cache.make_key = CacheService.make_key  # static method, not async
    with patch("app.routers.github_repos.get_cache", return_value=mock_cache):
        await evict_repo_cache("test-session-id")

    mock_cache.delete.assert_called_once_with("synthesis:repos:test-session-id")


async def test_evict_repo_cache_noop_when_no_cache():
    """evict_repo_cache should not raise when cache service is not initialized."""
    from app.routers.github_repos import evict_repo_cache

    with patch("app.routers.github_repos.get_cache", return_value=None):
        # Should not raise
        await evict_repo_cache("test-session-id")


async def test_list_repos_uses_cache():
    """list_repos should return cached repos when available."""
    from app.routers.github_repos import list_repos
    from app.services.cache_service import CacheService

    cached_repos = [{"name": "cached-repo", "full_name": "user/cached-repo"}]
    mock_cache = AsyncMock(spec=CacheService)
    mock_cache.make_key = CacheService.make_key  # static method, not async
    mock_cache.get = AsyncMock(return_value=cached_repos)

    mock_request = MagicMock()
    mock_request.session = {"session_id": "test-session"}

    mock_gh_token = MagicMock()
    mock_gh_token.github_user_id = 999
    mock_gh_token.github_login = "testuser"

    mock_user = MagicMock()
    mock_user.id = "user-uuid"
    mock_user.github_user_id = 999

    mock_current_user = MagicMock()
    mock_current_user.id = "user-uuid"

    gh_result = MagicMock()
    gh_result.scalar_one_or_none.return_value = mock_gh_token
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[gh_result, user_result])

    with patch("app.routers.github_repos.get_cache", return_value=mock_cache), \
         patch("app.routers.github_repos.github_service") as mock_svc:
        mock_svc.get_token_for_session = AsyncMock(return_value="ghp_test_token")
        result = await list_repos(
            request=mock_request,
            session=mock_session,
            current_user=mock_current_user,
        )

    assert result == cached_repos
    # Should not have called GitHub API since cache hit
    mock_svc.get_user_repos.assert_not_called()
