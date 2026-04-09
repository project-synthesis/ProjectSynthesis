"""Warm-path orchestrator — sequential phase execution with per-phase Q gates.

Receives ``engine`` and ``session_factory`` as parameters.  Never imports
TaxonomyEngine at runtime (uses TYPE_CHECKING only).

Phase order:
  0. Reconcile   — fresh session, always commits, then compute Q_baseline
  1. Split/Emerge — speculative (Q gate)
  2. Merge        — speculative (Q gate)
  3. Retire       — speculative (Q gate)
  4. Refresh      — fresh session, always commits
  5. Discover     — fresh session, always commits
  6. Audit        — fresh session, creates snapshot

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster
from app.services.taxonomy._constants import DEADLOCK_BREAKER_THRESHOLD, EXCLUDED_STRUCTURAL_STATES
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.quality import is_non_regressive
from app.services.taxonomy.warm_phases import (
    PhaseResult,
    _record_domain_split_block,
    phase_audit,
    phase_discover,
    phase_evaluate_candidates,
    phase_merge,
    phase_reconcile,
    phase_refresh,
    phase_retire,
    phase_split_emerge,
)

if TYPE_CHECKING:
    from app.services.taxonomy.engine import TaxonomyEngine

logger = logging.getLogger(__name__)

# Type alias for the async session factory (contextmanager returning AsyncSession)
SessionFactory = Callable[..., Any]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class WarmPathResult:
    """Aggregated result from a complete warm-path execution."""

    snapshot_id: str
    q_baseline: float | None
    q_final: float | None
    phase_results: list[PhaseResult]
    operations_attempted: int
    operations_accepted: int
    deadlock_breaker_used: bool
    deadlock_breaker_phase: str | None
    q_system: float | None = None  # Backward compat

    def __post_init__(self):
        if self.q_system is None and self.q_final is not None:
            self.q_system = self.q_final


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_active_nodes(
    db: AsyncSession,
    exclude_candidates: bool = False,
    project_id: str | None = None,  # ADR-005 Phase 2A
) -> list[PromptCluster]:
    """Load all non-domain, non-archived nodes from the database.

    Args:
        db: Active database session.
        exclude_candidates: When True, also exclude ``state="candidate"``
            from the result set. Used in Q computation to prevent low-coherence
            candidates from dragging Q_after below Q_before.
        project_id: When provided, restrict results to clusters under this
            project's domain subtree. Used for per-project Q scoping so that
            a bad merge in Project A cannot block Project B's warm cycle.
    """
    excluded = list(EXCLUDED_STRUCTURAL_STATES)
    if exclude_candidates:
        excluded.append("candidate")

    if project_id:
        # Load only clusters under this project's domain subtree
        from app.services.taxonomy.family_ops import _get_project_domain_ids
        domain_ids = await _get_project_domain_ids(db, project_id)

        if not domain_ids:
            return []

        # Include clusters under this project's domains AND cross-project
        # clusters that contain this project's optimizations (ADR-005 spec §3)
        from sqlalchemy import or_

        from app.models import Optimization

        cross_project_cluster_ids = select(Optimization.cluster_id).where(
            Optimization.project_id == project_id,
            Optimization.cluster_id.isnot(None),
        ).distinct().scalar_subquery()

        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(excluded),
                or_(
                    PromptCluster.parent_id.in_(domain_ids),
                    PromptCluster.id.in_(cross_project_cluster_ids),
                ),
            )
        )
    else:
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(excluded)
            )
        )
    return list(result.scalars().all())


async def _run_speculative_phase(
    phase_name: str,
    phase_fn: Callable,
    engine: TaxonomyEngine,
    session_factory: SessionFactory,
    split_protected_ids: set[str] | None = None,
    phase_idx: int = 0,
    dirty_ids: set[str] | None = None,  # ADR-005: None = process all
) -> PhaseResult:
    """Execute a single speculative phase with a per-phase Q gate.

    1. Snapshot the embedding index
    2. Open a fresh session
    3. Load active nodes, compute Q_before
    4. Run the phase function
    5. Re-query nodes, compute Q_after
    6. If non-regressive: commit and return accepted
    7. If regressive: rollback DB + restore embedding index snapshot

    Args:
        phase_name: Human-readable phase identifier (e.g. "split_emerge").
        phase_fn: Async phase function from warm_phases module.
        engine: TaxonomyEngine instance (not imported at module level).
        session_factory: Async context manager yielding AsyncSession.
        split_protected_ids: IDs protected from merge (output of split phase).
        phase_idx: Phase index (0=split_emerge, 1=merge, 2=retire).

    Returns:
        PhaseResult with accepted=True if Q gate passed, False otherwise.
    """
    idx_snapshot = await engine.embedding_index.snapshot()
    ti_snapshot = await engine._transformation_index.snapshot()

    async with session_factory() as db:
        # ADR-005 Phase 2A: scope Q to project when all dirty clusters are from one project
        _project_scope = None
        if dirty_ids:
            _dirty_projects = set()
            for cid in dirty_ids:
                pid = engine._cluster_project_cache.get(cid)
                if pid:
                    _dirty_projects.add(pid)
            if len(_dirty_projects) == 1:
                _project_scope = _dirty_projects.pop()

        # Load nodes and compute Q_before — exclude candidates to prevent
        # low-coherence candidate clusters from dragging Q down.
        nodes_before = await _load_active_nodes(
            db, exclude_candidates=True, project_id=_project_scope,
        )
        q_before = engine._compute_q_from_nodes(nodes_before)

        # Call the phase function with appropriate arguments
        if phase_name == "retire":
            # phase_retire does not take split_protected_ids
            phase_result = await phase_fn(engine, db)
        else:
            # phase_split_emerge and phase_merge take split_protected_ids
            phase_result = await phase_fn(
                engine, db, split_protected_ids or set(), dirty_ids=dirty_ids,
            )

        # Re-query nodes and compute Q_after — same exclusion as Q_before.
        nodes_after = await _load_active_nodes(
            db, exclude_candidates=True, project_id=_project_scope,
        )
        q_after = engine._compute_q_from_nodes(nodes_after)

        # Update phase result Q values
        phase_result.q_before = q_before
        phase_result.q_after = q_after

        # Non-regression gate
        if is_non_regressive(q_before, q_after, engine._warm_path_age):
            await db.commit()
            phase_result.accepted = True
            logger.info(
                "Phase %s accepted: Q %.4f -> %.4f (ops=%d)",
                phase_name, q_before, q_after, phase_result.ops_accepted,
            )
            # Only log accepted phases that actually did work — idle
            # phases (0 attempted, 0 accepted) are noise in the
            # Activity panel and JSONL when the system has converged.
            if phase_result.ops_attempted > 0:
                try:
                    get_event_logger().log_decision(
                        path="warm", op="phase", decision="accepted",
                        context={
                            "phase_name": phase_name,
                            "phase_idx": phase_idx,
                            "q_before": round(q_before, 4),
                            "q_after": round(q_after, 4),
                            "delta": round(q_after - q_before, 4),
                            "ops_accepted": phase_result.ops_accepted,
                            "ops_attempted": phase_result.ops_attempted,
                            "rejection_count": engine._phase_rejection_counters.get(phase_name, 0),
                            "operations": phase_result.operations[:10],
                            "accepted": True,
                        },
                    )
                except RuntimeError:
                    pass
            return phase_result
        else:
            await db.rollback()
            await engine.embedding_index.restore(idx_snapshot)
            await engine._transformation_index.restore(ti_snapshot)
            phase_result.accepted = False
            logger.warning(
                "Phase %s rejected (Q regression): Q %.4f -> %.4f",
                phase_name, q_before, q_after,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="phase", decision="rejected",
                    context={
                        "phase_name": phase_name,
                        "phase_idx": phase_idx,
                        "q_before": round(q_before, 4),
                        "q_after": round(q_after, 4),
                        "delta": round(q_after - q_before, 4),
                        "ops_attempted": phase_result.ops_attempted,
                        "rejection_count": engine._phase_rejection_counters.get(phase_name, 0),
                        "accepted": False,
                        "rolled_back_splits": phase_result.split_attempted_ids,
                    },
                )
            except RuntimeError:
                pass

    # Persist split attempt metadata OUTSIDE the rolled-back transaction.
    # Without this, the split_failures cooldown counter resets to 0 on every
    # Q-gate rejection, causing the same cluster to be split and rolled back
    # indefinitely (the "Groundhog Day" loop).
    if (
        not phase_result.accepted
        and phase_name == "split_emerge"
        and phase_result.split_attempted_ids
    ):
        try:
            async with session_factory() as meta_db:
                for cid in phase_result.split_attempted_ids:
                    cluster = await meta_db.get(PromptCluster, cid)
                    if cluster:
                        meta = read_meta(cluster.cluster_metadata)
                        content_hash = phase_result.split_content_hashes.get(cid, "")
                        cluster.cluster_metadata = write_meta(
                            cluster.cluster_metadata,
                            split_failures=meta["split_failures"] + 1,
                            split_attempt_member_count=cluster.member_count or 0,
                            split_content_hash=content_hash,
                        )
                await meta_db.commit()
                logger.info(
                    "Persisted split_failures for %d clusters after Q-gate rejection",
                    len(phase_result.split_attempted_ids),
                )
                # Also record at domain level for cross-ID Groundhog Day protection
                try:
                    for cid in phase_result.split_attempted_ids:
                        cluster = await meta_db.get(PromptCluster, cid)
                        if cluster:
                            content_hash = phase_result.split_content_hashes.get(cid, "")
                            if content_hash:
                                await _record_domain_split_block(
                                    meta_db, cluster.domain or "general",
                                    content_hash, cluster.label or "?",
                                    source="q_gate_rejection",
                                )
                    await meta_db.commit()
                except Exception as dh_exc:
                    logger.debug("Domain hash recording on Q-gate rejection failed (non-fatal): %s", dh_exc)
        except Exception as meta_exc:
            logger.warning(
                "Failed to persist split failure metadata (non-fatal): %s",
                meta_exc,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="split", decision="metadata_persist_failed",
                    context={
                        "cluster_ids": list(phase_result.split_attempted_ids)[:10],
                        "error_type": type(meta_exc).__name__,
                        "error_message": str(meta_exc)[:300],
                    },
                )
            except RuntimeError:
                pass

    return phase_result


def _extract_split_protected_ids(phase_result: PhaseResult) -> set[str]:
    """Extract IDs of nodes created by split/emerge for merge protection.

    Newly split/emerged nodes should not be immediately merged back in the
    same warm-path cycle — their centroids are fresh and may not yet reflect
    their full membership.
    """
    protected: set[str] = set()
    for op in phase_result.operations:
        op_type = op.get("type", "")
        if op_type in ("split", "emerge", "family_split"):
            node_id = op.get("node_id")
            if node_id:
                protected.add(node_id)
            # Also protect child nodes from splits
            for child_id in op.get("child_ids", []):
                protected.add(child_id)
    return protected


def _update_phase_rejection_counters(
    engine: TaxonomyEngine,
    speculative_results: list[tuple[str, PhaseResult]],
) -> tuple[bool, str | None]:
    """Update per-phase rejection counters and check for deadlock.

    For each speculative phase:
    - If rejected: increment its counter
    - If accepted: reset its counter to 0

    If any counter reaches DEADLOCK_BREAKER_THRESHOLD, set
    engine._cold_path_needed = True.

    Returns:
        Tuple of (deadlock_breaker_used, deadlock_breaker_phase).
    """
    deadlock_used = False
    deadlock_phase = None

    for phase_name, result in speculative_results:
        if not result.accepted:
            engine._phase_rejection_counters[phase_name] = (
                engine._phase_rejection_counters.get(phase_name, 0) + 1
            )
        else:
            engine._phase_rejection_counters[phase_name] = 0

        if engine._phase_rejection_counters.get(phase_name, 0) >= DEADLOCK_BREAKER_THRESHOLD:
            deadlock_used = True
            deadlock_phase = phase_name
            engine._cold_path_needed = True
            logger.warning(
                "Per-phase deadlock breaker triggered for '%s' "
                "(rejected %d consecutive times) -- scheduling cold path",
                phase_name,
                engine._phase_rejection_counters[phase_name],
            )

    return deadlock_used, deadlock_phase


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def execute_warm_path(
    engine: TaxonomyEngine,
    session_factory: SessionFactory,
) -> WarmPathResult:
    """Orchestrate the complete warm path: 7 phases in strict sequential order.

    Phase 0 (Reconcile) and Phases 4-5 (Refresh, Discover) always commit.
    Phases 1-3 (Split/Emerge, Merge, Retire) are speculative with per-phase
    Q gates. Phase 6 (Audit) creates the snapshot.

    Args:
        engine: TaxonomyEngine instance.
        session_factory: Async context manager yielding fresh AsyncSession.

    Returns:
        WarmPathResult aggregating all phase outcomes.
    """
    import time as _time
    _cycle_start = _time.monotonic()

    all_phase_results: list[PhaseResult] = []
    q_baseline: float | None = None

    # ADR-005: Snapshot dirty set — first cycle does full scan
    if engine.is_first_warm_cycle():
        dirty_ids: set[str] | None = None  # None = process all clusters (restart recovery)
    else:
        dirty_ids = engine.snapshot_dirty_set() or None  # empty set → None (full scan)

    logger.info(
        "Warm path cycle: dirty_ids=%s (first_cycle=%s)",
        len(dirty_ids) if dirty_ids is not None else "all",
        engine.is_first_warm_cycle(),
    )

    # ------------------------------------------------------------------
    # Phase 0: Reconcile — fresh session, always commits
    # ADR-005: Full scan — reconciliation needs complete cluster state
    # ------------------------------------------------------------------
    async with session_factory() as db:
        reconcile_result = await phase_reconcile(engine, db)
        await db.commit()
        logger.info(
            "Phase 0 (reconcile): fixed=%d coherence=%d scores=%d "
            "zombies=%d outliers_ejected=%d",
            reconcile_result.member_counts_fixed,
            reconcile_result.coherence_updated,
            reconcile_result.scores_reconciled,
            reconcile_result.zombies_archived,
            reconcile_result.outliers_ejected,
        )

        # Compute Q_baseline from the reconciled state
        nodes = await _load_active_nodes(db)
        q_baseline = engine._compute_q_from_nodes(nodes)

    # ------------------------------------------------------------------
    # Phase 0.5: Evaluate candidates — NOT Q-gated, always commits
    # ADR-005: Full scan — candidate evaluation needs complete cluster state
    # ------------------------------------------------------------------
    async with session_factory() as db:
        candidate_result = await phase_evaluate_candidates(db)
        await db.commit()
        if candidate_result["promoted"] > 0 or candidate_result["rejected"] > 0:
            logger.info(
                "Phase 0.5 (candidate_eval): promoted=%d rejected=%d splits_fully_reversed=%d",
                candidate_result["promoted"],
                candidate_result["rejected"],
                candidate_result["splits_fully_reversed"],
            )
            # Publish taxonomy_changed so the frontend re-renders the topology
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("taxonomy_changed", {
                    "trigger": "candidate_evaluation",
                    "promoted": candidate_result["promoted"],
                    "rejected": candidate_result["rejected"],
                })
            except Exception as _evt_exc:
                logger.warning(
                    "Failed to publish taxonomy_changed after candidate evaluation: %s",
                    _evt_exc,
                )

    # ------------------------------------------------------------------
    # Phase 1: Split/Emerge — speculative
    # ------------------------------------------------------------------
    split_result = await _run_speculative_phase(
        "split_emerge", phase_split_emerge, engine, session_factory,
        split_protected_ids=set(),
        phase_idx=0,
        dirty_ids=dirty_ids,  # ADR-005: only split dirty clusters
    )
    all_phase_results.append(split_result)

    # Extract IDs from split operations for merge protection
    split_protected_ids = _extract_split_protected_ids(split_result)

    # ------------------------------------------------------------------
    # Phase 2: Merge — speculative (receives split_protected_ids)
    # ------------------------------------------------------------------
    merge_result = await _run_speculative_phase(
        "merge", phase_merge, engine, session_factory,
        split_protected_ids=split_protected_ids,
        phase_idx=1,
        dirty_ids=dirty_ids,  # ADR-005: only merge when ≥1 partner is dirty
    )
    all_phase_results.append(merge_result)

    # ------------------------------------------------------------------
    # Phase 3: Retire — speculative
    # ADR-005: Full scan — retirement needs complete cluster state
    # ------------------------------------------------------------------
    retire_result = await _run_speculative_phase(
        "retire", phase_retire, engine, session_factory,
        phase_idx=2,
    )
    all_phase_results.append(retire_result)

    # ------------------------------------------------------------------
    # Per-phase deadlock breaker check
    # ------------------------------------------------------------------
    speculative_phases = [
        ("split_emerge", split_result),
        ("merge", merge_result),
        ("retire", retire_result),
    ]
    deadlock_used, deadlock_phase = _update_phase_rejection_counters(
        engine, speculative_phases,
    )

    # ------------------------------------------------------------------
    # Phase 4: Refresh — fresh session, always commits
    # ADR-005: Full scan — label/pattern refresh needs complete cluster state
    # ------------------------------------------------------------------
    async with session_factory() as db:
        refresh_result = await phase_refresh(engine, db)
        await db.commit()
        logger.info(
            "Phase 4 (refresh): clusters_refreshed=%d",
            refresh_result.clusters_refreshed,
        )

    # ------------------------------------------------------------------
    # Phase 4.5: Global Pattern Promotion + Validation (ADR-005 Phase 2B)
    # Runs every Nth cycle with wall-clock gate. Full scan (ignores dirty_ids).
    # ------------------------------------------------------------------
    import time as _gp_time  # noqa: PLC0415

    from app.services.taxonomy._constants import (
        GLOBAL_PATTERN_CYCLE_INTERVAL,
        GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES,
    )

    _gp_age_gate = (engine._warm_path_age % GLOBAL_PATTERN_CYCLE_INTERVAL == 0)
    _gp_wall_gate = (
        _gp_time.monotonic() - engine._last_global_pattern_check
        >= GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES * 60
    )

    if _gp_age_gate and _gp_wall_gate:
        try:
            async with session_factory() as db:
                from app.services.taxonomy.global_patterns import run_global_pattern_phase
                gp_stats = await run_global_pattern_phase(db, engine._warm_path_age)
                await db.commit()
                engine._last_global_pattern_check = _gp_time.monotonic()
                if gp_stats.get("promoted", 0) or gp_stats.get("demoted", 0) or gp_stats.get("retired", 0):
                    logger.info(
                        "Phase 4.5 (global patterns): promoted=%d demoted=%d re_promoted=%d retired=%d evicted=%d",
                        gp_stats.get("promoted", 0),
                        gp_stats.get("demoted", 0),
                        gp_stats.get("re_promoted", 0),
                        gp_stats.get("retired", 0),
                        gp_stats.get("evicted", 0),
                    )
        except Exception as gp_exc:
            logger.warning("Phase 4.5 (global patterns) failed (non-fatal): %s", gp_exc)

    # ------------------------------------------------------------------
    # Phase 5: Discover — fresh session, always commits
    # ADR-005: Full scan — domain discovery needs complete cluster state
    # ------------------------------------------------------------------
    async with session_factory() as db:
        discover_result = await phase_discover(engine, db)
        await db.commit()
        logger.info(
            "Phase 5 (discover): domains=%d candidates=%d",
            discover_result.domains_created,
            discover_result.candidates_detected,
        )

    # ------------------------------------------------------------------
    # Phase 6: Audit — fresh session, creates snapshot
    # ADR-005: Full scan — audit/snapshot needs complete cluster state
    # ------------------------------------------------------------------
    async with session_factory() as db:
        audit_result = await phase_audit(
            engine, db, all_phase_results, q_baseline,
        )
        await db.commit()
        logger.info(
            "Phase 6 (audit): snapshot=%s q_final=%.4f deadlock=%s",
            audit_result.snapshot_id,
            audit_result.q_final or 0.0,
            audit_result.deadlock_breaker_used,
        )

    # ------------------------------------------------------------------
    # Snapshot pruning — tiered retention policy (own session + commit).
    # prune_snapshots() keeps 0-24h: all, 1-30d: best/day, 30+d: best/week.
    # ------------------------------------------------------------------
    try:
        async with session_factory() as db:
            from app.services.taxonomy.snapshot import prune_snapshots
            pruned = await prune_snapshots(db)
            if pruned:
                logger.info("Pruned %d old snapshots via retention policy", pruned)
    except Exception as prune_exc:
        logger.warning("Snapshot pruning failed (non-fatal): %s", prune_exc)

    # ------------------------------------------------------------------
    # Finalize: invalidate cache (warm_path_age already incremented
    # by phase_audit; _invalidate_stats_cache also called there but
    # we call again defensively in case audit was skipped on error).
    # ------------------------------------------------------------------
    engine._invalidate_stats_cache()

    # Merge audit-level deadlock info with per-phase deadlock info
    final_deadlock_used = deadlock_used or audit_result.deadlock_breaker_used
    final_deadlock_phase = deadlock_phase or audit_result.deadlock_breaker_phase

    # Aggregate totals
    total_attempted = sum(pr.ops_attempted for pr in all_phase_results)
    total_accepted = sum(pr.ops_accepted for pr in all_phase_results)

    # ADR-005: Record cycle measurement for adaptive scheduling.
    # Only record dirty-only cycles — full-scan cycles (dirty_ids=None) lack
    # a meaningful dirty_count and would corrupt Phase 3 regression analysis.
    _cycle_duration_ms = int((_time.monotonic() - _cycle_start) * 1000)
    if dirty_ids is not None:
        engine._scheduler.record(
            dirty_count=len(dirty_ids),
            duration_ms=_cycle_duration_ms,
        )
    logger.debug(
        "Warm cycle measurement recorded: duration_ms=%d dirty_count=%s scheduler=%s",
        _cycle_duration_ms,
        len(dirty_ids) if dirty_ids is not None else "all",
        engine._scheduler.snapshot(),
    )

    return WarmPathResult(
        snapshot_id=audit_result.snapshot_id,
        q_baseline=q_baseline,
        q_final=audit_result.q_final,
        phase_results=all_phase_results,
        operations_attempted=total_attempted,
        operations_accepted=total_accepted,
        deadlock_breaker_used=final_deadlock_used,
        deadlock_breaker_phase=final_deadlock_phase,
    )
