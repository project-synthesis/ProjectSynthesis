# backend/tests/integration/test_github_api.py
"""Integration tests for GitHub auth status and me endpoints."""
from __future__ import annotations

from httpx import AsyncClient


async def test_github_me_no_session_returns_not_connected(client: AsyncClient):
    """Without a session cookie, /auth/github/me returns connected=False (not 401)."""
    resp = await client.get("/auth/github/me")
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


async def test_github_me_with_auth_no_github_token_returns_not_connected(
    client: AsyncClient, auth_headers
):
    """Authenticated JWT user without a GitHub OAuth session returns connected=False."""
    resp = await client.get("/auth/github/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["connected"] is False
