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

v0.4.13 cycle 8: both entry points accept an optional ``write_queue``
keyword argument. When supplied, the final commit routes through
``write_queue.submit()`` under ``operation_label='gc_startup_commit'``
or ``'gc_recurring_commit'``. The per-pass mutations still build up on
the supplied ``db`` (which under cycle 9 lifespan wiring is a writer
session anyway); this preserves the existing single-transaction
semantics of the GC sweep.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)

# Foundation P3 (v0.4.18): unified run orphan sweep — both topic_probe
# and seed_agent mode rows in 'running' state past this TTL are stragglers
# from client-disconnect or server-restart scenarios. ``_gc_orphan_runs``
# marks them failed at startup. Runs are user-driven (typical <30min).
RUN_ORPHAN_TTL_HOURS = 1

# Backward-compat alias preserved for v0.4.18 (PR1). Deleted in PR2 once
# the legacy ``_gc_orphan_probe_runs`` no-op stub is removed.
PROBE_ORPHAN_TTL_HOURS = RUN_ORPHAN_TTL_HOURS


def _utcnow() -> datetime:
    """Naive UTC now — matches the DateTime columns in models.py which store naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def run_startup_gc(
    db: AsyncSession,
    *,
    write_queue: "WriteQueue | None" = None,
) -> None:
    """Run all garbage collection passes in a single transaction.

    Called during backend lifespan startup. Each pass is independent —
    if one fails, the others still run.

    v0.4.13 cycle 8: when ``write_queue`` is supplied, the entire sweep
    (read + DELETEs + UPDATE + commit) runs inside a single
    ``write_queue.submit()`` callback under
    ``operation_label='gc_startup_commit'`` so the cumulative writes
    serialize against every other backend writer through the
    single-writer queue. The legacy direct-session path is retained
    behind the ``write_queue is None`` branch.
    """
    if write_queue is not None:
        async def _do_sweep(write_db: AsyncSession) -> int:
            total = 0
            total += await _gc_failed_optimizations(write_db)
            total += await _gc_archived_zero_member_clusters(write_db)
            total += await _gc_orphan_meta_patterns(write_db)
            total += await _gc_orphan_probe_runs(write_db)  # legacy no-op (PR1; deleted PR2)
            total += await _gc_orphan_runs(write_db)  # P3: sweeps topic_probe + seed_agent
            total += await _gc_orphan_repo_index_runs(write_db)
            total += await _gc_test_leak_optimizations(write_db)
            total += await _gc_reconcile_member_counts(write_db)
            if total > 0:
                await write_db.commit()
            return total

        total_cleaned = await write_queue.submit(
            _do_sweep, operation_label="gc_startup_commit",
        )
        if total_cleaned > 0:
            logger.info("Startup GC: cleaned %d records total", total_cleaned)
        else:
            logger.debug("Startup GC: nothing to clean")
        return

    # Legacy: write through ``db`` directly.
    total_cleaned = 0

    total_cleaned += await _gc_failed_optimizations(db)
    total_cleaned += await _gc_archived_zero_member_clusters(db)
    total_cleaned += await _gc_orphan_meta_patterns(db)
    total_cleaned += await _gc_orphan_probe_runs(db)  # legacy no-op (PR1; deleted PR2)
    total_cleaned += await _gc_orphan_runs(db)  # P3: sweeps topic_probe + seed_agent
    total_cleaned += await _gc_orphan_repo_index_runs(db)
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
    """Legacy alias — superseded by ``_gc_orphan_runs`` in Foundation P3 (v0.4.18).

    Returns 0; the unified ``_gc_orphan_runs`` sweep covers both
    ``topic_probe`` and ``seed_agent`` mode rows including legacy
    probe-mode rows (table is the unified ``run_row``). This function
    will be deleted in PR2 once all callers have migrated. The signature
    is preserved (``db: AsyncSession) -> int``) so the helper composes
    inside ``run_startup_gc._do_sweep`` without behavioural drift.

    Note on double-processing: with the option (b) Python-alias
    ``ProbeRun``, ``select(ProbeRun)`` returns ALL ``run_row`` rows
    regardless of mode (no STI discriminator filter). If both this
    helper (operating via ``select(ProbeRun)``) and ``_gc_orphan_runs``
    (operating via ``select(RunRow)``) executed in ``_do_sweep``, they
    would sweep the same row set twice — identical UPDATE statements.
    The no-op body avoids that redundancy.
    """
    return 0


async def _gc_orphan_runs(db: AsyncSession) -> int:
    """Sweep stale ``status='running'`` RunRow rows past ``RUN_ORPHAN_TTL_HOURS``.

    Foundation P3 (v0.4.18) — supersedes ``_gc_orphan_probe_runs``. Sweeps
    both ``topic_probe`` and ``seed_agent`` mode rows in one pass. Rows in
    ``status='running'`` whose ``started_at`` predates ``RUN_ORPHAN_TTL_HOURS``
    are stragglers from client-disconnect or server-restart scenarios; the
    orchestrator coroutine that was managing them died with the previous
    process. Marks them ``status='failed'``, ``error='orphaned (ttl exceeded)'``,
    ``completed_at=now``.

    Caller is responsible for committing — composes inside
    ``run_startup_gc._do_sweep`` batched commit. Mirrors the
    ``_gc_failed_optimizations`` / legacy ``_gc_orphan_probe_runs`` pattern.

    Idempotent: safe to call on a DB with no orphan rows.

    Returns the rowcount of rows flipped to ``status='failed'``.
    """
    from app.models import RunRow

    now = _utcnow()
    cutoff = now - timedelta(hours=RUN_ORPHAN_TTL_HOURS)
    result = await db.execute(
        update(RunRow)
        .where(RunRow.status == "running")
        .where(RunRow.started_at < cutoff)
        .values(
            status="failed",
            error="orphaned (ttl exceeded)",
            completed_at=now,
        )
    )
    cleaned = result.rowcount or 0  # type: ignore[attr-defined]
    if cleaned:
        logger.info(
            "GC: marked %d orphan run_row rows as failed "
            "(status='running' past TTL=%dh)", cleaned, RUN_ORPHAN_TTL_HOURS,
        )
    return cleaned


async def _gc_orphan_repo_index_runs(db: AsyncSession) -> int:
    """Sweep stuck status='indexing' rows older than REPO_INDEX_LOCK_TTL_MIN.

    v0.4.16 P1b § 3.4 — flips ``RepoIndexMeta.status='indexing'`` rows
    whose ``indexed_at`` predates ``REPO_INDEX_LOCK_TTL_MIN`` minutes ago
    to ``status='error'`` with the documented orphan-recovery error
    message. Runs only at lifespan startup (before user requests can
    trigger ``_bg_index``), so there is no race window with concurrent
    builds.

    Outer caller (``run_startup_gc``) commits at the end of the sweep —
    this helper does NOT commit internally, matching the
    ``_gc_orphan_probe_runs`` convention.

    Cycle 2 will additionally:
      * publish ``index_phase_changed`` SSE per stuck row,
      * emit ``repo_index_recovered`` decision event per stuck row.

    Returns the count of rows flipped to ``status='error'``.
    """
    from app.models import RepoIndexMeta
    from app.services.repo_index_service import (
        _emit_decision_event,
        _publish_phase_change,
    )
    from app.services.taxonomy._constants import REPO_INDEX_LOCK_TTL_MIN

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=REPO_INDEX_LOCK_TTL_MIN,
    )
    now = datetime.now(timezone.utc)
    q = await db.execute(
        select(RepoIndexMeta).where(
            RepoIndexMeta.status == "indexing",
            RepoIndexMeta.indexed_at < cutoff,
        )
    )
    stuck = q.scalars().all()
    for meta in stuck:
        meta.status = "error"
        meta.index_phase = "error"
        meta.error_message = "orphan_recovery: crashed mid-build"
        # v0.4.16 P1b § 4.1 row 8 — publish SSE phase change + emit
        # repo_index_recovered decision event per stuck row.
        # Compute age relative to the row's stored indexed_at; if missing
        # (defensive — should not happen for status='indexing' rows), use 0.
        prev_iso: str | None = None
        age_minutes = 0
        if meta.indexed_at is not None:
            # Models store naive UTC; normalise so the diff is consistent.
            prev_iso = meta.indexed_at.isoformat()
            indexed_at_aware = meta.indexed_at
            if indexed_at_aware.tzinfo is None:
                indexed_at_aware = indexed_at_aware.replace(tzinfo=timezone.utc)
            age_minutes = int(
                (now - indexed_at_aware).total_seconds() / 60
            )
        try:
            await _publish_phase_change(
                meta.repo_full_name, meta.branch,
                phase="error", status="error",
                files_seen=meta.files_seen or 0,
                files_total=meta.files_total or 0,
            )
        except Exception:
            logger.debug(
                "orphan recovery SSE publish failed", exc_info=True,
            )
        _emit_decision_event("repo_index_recovered", {
            "repo_full_name": meta.repo_full_name,
            "branch": meta.branch,
            "previous_indexed_at_iso": prev_iso,
            "age_minutes": age_minutes,
            "reason": "orphan_recovery",
        })
    if stuck:
        logger.info(
            "GC: flipped %d orphan repo_index_meta rows to status='error' "
            "(crashed mid-build)", len(stuck),
        )
    return len(stuck)


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


async def run_recurring_gc(
    db: AsyncSession,
    *,
    write_queue: "WriteQueue | None" = None,
) -> None:
    """Run all recurring garbage collection passes in a single transaction.

    Called hourly by ``recurring_gc_task`` in ``main.py``. Keeps token and
    session state from accumulating between restarts.

    v0.4.13 cycle 8: when ``write_queue`` is supplied, the entire sweep
    runs inside a single ``write_queue.submit()`` callback under
    ``operation_label='gc_recurring_commit'`` so the cumulative writes
    serialize against every other backend writer.
    """
    if write_queue is not None:
        async def _do_sweep(write_db: AsyncSession) -> int:
            total = 0
            total += await _gc_expired_github_tokens(write_db)
            total += await _gc_orphan_linked_repos(write_db)
            if total > 0:
                await write_db.commit()
            return total

        total_cleaned = await write_queue.submit(
            _do_sweep, operation_label="gc_recurring_commit",
        )
        if total_cleaned > 0:
            logger.info("Recurring GC: cleaned %d records total", total_cleaned)
        else:
            logger.debug("Recurring GC: nothing to clean")
        return

    # Legacy: write through ``db`` directly.
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
