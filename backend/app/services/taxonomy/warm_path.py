"""Warm-path orchestrator — sequential phase execution with per-phase Q gates.

Receives ``engine`` and ``session_factory`` as parameters.  Never imports
TaxonomyEngine at runtime (uses TYPE_CHECKING only).

Two execution groups:

**Lifecycle group** (dirty-cluster-gated):
  0.   Reconcile    — fresh session, always commits, then compute Q_baseline
  0.5  Evaluate     — candidate promotion/rejection
  1.   Split/Emerge — speculative (Q gate)
  2.   Merge        — speculative (Q gate)
  3.   Retire       — speculative (Q gate)
  4.   Refresh      — fresh session, always commits
  4.25 Sub-domain pattern aggregation
  4.5  Global pattern promotion/validation (periodic gate)
  4.75 Task-type signal refresh

**Maintenance group** (cadence-gated, independent of dirty clusters):
  5.  Discover     — fresh session, try/except with retry flag
  5.5 Archive      — sub-domain garbage collection
  6.  Audit        — fresh session, creates snapshot

Maintenance runs every ``MAINTENANCE_CYCLE_INTERVAL`` warm cycles (default 6,
~30 min at 5-min interval), or immediately when ``engine._maintenance_pending``
is set after a transient failure.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
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
    phase_archive_empty_sub_domains,
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
    from app.services.write_queue import WriteQueue

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


def _resolve_project_scope(
    engine: TaxonomyEngine, dirty_ids: set[str] | None,
) -> str | None:
    """Resolve per-project Q scope when all dirty clusters share a project.

    ADR-005 Phase 2A: scope Q to project when all dirty clusters are from
    one project so a bad merge in Project A cannot block Project B's
    warm cycle. Returns ``None`` for cross-project or unscoped cycles.
    """
    if not dirty_ids:
        return None
    _dirty_projects: set[str] = set()
    for cid in dirty_ids:
        pid = engine._cluster_project_cache.get(cid)
        if pid:
            _dirty_projects.add(pid)
    if len(_dirty_projects) == 1:
        return _dirty_projects.pop()
    return None


def _q_fmt(q: float | None) -> str:
    """Format a Q value preserving None as 'undefined' for log readability."""
    return "undefined" if q is None else f"{q:.4f}"


def _q_round(q: float | None) -> float | None:
    """Round Q to 4 dp, preserving None for JSON event encoding."""
    return None if q is None else round(q, 4)


async def _persist_split_failure_metadata(
    db: AsyncSession,
    phase_result: PhaseResult,
) -> None:
    """Persist split_failures counter + domain hash on Q-rejection.

    Cycle 6 (v0.4.13): consolidates the v0.4.12 ``meta_db`` separate-session
    pattern (warm_path lines 322 + 339) into a single function callable
    inside either the queue callback (after savepoint.rollback() with
    autobegin transaction) OR the legacy two-session path.

    The function does NOT commit -- callers commit at the end of their
    own transaction so the writes ride on the same atomic boundary as
    any other post-rejection metadata.

    Args:
        db: AsyncSession with an open (autobegin or explicit) transaction.
        phase_result: Result from a rejected speculative phase containing
            ``split_attempted_ids`` + ``split_content_hashes``.
    """
    for cid in phase_result.split_attempted_ids:
        cluster = await db.get(PromptCluster, cid)
        if cluster:
            meta = read_meta(cluster.cluster_metadata)
            content_hash = phase_result.split_content_hashes.get(cid, "")
            cluster.cluster_metadata = write_meta(
                cluster.cluster_metadata,
                split_failures=meta["split_failures"] + 1,
                split_attempt_member_count=cluster.member_count or 0,
                split_content_hash=content_hash,
            )
    logger.info(
        "Persisted split_failures for %d clusters after Q-gate rejection",
        len(phase_result.split_attempted_ids),
    )

    # Also record at domain level for cross-ID Groundhog Day protection.
    # Failures here are non-fatal -- the per-cluster split_failures counter
    # alone is sufficient, the domain-level hash is defense in depth.
    try:
        for cid in phase_result.split_attempted_ids:
            cluster = await db.get(PromptCluster, cid)
            if cluster:
                content_hash = phase_result.split_content_hashes.get(cid, "")
                if content_hash:
                    await _record_domain_split_block(
                        db, cluster.domain or "general",
                        content_hash, cluster.label or "?",
                        source="q_gate_rejection",
                    )
    except Exception as dh_exc:
        logger.debug(
            "Domain hash recording on Q-gate rejection failed (non-fatal): %s",
            dh_exc,
        )


def _log_metadata_persist_failure(
    phase_result: PhaseResult,
    meta_exc: BaseException,
) -> None:
    """Warn-log + emit decision event for split-failure metadata write errors.

    Cycle 6 (v0.4.13): Shared by both the queue (savepoint+autobegin) path
    and the legacy (separate ``meta_db`` session) path so the failure
    surface is identical regardless of dispatch. The split-failures counter
    is best-effort observability — losing one increment slows the
    Groundhog Day cooldown by one cycle but never blocks correctness.
    """
    logger.warning(
        "Failed to persist split failure metadata (non-fatal): %s",
        meta_exc,
    )
    try:
        get_event_logger().log_decision(
            path="warm", op="split", decision="metadata_persist_failed",
            context={
                "cluster_ids": list(
                    phase_result.split_attempted_ids,
                )[:10],
                "error_type": type(meta_exc).__name__,
                "error_message": str(meta_exc)[:300],
            },
        )
    except RuntimeError:
        pass


async def _execute_phase_in_session(
    db: AsyncSession,
    *,
    phase_name: str,
    phase_fn: Callable[..., Awaitable[PhaseResult]],
    engine: TaxonomyEngine,
    split_protected_ids: set[str] | None,
    phase_idx: int,
    dirty_ids: set[str] | None,
    idx_snapshot: Any,
    ti_snapshot: Any,
) -> PhaseResult:
    """Run a speculative phase + Q gate inside a single AsyncSession.

    Cycle 6 (v0.4.13): shared body used by BOTH the legacy ``session_factory``
    path AND the new ``write_queue.submit()`` callback. The queue path wraps
    the body in ``begin_nested()`` SAVEPOINT so the speculative writes can
    be rolled back atomically; the legacy path uses session-level rollback.

    The function commits the accepted writes in-line on accept, rolls back
    on reject, and (on reject of a split_emerge phase with
    ``split_attempted_ids``) persists the split_failures counter via
    ``_persist_split_failure_metadata`` -- in the queue path this rides
    on the autobegin transaction that follows ``savepoint.rollback()``;
    in the legacy path it rides on the same session and commits in-line.
    """
    _project_scope = _resolve_project_scope(engine, dirty_ids)

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

    # Update phase result Q values.
    # Type unsoundness pre-dates cycle 6: ``_compute_q_from_nodes`` returns
    # ``float | None`` (None when fewer than 2 active non-structural nodes
    # exist) but ``PhaseResult.q_before/q_after`` are typed ``float``.
    # ``is_non_regressive`` and ``_q_round`` both handle ``None`` correctly,
    # and downstream JSONL serialization tolerates it. Widening
    # ``PhaseResult`` is out of scope for cycle 6 -- the assignments only
    # surfaced once cycle 6 REFACTOR tightened ``phase_fn``'s annotation
    # from bare ``Callable`` to ``Callable[..., Awaitable[PhaseResult]]``.
    phase_result.q_before = q_before  # type: ignore[assignment]
    phase_result.q_after = q_after  # type: ignore[assignment]

    _q_delta = (
        None if (q_before is None or q_after is None)
        else round(q_after - q_before, 4)
    )

    if is_non_regressive(q_before, q_after, engine._warm_path_age):
        phase_result.accepted = True
        logger.info(
            "Phase %s accepted: Q %s -> %s (ops=%d)",
            phase_name, _q_fmt(q_before), _q_fmt(q_after),
            phase_result.ops_accepted,
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
                        "q_before": _q_round(q_before),
                        "q_after": _q_round(q_after),
                        "delta": _q_delta,
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

    # Q regression -- caller (legacy or queue) handles transaction rollback.
    # We only need to restore the in-memory indices here.
    await engine.embedding_index.restore(idx_snapshot)
    await engine._transformation_index.restore(ti_snapshot)
    phase_result.accepted = False
    logger.warning(
        "Phase %s rejected (Q regression): Q %s -> %s",
        phase_name, _q_fmt(q_before), _q_fmt(q_after),
    )
    try:
        get_event_logger().log_decision(
            path="warm", op="phase", decision="rejected",
            context={
                "phase_name": phase_name,
                "phase_idx": phase_idx,
                "q_before": _q_round(q_before),
                "q_after": _q_round(q_after),
                "delta": _q_delta,
                "ops_attempted": phase_result.ops_attempted,
                "rejection_count": engine._phase_rejection_counters.get(phase_name, 0),
                "accepted": False,
                "rolled_back_splits": phase_result.split_attempted_ids,
            },
        )
    except RuntimeError:
        pass
    return phase_result


async def _run_speculative_phase(
    phase_name: str,
    phase_fn: Callable[..., Awaitable[PhaseResult]],
    engine: TaxonomyEngine,
    session_factory: SessionFactory | None = None,
    split_protected_ids: set[str] | None = None,
    phase_idx: int = 0,
    dirty_ids: set[str] | None = None,  # ADR-005: None = process all
    *,
    write_queue: WriteQueue | None = None,
) -> PhaseResult:
    """Execute a single speculative phase with a per-phase Q gate.

    1. Snapshot the embedding index
    2. Open a fresh session (legacy) OR submit to writer queue (cycle 6+)
    3. Load active nodes, compute Q_before
    4. Run the phase function
    5. Re-query nodes, compute Q_after
    6. If non-regressive: commit and return accepted
    7. If regressive: rollback DB + restore embedding index snapshot

    **Cycle 6 dual-typed dispatch (Option C, v0.4.13)**

    * ``write_queue=`` canonical: per spec § 3.6 the speculative phase runs
      inside a single ``submit()`` callback that wraps the body in
      ``begin_nested()`` SAVEPOINT. On accept -> ``savepoint.commit()`` +
      ``db.commit()``. On reject -> ``savepoint.rollback()`` lets the
      autobegin transaction stay live for the post-rejection
      ``_persist_split_failure_metadata`` write, then ``db.commit()`` is
      the single commit-or-roll for the write. The 12 v0.4.12 commit
      sites collapse into ONE submit per phase invocation.
    * ``session_factory=`` legacy: retained until cycle 7 wires the
      queue from the orchestrator. Two-session pattern preserved.

    Args:
        phase_name: Human-readable phase identifier (e.g. "split_emerge").
        phase_fn: Async phase function from warm_phases module.
        engine: TaxonomyEngine instance (not imported at module level).
        session_factory: Async context manager yielding AsyncSession
            (legacy path). Pass ``None`` when threading ``write_queue``.
        split_protected_ids: IDs protected from merge (output of split phase).
        phase_idx: Phase index (0=split_emerge, 1=merge, 2=retire).
        dirty_ids: ADR-005 dirty-cluster scoping.
        write_queue: Single-writer queue (canonical cycle 6+ path).
            Mutually exclusive with ``session_factory``.

    Returns:
        PhaseResult with accepted=True if Q gate passed, False otherwise.
    """
    if write_queue is None and session_factory is None:
        raise TypeError(
            "_run_speculative_phase requires either write_queue= (canonical) "
            "or session_factory= (legacy). Both are None.",
        )

    idx_snapshot = await engine.embedding_index.snapshot()
    ti_snapshot = await engine._transformation_index.snapshot()

    # ----------------------------------------------------------------------
    # Canonical cycle 6+ path: single submit per phase + savepoint pattern.
    # ----------------------------------------------------------------------
    if write_queue is not None:
        async def _do_speculative(db: AsyncSession) -> PhaseResult:
            """All v0.4.12 commit sites collapse into one callback.

            Per spec § 3.6: speculative writes go in a savepoint so a Q-gate
            rejection can roll them back without losing pre-phase state.
            After ``savepoint.rollback()`` the AsyncSession remains in an
            outer (autobegin) transaction; the post-rejection
            ``_persist_split_failure_metadata`` write rides on that
            transaction and is committed by the explicit ``db.commit()``
            below. Do NOT add an explicit ``db.begin()`` here -- autobegin
            handles it (HIGH-6).
            """
            async with db.begin_nested() as savepoint:
                phase_result = await _execute_phase_in_session(
                    db,
                    phase_name=phase_name,
                    phase_fn=phase_fn,
                    engine=engine,
                    split_protected_ids=split_protected_ids,
                    phase_idx=phase_idx,
                    dirty_ids=dirty_ids,
                    idx_snapshot=idx_snapshot,
                    ti_snapshot=ti_snapshot,
                )
                if phase_result.accepted:
                    await savepoint.commit()
                else:
                    await savepoint.rollback()

            # Post-savepoint: speculative writes are gone (on reject) or
            # released (on accept). Either way we may need a final
            # housekeeping write before the outer commit:
            #
            # * Reject + split_emerge + split_attempted_ids -> persist
            #   split_failures counter so the Groundhog Day cooldown
            #   accumulates across cycles.
            # * Accept -> nothing extra to do; just commit.
            if (
                not phase_result.accepted
                and phase_name == "split_emerge"
                and phase_result.split_attempted_ids
            ):
                try:
                    await _persist_split_failure_metadata(db, phase_result)
                except Exception as meta_exc:
                    _log_metadata_persist_failure(phase_result, meta_exc)

            await db.commit()
            return phase_result

        return await write_queue.submit(
            _do_speculative,
            timeout=600.0,  # warm phases can run long under load
            operation_label=f"warm_phase_{phase_name}",
        )

    # ----------------------------------------------------------------------
    # Legacy session_factory path -- preserved verbatim for cycle 7+ rollback
    # ----------------------------------------------------------------------
    assert session_factory is not None  # narrowed by guard above
    async with session_factory() as db:
        phase_result = await _execute_phase_in_session(
            db,
            phase_name=phase_name,
            phase_fn=phase_fn,
            engine=engine,
            split_protected_ids=split_protected_ids,
            phase_idx=phase_idx,
            dirty_ids=dirty_ids,
            idx_snapshot=idx_snapshot,
            ti_snapshot=ti_snapshot,
        )
        if phase_result.accepted:
            await db.commit()
            return phase_result
        await db.rollback()

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
                await _persist_split_failure_metadata(meta_db, phase_result)
                await meta_db.commit()
        except Exception as meta_exc:
            _log_metadata_persist_failure(phase_result, meta_exc)

    return phase_result


async def run_phase_4_5(
    write_queue: WriteQueue,
    *,
    warm_path_age: float = 0.0,
) -> dict[str, int]:
    """Run Phase 4.5 (global pattern lifecycle) as 3 isolated submits.

    **Cycle 6 H-v4-3 (v0.4.13):** v0.4.12 ran the 3 sub-steps inside
    ``run_global_pattern_phase`` as ``begin_nested()`` SAVEPOINTs of a
    single session so a transient autoflush failure in one sub-step did
    not poison the whole maintenance transaction. Cycle 6 preserves that
    isolation by promoting EACH sub-step to its own ``submit()`` call
    with a try/except around each so an exception in one sub-step does
    NOT prevent the others from running.

    **Cycle 6 review I1 clarification (cycle 7d, v0.4.13):** each sub-step
    durably commits BEFORE the next submit fires -- cycle 6 promoted the
    SAVEPOINT-release-on-outer-commit pattern to per-submit independent
    commits. Failure isolation is strictly stronger than the v0.4.12
    nested-SAVEPOINT model. Cross-step transactional dependencies are
    NOT supported and would require a single-callback design (one
    submit() that runs all 3 steps in one queue session). If a future
    sub-step needs to read a committed value from an earlier step, that
    is fine -- the per-submit commit ordering guarantees it. If a sub-
    step needs to roll back BOTH its own writes and an earlier sibling's
    on a late failure, that scenario is not expressible in the current
    architecture and the design needs to revert to a single-callback
    composition.

    The 3 sub-steps mirror v0.4.12:

    1. ``_discover_promotion_candidates`` — promote MetaPattern siblings
       crossing the cross-cluster + cross-project gates to ``GlobalPattern``.
    2. ``_validate_existing_patterns`` — demote / re-promote / retire
       active patterns based on rolling avg_score signals.
    3. ``_enforce_retention_cap`` — LRU-evict the oldest active patterns
       above the 500-row cap.

    Args:
        write_queue: Single-writer queue threading writes through the worker.
        warm_path_age: Forwarded for telemetry only (not gating; cadence
            is gated by the caller in ``execute_warm_path``).

    Returns:
        Aggregated stats dict: ``{promoted, updated, demoted, re_promoted,
        retired, evicted}``. Sub-steps that raised contribute ``0`` to
        their respective counters.

    Failure semantics:
        Each sub-step is wrapped in a per-step ``try/except Exception`` so
        a transient failure (e.g. ``WriteQueueOverloadedError``,
        ``WriteQueueDeadError``, ``WriteQueueStoppedError``,
        ``asyncio.TimeoutError``, autoflush integrity error in
        ``_validate_existing_patterns``) only suppresses that step's
        contribution to ``stats``; the outer caller never sees the
        exception. This mirrors v0.4.12's ``begin_nested()`` SAVEPOINT
        semantics where one sub-step's failure rolled back ITS writes
        but left the surrounding maintenance transaction live for the
        next sub-step. The ``warm_phase_4_5_*`` callers in
        ``execute_warm_path`` therefore degrade gracefully: a Phase 4.5
        cycle with one bad sub-step still returns valid (partial)
        stats for the other two, and the next maintenance cycle retries
        the failing step.
    """
    _ = warm_path_age  # reserved for future per-cycle gating
    stats: dict[str, int] = {
        "promoted": 0,
        "updated": 0,
        "demoted": 0,
        "re_promoted": 0,
        "retired": 0,
        "evicted": 0,
    }

    # Lazy import to avoid pulling SQLAlchemy heavyweights at module load.
    from app.services.taxonomy import global_patterns as _gp

    # ------------------------------------------------------------------
    # Sub-step 1: discover + promote
    # ------------------------------------------------------------------
    async def _do_promote(db: AsyncSession) -> tuple[int, int]:
        result = await _gp._discover_promotion_candidates(db)
        await db.commit()
        return result

    try:
        promoted, updated = await write_queue.submit(
            _do_promote,
            timeout=600.0,
            operation_label="warm_phase_4_5_promote",
        )
        stats["promoted"] = promoted
        stats["updated"] = updated
    except Exception as exc:
        root_cause = getattr(exc, "orig", None) or getattr(exc, "__cause__", None)
        logger.warning(
            "Phase 4.5 step 1 (promote) failed: %s | root_cause=%r",
            exc, root_cause,
        )

    # ------------------------------------------------------------------
    # Sub-step 2: validate
    # ------------------------------------------------------------------
    async def _do_validate(db: AsyncSession) -> tuple[int, int, int]:
        result = await _gp._validate_existing_patterns(db)
        await db.commit()
        return result

    try:
        demoted, re_promoted, retired = await write_queue.submit(
            _do_validate,
            timeout=600.0,
            operation_label="warm_phase_4_5_validate",
        )
        stats["demoted"] = demoted
        stats["re_promoted"] = re_promoted
        stats["retired"] = retired
    except Exception as exc:
        root_cause = getattr(exc, "orig", None) or getattr(exc, "__cause__", None)
        logger.warning(
            "Phase 4.5 step 2 (validate) failed: %s | root_cause=%r",
            exc, root_cause,
        )

    # ------------------------------------------------------------------
    # Sub-step 3: enforce retention cap
    # ------------------------------------------------------------------
    async def _do_retire(db: AsyncSession) -> int:
        result = await _gp._enforce_retention_cap(db)
        await db.commit()
        return result

    try:
        evicted = await write_queue.submit(
            _do_retire,
            timeout=600.0,
            operation_label="warm_phase_4_5_retire",
        )
        stats["evicted"] = evicted
    except Exception as exc:
        root_cause = getattr(exc, "orig", None) or getattr(exc, "__cause__", None)
        logger.warning(
            "Phase 4.5 step 3 (cap) failed: %s | root_cause=%r",
            exc, root_cause,
        )

    return stats


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


async def _run_in_writer_session(
    write_queue: WriteQueue | None,
    session_factory: SessionFactory | None,
    work: Callable[[AsyncSession], Awaitable[Any]],
    *,
    operation_label: str,
    timeout: float = 600.0,
) -> Any:
    """Dual-typed dispatch helper for non-speculative phase commits.

    Cycle 6 (v0.4.13): consolidates the v0.4.12 ``async with
    session_factory() as db: ... await db.commit()`` pattern across the
    9 non-speculative commit sites in ``warm_path.py``. When a
    ``WriteQueue`` is supplied the work is submitted to the single-writer
    queue worker; otherwise the legacy session_factory path is used.

    The work callable receives an open ``AsyncSession`` and is responsible
    for calling ``await db.commit()`` itself -- mirrors the v0.4.12 pattern
    where each phase block ended with an explicit commit.

    Args:
        write_queue: Single-writer queue (canonical cycle 6+ path).
        session_factory: Async context manager (legacy path).
        work: Coroutine factory taking an ``AsyncSession``.
        operation_label: Label attached to the queue submit for metrics.
        timeout: Per-submit deadline in seconds (default 600s).

    Returns:
        Whatever ``work(db)`` returned.
    """
    if write_queue is not None:
        return await write_queue.submit(
            work, timeout=timeout, operation_label=operation_label,
        )
    if session_factory is None:
        raise TypeError(
            "_run_in_writer_session requires either write_queue= or "
            "session_factory=; both are None.",
        )
    async with session_factory() as db:
        return await work(db)


async def execute_maintenance_phases(
    engine: TaxonomyEngine,
    session_factory: SessionFactory | None = None,
    phase_results: list[PhaseResult] | None = None,
    q_baseline: float | None = None,
    *,
    write_queue: WriteQueue | None = None,
) -> WarmPathResult:
    """Run maintenance phases independently of the dirty-cluster lifecycle.

    Phases: 5 (Discover), 5.5 (Archive sub-domains), 6 (Audit).
    These phases scan the complete taxonomy state and do not depend on
    dirty clusters.  Phase 5 is wrapped in try/except — on transient
    failure (e.g. SQLite lock), ``engine._maintenance_pending`` is set
    so the next warm cycle retries immediately.

    **Cycle 6 dual-typed dispatch (v0.4.13):** when ``write_queue`` is
    supplied, every phase commit routes through the single-writer queue
    via ``_run_in_writer_session``. Otherwise the legacy
    ``session_factory`` path is used. Each path crosses through the same
    helper so the body of each phase block stays single-source.

    Args:
        engine: TaxonomyEngine instance.
        session_factory: Async context manager yielding fresh AsyncSession
            (legacy). Pass ``None`` when threading ``write_queue``.
        phase_results: Speculative phase results from the lifecycle group
            (empty list if running maintenance-only on an idle cycle).
        q_baseline: Q baseline from lifecycle Phase 0 (None if idle cycle).
        write_queue: Single-writer queue (canonical cycle 6+ path).

    Returns:
        WarmPathResult with audit snapshot.
    """
    if write_queue is None and session_factory is None:
        raise TypeError(
            "execute_maintenance_phases requires either write_queue= (canonical) "
            "or session_factory= (legacy); both are None.",
        )
    if phase_results is None:
        phase_results = []

    # ------------------------------------------------------------------
    # Phase 4.95: Qualifier vocabulary refresh — isolated session
    # Runs vocab generation (Haiku qualifier keywords) in its own session
    # so they persist independently of Phase 5 discover's autoflush-prone
    # orchestration. Failures here are non-fatal — Phase 5 still runs.
    # ------------------------------------------------------------------
    async def _do_vocab_refresh(db: AsyncSession) -> None:
        await engine._propose_sub_domains(db, vocab_only=True)
        await db.commit()

    try:
        await _run_in_writer_session(
            write_queue, session_factory, _do_vocab_refresh,
            operation_label="warm_phase_4_95_vocab_refresh",
        )
    except Exception as vocab_exc:
        logger.warning(
            "Phase 4.95 (vocab refresh) failed (non-fatal): %s", vocab_exc,
        )
        try:
            get_event_logger().log_decision(
                path="warm", op="discover",
                decision="vocab_refresh_failed",
                context={"error": str(vocab_exc)[:300]},
            )
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Phase 5: Discover — fresh session, always commits
    # ADR-005: Full scan — domain discovery needs complete cluster state
    # ------------------------------------------------------------------
    async def _do_discover(db: AsyncSession) -> Any:
        result = await phase_discover(engine, db)
        await db.commit()

        # Persist readiness snapshots for trajectory visualization
        # (deferred item 1). Failures are non-fatal — history is
        # observability, never load-bearing.
        try:
            from app.services.taxonomy import sub_domain_readiness as _r
            from app.services.taxonomy.readiness_history import (
                record_snapshot,
            )

            # Cycle 6: read-only snapshot computation reuses the same
            # writer-session that just committed discover. The session
            # is single-connection on the writer engine -- no new pool
            # checkout needed, and reads are on the freshly committed
            # state.
            reports = await _r.compute_all_domain_readiness(
                db, fresh=True,
            )
            # Concurrent fire-and-forget: record_snapshot internally
            # offloads to a thread pool, so gather lets per-report
            # disk I/O overlap.  return_exceptions=True ensures one
            # bad report never poisons a siblings write.
            if reports:
                results = await asyncio.gather(
                    *[record_snapshot(r) for r in reports],
                    return_exceptions=True,
                )
                for r, outcome in zip(reports, results):
                    if isinstance(outcome, Exception):
                        logger.warning(
                            "readiness snapshot failed for domain %s: %s",
                            r.domain_id, outcome,
                        )
        except Exception as snap_exc:
            logger.warning(
                "readiness snapshot batch failed (non-fatal): %s",
                snap_exc,
            )
        return result

    try:
        discover_result = await _run_in_writer_session(
            write_queue, session_factory, _do_discover,
            operation_label="warm_phase_5_discover",
        )
        logger.info(
            "Phase 5 (discover): domains=%d candidates=%d",
            discover_result.domains_created,
            discover_result.candidates_detected,
        )
        # Success — clear retry flag
        engine._maintenance_pending = False

        # Daily retention prune — runs at most once per UTC day per
        # process. Idempotent — guarded by date. Disk-only operation;
        # no writer session needed.
        try:
            from datetime import datetime, timezone

            from app.services.taxonomy.readiness_history import (
                prune_old_snapshots,
            )
            today = datetime.now(timezone.utc).date()
            last_prune = getattr(engine, "_readiness_pruned_on", None)
            if last_prune != today:
                removed = prune_old_snapshots()
                engine._readiness_pruned_on = today
                if removed:
                    logger.info(
                        "readiness history pruned: %d files", removed,
                    )
        except Exception as prune_exc:
            logger.warning(
                "readiness prune failed: %s", prune_exc,
            )
    except Exception as discover_exc:
        logger.warning(
            "Phase 5 (discover) failed — will retry next cycle: %s",
            discover_exc,
        )
        engine._maintenance_pending = True
        try:
            get_event_logger().log_decision(
                path="warm", op="discover",
                decision="discover_failed_will_retry",
                context={"error": str(discover_exc)},
            )
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Phase 5.5: Archive empty sub-domains — fresh session, always commits
    # ------------------------------------------------------------------
    async def _do_archive(db: AsyncSession) -> int:
        result = await phase_archive_empty_sub_domains(engine, db)
        await db.commit()
        return result

    try:
        sub_domains_archived = await _run_in_writer_session(
            write_queue, session_factory, _do_archive,
            operation_label="warm_phase_5_5_archive",
        )
        if sub_domains_archived:
            logger.info(
                "Phase 5.5 (sub-domain cleanup): archived=%d",
                sub_domains_archived,
            )
    except Exception as archive_exc:
        logger.warning("Phase 5.5 (archive sub-domains) failed (non-fatal): %s", archive_exc)

    # ------------------------------------------------------------------
    # Phase 6: Audit — fresh session, creates snapshot
    # ADR-005: Full scan — audit/snapshot needs complete cluster state
    # ------------------------------------------------------------------
    async def _do_audit(db: AsyncSession) -> Any:
        result = await phase_audit(
            engine, db, phase_results, q_baseline,
        )
        await db.commit()
        return result

    audit_result = await _run_in_writer_session(
        write_queue, session_factory, _do_audit,
        operation_label="warm_phase_6_audit",
    )
    logger.info(
        "Phase 6 (audit): snapshot=%s q_final=%.4f deadlock=%s",
        audit_result.snapshot_id,
        audit_result.q_final or 0.0,
        audit_result.deadlock_breaker_used,
    )

    # ------------------------------------------------------------------
    # Snapshot pruning — tiered retention policy
    # ------------------------------------------------------------------
    async def _do_prune(db: AsyncSession) -> int:
        from app.services.taxonomy.snapshot import prune_snapshots
        return await prune_snapshots(db)

    try:
        # v0.4.13 cycle 9 (I3): renamed to fit ``warm_phase_*`` namespace.
        pruned = await _run_in_writer_session(
            write_queue, session_factory, _do_prune,
            operation_label="warm_phase_snapshot_prune",
        )
        if pruned:
            logger.info("Pruned %d old snapshots via retention policy", pruned)
    except Exception as prune_exc:
        logger.warning("Snapshot pruning failed (non-fatal): %s", prune_exc)

    engine._invalidate_stats_cache()

    return WarmPathResult(
        snapshot_id=audit_result.snapshot_id,
        q_baseline=q_baseline,
        q_final=audit_result.q_final,
        phase_results=phase_results,
        operations_attempted=sum(pr.ops_attempted for pr in phase_results),
        operations_accepted=sum(pr.ops_accepted for pr in phase_results),
        deadlock_breaker_used=audit_result.deadlock_breaker_used,
        deadlock_breaker_phase=audit_result.deadlock_breaker_phase,
    )


async def execute_warm_path(
    engine: TaxonomyEngine,
    session_factory: SessionFactory | None = None,
    *,
    write_queue: WriteQueue | None = None,
) -> WarmPathResult:
    """Orchestrate the complete warm path: lifecycle + maintenance groups.

    The lifecycle group (Phases 0–4) is gated by dirty clusters — when no
    clusters have been modified since the last cycle, these phases are skipped.
    The maintenance group (Phases 5–6) runs via ``execute_maintenance_phases()``
    on its own cadence or when retrying after a transient failure.

    **Cycle 6 dual-typed dispatch (v0.4.13):** when ``write_queue`` is
    supplied, every phase commit routes through the single-writer queue.
    The 12 v0.4.12 commit sites collapse into ~10 phase-level submits
    (3 speculative phases + 7 non-speculative phases + 3 H-v4-3 sub-step
    submits for Phase 4.5). When ``write_queue`` is None, the legacy
    ``session_factory`` path is used unchanged.

    Concurrency contract: every ``AsyncSession`` opened via
    ``session_factory()`` is a ``WriterLockedAsyncSession`` (see
    ``app/database.py``). The session subclass automatically holds
    ``db_writer_lock`` for the entire write-transaction span (first
    ``flush()`` to ``commit()``/``rollback()``/``close()``), so the warm
    cycle's writes serialize correctly against probe/seed batches and
    other process-mate writers without explicit lock wrapping here.

    Args:
        engine: TaxonomyEngine instance.
        session_factory: Async context manager yielding fresh AsyncSession
            (legacy). Pass ``None`` when threading ``write_queue``.
        write_queue: Single-writer queue (canonical cycle 6+ path).

    Returns:
        WarmPathResult aggregating all phase outcomes.
    """
    if write_queue is None and session_factory is None:
        raise TypeError(
            "execute_warm_path requires either write_queue= (canonical) "
            "or session_factory= (legacy); both are None.",
        )

    import time as _time
    _cycle_start = _time.monotonic()

    all_phase_results: list[PhaseResult] = []
    q_baseline: float | None = None

    # ADR-005 Phase 3A: snapshot dirty set with project breakdown
    dirty_by_project: dict[str, set[str]] | None = None
    if engine.is_first_warm_cycle():
        dirty_ids: set[str] | None = None
    else:
        dirty_ids, dirty_by_project = engine.snapshot_dirty_set_with_projects()
        if not dirty_ids:
            # Nothing changed since last cycle — skip speculative phases.
            # But maintenance phases (discover, archive, audit) run on
            # their own cadence or when retrying after a transient failure.
            from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL

            cadence_gate = (engine._warm_path_age % MAINTENANCE_CYCLE_INTERVAL == 0)
            should_maintain = cadence_gate or engine._maintenance_pending

            if should_maintain:
                logger.info(
                    "Warm path: no dirty clusters but running maintenance "
                    "(cadence=%s pending=%s age=%d)",
                    cadence_gate, engine._maintenance_pending, engine._warm_path_age,
                )
                try:
                    get_event_logger().log_decision(
                        path="warm", op="maintenance",
                        decision="maintenance_on_idle",
                        context={
                            "warm_path_age": engine._warm_path_age,
                            "cadence_gate": cadence_gate,
                            "retry_pending": engine._maintenance_pending,
                        },
                    )
                except RuntimeError:
                    pass

                # NOTE: do NOT increment _warm_path_age here — phase_audit()
                # inside execute_maintenance_phases() does it unconditionally.
                return await execute_maintenance_phases(
                    engine, session_factory, write_queue=write_queue,
                )

            # Neither cadence nor retry — skip entirely
            logger.debug("Warm path skipped — no dirty clusters (age=%d)", engine._warm_path_age)
            try:
                get_event_logger().log_decision(
                    path="warm", op="skip", decision="no_dirty_clusters",
                    context={"warm_path_age": engine._warm_path_age},
                )
            except RuntimeError:
                pass
            engine._warm_path_age += 1
            return WarmPathResult(
                snapshot_id="skipped",
                q_baseline=None,
                q_final=None,
                phase_results=[],
                operations_attempted=0,
                operations_accepted=0,
                deadlock_breaker_used=False,
                deadlock_breaker_phase=None,
            )

    # Phase 3A: scheduling mode decision
    _total_dirty_count = len(dirty_ids) if dirty_ids is not None else None  # before scoping
    mode = engine._scheduler.decide_mode(dirty_ids, dirty_by_project)
    if mode.is_round_robin:
        dirty_ids = mode.scoped_dirty_ids
        budget_summary = ", ".join(
            f"{pid}={b}" for pid, b in sorted(
                (mode.project_budgets or {}).items(),
            )
        )
        logger.info(
            "Warm path: budget mode, %d projects (%s), scoped=%d dirty, boundary=%d",
            len(mode.project_budgets or {}),
            budget_summary,
            len(dirty_ids) if dirty_ids else 0,
            engine._scheduler._compute_boundary(),
        )
    else:
        logger.info(
            "Warm path cycle: dirty_ids=%s (first_cycle=%s, boundary=%d)",
            len(dirty_ids) if dirty_ids is not None else "all",
            engine.is_first_warm_cycle(),
            engine._scheduler._compute_boundary(),
        )

    # ------------------------------------------------------------------
    # Phase 0: Reconcile — fresh session, always commits
    # ADR-005: Full scan — reconciliation needs complete cluster state
    # ------------------------------------------------------------------
    async def _do_reconcile(db: AsyncSession) -> tuple[Any, float | None]:
        result = await phase_reconcile(engine, db)
        await db.commit()
        # Compute Q_baseline from the reconciled state (read-only after commit).
        nodes = await _load_active_nodes(db)
        q_base = engine._compute_q_from_nodes(nodes)
        return result, q_base

    reconcile_result, q_baseline = await _run_in_writer_session(
        write_queue, session_factory, _do_reconcile,
        operation_label="warm_phase_0_reconcile",
    )
    logger.info(
        "Phase 0 (reconcile): fixed=%d coherence=%d scores=%d "
        "zombies=%d outliers_ejected=%d",
        reconcile_result.member_counts_fixed,
        reconcile_result.coherence_updated,
        reconcile_result.scores_reconciled,
        reconcile_result.zombies_archived,
        reconcile_result.outliers_ejected,
    )

    # ------------------------------------------------------------------
    # Phase 0.5: Evaluate candidates — NOT Q-gated, always commits
    # ADR-005: Full scan — candidate evaluation needs complete cluster state
    # ------------------------------------------------------------------
    async def _do_evaluate_candidates(db: AsyncSession) -> dict:
        result = await phase_evaluate_candidates(db)
        await db.commit()
        return result

    candidate_result = await _run_in_writer_session(
        write_queue, session_factory, _do_evaluate_candidates,
        operation_label="warm_phase_0_5_evaluate_candidates",
    )
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
        write_queue=write_queue,
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
        write_queue=write_queue,
    )
    all_phase_results.append(merge_result)

    # ------------------------------------------------------------------
    # Phase 3: Retire — speculative
    # ADR-005: Full scan — retirement needs complete cluster state
    # ------------------------------------------------------------------
    retire_result = await _run_speculative_phase(
        "retire", phase_retire, engine, session_factory,
        phase_idx=2,
        write_queue=write_queue,
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
    async def _do_refresh(db: AsyncSession) -> Any:
        result = await phase_refresh(engine, db)
        await db.commit()
        return result

    refresh_result = await _run_in_writer_session(
        write_queue, session_factory, _do_refresh,
        operation_label="warm_phase_4_refresh",
    )
    logger.info(
        "Phase 4 (refresh): clusters_refreshed=%d",
        refresh_result.clusters_refreshed,
    )
    # Cache injection effectiveness on engine for health endpoint
    if refresh_result.injection_effectiveness:
        engine._injection_effectiveness = refresh_result.injection_effectiveness

    # Save embedding index to disk so MCP process can reload it.
    # Without this, MCP's disk-cached index goes stale after warm-path
    # adds new clusters/patterns. Saved every warm cycle (~5 min).
    try:
        from app.config import DATA_DIR
        _idx_path = DATA_DIR / "embedding_index.pkl"
        await engine.embedding_index.save_cache(_idx_path)
        logger.debug("Embedding index saved (%d entries)", engine.embedding_index.size)
    except Exception as _idx_exc:
        logger.warning("Embedding index save failed (non-fatal): %s", _idx_exc)

    # ------------------------------------------------------------------
    # Phase 4.25: Sub-domain meta-pattern aggregation
    # Rolls up child-cluster patterns into sub-domain nodes.
    # ------------------------------------------------------------------
    async def _do_aggregate_sub_domain_patterns(db: AsyncSession) -> None:
        from app.services.taxonomy.warm_phases import aggregate_sub_domain_patterns
        await aggregate_sub_domain_patterns(db)
        await db.commit()

    await _run_in_writer_session(
        write_queue, session_factory, _do_aggregate_sub_domain_patterns,
        operation_label="warm_phase_4_25_aggregate_sub_domain_patterns",
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
        if write_queue is not None:
            # Cycle 6 H-v4-3 path: each sub-step is its own submit.
            try:
                gp_stats = await run_phase_4_5(
                    write_queue, warm_path_age=engine._warm_path_age,
                )
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
                root_cause = getattr(gp_exc, "orig", None) or getattr(gp_exc, "__cause__", None)
                logger.warning(
                    "Phase 4.5 (global patterns) failed (non-fatal): %s | root_cause=%r",
                    gp_exc, root_cause,
                )
        else:
            # Legacy single-session path: kept for parity until cycle 7.
            assert session_factory is not None
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
                root_cause = getattr(gp_exc, "orig", None) or getattr(gp_exc, "__cause__", None)
                logger.warning(
                    "Phase 4.5 (global patterns) failed (non-fatal): %s | root_cause=%r",
                    gp_exc, root_cause,
                )

    # ------------------------------------------------------------------
    # Phase 4.75: Task-type signal refresh
    # Re-extract TF-IDF task-type keywords from optimization history.
    # ------------------------------------------------------------------
    async def _do_task_type_signals(db: AsyncSession) -> None:
        from app.services.heuristic_analyzer import set_task_type_signals
        from app.services.task_type_signal_extractor import extract_task_type_signals
        tt_signals = await extract_task_type_signals(db)
        if tt_signals:
            # A4: signals dict keys are exactly the task_types that crossed
            # MIN_SAMPLES this run — pass them explicitly so heuristic
            # analyzer can flag signal_source="dynamic" only for them.
            set_task_type_signals(
                tt_signals,
                extracted_task_types=set(tt_signals.keys()),
            )
            import json as _tt_json

            from app.config import DATA_DIR
            _tt_cache = DATA_DIR / "task_type_signals.json"
            try:
                _tt_cache.write_text(_tt_json.dumps(
                    {k: [[kw, w] for kw, w in v] for k, v in tt_signals.items()},
                    indent=2,
                ))
                logger.info("Phase 4.75 (task-type signals): persisted to %s", _tt_cache)
            except Exception as _persist_exc:
                logger.warning("Phase 4.75: persistence failed (%s) — in-memory only", _persist_exc)

    try:
        await _run_in_writer_session(
            write_queue, session_factory, _do_task_type_signals,
            operation_label="warm_phase_4_75_task_type_signals",
        )
    except Exception as _tt_exc:
        logger.warning("Phase 4.75 (task-type signals) failed (non-fatal): %s", _tt_exc)

    # ------------------------------------------------------------------
    # Phase 4.76: Active-learning signal adjuster (#3)
    # Consume TaskTypeTelemetry rows — tokens that recur in Haiku-
    # classified prompts for the same task_type are merged into the
    # signal table so the heuristic learns from the ambiguous cases.
    # Runs AFTER Phase 4.75 so its additions layer on top of the fresh
    # TF-IDF extraction instead of being overwritten by it.
    # ------------------------------------------------------------------
    async def _do_signal_adjuster(db: AsyncSession) -> Any:
        from app.services.signal_adjuster import adjust_signals_from_telemetry
        return await adjust_signals_from_telemetry(db)

    try:
        adjust_result = await _run_in_writer_session(
            write_queue, session_factory, _do_signal_adjuster,
            operation_label="warm_phase_4_76_signal_adjuster",
        )
        if adjust_result.signals_added:
            logger.info(
                "Phase 4.76 (signal adjuster): %d signals added from %d "
                "telemetry rows (task_types: %s)",
                adjust_result.signals_added,
                adjust_result.rows_processed,
                sorted(adjust_result.task_types_touched),
            )
    except Exception as _adj_exc:
        logger.warning("Phase 4.76 (signal adjuster) failed (non-fatal): %s", _adj_exc)

    # ------------------------------------------------------------------
    # Maintenance group: Phases 5, 5.5, 6 + snapshot pruning
    # Delegated to execute_maintenance_phases() which handles discovery
    # retry and error isolation independently.
    # ------------------------------------------------------------------
    maint_result = await execute_maintenance_phases(
        engine, session_factory,
        phase_results=all_phase_results,
        q_baseline=q_baseline,
        write_queue=write_queue,
    )

    # Merge audit-level deadlock info with per-phase deadlock info
    final_deadlock_used = deadlock_used or maint_result.deadlock_breaker_used
    final_deadlock_phase = deadlock_phase or maint_result.deadlock_breaker_phase

    # ADR-005: Record cycle measurement for adaptive scheduling.
    _cycle_duration_ms = int((_time.monotonic() - _cycle_start) * 1000)
    if _total_dirty_count is not None:
        engine._scheduler.record(
            dirty_count=_total_dirty_count,
            duration_ms=_cycle_duration_ms,
        )
    logger.debug(
        "Warm cycle measurement recorded: duration_ms=%d dirty_count=%s scheduler=%s",
        _cycle_duration_ms,
        len(dirty_ids) if dirty_ids is not None else "all",
        engine._scheduler.snapshot(),
    )

    # ADR-005 Phase 3A: re-inject non-processed dirty clusters after budget allocation
    if mode.is_round_robin and dirty_by_project:
        _processed = mode.scoped_dirty_ids or set()
        _reinjected = 0
        _reinjected_projects = 0
        for pid, cids in dirty_by_project.items():
            remaining = cids - _processed
            if remaining:
                _reinjected_projects += 1
                raw_pid = None if pid == "legacy" else pid
                for cid in remaining:
                    engine.mark_dirty(cid, project_id=raw_pid)
                _reinjected += len(remaining)
        if _reinjected:
            logger.info(
                "Warm path: re-injected %d dirty clusters from %d projects",
                _reinjected,
                _reinjected_projects,
            )

    return WarmPathResult(
        snapshot_id=maint_result.snapshot_id,
        q_baseline=q_baseline,
        q_final=maint_result.q_final,
        phase_results=all_phase_results,
        operations_attempted=sum(pr.ops_attempted for pr in all_phase_results),
        operations_accepted=sum(pr.ops_accepted for pr in all_phase_results),
        deadlock_breaker_used=final_deadlock_used,
        deadlock_breaker_phase=final_deadlock_phase,
    )
