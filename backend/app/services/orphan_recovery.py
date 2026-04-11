"""OrphanRecoveryService — detect and recover taxonomy orphan optimizations.

Orphan optimizations are rows where ``process_optimization()`` failed
mid-transaction, leaving ``embedding IS NULL`` while ``overall_score IS NOT NULL``.
This service scans for stale orphans, recomputes their embeddings, assigns
clusters, and creates the missing ``OptimizationPattern`` join record.

Copyright 2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, OptimizationPattern, PromptCluster
from app.services.taxonomy._constants import _utcnow
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.family_ops import assign_cluster
from app.utils.text_cleanup import parse_domain

if TYPE_CHECKING:
    from app.services.taxonomy.engine import TaxonomyEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STALENESS_MINUTES = 5
_MAX_ORPHANS_PER_SCAN = 20
_MAX_RETRY_ATTEMPTS = 3
# Exponential backoff: attempt 1 → 30s, attempt 2 → 120s, attempt 3 → 480s
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_MULTIPLIER = 4


# ---------------------------------------------------------------------------
# Recovery metadata helpers — nested under "_recovery" key in heuristic_flags
# to avoid colliding with the pipeline's list-of-strings (divergence flags).
# ---------------------------------------------------------------------------


def _get_recovery_meta(flags: Any) -> dict:
    """Extract the _recovery dict from heuristic_flags (any format)."""
    if isinstance(flags, dict):
        return dict(flags.get("_recovery", {}))
    return {}


def _set_recovery_meta(flags: Any, recovery: dict) -> dict:
    """Return heuristic_flags with updated _recovery key, preserving existing data."""
    if isinstance(flags, list):
        # Pipeline wrote a list of divergence flags — wrap in dict
        return {"divergence_flags": flags, "_recovery": recovery}
    if isinstance(flags, dict):
        return {**flags, "_recovery": recovery}
    return {"_recovery": recovery}


def _is_recovery_exhausted(flags: Any) -> bool:
    """Check if recovery has been exhausted."""
    return bool(_get_recovery_meta(flags).get("exhausted"))


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OrphanRecoveryService:
    """Scans for and recovers orphan optimizations missing embeddings."""

    def __init__(self) -> None:
        self._last_scan_orphan_count: int = 0
        self._recovered_total: int = 0
        self._failed_total: int = 0
        self._last_scan_at: datetime | None = None
        self._last_recovery_at: datetime | None = None
        self._in_progress: set[str] = set()

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def _scan_orphans(self, db: AsyncSession) -> list[Optimization]:
        """Find orphan optimizations older than the staleness threshold.

        Orphans have ``embedding IS NULL``, ``overall_score IS NOT NULL``,
        ``raw_prompt IS NOT NULL``, and ``created_at`` older than
        ``_STALENESS_MINUTES``.  Post-filters out rows flagged as
        ``recovery_exhausted`` or still within their backoff window.
        """
        cutoff = _utcnow() - timedelta(minutes=_STALENESS_MINUTES)

        stmt = (
            select(Optimization)
            .where(
                Optimization.embedding.is_(None),
                Optimization.overall_score.isnot(None),
                Optimization.raw_prompt.isnot(None),
                Optimization.created_at < cutoff,
            )
            .limit(_MAX_ORPHANS_PER_SCAN)
        )
        result = await db.execute(stmt)
        candidates = list(result.scalars().all())

        now = _utcnow()
        orphans: list[Optimization] = []
        for opt in candidates:
            if _is_recovery_exhausted(opt.heuristic_flags):
                continue
            # Exponential backoff: skip if within retry window
            rec = _get_recovery_meta(opt.heuristic_flags)
            next_retry = rec.get("next_retry_after")
            if next_retry:
                try:
                    next_dt = datetime.fromisoformat(next_retry)
                    if now < next_dt:
                        continue
                except (ValueError, TypeError):
                    pass  # Malformed timestamp — allow retry
            orphans.append(opt)

        return orphans

    # ------------------------------------------------------------------
    # Recover one
    # ------------------------------------------------------------------

    async def _recover_one(
        self,
        optimization_id: str,
        db: AsyncSession,
        engine: TaxonomyEngine,
    ) -> bool:
        """Attempt to recover a single orphan optimization.

        Returns True on success, False if skipped or budget exhausted.
        Raises on unexpected errors (caller handles retry accounting).
        """
        # Concurrency guard
        if optimization_id in self._in_progress:
            logger.debug("Skipping %s — already in progress", optimization_id)
            return False
        self._in_progress.add(optimization_id)
        try:
            return await self._do_recover(optimization_id, db, engine)
        finally:
            self._in_progress.discard(optimization_id)

    async def _do_recover(
        self,
        optimization_id: str,
        db: AsyncSession,
        engine: TaxonomyEngine,
    ) -> bool:
        """Inner recovery logic (runs inside concurrency guard)."""
        result = await db.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        opt = result.scalar_one_or_none()
        if opt is None:
            return False

        # Idempotent: skip if embedding already set
        if opt.embedding is not None:
            return False

        # Check retry budget
        rec = _get_recovery_meta(opt.heuristic_flags)
        if rec.get("attempts", 0) >= _MAX_RETRY_ATTEMPTS:
            rec["exhausted"] = True
            opt.heuristic_flags = _set_recovery_meta(opt.heuristic_flags, rec)
            return False

        # --- Compute embeddings (CPU/model inference, no DB writes) ---
        embedding_svc = engine._embedding
        raw_emb = await embedding_svc.aembed_single(opt.raw_prompt)

        optimized_emb = None
        transformation_emb = None
        if opt.optimized_prompt:
            optimized_emb = await embedding_svc.aembed_single(opt.optimized_prompt)
            transform = optimized_emb - raw_emb
            t_norm = np.linalg.norm(transform)
            if t_norm > 1e-9:
                transformation_emb = (transform / t_norm).astype(np.float32)

        # --- Write embeddings ---
        opt.embedding = raw_emb.astype(np.float32).tobytes()
        if optimized_emb is not None:
            opt.optimized_embedding = optimized_emb.astype(np.float32).tobytes()
        if transformation_emb is not None:
            opt.transformation_embedding = transformation_emb.astype(np.float32).tobytes()

        # If cluster_id points to an archived cluster, clear it
        if opt.cluster_id:
            cluster_q = await db.execute(
                select(PromptCluster).where(PromptCluster.id == opt.cluster_id)
            )
            existing_cluster = cluster_q.scalar_one_or_none()
            if existing_cluster and existing_cluster.state == "archived":
                opt.cluster_id = None

        # Assign cluster if needed
        if not opt.cluster_id:
            domain_primary, _ = parse_domain(opt.domain or "general")
            cluster = await assign_cluster(
                db=db,
                embedding=raw_emb,
                label=opt.intent_label or "general",
                domain=domain_primary,
                task_type=opt.task_type or "general",
                overall_score=opt.overall_score,
                embedding_index=engine._embedding_index,
                project_id=opt.project_id,
            )
            opt.cluster_id = cluster.id
            engine.mark_dirty(cluster.id, project_id=opt.project_id)

        # Create OptimizationPattern (source) if not exists
        existing_pattern = await db.execute(
            select(OptimizationPattern).where(
                OptimizationPattern.optimization_id == optimization_id,
                OptimizationPattern.relationship == "source",
            )
        )
        if not existing_pattern.scalars().first():
            db.add(OptimizationPattern(
                optimization_id=optimization_id,
                cluster_id=opt.cluster_id,
                relationship="source",
            ))

        await db.flush()
        return True

    # ------------------------------------------------------------------
    # Retry accounting
    # ------------------------------------------------------------------

    async def _increment_retry(
        self,
        optimization_id: str,
        db: AsyncSession,
        error: Exception,
    ) -> None:
        """Increment retry counter, compute next backoff window, record error.

        Recovery metadata is nested under a ``_recovery`` dict key within
        ``heuristic_flags`` to avoid colliding with the pipeline's list-of-
        strings format (divergence flags).
        """
        result = await db.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        opt = result.scalar_one_or_none()
        if opt is None:
            return

        rec = _get_recovery_meta(opt.heuristic_flags)
        attempts = rec.get("attempts", 0) + 1
        rec["attempts"] = attempts
        rec["last_error"] = str(error)[:500]

        # Exponential backoff: 30s, 120s, 480s
        backoff_seconds = _BACKOFF_BASE_SECONDS * (_BACKOFF_MULTIPLIER ** (attempts - 1))
        rec["next_retry_after"] = (
            _utcnow() + timedelta(seconds=backoff_seconds)
        ).isoformat()

        if attempts >= _MAX_RETRY_ATTEMPTS:
            rec["exhausted"] = True

        opt.heuristic_flags = _set_recovery_meta(opt.heuristic_flags, rec)
        await db.commit()

    # ------------------------------------------------------------------
    # Full scan-and-recover cycle
    # ------------------------------------------------------------------

    async def scan_and_recover(
        self,
        session_factory: Callable[..., Any],
        engine: TaxonomyEngine,
    ) -> dict[str, Any]:
        """Run a full orphan scan and recovery cycle.

        Args:
            session_factory: Callable that returns an async context manager
                yielding an ``AsyncSession``.
            engine: The ``TaxonomyEngine`` instance for embeddings and
                cluster assignment.

        Returns:
            Dict with scan/recovery statistics.
        """
        self._last_scan_at = _utcnow()

        # Phase 1: scan in one session
        async with session_factory() as scan_db:
            orphans = await self._scan_orphans(scan_db)
            orphan_ids = [o.id for o in orphans]

        self._last_scan_orphan_count = len(orphan_ids)

        # Skip event logging and processing when no orphans found
        if not orphan_ids:
            return {
                "scanned": 0,
                "recovered": 0,
                "failed": 0,
                "recovered_total": self._recovered_total,
                "failed_total": self._failed_total,
            }

        try:
            get_event_logger().log_decision(
                path="warm",
                op="recovery",
                decision="scan",
                context={"orphan_count": len(orphan_ids)},
            )
        except (RuntimeError, Exception):
            pass

        logger.info("Orphan recovery: found %d candidates", len(orphan_ids))

        recovered = 0
        failed = 0

        # Phase 2: per-orphan recovery in fresh sessions
        for oid in orphan_ids:
            try:
                async with session_factory() as db:
                    success = await self._recover_one(oid, db, engine)
                    if success:
                        await db.commit()
                        recovered += 1
                        self._last_recovery_at = _utcnow()

                        # Read back cluster info for observability
                        cluster_id = None
                        cluster_label = None
                        opt_row = (await db.execute(
                            select(Optimization.cluster_id)
                            .where(Optimization.id == oid)
                        )).scalar_one_or_none()
                        if opt_row:
                            cluster_id = opt_row
                            cl = (await db.execute(
                                select(PromptCluster.label)
                                .where(PromptCluster.id == cluster_id)
                            )).scalar_one_or_none()
                            cluster_label = cl

                        try:
                            get_event_logger().log_decision(
                                path="warm",
                                op="recovery",
                                decision="success",
                                optimization_id=oid,
                                cluster_id=cluster_id,
                                context={
                                    "cluster_label": cluster_label,
                                },
                            )
                        except (RuntimeError, Exception):
                            pass
                    else:
                        # Skipped (idempotent or exhausted) — still commit flags
                        await db.commit()
            except Exception as exc:
                failed += 1
                logger.warning(
                    "Orphan recovery failed for %s: %s", oid, exc,
                )
                try:
                    async with session_factory() as retry_db:
                        await self._increment_retry(oid, retry_db, exc)
                except Exception:
                    logger.exception("Failed to increment retry for %s", oid)

                try:
                    get_event_logger().log_decision(
                        path="warm",
                        op="recovery",
                        decision="failed",
                        optimization_id=oid,
                        context={
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:200],
                        },
                    )
                except (RuntimeError, Exception):
                    pass

        self._recovered_total += recovered
        self._failed_total += failed

        if recovered or failed:
            logger.info("Orphan recovery: recovered=%d failed=%d", recovered, failed)

        return {
            "scanned": len(orphan_ids),
            "recovered": recovered,
            "failed": failed,
            "recovered_total": self._recovered_total,
            "failed_total": self._failed_total,
        }

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_metrics(self) -> dict[str, Any]:
        """Return current recovery counters for the health endpoint."""
        return {
            "last_scan_orphan_count": self._last_scan_orphan_count,
            "recovered_total": self._recovered_total,
            "failed_total": self._failed_total,
            "last_scan_at": self._last_scan_at.isoformat() if self._last_scan_at else None,
            "last_recovery_at": self._last_recovery_at.isoformat() if self._last_recovery_at else None,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

recovery_service = OrphanRecoveryService()
