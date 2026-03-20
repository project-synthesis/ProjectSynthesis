"""Tests for taxonomy snapshot CRUD and retention policy."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import TaxonomySnapshot
from app.services.taxonomy.snapshot import (
    create_snapshot,
    get_latest_snapshot,
    prune_snapshots,
)


@pytest.mark.asyncio
async def test_create_snapshot(db):
    snap = await create_snapshot(
        db,
        trigger="warm_path",
        q_system=0.85,
        q_coherence=0.82,
        q_separation=0.88,
        q_coverage=0.95,
        q_dbcv=0.0,
        operations=[{"type": "emerge", "node_id": "abc"}],
        nodes_created=1,
    )
    assert snap.id is not None
    assert snap.trigger == "warm_path"
    assert snap.q_system == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_get_latest_snapshot(db):
    await create_snapshot(db, trigger="warm_path", q_system=0.80, q_coherence=0.7,
                          q_separation=0.8, q_coverage=0.9, q_dbcv=0.0)
    await create_snapshot(db, trigger="warm_path", q_system=0.85, q_coherence=0.75,
                          q_separation=0.85, q_coverage=0.92, q_dbcv=0.0)

    latest = await get_latest_snapshot(db)
    assert latest is not None
    assert latest.q_system == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_get_latest_snapshot_empty(db):
    assert await get_latest_snapshot(db) is None


@pytest.mark.asyncio
async def test_prune_keeps_recent_snapshots(db):
    """Snapshots from the last 24h should all be kept."""
    now = datetime.now(timezone.utc)
    for i in range(5):
        snap = TaxonomySnapshot(
            trigger="warm_path",
            q_system=0.8 + i * 0.01,
            q_coherence=0.7, q_separation=0.8, q_coverage=0.9, q_dbcv=0.0,
        )
        snap.created_at = now - timedelta(hours=i)
        db.add(snap)
    await db.commit()

    pruned = await prune_snapshots(db)
    assert pruned == 0  # all within 24h, nothing pruned
