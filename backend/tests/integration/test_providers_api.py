# backend/tests/integration/test_providers_api.py
"""Integration tests for providers, settings, and health endpoints."""
from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"


async def test_providers_detect_requires_auth(client: AsyncClient):
    resp = await client.get("/api/providers/detect")
    assert resp.status_code == 401


async def test_providers_detect_returns_provider_map(client: AsyncClient, auth_headers):
    resp = await client.get("/api/providers/detect", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "providers" in body


async def test_providers_status_requires_auth(client: AsyncClient):
    resp = await client.get("/api/providers/status")
    assert resp.status_code == 401


async def test_providers_status_returns_healthy_flag(client: AsyncClient, auth_headers):
    resp = await client.get("/api/providers/status", headers=auth_headers)
    assert resp.status_code == 200
    assert "healthy" in resp.json()


async def test_settings_get_requires_auth(client: AsyncClient):
    resp = await client.get("/api/settings")
    assert resp.status_code == 401


async def test_settings_round_trip(client: AsyncClient, auth_headers):
    get_resp = await client.get("/api/settings", headers=auth_headers)
    assert get_resp.status_code == 200
    original = get_resp.json()

    # Patch one boolean field and verify it persists
    field = next(
        (k for k, v in original.items() if isinstance(v, bool)),
        None,
    )
    if field:
        new_val = not original[field]
        patch_resp = await client.patch(
            "/api/settings", json={field: new_val}, headers=auth_headers
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()[field] == new_val
