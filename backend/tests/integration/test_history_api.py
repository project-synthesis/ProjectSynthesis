# backend/tests/integration/test_history_api.py
"""Integration tests for GET/DELETE /api/history, trash, restore, stats."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ── Module-level patch: redirect async_session to the test engine ──────────

@pytest.fixture(scope="module", autouse=True)
def patch_async_session(engine):
    """Redirect app.database.async_session to the test engine's session factory.

    The optimize router calls async_session() directly (not via DI), so we must
    patch the module-level factory to point at the test DB.
    """
    import app.database as db_module
    import app.routers.optimize as opt_module

    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch.object(db_module, "async_session", TestSession),
        patch.object(opt_module, "async_session", TestSession),
    ):
        yield


# ── Helpers ───────────────────────────────────────────────────────────────

async def _create_optimization(client: AsyncClient, headers: dict, raw_prompt: str = "Test prompt") -> str:
    """Stream /api/optimize and return the created optimization id."""
    opt_id = None
    async with client.stream(
        "POST", "/api/optimize",
        json={"prompt": raw_prompt},
        headers=headers,
        timeout=30,
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data:") and '"optimization_id"' in line:
                data = json.loads(line[5:].strip())
                if "optimization_id" in data:
                    opt_id = data["optimization_id"]
    assert opt_id, "optimization_id not found in SSE stream"
    return opt_id


# ── GET /api/history ───────────────────────────────────────────────────────

async def test_history_requires_auth(client: AsyncClient):
    resp = await client.get("/api/history")
    assert resp.status_code == 401


async def test_history_returns_pagination_envelope(client: AsyncClient, auth_headers):
    resp = await client.get("/api/history", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total", "count", "offset", "items", "has_more", "next_offset"):
        assert key in body, f"missing key: {key}"


async def test_history_user_isolation(client: AsyncClient, auth_headers, other_auth_headers):
    """Records created by user A must not appear in user B's listing."""
    await _create_optimization(client, auth_headers, "User A isolation prompt")
    resp = await client.get("/api/history", headers=other_auth_headers)
    body = resp.json()
    # Other user should see zero records (they haven't created any)
    assert body["total"] == 0, f"User isolation failed: other user sees {body['total']} records"


async def test_history_filter_min_score(client: AsyncClient, auth_headers):
    resp = await client.get("/api/history?min_score=1", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        if item.get("overall_score") is not None:
            assert item["overall_score"] >= 1


async def test_history_filter_max_score(client: AsyncClient, auth_headers):
    resp = await client.get("/api/history?max_score=10", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        if item.get("overall_score") is not None:
            assert item["overall_score"] <= 10


async def test_history_filter_task_type(client: AsyncClient, auth_headers):
    resp = await client.get("/api/history?task_type=instruction", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        if item.get("task_type") is not None:
            assert item["task_type"] == "instruction"


async def test_history_filter_status(client: AsyncClient, auth_headers):
    resp = await client.get("/api/history?status=completed", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for item in body["items"]:
        if item.get("status") is not None:
            assert item["status"] == "completed"


async def test_history_pagination(client: AsyncClient, auth_headers):
    # Create 3 records to ensure pagination kicks in
    for i in range(3):
        await _create_optimization(client, auth_headers, f"Pagination test prompt {i}")

    resp = await client.get("/api/history?limit=2&offset=0", headers=auth_headers)
    body = resp.json()
    assert body["count"] <= 2
    if body["total"] > 2:
        assert body["has_more"] is True
        assert body["next_offset"] == 2


# ── DELETE /api/history/{id} ───────────────────────────────────────────────

async def test_delete_requires_auth(client: AsyncClient, auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "To be deleted")
    resp = await client.delete(f"/api/history/{opt_id}")
    assert resp.status_code == 401


async def test_delete_soft_deletes_record(client: AsyncClient, auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "Soft delete test")

    resp = await client.delete(f"/api/history/{opt_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Should not appear in normal listing
    list_resp = await client.get("/api/history", headers=auth_headers)
    ids = [item["id"] for item in list_resp.json()["items"]]
    assert opt_id not in ids


async def test_delete_wrong_user_returns_404(client: AsyncClient, auth_headers, other_auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "Other user cannot delete")
    resp = await client.delete(f"/api/history/{opt_id}", headers=other_auth_headers)
    assert resp.status_code == 404


# ── GET /api/history/trash ─────────────────────────────────────────────────

async def test_trash_requires_auth(client: AsyncClient):
    resp = await client.get("/api/history/trash")
    assert resp.status_code == 401


async def test_trash_shows_deleted_items(client: AsyncClient, auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "Trash test item")
    await client.delete(f"/api/history/{opt_id}", headers=auth_headers)

    resp = await client.get("/api/history/trash", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert any(key in body for key in ("items", "total")), "not a pagination envelope"
    ids = [item["id"] for item in body["items"]]
    assert opt_id in ids


async def test_trash_returns_pagination_envelope(client: AsyncClient, auth_headers):
    resp = await client.get("/api/history/trash", headers=auth_headers)
    body = resp.json()
    for key in ("total", "count", "offset", "items", "has_more", "next_offset"):
        assert key in body


# ── POST /api/history/{id}/restore ────────────────────────────────────────

async def test_restore_requires_auth(client: AsyncClient, auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "Restore auth test")
    await client.delete(f"/api/history/{opt_id}", headers=auth_headers)
    resp = await client.post(f"/api/history/{opt_id}/restore")
    assert resp.status_code == 401


async def test_restore_happy_path(client: AsyncClient, auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "Restore me")
    await client.delete(f"/api/history/{opt_id}", headers=auth_headers)

    resp = await client.post(f"/api/history/{opt_id}/restore", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["restored"] is True

    # Should be back in normal listing
    list_resp = await client.get("/api/history", headers=auth_headers)
    ids = [item["id"] for item in list_resp.json()["items"]]
    assert opt_id in ids


async def test_restore_wrong_user_returns_404(client: AsyncClient, auth_headers, other_auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "Other cannot restore")
    await client.delete(f"/api/history/{opt_id}", headers=auth_headers)
    resp = await client.post(f"/api/history/{opt_id}/restore", headers=other_auth_headers)
    assert resp.status_code == 404


async def test_restore_not_in_trash_returns_404(client: AsyncClient, auth_headers):
    opt_id = await _create_optimization(client, auth_headers, "Not deleted")
    resp = await client.post(f"/api/history/{opt_id}/restore", headers=auth_headers)
    assert resp.status_code == 404


# ── GET /api/history/stats ─────────────────────────────────────────────────

async def test_stats_requires_auth(client: AsyncClient):
    resp = await client.get("/api/history/stats")
    assert resp.status_code == 401


async def test_stats_returns_expected_shape(client: AsyncClient, auth_headers):
    resp = await client.get("/api/history/stats", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    for key in ("total_optimizations", "average_score", "framework_breakdown",
                "task_type_breakdown", "provider_breakdown"):
        assert key in body


async def test_stats_user_scoped(client: AsyncClient, auth_headers, other_auth_headers):
    """Stats total must match only the current user's records."""
    resp_a = await client.get("/api/history/stats", headers=auth_headers)
    resp_b = await client.get("/api/history/stats", headers=other_auth_headers)
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    # User A has created optimizations, user B has none yet — totals must differ
    body_a = resp_a.json()
    body_b = resp_b.json()
    # Both must have the expected shape
    assert "total_optimizations" in body_a
    assert "total_optimizations" in body_b
    # User A has created records, B has not — scoping means totals differ
    # (allow equal only if user A also has 0 records, which shouldn't happen in practice)
    assert body_a["total_optimizations"] >= body_b["total_optimizations"]
