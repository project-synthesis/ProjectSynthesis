"""Taxonomy snapshot CRUD and retention policy.

Snapshots record system-wide quality metrics after each warm/cold path
cycle, providing a historical audit trail and enabling quality regression
detection.

Reference: Spec Section 5.2
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TaxonomySnapshot

logger = logging.getLogger(__name__)


async def create_snapshot(
    db: AsyncSession,
    *,
    trigger: str,
    q_system: float | None,
    q_coherence: float,
    q_separation: float,
    q_coverage: float,
    q_dbcv: float = 0.0,
    q_health: float | None = None,
    operations: list[dict[str, Any]] | None = None,
    nodes_created: int = 0,
    nodes_retired: int = 0,
    nodes_merged: int = 0,
    nodes_split: int = 0,
) -> TaxonomySnapshot:
    """Create and persist a TaxonomySnapshot.

    Args:
        db: Async database session.
        trigger: What triggered this snapshot — 'warm_path', 'cold_path', or 'manual'.
        q_system: Composite system quality score.
        q_coherence: Mean intra-cluster coherence.
        q_separation: Mean inter-cluster separation.
        q_coverage: Fraction of optimizations covered by active nodes.
        q_dbcv: DBCV validity score (0.0 when < 5 active nodes).
        q_health: Member-weighted composite health score (None when unavailable).
        operations: List of tree-mutation operation dicts (serialized as JSON).
        nodes_created: Count of nodes created in this cycle.
        nodes_retired: Count of nodes retired in this cycle.
        nodes_merged: Count of nodes merged in this cycle.
        nodes_split: Count of nodes split in this cycle.

    Returns:
        Persisted TaxonomySnapshot with id populated.

    Note:
        The ``tree_state`` column on the model exists for future tree recovery
        but is intentionally not populated — recovery uses the live
        PromptCluster table instead.  The parameter was removed to prevent
        dead-data accumulation (~50KB per snapshot).
    """
    # A5: TaxonomySnapshot.q_system is NOT NULL at the DB layer; coerce a
    # None (insufficient-clusters) Q to 0.0 for persistence. Live endpoints
    # recompute Q from current cluster state and surface None to consumers.
    snap = TaxonomySnapshot(
        trigger=trigger,
        q_system=0.0 if q_system is None else q_system,
        q_coherence=q_coherence,
        q_separation=q_separation,
        q_coverage=q_coverage,
        q_dbcv=q_dbcv,
        q_health=q_health,
        operations=json.dumps(operations if operations is not None else []),
        nodes_created=nodes_created,
        nodes_retired=nodes_retired,
        nodes_merged=nodes_merged,
        nodes_split=nodes_split,
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return snap


async def get_latest_snapshot(db: AsyncSession) -> TaxonomySnapshot | None:
    """Return the most recent TaxonomySnapshot, or None if none exist."""
    result = await db.execute(
        select(TaxonomySnapshot).order_by(TaxonomySnapshot.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_snapshot_history(
    db: AsyncSession,
    limit: int = 30,
) -> list[TaxonomySnapshot]:
    """Return up to `limit` recent snapshots ordered newest-first.

    Used for sparkline data in the UI.

    Args:
        db: Async database session.
        limit: Maximum number of snapshots to return.

    Returns:
        List of TaxonomySnapshot objects, newest first.
    """
    result = await db.execute(
        select(TaxonomySnapshot)
        .order_by(TaxonomySnapshot.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def prune_snapshots(db: AsyncSession) -> int:
    """Apply retention policy and delete excess snapshots.

    Retention tiers:
    - 0–24h: keep all snapshots.
    - 1–30 days: keep the best q_system per calendar day.
    - 30+ days: keep the best q_system per ISO week.

    Returns:
        Number of snapshots deleted.
    """
    try:
        now = datetime.now(timezone.utc)
        cutoff_24h = now - timedelta(hours=24)
        cutoff_30d = now - timedelta(days=30)

        # Fetch all snapshots outside the 24h keep-all window.
        result = await db.execute(
            select(TaxonomySnapshot).where(TaxonomySnapshot.created_at < cutoff_24h)
        )
        old_snapshots = list(result.scalars().all())

        if not old_snapshots:
            return 0

        # Partition into 1–30d and 30+ buckets.
        mid_range: list[TaxonomySnapshot] = []
        long_range: list[TaxonomySnapshot] = []

        for snap in old_snapshots:
            snap_dt = snap.created_at
            # Ensure timezone-aware for comparison.
            if snap_dt.tzinfo is None:
                snap_dt = snap_dt.replace(tzinfo=timezone.utc)
            if snap_dt >= cutoff_30d:
                mid_range.append(snap)
            else:
                long_range.append(snap)

        ids_to_delete: set[str] = set()

        # 1–30d: keep best per calendar day (UTC date string "YYYY-MM-DD").
        ids_to_delete.update(_keep_best_per_group(mid_range, _day_key))

        # 30+ days: keep best per ISO week ("YYYY-WNN").
        ids_to_delete.update(_keep_best_per_group(long_range, _week_key))

        if not ids_to_delete:
            return 0

        await db.execute(
            delete(TaxonomySnapshot).where(TaxonomySnapshot.id.in_(list(ids_to_delete)))
        )
        await db.commit()
        logger.info("Pruned %d snapshots", len(ids_to_delete))
        return len(ids_to_delete)
    except Exception as exc:
        logger.error("Snapshot pruning failed: %s", exc, exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _day_key(snap: TaxonomySnapshot) -> str:
    """Return 'YYYY-MM-DD' UTC date string for grouping."""
    dt = snap.created_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _week_key(snap: TaxonomySnapshot) -> str:
    """Return 'YYYY-WNN' ISO week string for grouping."""
    dt = snap.created_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _keep_best_per_group(
    snapshots: list[TaxonomySnapshot],
    key_fn: Callable[[TaxonomySnapshot], str],
) -> set[str]:
    """Return IDs of snapshots to delete, keeping highest q_system per group.

    Args:
        snapshots: Snapshots to process.
        key_fn: Function(TaxonomySnapshot) -> str grouping key.

    Returns:
        Set of snapshot IDs that should be deleted.
    """
    groups: dict[str, list[TaxonomySnapshot]] = {}
    for snap in snapshots:
        k = key_fn(snap)
        groups.setdefault(k, []).append(snap)

    to_delete: set[str] = set()
    for group in groups.values():
        if len(group) <= 1:
            continue
        # Keep the one with the highest q_system; ties: keep most recent.
        best = max(group, key=lambda s: (s.q_system, s.created_at))
        for snap in group:
            if snap.id != best.id:
                to_delete.add(snap.id)

    return to_delete
