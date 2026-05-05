"""Cold-path implementation — full HDBSCAN refit + UMAP 3D projection + OKLab
coloring for the Evolutionary Taxonomy Engine.

The cold path is the "defrag" operation: it reclusters all non-domain,
non-archived PromptCluster centroids via HDBSCAN, updates or creates cluster
nodes, runs UMAP 3D projection with Procrustes alignment, regenerates OKLab
colors, reconciles member_count / avg_score / coherence from Optimization
rows, and creates an audit snapshot.

v0.4.16 P1a Cycle 1 — chunked execution with cumulative Q-gates:
  - 4 phase functions (_phase_1_reembed, _phase_2_reassign, _phase_3_relabel,
    _phase_4_repair) execute inside a ``cp_pre_reembed`` outer SAVEPOINT.
  - Phase boundaries 1 and 2 run a per-phase Q-check; on regression the
    rollback target is always ``cp_pre_reembed`` (full revert).
  - Typed exceptions ``ColdPathPhaseFailure`` / ``ColdPathQCheckEvalFailure``
    distinguish controlled-expected (Q regression) from controlled-unexpected
    (phase exception or Q-eval exception) failures.
  - Module-level ``_COLD_PATH_LOCK`` (asyncio.Lock) serializes concurrent
    invocations — second call blocks until the first releases.
  - ``_COLD_PATH_RUN_ID`` ContextVar tags every log line with the per-refit
    UUID so multi-line log archaeology can correlate without timestamp
    guesswork.

Key improvements over the original engine._run_cold_path_inner():
  - Fix #5:  Archived clusters excluded from HDBSCAN input (original used
             ``state != "domain"`` which includes archived clusters).
  - Fix #6:  Mature/template states included in existing-node matching
             (original used ``state.in_(["active", "candidate"])``).
  - Fix #14: Reset ``split_failures`` metadata on matched nodes after refit.
  - NEW:     Quality gate via ``is_cold_path_non_regressive()`` — bad refits
             are rolled back instead of committed unconditionally.

This module receives ``engine`` and ``db`` as parameters. It NEVER imports
TaxonomyEngine at runtime (TYPE_CHECKING only) to avoid circular imports.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.models import Optimization, PromptCluster
from app.services.taxonomy._constants import (
    CLUSTERING_BLEND_W_OPTIMIZED,
    CLUSTERING_BLEND_W_QUALIFIER,
    CLUSTERING_BLEND_W_TRANSFORM,
    EXCLUDED_STRUCTURAL_STATES,
    MEGA_CLUSTER_MEMBER_FLOOR,
    SPLIT_COHERENCE_FLOOR,
)
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.clustering import (
    batch_cluster,
    blend_embeddings,
    compute_pairwise_coherence,
    cosine_similarity,
    l2_normalize_1d,
)
from app.services.taxonomy.coloring import enforce_minimum_delta_e, generate_color
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.family_ops import adaptive_merge_threshold, score_to_centroid_weight
from app.services.taxonomy.labeling import generate_label
from app.services.taxonomy.projection import UMAPProjector, procrustes_align
from app.services.taxonomy.quality import COLD_PATH_EPSILON, is_cold_path_non_regressive
from app.services.taxonomy.snapshot import create_snapshot, get_latest_snapshot

if TYPE_CHECKING:
    from app.services.taxonomy.engine import TaxonomyEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v0.4.16 P1a Cycle 1: concurrent invocation lock + per-refit UUID
# ---------------------------------------------------------------------------
# Module-level asyncio.Lock serializes ``execute_cold_path()`` invocations.
# Two simultaneous triggers (manual /api/clusters/recluster + warm-path-detected
# mega_cluster_prevention) used to race; with chunking the race window widens
# so we explicitly serialize.  Second invocation blocks until the first
# releases.
_COLD_PATH_LOCK: asyncio.Lock = asyncio.Lock()

# Per-refit UUID propagated through every log line + decision event so
# multi-line archaeology can correlate without timestamp guesswork.
# Cycle 2 will wire this into ``log_decision()`` payloads; Cycle 1 just
# uses it for ``logger.info`` / ``logger.warning`` correlation.
_COLD_PATH_RUN_ID: ContextVar[str | None] = ContextVar(
    "_COLD_PATH_RUN_ID", default=None,
)

# Internal sentinel placed in the rejected_result_holder list inside the
# outer SAVEPOINT scope.  The actual ``ColdPathResult(accepted=False)`` is
# materialized post-rollback (``create_snapshot`` commits, which would
# tear down the SAVEPOINT context manager mid-flight if called inline).
_REJECTION_SENTINEL: object = object()


# ---------------------------------------------------------------------------
# v0.4.16 P1a Cycle 1: typed exceptions
# ---------------------------------------------------------------------------


class ColdPathPhaseFailure(RuntimeError):  # noqa: N818
    """Raised when a cold-path phase function aborts mid-execution.

    Caused by ``SQLAlchemyError`` mid-batch, network blip on submit, or any
    other unhandled exception inside ``_phase_1_reembed`` /
    ``_phase_2_reassign`` / ``_phase_3_relabel`` / ``_phase_4_repair``.
    Callers (warm-path scheduler, manual ``/api/clusters/recluster`` handler)
    distinguish "Q regression rejection" (controlled, expected) from
    "phase-execution failure" (controlled, unexpected) via this typed
    exception.

    The outer ``execute_cold_path()`` rolls back to ``cp_pre_reembed`` and
    restores ``engine._last_silhouette`` before re-raising.
    """

    def __init__(self, *, phase: int, cause: BaseException) -> None:
        self.phase = phase
        self.cause = cause
        super().__init__(
            f"Cold-path phase {phase} failed: {type(cause).__name__}: {cause}"
        )


class ColdPathQCheckEvalFailure(RuntimeError):  # noqa: N818
    """Raised when ``engine._compute_q_from_nodes()`` raises during a Q-gate.

    Distinguishes "math is broken" (this exception) from "refit produced
    a regressive result" (controlled q-gate rejection that returns
    ``ColdPathResult(accepted=False)``).  Treated as conservative rollback
    target ``cp_pre_reembed`` (unknown Q == assume regression).
    """

    def __init__(self, *, phase: int, cause: BaseException) -> None:
        self.phase = phase
        self.cause = cause
        super().__init__(
            f"Cold-path Q-check eval failed at phase {phase}: "
            f"{type(cause).__name__}: {cause}"
        )


# ---------------------------------------------------------------------------
# ADR-005 Phase 2A: project resolution for embedding index rebuild
# ---------------------------------------------------------------------------


async def _resolve_cluster_project_ids(
    db: AsyncSession,
) -> dict[str, str | None]:
    """Build cluster_id -> dominant project_id mapping from Optimization rows.

    For clusters with members from multiple projects, returns the project_id
    with the highest member count. Ties are broken by preferring non-Legacy
    projects, then by lexical project_id for determinism — matches warm
    Phase 0 and the S1 migration backfill rule.
    """
    rows = (await db.execute(
        select(
            Optimization.cluster_id,
            Optimization.project_id,
            sa_func.count().label("ct"),
        ).where(
            Optimization.cluster_id.isnot(None),
            Optimization.project_id.isnot(None),
        ).group_by(
            Optimization.cluster_id,
            Optimization.project_id,
        )
    )).all()

    label_rows = (await db.execute(
        select(PromptCluster.id, PromptCluster.label).where(
            PromptCluster.state == "project",
        )
    )).all()
    project_labels = {pid: lbl for pid, lbl in label_rows}

    buckets: dict[str, list[tuple[str, int]]] = {}
    for cluster_id, project_id, ct in rows:
        buckets.setdefault(cluster_id, []).append((project_id, int(ct)))

    resolved: dict[str, str | None] = {}
    for cid, pairs in buckets.items():
        pairs.sort(
            key=lambda x: (
                -x[1],
                0 if project_labels.get(x[0]) != "Legacy" else 1,
                x[0],
            )
        )
        resolved[cid] = pairs[0][0]
    return resolved


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ColdPathResult:
    """Return value from execute_cold_path().

    Extended from the original engine.ColdPathResult with quality-gate
    fields (q_before, q_after, accepted) that enable the caller to
    distinguish accepted refits from rolled-back ones.
    """

    snapshot_id: str
    q_before: float | None
    q_after: float | None
    accepted: bool
    nodes_created: int
    nodes_updated: int
    umap_fitted: bool
    q_system: float | None = None  # Backward compat with engine.ColdPathResult

    def __post_init__(self) -> None:
        if self.q_system is None and self.q_after is not None:
            self.q_system = self.q_after


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def execute_cold_path(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> ColdPathResult:
    """Execute the cold path — full HDBSCAN refit + UMAP + OKLab coloring.

    v0.4.16 P1a Cycle 1: concurrent invocations are serialized via
    ``_COLD_PATH_LOCK``.  Each invocation gets a fresh UUID stamped into
    ``_COLD_PATH_RUN_ID`` ContextVar for log correlation.

    Args:
        engine: The TaxonomyEngine instance (used for helper methods and state).
        db: AsyncSession — caller manages the outer lock; this function manages
            commit/rollback via the quality gate.

    Returns:
        ColdPathResult with quality metrics and acceptance status.
    """
    async with _COLD_PATH_LOCK:
        run_id = uuid.uuid4().hex
        token = _COLD_PATH_RUN_ID.set(run_id)
        try:
            logger.info("Cold path: entering refit run_id=%s", run_id)
            # v0.4.13 cycle 9 (HIGH-3, C-v4-3): cold path performs a full
            # refit via the read engine (writes flow through
            # ``WriterLockedAsyncSession`` — kept for v0.4.16 Cycle 1 as
            # defense-in-depth; retired in v0.4.16.x patch after the 7-day
            # quiet-period gate).  Toggle ``cold_path_mode`` so the
            # read-engine audit hook bypasses cleanly.  ``finally`` clears
            # the flag even under exception.
            from app.database import read_engine_meta
            read_engine_meta.cold_path_mode = True
            try:
                return await _execute_cold_path_inner(engine, db)
            finally:
                read_engine_meta.cold_path_mode = False
        finally:
            _COLD_PATH_RUN_ID.reset(token)


async def _execute_cold_path_inner(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> ColdPathResult:
    """Chunked cold-path body — 4 phases inside the ``cp_pre_reembed``
    outer SAVEPOINT, with per-phase Q-checks at Phase 1 and Phase 2.

    v0.4.16 P1a Cycle 1: replaces the pre-Cycle-1 monolithic body with a
    structured 4-phase decomposition.  Each phase runs inside its own
    ``begin_nested()`` SAVEPOINT, all of which nest inside the outer
    ``cp_pre_reembed`` anchor.  Q-gates fire at the Phase 1 and Phase 2
    boundaries (Phases 3-4 are housekeeping with no Q-impact).

    The ``phase_ctx: dict[str, Any]`` shared context carries intermediate
    state between phases:

      * ``engine`` — TaxonomyEngine instance (input)
      * ``db`` — AsyncSession (input)
      * ``run_id`` — UUID hex from ``_COLD_PATH_RUN_ID``
      * ``q_before`` — pre-refit Q baseline
      * ``saved_silhouette`` — pre-refit ``engine._last_silhouette``
      * ``short_circuit`` — Phase 1 sets True if < 3 valid centroids
      * ``valid_families``, ``embeddings``, ``all_nodes``, ... — Phase 1 outputs
      * ``cluster_result`` — HDBSCAN result from Phase 1
      * ``phase_silhouette`` — Phase 1's HDBSCAN silhouette
      * ``q_post_phase_N`` — per-phase Q after gate
      * ``q_after`` — canonical end-of-refit Q (set in Phase 2 gate)
      * ``mean_coherence``, ``separation``, ``q_health_value`` — Phase 4
        outputs feeding the post-savepoint snapshot
      * ``snapshot_id``, ``nodes_created``, ``nodes_updated``,
        ``umap_fitted`` — final result fields

    Cycle 2 will replace this dict with a frozen TypedDict-or-dataclass.

    Failure modes:
      * Q regression at Phase 1 or 2 boundary: rollback to cp_pre_reembed,
        return ``ColdPathResult(accepted=False)``.
      * Phase function raises: rollback to cp_pre_reembed, raise
        ``ColdPathPhaseFailure``.
      * Q-eval raises: rollback to cp_pre_reembed, raise
        ``ColdPathQCheckEvalFailure``.

    See ``execute_cold_path`` for the lock + audit-hook wrapper.
    """
    run_id = _COLD_PATH_RUN_ID.get() or "unknown"
    import time as _time
    _cold_t0 = _time.monotonic()

    # Capture silhouette baseline early so the baseline Q-eval failure
    # path can restore it if ``_compute_q_from_nodes`` raises before we
    # enter the SAVEPOINT scope.
    _saved_silhouette_pre_baseline = engine._last_silhouette

    # ------------------------------------------------------------------
    # Step 1: Snapshot pre-refit state for full-revert target.
    # The cp_pre_reembed SAVEPOINT is the rollback target on ANY Q-gate
    # failure, phase exception, or Q-eval exception (full revert).
    # ------------------------------------------------------------------
    q_before_result = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    q_before_nodes = list(q_before_result.scalars().all())
    try:
        q_before = engine._compute_q_from_nodes(q_before_nodes)
    except Exception as exc:
        # Same conservative-rollback semantics as a phase-boundary
        # Q-eval failure: unknown Q == assume regression.
        logger.warning(
            "Cold path: Q-eval failed at baseline run_id=%s: %s",
            run_id, exc, exc_info=True,
        )
        engine._last_silhouette = _saved_silhouette_pre_baseline
        raise ColdPathQCheckEvalFailure(phase=0, cause=exc) from exc

    # Save the silhouette baseline so we can restore on rollback.  Mutated
    # inside Phase 2; restored in the rejection / exception paths below.
    _saved_silhouette = engine._last_silhouette

    # Shared phase-context dict — mutable container threaded through the
    # phase functions so they can pass intermediate state without ballooning
    # individual signatures.  Cycle 2 will replace this with a typed
    # dataclass; Cycle 1 keeps it minimal.
    phase_ctx: dict[str, Any] = {
        "engine": engine,
        "db": db,
        "run_id": run_id,
        "q_before": q_before,
        "saved_silhouette": _saved_silhouette,
        "t0": _cold_t0,
    }

    # ``rejected_result_holder`` is a 1-element list used as a sentinel
    # marker — a non-empty list means "rejection detected, build the
    # result post-savepoint".  Typed as ``list[object]`` because the
    # element is the module-level ``_REJECTION_SENTINEL`` (an
    # ``object()``) — the actual ``ColdPathResult(accepted=False)`` is
    # materialized OUTSIDE the savepoint in ``_build_rejected_result``
    # (snapshot creation issues a commit, which would tear down the
    # parent context manager mid-flight).
    rejected_result_holder: list[object] = []
    rejection_pending: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Outer cp_pre_reembed SAVEPOINT — anchor for full-revert rollback.
    # All four phases nest inside this scope.  Q-gate failure / phase
    # exception / Q-eval exception → rollback to this anchor.
    # ------------------------------------------------------------------
    try:
        async with db.begin_nested() as cp_pre_reembed:
            # ----------------------------------------------------------
            # Phase 1: Re-embedding (HDBSCAN + match/create + parent links)
            # ----------------------------------------------------------
            try:
                async with db.begin_nested():
                    await _phase_1_reembed(phase_ctx)
            except ColdPathPhaseFailure:
                raise
            except Exception as exc:
                logger.warning(
                    "Cold path: phase 1 failed run_id=%s: %s",
                    run_id, exc, exc_info=True,
                )
                raise ColdPathPhaseFailure(phase=1, cause=exc) from exc

            await asyncio.sleep(0)  # cooperative yield

            # Q-gate after Phase 1 — re-embedding alone changed centroids,
            # so coherence/separation may have drifted.  Skip the gate
            # entirely when Phase 1 short-circuited (< 3 valid centroids
            # for HDBSCAN); the original pre-Cycle-1 code returned
            # accepted=True without running any gate, and the empty-DB
            # acceptance behavior is an explicit invariant covered by
            # tests/taxonomy/test_engine_cold_path.py.
            if phase_ctx.get("short_circuit"):
                gate_1_passed = True
            else:
                gate_1_passed = await _run_q_gate(phase_ctx, phase=1)
            if not gate_1_passed:
                # Q regression — full revert via cp_pre_reembed rollback.
                # Stash the rejection metadata; the snapshot + decision
                # event are emitted OUTSIDE the SAVEPOINT scope after
                # rollback completes (create_snapshot's db.commit()
                # would otherwise close the parent context manager
                # mid-flight).
                engine._last_silhouette = _saved_silhouette
                rejection_pending.update({
                    "q_after": phase_ctx.get("q_post_phase_1"),
                    "families": phase_ctx.get("families", []),
                    "cluster_result": phase_ctx.get("cluster_result"),
                })
                await cp_pre_reembed.rollback()
                rejected_result_holder.append(_REJECTION_SENTINEL)

            # ----------------------------------------------------------
            # Phase 2: Cluster reassignment + parent-link restoration +
            #          member migration + reconcile member_count + dpid +
            #          coherence recompute.
            # ----------------------------------------------------------
            if not rejected_result_holder:
                try:
                    async with db.begin_nested():
                        await _phase_2_reassign(phase_ctx)
                except ColdPathPhaseFailure:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Cold path: phase 2 failed run_id=%s: %s",
                        run_id, exc, exc_info=True,
                    )
                    raise ColdPathPhaseFailure(phase=2, cause=exc) from exc

                await asyncio.sleep(0)

                # Q-gate after Phase 2 — reassignment shuffled members across
                # clusters, so cross-domain assignments may have hurt
                # separation.  Same short-circuit rationale as Phase 1.
                if phase_ctx.get("short_circuit"):
                    gate_2_passed = True
                else:
                    gate_2_passed = await _run_q_gate(phase_ctx, phase=2)
                if not gate_2_passed:
                    engine._last_silhouette = _saved_silhouette
                    rejection_pending.update({
                        "q_after": phase_ctx.get("q_post_phase_2"),
                        "families": phase_ctx.get("families", []),
                        "cluster_result": phase_ctx.get("cluster_result"),
                    })
                    await cp_pre_reembed.rollback()
                    rejected_result_holder.append(_REJECTION_SENTINEL)

            # ----------------------------------------------------------
            # Phase 3: Label reconciliation (Haiku, cosmetic — no Q-impact).
            # ----------------------------------------------------------
            if not rejected_result_holder:
                try:
                    async with db.begin_nested():
                        await _phase_3_relabel(phase_ctx)
                except ColdPathPhaseFailure:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Cold path: phase 3 failed run_id=%s: %s",
                        run_id, exc, exc_info=True,
                    )
                    raise ColdPathPhaseFailure(phase=3, cause=exc) from exc

                await asyncio.sleep(0)

            # ----------------------------------------------------------
            # Phase 4: Repair — UMAP, coloring, separation, embedding-index
            # rebuild, snapshot, mega-cluster split, taxonomy_changed event.
            # ----------------------------------------------------------
            if not rejected_result_holder:
                try:
                    async with db.begin_nested():
                        await _phase_4_repair(phase_ctx)
                except ColdPathPhaseFailure:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Cold path: phase 4 failed run_id=%s: %s",
                        run_id, exc, exc_info=True,
                    )
                    raise ColdPathPhaseFailure(phase=4, cause=exc) from exc
    except ColdPathPhaseFailure:
        # Phase exception — restore silhouette baseline (state may have been
        # mutated inside the phase before the failure).  The outer SAVEPOINT
        # context manager has already rolled back via the exception.
        engine._last_silhouette = _saved_silhouette
        raise
    except ColdPathQCheckEvalFailure:
        # Conservative rollback — unknown Q == assume regression.
        engine._last_silhouette = _saved_silhouette
        raise

    if rejected_result_holder:
        # Materialize the rejection result OUTSIDE the SAVEPOINT scope so
        # ``create_snapshot``'s ``db.commit()`` can run cleanly.
        return await _build_rejected_result(
            engine=engine,
            db=db,
            q_before=q_before,
            q_after=rejection_pending.get("q_after"),
            families=rejection_pending.get("families", []),
            cluster_result=rejection_pending.get("cluster_result"),
            run_id=run_id,
        )

    # ------------------------------------------------------------------
    # Post-SAVEPOINT: snapshot creation + final commit.
    # ``create_snapshot`` issues a ``db.commit()`` which would have closed
    # every nested SAVEPOINT.  Run it here, OUTSIDE the cp_pre_reembed
    # scope, so the chunked phases above retain their rollback anchors.
    # ------------------------------------------------------------------
    if phase_ctx.get("short_circuit"):
        # Empty / sparse taxonomy — write the no-op snapshot here, in the
        # post-SAVEPOINT scope where ``db.commit()`` is safe.
        try:
            snap = await create_snapshot(
                db,
                trigger="cold_path",
                q_system=0.0,
                q_coherence=0.0,
                q_separation=0.0,
                q_coverage=0.0,
                q_health=None,
                nodes_created=0,
            )
            short_circuit_snapshot_id = snap.id
        except Exception as snap_exc:
            logger.warning(
                "Cold path: short-circuit snapshot persistence failed (non-fatal) run_id=%s: %s",
                run_id, snap_exc,
            )
            short_circuit_snapshot_id = ""
        return ColdPathResult(
            snapshot_id=short_circuit_snapshot_id,
            q_before=q_before,
            q_after=phase_ctx.get("q_after", 0.0),
            accepted=True,
            nodes_created=phase_ctx.get("nodes_created", 0),
            nodes_updated=phase_ctx.get("nodes_updated", 0),
            umap_fitted=phase_ctx.get("umap_fitted", False),
        )

    snapshot_id = phase_ctx.get("snapshot_id", "")
    q_after_value = phase_ctx.get("q_after")
    if not snapshot_id and "mean_coherence" in phase_ctx:
        # Phase 4 populated the metrics; create the snapshot now.
        try:
            snap = await create_snapshot(
                db,
                trigger="cold_path",
                q_system=q_after_value,
                q_coherence=phase_ctx.get("mean_coherence", 0.0),
                q_separation=phase_ctx.get("separation", 0.0),
                q_coverage=1.0,
                q_health=phase_ctx.get("q_health_value"),
                nodes_created=phase_ctx.get("nodes_created", 0),
            )
            snapshot_id = snap.id
        except Exception as snap_exc:
            logger.warning(
                "Cold path: snapshot persistence failed (non-fatal) run_id=%s: %s",
                run_id, snap_exc,
            )

    return ColdPathResult(
        snapshot_id=snapshot_id,
        q_before=q_before,
        q_after=q_after_value,
        accepted=True,
        nodes_created=phase_ctx.get("nodes_created", 0),
        nodes_updated=phase_ctx.get("nodes_updated", 0),
        umap_fitted=phase_ctx.get("umap_fitted", False),
    )


# ---------------------------------------------------------------------------
# Q-gate helper — wraps engine._compute_q_from_nodes + the typed eval-failure
# exception path
# ---------------------------------------------------------------------------


async def _run_q_gate(phase_ctx: dict[str, Any], *, phase: int) -> bool:
    """Run the per-phase Q-gate. Returns True if non-regressive, False if
    regression detected. Raises ``ColdPathQCheckEvalFailure`` if
    ``_compute_q_from_nodes`` itself raises (conservative rollback target).

    v0.4.16 P1a Cycle 1: introduced as the per-phase variant of the
    pre-Cycle-1 single end-of-refit Q-gate.  Threads ``phase=N`` to
    ``is_cold_path_non_regressive`` so observability + test injection
    can distinguish the post-re-embed gate from the post-reassign gate.

    The active-set Q is recomputed from a live DB query (not relying on a
    phase_ctx-cached value) so a Q-gate fires even when phase functions
    are mocked in unit tests — the test sees ``is_cold_path_non_regressive``
    invoked exactly N times with ``phase=N``.
    """
    engine = phase_ctx["engine"]
    db = phase_ctx["db"]
    run_id = phase_ctx.get("run_id", "unknown")
    q_before = phase_ctx["q_before"]

    # Live Q computation — picks up whatever the phase actually mutated
    # (or didn't, if the phase was mocked to a no-op).  Phase 1 short-
    # circuit (< 3 valid embeddings) provides a populated active set
    # via the live DB regardless.
    active_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
        )
    )
    active_nodes = list(active_q.scalars().all())
    silhouette = phase_ctx.get("phase_silhouette") or 0.0

    try:
        q_after = engine._compute_q_from_nodes(active_nodes, silhouette=silhouette)
    except Exception as exc:
        logger.warning(
            "Cold path: Q-eval failed at phase %d run_id=%s: %s",
            phase, run_id, exc, exc_info=True,
        )
        raise ColdPathQCheckEvalFailure(phase=phase, cause=exc) from exc

    phase_ctx[f"q_post_phase_{phase}"] = q_after
    if phase == 2:
        # Phase 2's Q is the canonical "Q_after" for the snapshot.
        phase_ctx["q_after"] = q_after

    accepted = is_cold_path_non_regressive(q_before, q_after, phase=phase)
    if not accepted:
        logger.warning(
            "Cold path: Q regression at phase %d run_id=%s: "
            "q_before=%s q_after=%s epsilon=%s",
            phase, run_id, q_before, q_after, COLD_PATH_EPSILON,
        )
    return accepted


# ---------------------------------------------------------------------------
# Phase 1 — Re-embedding (HDBSCAN clustering + match/create + parent links)
# ---------------------------------------------------------------------------


async def _phase_1_reembed(phase_ctx: dict[str, Any]) -> None:
    """Phase 1: re-embed cluster centroids via blended-embedding HDBSCAN,
    match against existing nodes, create new nodes for unmatched groups,
    link family clusters to their new parents.

    v0.4.16 P1a Cycle 1 boundary: corresponds to Steps 1-12 of the
    original ``_execute_cold_path_inner``. Q-impact = HIGH (bad centroids
    hurt coherence).
    """
    engine = phase_ctx["engine"]
    db = phase_ctx["db"]
    run_id = phase_ctx.get("run_id", "unknown")
    import time as _time
    _t1 = _time.monotonic()

    # Step 2: Load non-domain, non-archived clusters for HDBSCAN input
    fam_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
        )
    )
    families = list(fam_result.scalars().all())
    phase_ctx["families"] = families

    # Step 3: Extract valid centroid embeddings
    embeddings: list[np.ndarray] = []
    valid_families: list[PromptCluster] = []
    family_by_id: dict[str, PromptCluster] = {}
    for f in families:
        try:
            emb = np.frombuffer(f.centroid_embedding, dtype=np.float32)
            embeddings.append(emb)
            valid_families.append(f)
            family_by_id[f.id] = f
        except (ValueError, TypeError):
            logger.warning(
                "Skipping cluster '%s' run_id=%s -- corrupt centroid",
                f.label, run_id,
            )

    # Step 4: Early return if < 3 valid embeddings — short-circuit the
    # entire refit.  Phase 1 stamps the no-op result into phase_ctx so
    # phases 2-4 know to noop. The no-op snapshot is created OUTSIDE
    # the SAVEPOINT scope by ``_execute_cold_path_inner`` after all
    # phases exit cleanly (create_snapshot's db.commit() would
    # otherwise close the parent context manager mid-flight).
    if len(embeddings) < 3:
        phase_ctx["q_after"] = 0.0
        phase_ctx["nodes_created"] = 0
        phase_ctx["nodes_updated"] = 0
        phase_ctx["umap_fitted"] = False
        phase_ctx["short_circuit"] = True
        # Empty active set — Q-gate on empty set returns True (None → None
        # transition is "no change"); but to keep the Q-gate signature
        # called twice we still populate active_after_phase_*.
        phase_ctx["active_after_phase_1"] = []
        phase_ctx["active_after_phase_2"] = []
        phase_ctx["phase_silhouette"] = None
        return

    logger.info(
        "Cold path: Steps 1-4 (load+validate) %.1fs run_id=%s",
        _time.monotonic() - _t1, run_id,
    )
    _t2 = _time.monotonic()

    # Step 4b: Blend embeddings for multi-signal HDBSCAN
    blended_embeddings: list[np.ndarray] = []
    opt_idx = getattr(engine, "_optimized_index", None)
    trans_idx = getattr(engine, "_transformation_index", None)
    qual_idx = getattr(engine, "_qualifier_index", None)
    for i, f in enumerate(valid_families):
        opt_vec = opt_idx.get_vector(f.id) if opt_idx else None
        trans_vec = trans_idx.get_vector(f.id) if trans_idx else None
        qual_vec = qual_idx.get_vector(f.id) if qual_idx else None

        w_opt = CLUSTERING_BLEND_W_OPTIMIZED
        out_coh = read_meta(f.cluster_metadata).get("output_coherence")
        if out_coh is not None and out_coh < 0.5:
            w_opt = CLUSTERING_BLEND_W_OPTIMIZED * max(0.25, out_coh / 0.5)
        w_raw = 1.0 - w_opt - CLUSTERING_BLEND_W_TRANSFORM - CLUSTERING_BLEND_W_QUALIFIER

        blended_embeddings.append(
            blend_embeddings(
                raw=embeddings[i],
                optimized=opt_vec,
                transformation=trans_vec,
                w_raw=w_raw,
                w_optimized=w_opt,
                w_transform=CLUSTERING_BLEND_W_TRANSFORM,
                qualifier=qual_vec,
            )
        )

    logger.info(
        "Cold path: Step 4b (blend) %.1fs run_id=%s",
        _time.monotonic() - _t2, run_id,
    )
    _t3 = _time.monotonic()

    # Step 5: HDBSCAN clustering on blended embeddings
    cluster_result = batch_cluster(blended_embeddings, min_cluster_size=3)
    _hdbscan_ms = int((_time.monotonic() - _t3) * 1000)
    logger.info(
        "Cold path: Step 5 (HDBSCAN) %.1fs run_id=%s — %d clusters, %d noise",
        _hdbscan_ms / 1000, run_id, cluster_result.n_clusters,
        cluster_result.noise_count,
    )
    try:
        get_event_logger().log_decision(
            path="cold", op="hdbscan", decision="complete",
            context={
                "clusters_found": cluster_result.n_clusters,
                "noise_count": cluster_result.noise_count,
                "input_nodes": len(blended_embeddings),
                "noise_pct": round(cluster_result.noise_count / max(len(blended_embeddings), 1) * 100, 1),
                "silhouette": round(cluster_result.silhouette, 4),
                "duration_ms": _hdbscan_ms,
            },
        )
    except RuntimeError:
        pass

    if cluster_result.n_clusters == 0:
        logger.info(
            "Cold path: HDBSCAN found 0 clusters (%d points, all noise) run_id=%s — "
            "skipping cluster creation, proceeding to reconciliation",
            len(blended_embeddings), run_id,
        )
        try:
            get_event_logger().log_decision(
                path="cold", op="refit", decision="zero_clusters",
                context={
                    "total_points": len(blended_embeddings),
                    "noise_count": cluster_result.noise_count,
                    "min_cluster_size": 3,
                },
            )
        except RuntimeError:
            pass

    _t4 = _time.monotonic()

    # Step 6: Load existing nodes for matching
    existing_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
        )
    )
    existing_nodes = {n.id: n for n in existing_result.scalars().all()}

    node_embeddings: list[np.ndarray] = []
    node_umap_embeddings: list[np.ndarray] = []
    all_nodes: list[PromptCluster] = []
    nodes_created = 0
    nodes_updated = 0

    # Steps 7-10: Process each HDBSCAN cluster
    for label_idx in range(cluster_result.n_clusters):
        mask = cluster_result.labels == label_idx
        cluster_fam_ids = [
            valid_families[i].id for i in range(len(valid_families)) if mask[i]
        ]
        cluster_embs = [
            embeddings[i] for i in range(len(embeddings)) if mask[i]
        ]

        if not cluster_embs:
            continue

        centroid = l2_normalize_1d(
            np.mean(np.stack(cluster_embs, axis=0), axis=0).astype(np.float32)
        )

        coherence = compute_pairwise_coherence(cluster_embs)

        # Step 7: Try to match existing node by cosine >= adaptive threshold
        matched_node = None
        if existing_nodes:
            best_match_id = None
            best_sim = -1.0
            for nid, existing in existing_nodes.items():
                try:
                    ex_emb = np.frombuffer(
                        existing.centroid_embedding, dtype=np.float32,
                    )
                    sim = cosine_similarity(centroid, ex_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_match_id = nid
                except (ValueError, TypeError) as _ex_exc:
                    logger.warning(
                        "Corrupt existing node centroid in cold refit matching, node='%s' run_id=%s: %s",
                        existing.label, run_id, _ex_exc,
                    )
                    continue

            if best_match_id:
                candidate = existing_nodes[best_match_id]
                cold_threshold = adaptive_merge_threshold(
                    candidate.member_count or 1,
                )
                if best_sim >= cold_threshold:
                    matched_node = existing_nodes.pop(best_match_id)

        if matched_node:
            # Step 8: Update existing node — preserve higher lifecycle states
            matched_node.centroid_embedding = centroid.astype(
                np.float32
            ).tobytes()
            matched_node.coherence = coherence
            if matched_node.state == "candidate":
                matched_node.state = "active"

            meta = read_meta(matched_node.cluster_metadata)
            if meta.get("split_failures", 0) > 0:
                matched_node.cluster_metadata = write_meta(
                    matched_node.cluster_metadata,
                    split_failures=0,
                )

            nodes_updated += 1
            node = matched_node
        else:
            # Step 9: Create new node
            member_texts = [
                f.label
                for f in valid_families
                if f.id in set(cluster_fam_ids) and f.label
            ]
            label = await generate_label(
                provider=engine._provider,
                member_texts=member_texts,
                model=settings.MODEL_HAIKU,
            )
            node = PromptCluster(
                label=label,
                centroid_embedding=centroid.astype(np.float32).tobytes(),
                member_count=0,
                coherence=coherence,
                state="active",
                color_hex=generate_color(0.0, 0.0, 0.0),
            )
            db.add(node)
            await db.flush()
            nodes_created += 1

        # Step 10: Link families to this node (skip self-references)
        for fid in cluster_fam_ids:
            fam = family_by_id.get(fid)
            if fam and fam.id != node.id:
                fam.parent_id = node.id

        node_embeddings.append(centroid)
        if label_idx < len(cluster_result.centroids):
            node_umap_embeddings.append(cluster_result.centroids[label_idx])
        else:
            node_umap_embeddings.append(centroid)
        all_nodes.append(node)

    logger.info(
        "Cold path: Steps 6-10 (match/create) %.1fs run_id=%s — created=%d updated=%d",
        _time.monotonic() - _t4, run_id, nodes_created, nodes_updated,
    )

    # Step 11: Include leftover unmatched nodes for UMAP coordinates
    for leftover_node in existing_nodes.values():
        if leftover_node.centroid_embedding:
            try:
                emb = np.frombuffer(
                    leftover_node.centroid_embedding, dtype=np.float32,
                ).copy()
                node_embeddings.append(emb)
                opt_vec = opt_idx.get_vector(leftover_node.id) if opt_idx else None
                trans_vec = trans_idx.get_vector(leftover_node.id) if trans_idx else None
                qual_vec = qual_idx.get_vector(leftover_node.id) if qual_idx else None
                node_umap_embeddings.append(
                    blend_embeddings(raw=emb, optimized=opt_vec, transformation=trans_vec,
                                     qualifier=qual_vec)
                )
                all_nodes.append(leftover_node)
            except (ValueError, TypeError) as _lo_exc:
                logger.warning(
                    "Corrupt leftover node centroid in cold refit, node='%s' run_id=%s: %s",
                    leftover_node.label, run_id, _lo_exc,
                )

    # Stash phase 1 outputs for phase 2 + Q-gate.
    phase_ctx["valid_families"] = valid_families
    phase_ctx["family_by_id"] = family_by_id
    phase_ctx["embeddings"] = embeddings
    phase_ctx["all_nodes"] = all_nodes
    phase_ctx["node_embeddings"] = node_embeddings
    phase_ctx["node_umap_embeddings"] = node_umap_embeddings
    phase_ctx["nodes_created"] = nodes_created
    phase_ctx["nodes_updated"] = nodes_updated
    phase_ctx["cluster_result"] = cluster_result
    phase_ctx["phase_silhouette"] = cluster_result.silhouette

    # Active set for Q-gate: re-load post-Phase-1 (matches the original
    # end-of-refit Q computation that read active_after).
    active_q_1 = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    phase_ctx["active_after_phase_1"] = list(active_q_1.scalars().all())


# ---------------------------------------------------------------------------
# Phase 2 — Cluster reassignment + parent links + member migration + reconcile
# ---------------------------------------------------------------------------


async def _phase_2_reassign(phase_ctx: dict[str, Any]) -> None:
    """Phase 2: restore domain->cluster parent_id links, reconcile
    member_count / avg_score / dominant_project_id / weighted_member_sum,
    recompute per-member coherence.

    v0.4.16 P1a Cycle 1 boundary: corresponds to Steps 12-16 of the
    original ``_execute_cold_path_inner``. Q-impact = HIGH (cross-domain
    assignments hurt separation).
    """
    if phase_ctx.get("short_circuit"):
        # Phase 1 short-circuited (< 3 valid embeddings) — Phase 2 is a no-op.
        return

    db = phase_ctx["db"]
    run_id = phase_ctx.get("run_id", "unknown")
    all_nodes: list[PromptCluster] = phase_ctx["all_nodes"]
    import time as _time
    _t = _time.monotonic()

    # Step 12: Restore domain->cluster parent_id links
    domain_node_map: dict[str, str] = {}
    domain_id_to_label: dict[str, str] = {}
    domain_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "domain")
    )
    all_domain_nodes_cold = list(domain_q.scalars().all())
    for dn in all_domain_nodes_cold:
        domain_node_map[dn.label] = dn.id
        domain_id_to_label[dn.id] = dn.label

    sub_domain_ids_by_domain: dict[str, set[str]] = {}
    for dn in all_domain_nodes_cold:
        if dn.parent_id and dn.parent_id in domain_id_to_label:
            parent_label = domain_id_to_label[dn.parent_id]
            sub_domain_ids_by_domain.setdefault(parent_label, set()).add(dn.id)

    parent_repairs = 0
    for node in all_nodes:
        if node.state == "domain":
            continue
        correct_parent = domain_node_map.get(node.domain)
        if not correct_parent:
            general_id = domain_node_map.get("general")
            if general_id and node.parent_id != general_id:
                node.parent_id = general_id
                parent_repairs += 1
            continue
        valid_subs = sub_domain_ids_by_domain.get(node.domain, set())
        if node.parent_id in valid_subs:
            continue
        if node.parent_id != correct_parent:
            node.parent_id = correct_parent
            parent_repairs += 1
    if parent_repairs:
        logger.info(
            "Cold path: repaired %d parent_id links to domain nodes run_id=%s",
            parent_repairs, run_id,
        )
        try:
            get_event_logger().log_decision(
                path="cold", op="reconcile", decision="parent_repaired",
                context={
                    "parent_repairs": parent_repairs,
                    "total_nodes": len(all_nodes),
                },
            )
        except RuntimeError:
            pass

    phase_ctx["domain_node_map"] = domain_node_map

    # Step 13: Reconcile member_count from actual Optimization rows
    count_q = await db.execute(
        select(Optimization.cluster_id, sa_func.count().label("ct"))
        .where(Optimization.cluster_id.isnot(None))
        .group_by(Optimization.cluster_id)
    )
    actual_counts: dict[str, int] = dict(count_q.all())

    # Step 14: Reconcile avg_score and scored_count
    score_q = await db.execute(
        select(
            Optimization.cluster_id,
            sa_func.avg(Optimization.overall_score),
            sa_func.count(Optimization.overall_score),
        )
        .where(
            Optimization.cluster_id.isnot(None),
            Optimization.overall_score.isnot(None),
        )
        .group_by(Optimization.cluster_id)
    )
    score_map = {row[0]: (round(row[1], 2), row[2]) for row in score_q.all()}

    mc_repairs = 0
    for node in all_nodes:
        expected = actual_counts.get(node.id, 0)
        if node.member_count != expected:
            node.member_count = expected
            mc_repairs += 1
        avg, scored = score_map.get(node.id, (None, 0))
        node.avg_score = avg
        node.scored_count = scored

    phase_ctx["actual_counts"] = actual_counts

    # Step 14.5: Reconcile dominant_project_id
    dominant_map = await _resolve_cluster_project_ids(db)
    dpid_repairs = 0
    for node in all_nodes:
        if node.state in EXCLUDED_STRUCTURAL_STATES:
            if node.dominant_project_id is not None:
                node.dominant_project_id = None
                dpid_repairs += 1
            continue
        new_dpid = dominant_map.get(node.id)
        if node.dominant_project_id != new_dpid:
            node.dominant_project_id = new_dpid
            dpid_repairs += 1
    if dpid_repairs:
        logger.info(
            "Cold path: reconciled %d dominant_project_id values run_id=%s",
            dpid_repairs, run_id,
        )

    # Step 15: Reconcile domain node member_counts (child cluster count)
    for dn_label, dn_id in domain_node_map.items():
        dn_q = await db.execute(
            select(PromptCluster).where(PromptCluster.id == dn_id)
        )
        dn_opt = dn_q.scalar_one_or_none()
        if dn_opt is not None:
            dn = dn_opt
            child_count = (await db.execute(
                select(sa_func.count()).where(
                    PromptCluster.domain == dn_label,
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
            )).scalar() or 0
            dn.member_count = child_count

    # Recompute weighted_member_sum from per-member scores
    wms_q = await db.execute(
        select(
            Optimization.cluster_id,
            Optimization.overall_score,
        ).where(
            Optimization.cluster_id.isnot(None),
        )
    )
    wms_by_cluster: dict[str, float] = {}
    for cid, opt_score in wms_q.all():
        wms_by_cluster[cid] = wms_by_cluster.get(cid, 0.0) + score_to_centroid_weight(opt_score)
    for node in all_nodes:
        if node.id in wms_by_cluster:
            node.weighted_member_sum = wms_by_cluster[node.id]

    if mc_repairs:
        logger.info(
            "Cold path: reconciled %d member_counts from Optimization rows run_id=%s",
            mc_repairs, run_id,
        )

    # Cluster detail logging
    try:
        large_clusters = [
            n for n in all_nodes
            if n.state not in EXCLUDED_STRUCTURAL_STATES and (n.member_count or 0) >= 5
        ]
        if large_clusters:
            get_event_logger().log_decision(
                path="cold", op="refit", decision="cluster_detail",
                context={
                    "clusters": [
                        {
                            "id": n.id,
                            "label": n.label,
                            "domain": n.domain,
                            "member_count": n.member_count or 0,
                            "avg_score": round(n.avg_score, 2) if n.avg_score else None,
                            "coherence": round(n.coherence, 4) if n.coherence is not None else None,
                        }
                        for n in sorted(large_clusters, key=lambda x: -(x.member_count or 0))
                    ],
                    "total_nodes": len(all_nodes),
                    "total_optimizations": sum(actual_counts.values()),
                },
            )
    except RuntimeError:
        pass

    logger.info(
        "Cold path: Steps 11-15 (reconcile) %.1fs run_id=%s",
        _time.monotonic() - _t, run_id,
    )

    # Step 16: Recompute per-member coherence from optimization embeddings
    _t5 = _time.monotonic()
    all_opt_emb_q = await db.execute(
        select(Optimization.cluster_id, Optimization.embedding).where(
            Optimization.cluster_id.isnot(None),
            Optimization.embedding.isnot(None),
        )
    )
    cold_emb_by_cluster: dict[str, list[np.ndarray]] = {}
    for cid, emb_bytes in all_opt_emb_q.all():
        if emb_bytes is not None:
            try:
                cold_emb_by_cluster.setdefault(cid, []).append(
                    np.frombuffer(emb_bytes, dtype=np.float32).copy()
                )
            except (ValueError, TypeError) as _ce_exc:
                logger.warning(
                    "Corrupt embedding in cold coherence recomputation, cluster=%s run_id=%s: %s",
                    cid, run_id, _ce_exc,
                )

    coherence_repairs = 0
    for node in all_nodes:
        member_embs = cold_emb_by_cluster.get(node.id, [])
        if len(member_embs) >= 2:
            node.coherence = compute_pairwise_coherence(member_embs)
            coherence_repairs += 1
        elif len(member_embs) == 1:
            node.coherence = 1.0

    if coherence_repairs:
        logger.info(
            "Cold path: recomputed %d per-member coherence values run_id=%s",
            coherence_repairs, run_id,
        )
    await db.flush()

    logger.info(
        "Cold path: Step 16 (coherence recompute) %.1fs run_id=%s",
        _time.monotonic() - _t5, run_id,
    )

    # Active set for Phase 2 Q-gate.
    active_q_2 = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    phase_ctx["active_after_phase_2"] = list(active_q_2.scalars().all())


# ---------------------------------------------------------------------------
# Phase 3 — Label reconciliation (Haiku, cosmetic — no Q-impact)
# ---------------------------------------------------------------------------


async def _phase_3_relabel(phase_ctx: dict[str, Any]) -> None:
    """Phase 3: Haiku label regeneration. Cosmetic; no Q-impact.

    v0.4.16 P1a Cycle 1 boundary: corresponds to Step 19 of the original
    ``_execute_cold_path_inner``. Cycle 1 keeps this minimal — the
    original code already generated labels inline during Phase 1's node
    creation path; Phase 3 is reserved for future bulk-relabel Haiku
    work that will be wired in Cycle 2.

    The phase still issues a no-op DB read so SQLAlchemy actually emits
    the surrounding ``SAVEPOINT`` (lazy-emit behavior — without a real
    DB op the nested transaction context is optimized away, breaking
    the 5-SAVEPOINT-per-refit invariant).
    """
    if phase_ctx.get("short_circuit"):
        return
    db = phase_ctx["db"]
    # No-op SELECT to materialize the SAVEPOINT.  Cycle 2 replaces this
    # with the real bulk-relabel pass.
    await db.execute(select(sa_func.count()).select_from(PromptCluster))
    return


# ---------------------------------------------------------------------------
# Phase 4 — Repair: UMAP, coloring, separation, embedding-index rebuild,
#                   snapshot, mega-cluster split, taxonomy_changed event.
# ---------------------------------------------------------------------------


async def _phase_4_repair(phase_ctx: dict[str, Any]) -> None:
    """Phase 4: UMAP 3D projection + Procrustes alignment + OKLab coloring +
    per-node separation + embedding-index rebuild + snapshot creation +
    mega-cluster split + taxonomy_changed event publish.

    v0.4.16 P1a Cycle 1 boundary: corresponds to Steps 17-26 of the
    original ``_execute_cold_path_inner``. Q-impact = NONE (housekeeping).
    """
    if phase_ctx.get("short_circuit"):
        return

    engine = phase_ctx["engine"]
    db = phase_ctx["db"]
    run_id = phase_ctx.get("run_id", "unknown")
    all_nodes: list[PromptCluster] = phase_ctx["all_nodes"]
    node_umap_embeddings: list[np.ndarray] = phase_ctx["node_umap_embeddings"]
    cluster_result = phase_ctx["cluster_result"]
    nodes_created: int = phase_ctx["nodes_created"]
    nodes_updated: int = phase_ctx["nodes_updated"]
    families = phase_ctx.get("families", [])
    q_before = phase_ctx["q_before"]
    import time as _time
    _t6 = _time.monotonic()

    # Step 17-18: UMAP 3D projection with Procrustes alignment
    umap_fitted = False
    if node_umap_embeddings:
        projector = UMAPProjector()
        positions = projector.fit(node_umap_embeddings)

        old_positions = []
        has_old = True
        for node in all_nodes:
            if (
                node.umap_x is not None
                and node.umap_y is not None
                and node.umap_z is not None
            ):
                old_positions.append(
                    [node.umap_x, node.umap_y, node.umap_z]
                )
            else:
                has_old = False
                break

        if has_old and len(old_positions) == len(positions):
            old_arr = np.array(old_positions, dtype=np.float64)
            positions = procrustes_align(positions, old_arr)

        for i, node in enumerate(all_nodes):
            if i < len(positions):
                node.umap_x = float(positions[i, 0])
                node.umap_y = float(positions[i, 1])
                node.umap_z = float(positions[i, 2])
        umap_fitted = True

        try:
            domain_umap_q = await db.execute(
                select(PromptCluster).where(PromptCluster.state == "domain")
            )
            for dnode in domain_umap_q.scalars().all():
                await engine._set_domain_umap_from_children(db, dnode)
        except Exception as dom_umap_exc:
            logger.warning(
                "Domain UMAP centroid failed (non-fatal) run_id=%s: %s",
                run_id, dom_umap_exc,
            )

    _umap_ms = int((_time.monotonic() - _t6) * 1000)
    logger.info("Cold path: Step 17-18 (UMAP) %.1fs run_id=%s", _umap_ms / 1000, run_id)
    try:
        get_event_logger().log_decision(
            path="cold", op="umap", decision="projection_complete",
            context={
                "nodes_projected": sum(1 for n in all_nodes if n.umap_x is not None),
                "total_nodes": len(all_nodes),
                "duration_ms": _umap_ms,
            },
        )
    except RuntimeError:
        pass

    _t7 = _time.monotonic()
    # Step 19: OKLab coloring with minimum deltaE (skip domain nodes)
    color_pairs: list[tuple[str, str]] = []
    for node in all_nodes:
        if node.state == "domain":
            continue
        if (
            node.umap_x is not None
            and node.umap_y is not None
            and node.umap_z is not None
        ):
            new_color = generate_color(node.umap_x, node.umap_y, node.umap_z)
            color_pairs.append((node.id, new_color))

    if color_pairs:
        enforced = enforce_minimum_delta_e(color_pairs)
        node_by_id = {n.id: n for n in all_nodes}
        for node_id, color_hex in enforced:
            if node_id in node_by_id:
                node_by_id[node_id].color_hex = color_hex

    # Step 20: Compute per-node separation
    active_result = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
    )
    active_after = list(active_result.scalars().all())

    engine._update_per_node_separation(active_after)

    logger.info(
        "Cold path: Steps 19-20 (color+separation) %.1fs run_id=%s",
        _time.monotonic() - _t7, run_id,
    )

    # Step 21: Persist final Q + silhouette
    q_after = engine._compute_q_from_nodes(active_after, silhouette=cluster_result.silhouette)
    engine._last_silhouette = cluster_result.silhouette

    # Aggregate metrics for snapshot
    mean_coherence, separation = engine._snapshot_metrics(active_after)

    # Rebuild embedding index from active centroids
    index_centroids: dict[str, np.ndarray] = {}
    for n in active_after:
        try:
            emb = np.frombuffer(n.centroid_embedding, dtype=np.float32)
            if emb.shape[0] == 384:
                index_centroids[n.id] = emb
        except (ValueError, TypeError) as _idx_exc:
            logger.warning(
                "Corrupt centroid in embedding index rebuild, node='%s' run_id=%s: %s",
                n.label, run_id, _idx_exc,
            )
            continue
    try:
        cluster_project_ids = await _resolve_cluster_project_ids(db)
        await engine._embedding_index.rebuild(index_centroids, project_ids=cluster_project_ids)
        logger.info(
            "Taxonomy embedding index loaded with %d vectors run_id=%s",
            engine._embedding_index.size, run_id,
        )
        engine._cluster_project_cache = {k: v for k, v in cluster_project_ids.items() if v is not None}
    except Exception as rebuild_exc:
        logger.warning(
            "EmbeddingIndex rebuild failed (non-fatal) run_id=%s: %s",
            run_id, rebuild_exc,
        )

    try:
        await engine._embedding_index.save_cache(
            DATA_DIR / "embedding_index.pkl"
        )
    except Exception as cache_exc:
        logger.warning(
            "EmbeddingIndex cache save failed (non-fatal) run_id=%s: %s",
            run_id, cache_exc,
        )

    # Rebuild TransformationIndex from cluster mean transformation vectors
    try:
        ti_q = await db.execute(
            select(
                Optimization.cluster_id,
                Optimization.transformation_embedding,
            ).where(
                Optimization.cluster_id.isnot(None),
                Optimization.transformation_embedding.isnot(None),
            )
        )
        cluster_transforms: dict[str, list[np.ndarray]] = {}
        for cid, t_bytes in ti_q.all():
            try:
                cluster_transforms.setdefault(cid, []).append(
                    np.frombuffer(t_bytes, dtype=np.float32).copy()
                )
            except (ValueError, TypeError) as _ti_exc:
                logger.warning(
                    "Corrupt transformation embedding in cold index rebuild, cluster=%s run_id=%s: %s",
                    cid, run_id, _ti_exc,
                )
                continue

        active_ids = {n.id for n in active_after}
        transform_vectors: dict[str, np.ndarray] = {}
        for cid, vecs in cluster_transforms.items():
            if cid in active_ids and vecs:
                mean_vec = np.mean(np.stack(vecs), axis=0)
                norm = np.linalg.norm(mean_vec)
                if norm > 1e-9:
                    transform_vectors[cid] = (mean_vec / norm).astype(np.float32)
        await engine._transformation_index.rebuild(transform_vectors)
        logger.info(
            "TransformationIndex rebuilt with %d vectors after cold path run_id=%s",
            len(transform_vectors), run_id,
        )
    except Exception as ti_exc:
        logger.warning(
            "TransformationIndex rebuild failed (non-fatal) run_id=%s: %s",
            run_id, ti_exc,
        )

    try:
        await engine._transformation_index.save_cache(
            DATA_DIR / "transformation_index.pkl"
        )
    except Exception as ti_cache_exc:
        logger.warning(
            "TransformationIndex cache save failed (non-fatal) run_id=%s: %s",
            run_id, ti_cache_exc,
        )

    # Rebuild OptimizedEmbeddingIndex
    try:
        oi_q = await db.execute(
            select(
                Optimization.cluster_id,
                Optimization.optimized_embedding,
            ).where(
                Optimization.cluster_id.isnot(None),
                Optimization.optimized_embedding.isnot(None),
            )
        )
        cluster_opt_embs: dict[str, list[np.ndarray]] = {}
        for cid, o_bytes in oi_q.all():
            try:
                cluster_opt_embs.setdefault(cid, []).append(
                    np.frombuffer(o_bytes, dtype=np.float32).copy()
                )
            except (ValueError, TypeError) as _oi_exc:
                logger.warning(
                    "Corrupt optimized embedding in cold index rebuild, cluster=%s run_id=%s: %s",
                    cid, run_id, _oi_exc,
                )
                continue

        active_ids_oi = {n.id for n in active_after}
        optimized_vectors: dict[str, np.ndarray] = {}
        for cid, vecs in cluster_opt_embs.items():
            if cid in active_ids_oi and vecs:
                mean_vec = np.mean(np.stack(vecs), axis=0)
                norm = np.linalg.norm(mean_vec)
                if norm > 1e-9:
                    optimized_vectors[cid] = (mean_vec / norm).astype(np.float32)
        await engine._optimized_index.rebuild(optimized_vectors)
        logger.info(
            "OptimizedEmbeddingIndex rebuilt with %d vectors after cold path run_id=%s",
            len(optimized_vectors), run_id,
        )
    except Exception as oi_exc:
        logger.warning(
            "OptimizedEmbeddingIndex rebuild failed (non-fatal) run_id=%s: %s",
            run_id, oi_exc,
        )

    try:
        await engine._optimized_index.save_cache(
            DATA_DIR / "optimized_index.pkl"
        )
    except Exception as oi_cache_exc:
        logger.warning(
            "OptimizedEmbeddingIndex cache save failed (non-fatal) run_id=%s: %s",
            run_id, oi_cache_exc,
        )

    try:
        await engine._qualifier_index.save_cache(
            DATA_DIR / "qualifier_index.pkl"
        )
    except Exception as qi_cache_exc:
        logger.warning(
            "QualifierIndex cache save failed (non-fatal) run_id=%s: %s",
            run_id, qi_cache_exc,
        )

    # Snapshot creation is deferred to outside the outer SAVEPOINT scope
    # (see _execute_cold_path_inner — ``create_snapshot`` issues a
    # ``db.commit()`` which would close every SAVEPOINT below it).  Phase 4
    # stashes the metrics into ``phase_ctx`` for the post-savepoint commit.
    engine._invalidate_stats_cache()

    _cold_q_health = None
    try:
        _cold_health = engine._compute_q_health_from_nodes(
            active_after, silhouette=cluster_result.silhouette,
        )
        _cold_q_health = _cold_health.q_health
    except Exception as _qh_exc:
        logger.warning(
            "q_health computation failed in cold snapshot run_id=%s: %s",
            run_id, _qh_exc,
        )

    phase_ctx["mean_coherence"] = mean_coherence
    phase_ctx["separation"] = separation
    phase_ctx["q_health_value"] = _cold_q_health

    # Step 25: Mega-cluster split pass
    mega_split_created = 0
    try:
        mega_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                PromptCluster.member_count >= MEGA_CLUSTER_MEMBER_FLOOR,
                PromptCluster.coherence < SPLIT_COHERENCE_FLOOR,
            )
        )
        mega_clusters = list(mega_q.scalars().all())

        if mega_clusters:
            from app.services.taxonomy.split import split_cluster

            logger.info(
                "Mega-cluster split pass: %d candidates detected run_id=%s",
                len(mega_clusters), run_id,
            )
            try:
                get_event_logger().log_decision(
                    path="cold", op="split", decision="mega_clusters_detected",
                    context={
                        "mega_cluster_ids": [mc.id for mc in mega_clusters],
                        "member_counts": [mc.member_count or 0 for mc in mega_clusters],
                        "coherences": [round(mc.coherence or 0.0, 4) for mc in mega_clusters],
                    },
                )
            except RuntimeError:
                pass

            for mc in mega_clusters:
                mc_opt_q = await db.execute(
                    select(
                        Optimization.id,
                        Optimization.embedding,
                        Optimization.optimized_embedding,
                        Optimization.transformation_embedding,
                        Optimization.qualifier_embedding,
                    ).where(
                        Optimization.cluster_id == mc.id,
                        Optimization.embedding.isnot(None),
                    )
                )
                mc_opt_rows: list[tuple[str, bytes, bytes | None, bytes | None, bytes | None]] = [
                    (r[0], r[1], r[2], r[3], r[4]) for r in mc_opt_q.all()
                ]

                if len(mc_opt_rows) < MEGA_CLUSTER_MEMBER_FLOOR:
                    continue

                mc_result = await split_cluster(mc, engine, db, mc_opt_rows, log_path="cold")

                if mc_result.success:
                    mc.cluster_metadata = write_meta(
                        mc.cluster_metadata,
                        split_failures=0,
                        split_attempt_member_count=0,
                        split_content_hash="",
                    )
                    mega_split_created += mc_result.children_created
                    # NB: pre-Cycle-1 code had ``await db.commit()`` here
                    # to checkpoint each split; under chunked SAVEPOINTs
                    # the commit closes the outer scope.  Cycle 2 re-adds
                    # checkpointing via WriteQueue.submit() per split.
                    logger.info(
                        "Mega-cluster split: '%s' -> %d sub-clusters (%d noise) run_id=%s",
                        mc.label,
                        mc_result.children_created,
                        mc_result.noise_reassigned,
                        run_id,
                    )
                else:
                    logger.info(
                        "Mega-cluster split failed for '%s' (HDBSCAN found no sub-structure) run_id=%s",
                        mc.label, run_id,
                    )

            if mega_split_created > 0:
                engine._invalidate_stats_cache()
                nodes_created += mega_split_created
                logger.info(
                    "Mega-cluster split pass complete: %d new clusters created run_id=%s",
                    mega_split_created, run_id,
                )
    except Exception as mega_exc:
        logger.warning(
            "Mega-cluster split pass failed (non-fatal) run_id=%s: %s",
            run_id, mega_exc, exc_info=True,
        )

    # Log accepted refit AFTER mega-cluster pass so mega_splits count is accurate
    try:
        get_event_logger().log_decision(
            path="cold", op="refit", decision="accepted",
            context={
                "q_before": round(q_before, 4) if q_before is not None else None,
                "q_after": round(q_after, 4) if q_after is not None else None,
                "clusters_input": len(families),
                "hdbscan_clusters": cluster_result.n_clusters,
                "nodes_created": nodes_created,
                "nodes_updated": nodes_updated,
                "mega_splits": mega_split_created,
                "blended_weights": {
                    "raw": round(1.0 - CLUSTERING_BLEND_W_OPTIMIZED - CLUSTERING_BLEND_W_TRANSFORM, 3),
                    "optimized": round(CLUSTERING_BLEND_W_OPTIMIZED, 3),
                    "transform": round(CLUSTERING_BLEND_W_TRANSFORM, 3),
                },
                "accepted": True,
                "cold_path_run_id": run_id,
            },
        )
    except RuntimeError:
        pass

    engine._cold_path_needed = False

    # Snapshot creation is deferred to the post-SAVEPOINT outer scope
    # (see _execute_cold_path_inner accepted-path tail).  Phase 4 only
    # stages the metrics into phase_ctx.
    phase_ctx["nodes_created"] = nodes_created
    phase_ctx["nodes_updated"] = nodes_updated
    phase_ctx["umap_fitted"] = umap_fitted
    phase_ctx["q_after"] = q_after

    # Publish taxonomy_changed event (parity with warm path)
    try:
        from app.services.event_bus import event_bus

        event_bus.publish(
            "taxonomy_changed",
            {
                "trigger": "cold_path",
                "nodes_created": nodes_created,
                "nodes_updated": nodes_updated,
                "q_system": q_after,
            },
        )
    except Exception as evt_exc:
        logger.warning(
            "Failed to publish taxonomy_changed (cold) run_id=%s: %s",
            run_id, evt_exc,
        )


# ---------------------------------------------------------------------------
# Rejection-result helper — preserves all observability emitted by the
# pre-Cycle-1 rejection path.
# ---------------------------------------------------------------------------


async def _build_rejected_result(
    *,
    engine: TaxonomyEngine,
    db: AsyncSession,
    q_before: float | None,
    q_after: float | None,
    families: list[PromptCluster],
    cluster_result: Any,
    run_id: str,
) -> ColdPathResult:
    """Write rejection snapshot + emit decision event + publish
    taxonomy_changed SSE so the frontend refreshes even on rejection.

    v0.4.16 P1a Cycle 1: extracted from the inline rejection path of the
    pre-Cycle-1 monolithic body.  Called from ``_execute_cold_path_inner``
    AFTER the outer ``cp_pre_reembed`` SAVEPOINT exits, because
    ``create_snapshot`` issues a ``db.commit()`` that would otherwise
    close the parent context manager mid-flight.

    Mirrors the pre-Cycle-1 rejection path (lines 858-940 of the original
    cold_path.py) so existing observability hooks are preserved.
    """
    logger.warning(
        "Cold path quality regression run_id=%s: Q_before=%s Q_after=%s "
        "(epsilon=%.2f) -- rolling back refit",
        run_id, q_before, q_after, COLD_PATH_EPSILON,
    )

    # Carry forward q_health from prior snapshot to keep sparkline stable.
    _prev_q_health = None
    try:
        prev_snap = await get_latest_snapshot(db)
        if prev_snap and prev_snap.q_health is not None:
            _prev_q_health = prev_snap.q_health
    except Exception:
        pass
    snap = await create_snapshot(
        db,
        trigger="cold_path",
        q_system=q_before,
        q_coherence=0.0,
        q_separation=0.0,
        q_coverage=0.0,
        q_health=_prev_q_health,
        nodes_created=0,
    )
    try:
        get_event_logger().log_decision(
            path="cold", op="refit", decision="rejected",
            context={
                "q_before": round(q_before, 4) if q_before is not None else None,
                "q_after": round(q_after, 4) if q_after is not None else None,
                "clusters_input": len(families),
                "hdbscan_clusters": cluster_result.n_clusters if cluster_result is not None else 0,
                "accepted": False,
                "blended_weights": {
                    "raw": round(1.0 - CLUSTERING_BLEND_W_OPTIMIZED - CLUSTERING_BLEND_W_TRANSFORM, 3),
                    "optimized": round(CLUSTERING_BLEND_W_OPTIMIZED, 3),
                    "transform": round(CLUSTERING_BLEND_W_TRANSFORM, 3),
                },
                "cold_path_run_id": run_id,
            },
        )
    except RuntimeError:
        pass

    try:
        from app.services.event_bus import event_bus

        event_bus.publish(
            "taxonomy_changed",
            {
                "trigger": "cold_path_rejected",
                "nodes_created": 0,
                "nodes_updated": 0,
                "q_system": q_before,
            },
        )
    except Exception:
        pass

    return ColdPathResult(
        snapshot_id=snap.id,
        q_before=q_before,
        q_after=q_after,
        accepted=False,
        nodes_created=0,
        nodes_updated=0,
        umap_fitted=False,
    )


# ---------------------------------------------------------------------------
# UMAP-only projection (no HDBSCAN refit)
# ---------------------------------------------------------------------------


async def execute_umap_projection(
    engine: "TaxonomyEngine",
    db: AsyncSession,
) -> int:
    """Project UMAP-less clusters without re-clustering.

    Fits UMAP on all already-positioned clusters, then uses incremental
    transform to assign 3D coordinates to clusters that lack them.
    No HDBSCAN, no Q-gate, no node creation/deletion — purely additive.

    Also assigns OKLab colors to newly positioned nodes and updates
    domain node UMAP positions from their children.

    v0.4.13 cycle 9: writes occur on ``WriterLockedAsyncSession`` against
    the read engine — toggle ``cold_path_mode`` so the audit hook
    bypasses on the UPDATE statements that persist coordinates + colors.

    Returns:
        Number of clusters that received UMAP coordinates.
    """
    from app.database import read_engine_meta
    read_engine_meta.cold_path_mode = True
    try:
        return await _execute_umap_projection_inner(engine, db)
    finally:
        read_engine_meta.cold_path_mode = False


async def _execute_umap_projection_inner(
    engine: "TaxonomyEngine",
    db: AsyncSession,
) -> int:
    import time as _time

    _t0 = _time.monotonic()

    all_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
        )
    )
    all_clusters = list(all_q.scalars().all())

    positioned: list[PromptCluster] = []
    positioned_embs: list[np.ndarray] = []
    unpositioned: list[PromptCluster] = []
    unpositioned_embs: list[np.ndarray] = []

    opt_idx = getattr(engine, "_optimized_index", None)
    trans_idx = getattr(engine, "_transformation_index", None)
    qual_idx = getattr(engine, "_qualifier_index", None)

    for c in all_clusters:
        if not c.centroid_embedding:
            continue
        try:
            raw = np.frombuffer(c.centroid_embedding, dtype=np.float32)
            opt_vec = opt_idx.get_vector(c.id) if opt_idx else None
            trans_vec = trans_idx.get_vector(c.id) if trans_idx else None
            qual_vec = qual_idx.get_vector(c.id) if qual_idx else None
            blended = blend_embeddings(raw=raw, optimized=opt_vec, transformation=trans_vec,
                                       qualifier=qual_vec)
        except (ValueError, TypeError):
            continue

        if c.umap_x is not None and c.umap_y is not None and c.umap_z is not None:
            positioned.append(c)
            positioned_embs.append(blended)
        else:
            unpositioned.append(c)
            unpositioned_embs.append(blended)

    if not unpositioned:
        return 0

    projector = UMAPProjector()

    if len(positioned_embs) >= projector._MIN_POINTS_FOR_UMAP:
        projector.fit(positioned_embs)
        new_positions = projector.transform(unpositioned_embs)
    elif positioned_embs:
        all_embs = positioned_embs + unpositioned_embs
        all_positions = projector.fit(all_embs)
        new_positions = all_positions[len(positioned_embs):]
        for i, node in enumerate(positioned):
            if i < len(positioned_embs):
                node.umap_x = float(all_positions[i, 0])
                node.umap_y = float(all_positions[i, 1])
                node.umap_z = float(all_positions[i, 2])
    else:
        new_positions = projector.fit(unpositioned_embs)

    projected = 0
    for i, node in enumerate(unpositioned):
        if i < len(new_positions):
            node.umap_x = float(new_positions[i, 0])
            node.umap_y = float(new_positions[i, 1])
            node.umap_z = float(new_positions[i, 2])
            node.color_hex = generate_color(node.umap_x, node.umap_y, node.umap_z)
            projected += 1

    try:
        domain_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        for dnode in domain_q.scalars().all():
            await engine._set_domain_umap_from_children(db, dnode)
    except Exception as dom_exc:
        logger.warning("UMAP projection: domain positioning failed (non-fatal): %s", dom_exc)

    await db.commit()

    duration_ms = int((_time.monotonic() - _t0) * 1000)
    logger.info(
        "UMAP projection: positioned %d/%d clusters in %dms (no refit)",
        projected, len(unpositioned), duration_ms,
    )

    try:
        get_event_logger().log_decision(
            path="cold", op="umap", decision="projection_only",
            duration_ms=duration_ms,
            context={
                "projected": projected,
                "already_positioned": len(positioned),
                "total_active": len(all_clusters),
            },
        )
    except RuntimeError:
        pass

    return projected
