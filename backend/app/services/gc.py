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

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Probes are user-driven (typical run <30min). Rows stuck in
# 'running' past this TTL are stragglers from client-disconnect or
# server-restart scenarios — _gc_orphan_probe_runs marks them failed.
PROBE_ORPHAN_TTL_HOURS = 1


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
    total_cleaned += await _gc_orphan_probe_runs(db)
    # v0.4.12: defense-in-depth against test-leak (Optimization rows
    # with non-uuid IDs). Production code uses uuid4() exclusively;
    # any other shape is a test fixture leak (typically `opt-NN-XX` or
    # `tr-NN`) caused by an in-process import-binding edge case in
    # pytest's monkeypatch contract. Sweeping at startup keeps the DB
    # clean across server restarts without manual intervention.
    total_cleaned += await _gc_test_leak_optimizations(db)
    # Reconcile cluster member_count against actual row counts. Drifts
    # can occur on hard restart mid-warm-cycle, on cancelled probes that
    # incremented but didn't decrement, or on data imports from older
    # builds. Running on every startup keeps the topology view honest.
    total_cleaned += await _gc_reconcile_member_counts(db)

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


async def _gc_orphan_probe_runs(db: AsyncSession) -> int:
    """Mark stale ``status='running'`` probe_run rows as failed at startup.

    Probes are user-driven (typical run <30min); rows in 'running' state
    for >``PROBE_ORPHAN_TTL_HOURS`` are stragglers from client-disconnect
    or server-restart scenarios. Mirrors the ``_gc_failed_optimizations``
    pattern.

    Idempotent: safe to call on a DB with no orphan rows.

    v0.4.12: at startup, ALL ``status='running'`` rows are orphans by
    definition -- the orchestrator coroutine that was managing the
    probe died with the previous process. The TTL gate (used by the
    HOURLY ``run_recurring_gc`` sweep) is irrelevant on startup; a
    probe in 'running' state with no live coroutine cannot recover
    no matter how recent. The startup sweep therefore drops the TTL
    gate -- any restart immediately reconciles every orphan probe
    instead of leaving them dangling for an hour.
    """
    from app.models import ProbeRun

    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(ProbeRun)
        .where(ProbeRun.status == "running")
        .values(
            status="failed",
            error="orphaned_at_startup",
            completed_at=now,
        )
    )
    cleaned = result.rowcount or 0  # type: ignore[attr-defined]
    if cleaned:
        logger.info(
            "GC: marked %d orphan probe_run rows as failed at startup "
            "(coroutine died with previous process)", cleaned,
        )
    return cleaned


async def _gc_test_leak_optimizations(db: AsyncSession) -> int:
    """Sweep Optimization rows that match test-fixture leak patterns.

    Production uses ``uuid4()`` for every Optimization ``id`` AND
    ``trace_id``; any row whose ``id`` or ``trace_id`` doesn't match
    the canonical UUID shape is a test fixture leak. Two patterns
    surfaced in v0.4.12:

      1. ``id`` is non-uuid (e.g. ``opt-NN-XX``) -- direct fixture-id
         leak. Caught by the ID-shape gate in ``bulk_persist``
         prospectively.
      2. ``id`` is a valid uuid BUT ``trace_id`` is non-uuid (e.g.
         ``tr-NN``) -- the test fixture set a real uuid but a stub
         trace_id. Causes ``/api/optimize/{trace_id}`` to raise
         MultipleResultsFound (multiple rows share the stub trace_id).

    Both patterns are swept here. Cascades through
    ``OptimizationPattern`` via FK; cluster ``member_count`` drift is
    reconciled by ``_gc_reconcile_member_counts`` below.

    Idempotent: safe to call on a DB with no leaked rows.
    """
    from sqlalchemy import and_, or_
    from sqlalchemy import func as _func

    from app.models import Optimization
    # uuid4 always has length 36 + hyphens at positions 8/13/18/23.
    _UUID_GLOB = "________-____-____-____-____________"  # noqa: N806 — local constant
    result = await db.execute(
        delete(Optimization).where(
            or_(
                # Pattern 1: id is non-uuid
                _func.length(Optimization.id) != 36,
                ~Optimization.id.like(_UUID_GLOB),
                # Pattern 2: trace_id is non-null and non-uuid
                and_(
                    Optimization.trace_id.is_not(None),
                    or_(
                        _func.length(Optimization.trace_id) != 36,
                        ~Optimization.trace_id.like(_UUID_GLOB),
                    ),
                ),
            )
        )
    )
    cleaned = result.rowcount or 0  # type: ignore[attr-defined]
    if cleaned:
        logger.warning(
            "GC: deleted %d test-leak Optimization rows (non-uuid "
            "id or trace_id); this indicates pytest leaked into "
            "production -- check the test harness's session-factory "
            "monkeypatch and any PendingOptimization fixture that sets "
            "a stub trace_id.",
            cleaned,
        )
    return cleaned


async def _gc_reconcile_member_counts(db: AsyncSession) -> int:
    """Reconcile ``PromptCluster.member_count`` against actual rows.

    Drifts can occur when:
      * A probe is cancelled mid-batch after assign_cluster() ran
        (incrementing) but before bulk_persist's commit fired.
      * Optimization rows are deleted manually or via test-leak GC
        (above) without triggering the cluster's denormalized counter.
      * A hard server restart truncates the warm-path engine mid-cycle.
      * Domain nodes have stale counters from older builds (domain
        nodes never directly own optimizations -- their ``member_count``
        should reflect descendant clusters' aggregate, not direct opts).

    The fix is a single ``UPDATE ... SET member_count = (subquery)`` --
    no additional storage, no need to track which clusters drifted.
    """
    from app.models import Optimization, PromptCluster

    # Non-domain clusters: count direct optimization rows.
    result = await db.execute(
        update(PromptCluster)
        .where(PromptCluster.state.in_(("candidate", "active", "mature")))
        .values(
            member_count=(
                select(_count_func()).where(
                    Optimization.cluster_id == PromptCluster.id,
                ).scalar_subquery()
            ),
        )
    )
    cleaned = result.rowcount or 0  # type: ignore[attr-defined]

    # Domain nodes: count optimizations across all descendant clusters
    # (aggregate of children's member_counts). We skip this for now
    # because the warm-path engine reconciles domain member_count
    # itself from descendants -- our job is just to keep the leaf
    # cluster counts honest, and the warm path will roll them up.

    if cleaned:
        logger.info("GC: reconciled member_count on %d clusters", cleaned)
    return cleaned


def _count_func():
    """Lazy import for sqlalchemy.func.count to avoid top-level dep."""
    from sqlalchemy import func as _f
    return _f.count()


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
