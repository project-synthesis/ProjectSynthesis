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
from sqlalchemy import select

from app.models import AuditLog, Optimization, ValidationSuite
from app.services.event_bus import event_bus
from tests.conftest import drain_events_nonblocking as _drain_events_nonblocking


@pytest.fixture(autouse=True)
async def _enable_sqlite_fk_cascade(enable_sqlite_foreign_keys):
    """FK cascade requires the per-connection PRAGMA. Delegates to the
    shared ``enable_sqlite_foreign_keys`` fixture in ``conftest.py`` —
    single source of truth replacing the previous inline ``PRAGMA``."""
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
    from app.dependencies.rate_limit import reset_rate_limit_storage

    reset_rate_limit_storage()
    yield
    reset_rate_limit_storage()


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


@pytest.mark.asyncio
async def test_bulk_delete_emits_n_optimization_deleted_events(
    app_client, db_session,
):
    """One optimization_deleted event per deleted row."""
    ids = [await _seed_opt(db_session) for _ in range(3)]

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    event_bus._subscribers.add(queue)
    try:
        resp = await app_client.post(
            "/api/optimizations/delete", json={"ids": ids}
        )
        assert resp.status_code == 200
        events = _drain_events_nonblocking(queue)
    finally:
        event_bus._subscribers.discard(queue)

    deleted_events = [e for e in events if e.get("event") == "optimization_deleted"]
    assert len(deleted_events) == 3
    for e in deleted_events:
        assert e["data"]["reason"] == "user_request"
        assert e["data"]["id"] in ids


@pytest.mark.asyncio
async def test_bulk_delete_emits_single_taxonomy_changed_event(
    app_client, db_session,
):
    """Exactly one taxonomy_changed event per bulk call, regardless of N."""
    ids = [await _seed_opt(db_session) for _ in range(5)]

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    event_bus._subscribers.add(queue)
    try:
        resp = await app_client.post(
            "/api/optimizations/delete", json={"ids": ids}
        )
        assert resp.status_code == 200
        events = _drain_events_nonblocking(queue)
    finally:
        event_bus._subscribers.discard(queue)

    taxonomy_events = [e for e in events if e.get("event") == "taxonomy_changed"]
    assert len(taxonomy_events) == 1
    assert taxonomy_events[0]["data"].get("trigger") == "bulk_delete"


# ===========================================================================
# v0.4.37 Cycle 1 — provenance guard on POST /api/optimizations/delete
# ===========================================================================


async def _seed_referencing_suite_v37(db_session, opt_id: str, label: str) -> str:
    suite_id = str(uuid.uuid4())
    db_session.add(ValidationSuite(
        id=suite_id,
        source_run_id=None,
        label=label,
        prompts_snapshot=[{
            "raw_prompt": "p",
            "intent_label": None,
            "original_optimization_id": opt_id,
        }],
        baseline_scores={"mean_overall": 7.0, "per_prompt": []},
        tolerance_abs=0.5,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    await db_session.commit()
    return suite_id


@pytest.mark.asyncio
async def test_bulk_delete_referenced_returns_409_and_deletes_nothing(
    app_client, db_session,
):
    """AC-5: a bulk batch containing ONE referenced id 409s atomically —
    the unreferenced sibling survives too (guard raises pre-DELETE)."""
    referenced = str(uuid.uuid4())
    sibling = str(uuid.uuid4())
    for oid in (referenced, sibling):
        db_session.add(Optimization(id=oid, raw_prompt="raw", status="completed"))
    await db_session.commit()
    suite_id = await _seed_referencing_suite_v37(db_session, referenced, "bulk-guard")

    resp = await app_client.post(
        "/api/optimizations/delete", json={"ids": [referenced, sibling]},
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "suite_referenced"
    assert body["blocked"] == [{
        "optimization_id": referenced,
        "suite_id": suite_id,
        "suite_label": "bulk-guard",
    }]
    assert body["hint"] == "retry with force=true or retire the suite"
    for oid in (referenced, sibling):
        row = (await db_session.execute(
            select(Optimization).where(Optimization.id == oid)
        )).scalar_one_or_none()
        assert row is not None, f"{oid} must survive the blocked bulk delete"


@pytest.mark.asyncio
async def test_bulk_delete_force_body_field_overrides_guard(
    app_client, db_session,
):
    """AC-7: force=true in the bulk body deletes the referenced row.

    RED-gate teeth: pre-GREEN the 200/deleted=1 half passes trivially —
    ``BulkDeleteRequest`` has no extra-forbid config, so the unknown
    ``force`` field is silently dropped against the guard-less endpoint.
    The audit-row assertion is what makes this test genuinely fail in RED
    (no ``optimizations_deleted`` audit write exists anywhere pre-GREEN).
    """
    referenced = str(uuid.uuid4())
    db_session.add(Optimization(id=referenced, raw_prompt="raw", status="completed"))
    await db_session.commit()
    suite_id = await _seed_referencing_suite_v37(db_session, referenced, "bulk-forced")

    resp = await app_client.post(
        "/api/optimizations/delete",
        json={"ids": [referenced], "force": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 1
    audit_rows = list((await db_session.execute(
        select(AuditLog).where(AuditLog.action == "optimizations_deleted")
    )).scalars().all())
    assert len(audit_rows) == 1
    detail = audit_rows[0].detail
    assert detail["forced"] is True
    assert detail["source"] == "api_bulk"
    assert detail["suite_refs"] == [{
        "optimization_id": referenced,
        "suite_id": suite_id,
        "suite_label": "bulk-forced",
    }]
