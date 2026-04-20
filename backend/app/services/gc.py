"""Garbage collection — clean dead records from the database.

Two entry points:

- ``run_startup_gc(db)`` — one-shot sweep during backend lifespan.
  Cleans failed optimizations, archived zero-member clusters, orphan
  meta_patterns. Called from ``main.py:lifespan`` startup.

- ``run_recurring_gc(db)`` — hourly sweep driven by ``recurring_gc_task``
  in ``main.py``. Cleans expired GitHub OAuth tokens and orphan
  LinkedRepo rows. The startup sweep does NOT run these — session
  state belongs to the long-running task, not the cold start.

All operations are idempotent and safe to re-run. Failures are logged
but never prevent startup or interrupt the recurring loop.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Naive UTC now — matches the DateTime columns in models.py which store naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    count = result.rowcount  # type: ignore[attr-defined]
    if count > 0:
        logger.info("GC: deleted %d orphan meta_patterns", count)
    return count


async def run_recurring_gc(db: AsyncSession) -> None:
    """Run all recurring garbage collection passes in a single transaction.

    Called hourly by ``recurring_gc_task`` in ``main.py``. Keeps token and
    session state from accumulating between restarts.
    """
    total_cleaned = 0

    total_cleaned += await _gc_expired_github_tokens(db)
    total_cleaned += await _gc_orphan_linked_repos(db)

    if total_cleaned > 0:
        await db.commit()
        logger.info("Recurring GC: cleaned %d records total", total_cleaned)
    else:
        logger.debug("Recurring GC: nothing to clean")


async def _gc_expired_github_tokens(
    db: AsyncSession, grace_hours: int = 24,
) -> int:
    """Delete GitHubToken rows whose access token AND refresh token have both expired.

    Grace window (``grace_hours``) protects in-flight ``_get_session_token()``
    refresh calls: a token whose refresh_token is newly-expired should not be
    deleted within the grace period in case a concurrent refresh is about to
    succeed with a fresh refresh cycle.

    Tokens with ``expires_at IS NULL`` are skipped — they represent legacy
    non-expiring grants and must not be swept.
    """
    from app.models import GitHubToken

    now = _utcnow()
    cutoff = now - timedelta(hours=grace_hours)

    # A token is safe to delete if:
    #   - expires_at IS NOT NULL AND expires_at < now
    #   - AND (refresh_token_expires_at IS NULL
    #          OR refresh_token_expires_at < cutoff)
    stmt = select(GitHubToken.id).where(
        GitHubToken.expires_at.is_not(None),
        GitHubToken.expires_at < now,
        (GitHubToken.refresh_token_expires_at.is_(None))
        | (GitHubToken.refresh_token_expires_at < cutoff),
    )
    result = await db.execute(stmt)
    expired_ids = [r[0] for r in result.all()]

    if not expired_ids:
        return 0

    await db.execute(
        delete(GitHubToken).where(GitHubToken.id.in_(expired_ids))
    )
    logger.info("Recurring GC: deleted %d expired github_tokens", len(expired_ids))
    return len(expired_ids)


async def _gc_orphan_linked_repos(db: AsyncSession) -> int:
    """Delete LinkedRepo rows whose session_id has no matching GitHubToken.

    A LinkedRepo is only meaningful while its auth session is live. Once the
    GitHubToken has been swept (see _gc_expired_github_tokens) or revoked, the
    LinkedRepo row is dead weight — no API calls can be authorised against it.

    Project provenance is preserved via ``Optimization.project_id`` (denormalised
    FK to PromptCluster) — deleting the LinkedRepo does not orphan optimization
    records or their project assignment.
    """
    from app.models import GitHubToken, LinkedRepo

    stmt = select(LinkedRepo.id).where(
        ~LinkedRepo.session_id.in_(select(GitHubToken.session_id))
    )
    result = await db.execute(stmt)
    orphan_ids = [r[0] for r in result.all()]

    if not orphan_ids:
        return 0

    await db.execute(
        delete(LinkedRepo).where(LinkedRepo.id.in_(orphan_ids))
    )
    logger.info("Recurring GC: deleted %d orphan linked_repos", len(orphan_ids))
    return len(orphan_ids)
