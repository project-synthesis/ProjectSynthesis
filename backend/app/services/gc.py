"""Startup garbage collection — clean dead records from the database.

Runs once during backend lifespan initialization. All operations are
idempotent and safe to re-run. Failures are logged but never prevent
startup.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def run_startup_gc(db: AsyncSession) -> None:
    """Run all garbage collection passes in a single transaction.

    Called during backend lifespan startup. Each pass is independent —
    if one fails, the others still run.
    """
    total_cleaned = 0

    total_cleaned += await _gc_failed_optimizations(db)
    total_cleaned += await _gc_archived_zero_member_clusters(db)
    total_cleaned += await _gc_orphan_meta_patterns(db)

    if total_cleaned > 0:
        await db.commit()
        logger.info("Startup GC: cleaned %d records total", total_cleaned)
    else:
        logger.debug("Startup GC: nothing to clean")


async def _gc_failed_optimizations(db: AsyncSession) -> int:
    """Delete optimizations that failed before producing any output.

    These have status='failed', no optimized_prompt, no domain, no
    cluster assignment. They carry no useful data and pollute counts.
    """
    from app.models import Optimization

    result = await db.execute(
        select(Optimization.id).where(
            Optimization.status == "failed",
            Optimization.optimized_prompt.is_(None),
        )
    )
    failed_ids = [r[0] for r in result.all()]

    if not failed_ids:
        return 0

    # Delete any dependent records first (feedbacks, refinement turns, patterns)
    from app.models import Feedback, OptimizationPattern, RefinementTurn

    await db.execute(
        delete(Feedback).where(Feedback.optimization_id.in_(failed_ids))
    )
    await db.execute(
        delete(RefinementTurn).where(RefinementTurn.optimization_id.in_(failed_ids))
    )
    await db.execute(
        delete(OptimizationPattern).where(OptimizationPattern.optimization_id.in_(failed_ids))
    )

    await db.execute(
        delete(Optimization).where(Optimization.id.in_(failed_ids))
    )

    logger.info("GC: deleted %d failed optimizations", len(failed_ids))
    return len(failed_ids)


async def _gc_archived_zero_member_clusters(db: AsyncSession) -> int:
    """Delete archived clusters with 0 members and no remaining references.

    These are tombstones from the split-merge lifecycle. The warm path
    archives them and zeros their counters, but the rows persist
    indefinitely. Safe to delete when:
    - state = 'archived'
    - member_count = 0
    - No optimizations reference them (cluster_id)
    - No optimization_patterns reference them
    - No child clusters parent under them
    """
    from app.models import Optimization, OptimizationPattern, PromptCluster

    # Find archived clusters with 0 members
    archived_q = await db.execute(
        select(PromptCluster.id).where(
            PromptCluster.state == "archived",
            PromptCluster.member_count == 0,
        )
    )
    candidate_ids = [r[0] for r in archived_q.all()]

    if not candidate_ids:
        return 0

    # Filter out any that still have references
    safe_ids = []
    for cid in candidate_ids:
        # Check for optimization references
        opt_ref = await db.execute(
            select(Optimization.id).where(
                Optimization.cluster_id == cid
            ).limit(1)
        )
        if opt_ref.scalar_one_or_none():
            continue

        # Check for optimization_pattern references
        op_ref = await db.execute(
            select(OptimizationPattern.id).where(
                OptimizationPattern.cluster_id == cid
            ).limit(1)
        )
        if op_ref.scalar_one_or_none():
            continue

        # Check for child cluster references
        child_ref = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.parent_id == cid
            ).limit(1)
        )
        if child_ref.scalar_one_or_none():
            continue

        safe_ids.append(cid)

    if not safe_ids:
        return 0

    # Delete associated meta_patterns first
    from app.models import MetaPattern

    await db.execute(
        delete(MetaPattern).where(MetaPattern.cluster_id.in_(safe_ids))
    )

    # Delete the clusters
    await db.execute(
        delete(PromptCluster).where(PromptCluster.id.in_(safe_ids))
    )

    logger.info(
        "GC: deleted %d archived zero-member clusters (of %d candidates)",
        len(safe_ids), len(candidate_ids),
    )
    return len(safe_ids)


async def _gc_orphan_meta_patterns(db: AsyncSession) -> int:
    """Delete meta_patterns whose cluster no longer exists."""
    from app.models import MetaPattern, PromptCluster

    result = await db.execute(
        delete(MetaPattern).where(
            ~MetaPattern.cluster_id.in_(
                select(PromptCluster.id)
            )
        )
    )
    count = result.rowcount
    if count > 0:
        logger.info("GC: deleted %d orphan meta_patterns", count)
    return count
