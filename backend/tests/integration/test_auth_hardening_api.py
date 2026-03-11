"""Integration tests for auth hardening and onboarding endpoints.

Covers:
- POST /auth/logout (via AsyncClient against real app)
- PATCH /auth/me with onboarding_step (valid, invalid, null)
- GET /api/onboarding/funnel (empty, seeded events, action breakdown)
- POST /api/onboarding/events (tracking)

Run: cd backend && source .venv/bin/activate && pytest tests/integration/test_auth_hardening_api.py -v
"""
from __future__ import annotations

from httpx import AsyncClient

# ── POST /auth/logout ─────────────────────────────────────────────────────


async def test_logout_requires_auth(client: AsyncClient):
    """POST /auth/logout without auth returns 401."""
    resp = await client.post("/auth/logout")
    assert resp.status_code == 401


async def test_logout_returns_revoked_count(client: AsyncClient, auth_headers):
    """POST /auth/logout returns revoked_count (0 is valid when no RTs exist)."""
    resp = await client.post("/auth/logout", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "revoked_count" in body
    assert isinstance(body["revoked_count"], int)


# ── PATCH /auth/me — onboarding_step ──────────────────────────────────────


async def test_patch_auth_me_onboarding_step_valid(client: AsyncClient, auth_headers):
    """PATCH /auth/me with onboarding_step=2 persists and returns in GET."""
    resp = await client.patch(
        "/auth/me",
        json={"onboarding_step": 2},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    get_resp = await client.get("/auth/me", headers=auth_headers)
    assert get_resp.json()["onboarding_step"] == 2


async def test_patch_auth_me_onboarding_step_out_of_range(client: AsyncClient, auth_headers):
    """PATCH /auth/me with onboarding_step=0 returns 422 (below ge=1)."""
    resp = await client.patch(
        "/auth/me",
        json={"onboarding_step": 0},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_patch_auth_me_onboarding_step_above_max(client: AsyncClient, auth_headers):
    """PATCH /auth/me with onboarding_step=5 returns 422 (above le=4)."""
    resp = await client.patch(
        "/auth/me",
        json={"onboarding_step": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_patch_auth_me_onboarding_step_null_clears(client: AsyncClient, auth_headers):
    """PATCH /auth/me with onboarding_step=null clears the saved step."""
    # First set a step
    await client.patch(
        "/auth/me",
        json={"onboarding_step": 3},
        headers=auth_headers,
    )
    # Then clear it
    resp = await client.patch(
        "/auth/me",
        json={"onboarding_step": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    get_resp = await client.get("/auth/me", headers=auth_headers)
    assert get_resp.json()["onboarding_step"] is None


# ── POST /api/onboarding/events ───────────────────────────────────────────


async def test_track_onboarding_event(client: AsyncClient, auth_headers):
    """POST /api/onboarding/events creates an event record."""
    resp = await client.post(
        "/api/onboarding/events",
        json={"event_type": "wizard_started"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["tracked"] is True


async def test_track_onboarding_event_requires_auth(client: AsyncClient):
    """POST /api/onboarding/events without auth returns 401."""
    resp = await client.post(
        "/api/onboarding/events",
        json={"event_type": "wizard_started"},
    )
    assert resp.status_code == 401


# ── GET /api/onboarding/funnel ────────────────────────────────────────────


async def test_onboarding_funnel_requires_auth(client: AsyncClient):
    """GET /api/onboarding/funnel without auth returns 401."""
    resp = await client.get("/api/onboarding/funnel")
    assert resp.status_code == 401


async def test_onboarding_funnel_returns_structure(client: AsyncClient, auth_headers):
    """GET /api/onboarding/funnel returns the expected response shape."""
    resp = await client.get("/api/onboarding/funnel", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_users", "wizard_started", "wizard_completed", "wizard_skipped",
                "step_counts", "action_breakdown"):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["total_users"], int)
    assert isinstance(body["step_counts"], dict)
    assert isinstance(body["action_breakdown"], dict)


async def test_onboarding_funnel_counts_seeded_events(client: AsyncClient, auth_headers, engine):
    """GET /api/onboarding/funnel counts events tracked via the events endpoint."""
    # Seed events through the API
    for event_type in ("wizard_started", "wizard_step_1", "wizard_step_2", "wizard_completed"):
        metadata = {"action": "sample"} if event_type == "wizard_completed" else None
        await client.post(
            "/api/onboarding/events",
            json={"event_type": event_type, "metadata": metadata},
            headers=auth_headers,
        )

    resp = await client.get("/api/onboarding/funnel", headers=auth_headers)
    body = resp.json()

    assert body["wizard_started"] >= 1
    assert body["wizard_completed"] >= 1
    assert body["total_users"] >= 1
    # step_counts should contain our wizard_step_* events
    assert "wizard_step_1" in body["step_counts"]
    assert body["step_counts"]["wizard_step_1"] >= 1


async def test_onboarding_funnel_action_breakdown(client: AsyncClient, auth_headers, engine):
    """GET /api/onboarding/funnel action_breakdown parses wizard_completed metadata."""
    # Track a completion with a specific action
    await client.post(
        "/api/onboarding/events",
        json={"event_type": "wizard_completed", "metadata": {"action": "write"}},
        headers=auth_headers,
    )

    resp = await client.get("/api/onboarding/funnel", headers=auth_headers)
    body = resp.json()

    # action_breakdown should include "write" from our event
    assert "write" in body["action_breakdown"]
    assert body["action_breakdown"]["write"] >= 1
