"""Tests for the bulk delete endpoint + single-endpoint regression.

Drives the POST /api/optimizations/delete endpoint into existence via TDD.
Also regression-tests that the existing single-item endpoint's response
envelope gained the new ``requested: int`` field (always 1 for single).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from app.models import Optimization
from app.services.event_bus import event_bus


@pytest.fixture(autouse=True)
async def _enable_sqlite_fk_cascade(db_session):
    """FK cascade requires the per-connection PRAGMA. Mirrors the
    existing ``test_optimization_delete_router._enable_sqlite_fk_cascade``."""
    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    yield


@pytest.fixture(autouse=True)
def _reset_event_bus_shutdown():
    """Defensive: lifespan tests elsewhere in the suite may leave
    event_bus._shutting_down=True, which would turn publish() into a no-op."""
    event_bus._shutting_down = False
    yield
    event_bus._shutting_down = False


@pytest.fixture(autouse=True)
def _reset_rate_limit_storage():
    """Reset the in-memory rate limit storage before each test to ensure
    isolated rate limit state. The storage is a process-level singleton,
    so prior tests can consume quota from the moving window."""
    from app.dependencies.rate_limit import _storage

    _storage.reset()
    yield
    _storage.reset()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_opt(db_session, *, cluster_id: str | None = None) -> str:
    opt_id = str(uuid.uuid4())
    db_session.add(
        Optimization(
            id=opt_id,
            raw_prompt="test prompt",
            status="completed",
            created_at=_utcnow_naive(),
            cluster_id=cluster_id,
        )
    )
    await db_session.commit()
    return opt_id


@pytest.mark.asyncio
async def test_single_delete_response_includes_requested(app_client, db_session):
    """The existing DELETE /api/optimizations/{id} envelope must include
    ``requested: 1`` for envelope parity with the new bulk endpoint."""
    opt_id = await _seed_opt(db_session)

    resp = await app_client.delete(f"/api/optimizations/{opt_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert body["requested"] == 1
    assert "affected_cluster_ids" in body
    assert "affected_project_ids" in body


@pytest.mark.asyncio
async def test_bulk_delete_endpoint_ok(app_client, db_session):
    """POST /api/optimizations/delete with 3 valid ids returns
    deleted=3, requested=3, and both affected lists are JSON lists."""
    ids = [await _seed_opt(db_session) for _ in range(3)]

    resp = await app_client.post(
        "/api/optimizations/delete",
        json={"ids": ids, "reason": "user_request"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 3
    assert body["requested"] == 3
    assert isinstance(body["affected_cluster_ids"], list)
    assert isinstance(body["affected_project_ids"], list)

    # All 3 rows must be gone
    remaining = (
        await db_session.execute(
            select(Optimization.id).where(Optimization.id.in_(ids))
        )
    ).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_bulk_delete_partial(app_client, db_session):
    """Mix of real + fake ids → deleted < requested, both envelopes present."""
    real = [await _seed_opt(db_session) for _ in range(2)]
    fake = [str(uuid.uuid4()) for _ in range(3)]

    resp = await app_client.post(
        "/api/optimizations/delete",
        json={"ids": real + fake},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 2
    assert body["requested"] == 5


@pytest.mark.asyncio
async def test_bulk_delete_empty_ids_422(app_client):
    """min_length=1 on ids — empty list rejected at the validation layer."""
    resp = await app_client.post("/api/optimizations/delete", json={"ids": []})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bulk_delete_oversized_ids_422(app_client):
    """max_length=100 on ids — 101 ids rejected at the validation layer."""
    oversized = [str(uuid.uuid4()) for _ in range(101)]
    resp = await app_client.post(
        "/api/optimizations/delete", json={"ids": oversized}
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bulk_delete_rate_limit(app_client, db_session):
    """10/minute limit — 11th call in the same window returns 429.

    The RateLimit dependency uses the limits library's in-memory moving
    window. Firing 11 valid requests in quick succession must trip the
    limit on request 11. Empty ids_per_call keeps the DB churn zero.
    """
    opt_id = await _seed_opt(db_session)
    statuses = []
    for _ in range(11):
        resp = await app_client.post(
            "/api/optimizations/delete",
            json={"ids": [opt_id]},
        )
        statuses.append(resp.status_code)

    # First call deletes; next 9 are 200 (deleted=0 because id gone);
    # 11th is 429 per the 10/minute limit.
    assert statuses[:10].count(200) == 10
    assert statuses[10] == 429
