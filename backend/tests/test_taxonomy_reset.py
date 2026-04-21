"""Tests for ``POST /api/taxonomy/reset`` — admin recovery endpoint.

Used to clean up structural debris left over after a failed bulk delete
(archived clusters with ``member_count=0``, orphan project nodes, stale
signal caches). Forces immediate synchronous reconciliation, bypassing
the 30-second warm-path debounce.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import PromptCluster


@pytest.mark.asyncio
async def test_reset_force_prunes_zero_member_archived_regardless_of_age(
    app_client, db_session,
):
    """Freshly-archived 0-member clusters (``archived_at`` just now) are
    under the 24h grace floor that normal warm Phase 0 respects — reset
    must override that floor so bulk-delete recovery is immediate.
    """
    fresh_archived = str(uuid.uuid4())
    db_session.add(PromptCluster(
        id=fresh_archived, label="debris", state="archived",
        member_count=0, centroid_embedding=b"\x00" * 384,
    ))
    await db_session.commit()

    # Stub the engine so we don't actually run warm_path during this test —
    # the endpoint's contract is: (1) force-prune 0-member archived,
    # (2) delegate to warm_path for the rest. We verify (1) and the call
    # to (2); full warm-path behavior has its own coverage elsewhere.
    mock_engine = AsyncMock()
    mock_engine.run_warm_path.return_value = None
    with patch("app.routers.clusters._get_engine", return_value=mock_engine):
        resp = await app_client.post("/api/taxonomy/reset")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert data["archived_pruned"] == 1

    # The freshly-archived row must be gone despite <24h age.
    remaining = (await db_session.execute(
        select(PromptCluster).where(PromptCluster.id == fresh_archived)
    )).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_reset_invokes_warm_path_for_full_reconciliation(
    app_client, db_session,
):
    """Reset delegates the full reconciliation sweep (member_count,
    embedding index rebuild, signal-loader refresh) to warm_path rather
    than re-implementing it — that logic is already tested there.
    """
    mock_engine = AsyncMock()
    mock_engine.run_warm_path.return_value = None
    with patch("app.routers.clusters._get_engine", return_value=mock_engine):
        resp = await app_client.post("/api/taxonomy/reset")

    assert resp.status_code == 200
    mock_engine.run_warm_path.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_preserves_active_clusters(app_client, db_session):
    """Reset only prunes archived 0-member nodes — active clusters are
    untouched even if they happen to have 0 members (transient state
    during warm reconciliation)."""
    active_id = str(uuid.uuid4())
    db_session.add(PromptCluster(
        id=active_id, label="live", state="active", member_count=0,
        centroid_embedding=b"\x00" * 384,
    ))
    await db_session.commit()

    mock_engine = AsyncMock()
    mock_engine.run_warm_path.return_value = None
    with patch("app.routers.clusters._get_engine", return_value=mock_engine):
        resp = await app_client.post("/api/taxonomy/reset")

    assert resp.status_code == 200
    # Active cluster must survive.
    kept = (await db_session.execute(
        select(PromptCluster).where(PromptCluster.id == active_id)
    )).scalar_one_or_none()
    assert kept is not None


@pytest.mark.asyncio
async def test_reset_handles_engine_failure(app_client, db_session):
    """If warm_path raises, reset surfaces the error as 500 (it's an
    admin endpoint — no silent swallowing)."""
    mock_engine = AsyncMock()
    mock_engine.run_warm_path.side_effect = RuntimeError("warm path boom")
    with patch("app.routers.clusters._get_engine", return_value=mock_engine):
        resp = await app_client.post("/api/taxonomy/reset")

    assert resp.status_code == 500
