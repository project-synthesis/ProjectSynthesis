"""Tests for POST /api/optimizations/exists — v0.4.37 §3.5 liveness endpoint.

POST-with-body deliberately (100 ids ≈ 3.6 KB would risk proxy URL limits
as a GET). Read-only; backs the SuiteDetailView tombstone + OPEN IN
HISTORY affordances, called once per suite selection.
"""
from __future__ import annotations

import uuid

import pytest

from app.models import Optimization


async def _seed(db_session, *, trace_id: str | None = None) -> str:
    opt_id = str(uuid.uuid4())
    db_session.add(Optimization(
        id=opt_id, raw_prompt="raw", status="completed", trace_id=trace_id,
    ))
    await db_session.commit()
    return opt_id


@pytest.mark.asyncio
async def test_exists_returns_alive_subset_and_trace_ids(app_client, db_session):
    """AC-10: alive subset returned; dead ids omitted; trace_ids map alive
    id -> trace_id so the frontend can reuse the history open path."""
    alive_a = await _seed(db_session, trace_id="tr-exists-a")
    alive_b = await _seed(db_session, trace_id=None)
    ghost = str(uuid.uuid4())

    resp = await app_client.post(
        "/api/optimizations/exists", json={"ids": [alive_a, alive_b, ghost]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["alive"]) == sorted([alive_a, alive_b])
    assert body["trace_ids"] == {alive_a: "tr-exists-a"}


@pytest.mark.asyncio
async def test_exists_rejects_more_than_100_ids(app_client):
    """AC-10: 422 above 100 ids (Pydantic max_length boundary)."""
    resp = await app_client.post(
        "/api/optimizations/exists",
        json={"ids": [str(uuid.uuid4()) for _ in range(101)]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_exists_rejects_empty_list(app_client):
    """min_length=1 — an empty batch is a caller bug, not a valid query."""
    resp = await app_client.post("/api/optimizations/exists", json={"ids": []})
    assert resp.status_code == 422
