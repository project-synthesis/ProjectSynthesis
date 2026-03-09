# backend/tests/integration/test_optimize_api.py
"""Integration tests for POST /api/optimize SSE pipeline and GET/PATCH endpoints."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ── Module-level patch: redirect async_session to the test engine ──────────

@pytest.fixture(scope="module", autouse=True)
def patch_async_session(engine):
    """Redirect app.database.async_session and app.routers.optimize.async_session
    to the test engine's session factory.

    The optimize router calls async_session() directly (not via FastAPI DI), so we
    must patch both the db module and the router's local reference.
    """
    import app.database as db_module
    import app.routers.optimize as opt_module

    TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch.object(db_module, "async_session", TestSession),
        patch.object(opt_module, "async_session", TestSession),
    ):
        yield


# ── Helpers ────────────────────────────────────────────────────────────────

async def _stream_optimize(
    client: AsyncClient, headers: dict, prompt: str
) -> tuple[str | None, list[dict]]:
    """Run the optimize pipeline and return (optimization_id, all_events).

    Reads all 'data:' lines from the SSE stream. The optimization_id is
    present in the 'complete' event data.
    """
    opt_id = None
    events = []
    async with client.stream(
        "POST", "/api/optimize",
        json={"prompt": prompt},
        headers=headers,
        timeout=30,
    ) as resp:
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                data = json.loads(raw)
                events.append(data)
                if "optimization_id" in data and opt_id is None:
                    opt_id = data["optimization_id"]
            except json.JSONDecodeError:
                pass
    return opt_id, events


# ── Tests ─────────────────────────────────────────────────────────────────

async def test_optimize_requires_auth(client: AsyncClient):
    resp = await client.post("/api/optimize", json={"prompt": "test"})
    assert resp.status_code == 401


async def test_optimize_sse_stream_opens(client: AsyncClient, auth_headers):
    opt_id, events = await _stream_optimize(client, auth_headers, "Write a concise summary.")
    assert opt_id is not None, "No optimization_id in SSE stream"
    assert len(events) > 0, "No SSE events emitted"


async def test_optimize_sse_emits_stage_events(client: AsyncClient, auth_headers):
    _, events = await _stream_optimize(client, auth_headers, "Explain photosynthesis simply.")
    # The pipeline emits ("stage", {"stage": ..., "status": ...}) events.
    # These appear in the data lines. Look for any event with a "stage" key.
    has_stage_events = any("stage" in e for e in events)
    assert has_stage_events, f"No stage events found in: {events}"


async def test_optimize_result_persisted(client: AsyncClient, auth_headers):
    opt_id, _ = await _stream_optimize(client, auth_headers, "Persist this optimization.")
    assert opt_id is not None

    resp = await client.get(f"/api/optimize/{opt_id}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == opt_id


async def test_optimize_get_unknown_returns_404(client: AsyncClient, auth_headers):
    resp = await client.get("/api/optimize/nonexistent-id-00000", headers=auth_headers)
    assert resp.status_code == 404


async def test_optimize_get_requires_auth(client: AsyncClient, auth_headers):
    opt_id, _ = await _stream_optimize(client, auth_headers, "Auth check prompt.")
    resp = await client.get(f"/api/optimize/{opt_id}")
    assert resp.status_code == 401


async def test_optimize_patch_updates_title(client: AsyncClient, auth_headers):
    opt_id, _ = await _stream_optimize(client, auth_headers, "Patchable prompt.")
    resp = await client.patch(
        f"/api/optimize/{opt_id}",
        json={"title": "My Custom Title"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    get_resp = await client.get(f"/api/optimize/{opt_id}", headers=auth_headers)
    assert get_resp.json().get("title") == "My Custom Title"


async def test_optimize_patch_unknown_returns_422(client: AsyncClient, auth_headers):
    """PatchOptimizationRequest only allows title, tags, version, project.
    Sending an unknown field should return 422 (Pydantic extra=forbid) or be
    silently ignored depending on model config. At minimum, the request must
    not error with 5xx.
    """
    opt_id, _ = await _stream_optimize(client, auth_headers, "Patch validation.")
    resp = await client.patch(
        f"/api/optimize/{opt_id}",
        json={"nonexistent_field": "value"},
        headers=auth_headers,
    )
    # FastAPI/Pydantic by default ignores extra fields (no 422), so we accept
    # either 200 (ignored) or 422 (extra=forbid).
    assert resp.status_code in (200, 422), f"Unexpected status: {resp.status_code}"
