"""Tests for taxonomy snapshot CRUD and retention policy."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import TaxonomySnapshot
from app.services.taxonomy.snapshot import (
    create_snapshot,
    get_latest_snapshot,
    get_snapshot_history,
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


@pytest.mark.asyncio
async def test_get_snapshot_history(db):
    """get_snapshot_history returns up to `limit` snapshots, newest first."""
    for i in range(5):
        snap = TaxonomySnapshot(
            trigger="warm_path",
            q_system=0.8 + i * 0.01,
            q_coherence=0.7, q_separation=0.8, q_coverage=0.9, q_dbcv=0.0,
        )
        db.add(snap)
    await db.commit()

    history = await get_snapshot_history(db, limit=3)
    assert len(history) == 3
    # Newest first
    assert history[0].q_system >= history[1].q_system


@pytest.mark.asyncio
async def test_get_snapshot_history_empty(db):
    """Empty DB returns empty list."""
    history = await get_snapshot_history(db)
    assert history == []


@pytest.mark.asyncio
async def test_prune_daily_best_retention(db):
    """Snapshots between 1-30 days old: keep only the best per calendar day."""
    now = datetime.now(timezone.utc)

    # Create 3 snapshots for the same day (5 days ago).
    # Pin to noon UTC so hour offsets never cross a calendar-day boundary.
    base = (now - timedelta(days=5)).replace(hour=12, minute=0, second=0, microsecond=0)
    for i, q in enumerate([0.70, 0.85, 0.75]):
        snap = TaxonomySnapshot(
            trigger="warm_path",
            q_system=q,
            q_coherence=0.7, q_separation=0.8, q_coverage=0.9, q_dbcv=0.0,
        )
        snap.created_at = base + timedelta(hours=i)
        db.add(snap)
    await db.commit()

    pruned = await prune_snapshots(db)
    assert pruned == 2  # keep best (0.85), prune the other two

    remaining = (await db.execute(select(TaxonomySnapshot))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].q_system == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_prune_weekly_best_retention(db):
    """Snapshots older than 30 days: keep only the best per ISO week."""
    now = datetime.now(timezone.utc)

    # Create 3 snapshots guaranteed to be in the same ISO week (45 days ago).
    # Use hour offsets instead of day offsets so they always share the same
    # calendar date and therefore the same ISO week regardless of when the
    # test runs.
    base = now - timedelta(days=45)
    for i, q in enumerate([0.60, 0.80, 0.70]):
        snap = TaxonomySnapshot(
            trigger="warm_path",
            q_system=q,
            q_coherence=0.7, q_separation=0.8, q_coverage=0.9, q_dbcv=0.0,
        )
        # All within the same day (1 hour apart)
        snap.created_at = base + timedelta(hours=i)
        db.add(snap)
    await db.commit()

    pruned = await prune_snapshots(db)
    assert pruned == 2  # keep best (0.80), prune the other two

    remaining = (await db.execute(select(TaxonomySnapshot))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].q_system == pytest.approx(0.80)
