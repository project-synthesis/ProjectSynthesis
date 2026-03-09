# backend/tests/integration/test_auth_api.py
"""Integration tests for /auth/me (GET + PATCH) and /auth/jwt/refresh."""
from __future__ import annotations

from httpx import AsyncClient


async def test_auth_me_requires_auth(client: AsyncClient):
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


async def test_auth_me_returns_profile(client: AsyncClient, auth_headers):
    resp = await client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("display_name", "email", "github_login", "onboarding_completed"):
        assert key in body, f"missing key: {key}"


async def test_auth_me_patch_display_name(client: AsyncClient, auth_headers):
    resp = await client.patch(
        "/auth/me",
        json={"display_name": "Integration Tester"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    get_resp = await client.get("/auth/me", headers=auth_headers)
    assert get_resp.json()["display_name"] == "Integration Tester"


async def test_auth_me_patch_email(client: AsyncClient, auth_headers):
    resp = await client.patch(
        "/auth/me",
        json={"email": "updated@integration.test"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    get_resp = await client.get("/auth/me", headers=auth_headers)
    assert get_resp.json()["email"] == "updated@integration.test"


async def test_auth_me_patch_display_name_too_long_returns_422(client: AsyncClient, auth_headers):
    # display_name has max_length=128; 200 chars should trigger a 422
    resp = await client.patch(
        "/auth/me",
        json={"display_name": "x" * 200},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_auth_refresh_no_cookie_returns_401(client: AsyncClient):
    # JWT refresh endpoint requires a jwt_refresh_token cookie; no cookie → 401
    resp = await client.post("/auth/jwt/refresh")
    assert resp.status_code == 401
