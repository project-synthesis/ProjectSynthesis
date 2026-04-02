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
from app.services.taxonomy._constants import DEADLOCK_BREAKER_THRESHOLD
from app.services.taxonomy.quality import is_non_regressive
from app.services.taxonomy.warm_phases import (
    PhaseResult,
    phase_audit,
    phase_discover,
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


async def _load_active_nodes(db: AsyncSession) -> list[PromptCluster]:
    """Load all non-domain, non-archived nodes from the database."""
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(["domain", "archived"])
        )
    )
    return list(result.scalars().all())


async def _run_speculative_phase(
    phase_name: str,
    phase_fn: Callable,
    engine: TaxonomyEngine,
    session_factory: SessionFactory,
    split_protected_ids: set[str] | None = None,
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

    Returns:
        PhaseResult with accepted=True if Q gate passed, False otherwise.
    """
    idx_snapshot = await engine.embedding_index.snapshot()

    async with session_factory() as db:
        # Load nodes and compute Q_before
        nodes_before = await _load_active_nodes(db)
        q_before = engine._compute_q_from_nodes(nodes_before)

        # Call the phase function with appropriate arguments
        if phase_name == "retire":
            # phase_retire does not take split_protected_ids
            phase_result = await phase_fn(engine, db)
        else:
            # phase_split_emerge and phase_merge take split_protected_ids
            phase_result = await phase_fn(engine, db, split_protected_ids or set())

        # Re-query nodes and compute Q_after
        nodes_after = await _load_active_nodes(db)
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
            return phase_result
        else:
            await db.rollback()
            await engine.embedding_index.restore(idx_snapshot)
            phase_result.accepted = False
            logger.warning(
                "Phase %s rejected (Q regression): Q %.4f -> %.4f",
                phase_name, q_before, q_after,
            )
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
    all_phase_results: list[PhaseResult] = []
    q_baseline: float | None = None

    # ------------------------------------------------------------------
    # Phase 0: Reconcile — fresh session, always commits
    # ------------------------------------------------------------------
    async with session_factory() as db:
        reconcile_result = await phase_reconcile(engine, db)
        await db.commit()
        logger.info(
            "Phase 0 (reconcile): fixed=%d coherence=%d scores=%d zombies=%d",
            reconcile_result.member_counts_fixed,
            reconcile_result.coherence_updated,
            reconcile_result.scores_reconciled,
            reconcile_result.zombies_archived,
        )

        # Compute Q_baseline from the reconciled state
        nodes = await _load_active_nodes(db)
        q_baseline = engine._compute_q_from_nodes(nodes)

    # ------------------------------------------------------------------
    # Phase 1: Split/Emerge — speculative
    # ------------------------------------------------------------------
    split_result = await _run_speculative_phase(
        "split_emerge", phase_split_emerge, engine, session_factory,
        split_protected_ids=set(),
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
    )
    all_phase_results.append(merge_result)

    # ------------------------------------------------------------------
    # Phase 3: Retire — speculative
    # ------------------------------------------------------------------
    retire_result = await _run_speculative_phase(
        "retire", phase_retire, engine, session_factory,
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
    # ------------------------------------------------------------------
    async with session_factory() as db:
        refresh_result = await phase_refresh(engine, db)
        await db.commit()
        logger.info(
            "Phase 4 (refresh): clusters_refreshed=%d",
            refresh_result.clusters_refreshed,
        )

    # ------------------------------------------------------------------
    # Phase 5: Discover — fresh session, always commits
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
