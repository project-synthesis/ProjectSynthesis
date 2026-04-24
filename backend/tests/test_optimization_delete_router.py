"""Tests for ``DELETE /api/optimizations/{id}`` — REST delete endpoint.

The service method ``OptimizationService.delete_optimizations`` has existed
since commit ``a3135c5a`` (the delete-cascade plumbing). The REST endpoint
was never wired — bug #3 from the 2026-04-21 MCP audit. This test drives
the endpoint into existence via TDD.

Contract:
- 200 on success with ``{deleted, affected_cluster_ids, affected_project_ids}``
- 404 when the id doesn't exist (distinguishes user typo from silent no-op)
- DB row is gone + ``optimization_deleted`` event published (cascade etc.
  are already covered in ``test_optimization_service_delete.py``).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from app.models import Optimization, PromptCluster
from app.services.event_bus import event_bus


@pytest.fixture(autouse=True)
async def _enable_sqlite_fk_cascade(db_session):
    """The service relies on DB-level ``ondelete="CASCADE"``; SQLite needs
    the per-connection PRAGMA to enforce FKs, which the in-memory test DB
    doesn't apply by default. Mirrors ``test_optimization_service_delete``."""
    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    yield


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
async def test_delete_existing_opt_returns_200_and_removes_row(
    app_client, db_session,
):
    """Happy path: delete by id, 200 with envelope, row is gone."""
    opt_id = await _seed_opt(db_session)

    resp = await app_client.delete(f"/api/optimizations/{opt_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    # Envelope fields present even when empty — predictable for clients.
    assert "affected_cluster_ids" in body
    assert "affected_project_ids" in body

    # Row actually removed.
    remaining = (
        await db_session.execute(
            select(Optimization).where(Optimization.id == opt_id)
        )
    ).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_unknown_id_returns_404(app_client, db_session):
    """Unknown id → 404 (not a silent 200). Lets clients distinguish typos
    from successful no-ops. The service itself returns ``deleted=0`` silently
    for unknown ids; the router is the layer that translates to 404."""
    bogus = str(uuid.uuid4())
    resp = await app_client.delete(f"/api/optimizations/{bogus}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_returns_affected_cluster_id(app_client, db_session):
    """Clients use ``affected_cluster_ids`` to refresh cluster-scoped UI
    after a delete. The router must surface the service's snapshot."""
    cluster = PromptCluster(
        label="delete-target",
        state="active",
        member_count=1,
    )
    db_session.add(cluster)
    await db_session.commit()
    opt_id = await _seed_opt(db_session, cluster_id=cluster.id)

    resp = await app_client.delete(f"/api/optimizations/{opt_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 1
    assert cluster.id in body["affected_cluster_ids"]


@pytest.mark.asyncio
async def test_delete_publishes_optimization_deleted_event(
    app_client, db_session,
):
    """SSE subscribers (HistoryPanel etc.) expect an event per deleted row
    with payload ``{id, cluster_id, project_id, reason}``. The service
    already emits this — the router must not swallow it or change the type.

    Event-bus test pattern mirrors ``test_optimization_service_delete``:
    hook a queue directly onto ``_subscribers`` (the public ``subscribe()``
    generator only registers on the first ``__anext__()``, which races
    with the publish that fires synchronously from inside the DELETE).
    """
    opt_id = await _seed_opt(db_session)

    # Defend against a prior test that may have flipped the process-level
    # bus into shutdown mode (publish is a no-op while shutting down).
    # Mirrors the guard in test_optimization_service_delete.
    event_bus._shutting_down = False  # type: ignore[attr-defined]

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    event_bus._subscribers.add(queue)
    try:
        resp = await app_client.delete(f"/api/optimizations/{opt_id}")
        assert resp.status_code == 200

        delete_events: list[dict] = []
        while True:
            try:
                evt = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if evt.get("event") == "optimization_deleted":
                delete_events.append(evt)
    finally:
        event_bus._subscribers.discard(queue)

    assert len(delete_events) == 1
    data = delete_events[0]["data"]
    assert data["id"] == opt_id
    assert data["reason"] == "user_request"
