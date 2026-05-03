"""OptimizationService — CRUD, sort/filter, and score distribution for Optimizations.

v0.4.13 cycle 8: ``delete_optimizations`` routes the bulk DELETE through
``self._write_queue`` when set; ``self._session`` becomes read-side only
on that path. Legacy direct-session writes survive in the
``write_queue is None`` branch for backward-compat with tests + callers
that haven't been wired through the lifespan queue yet.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import asc, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization
from app.services.event_bus import event_bus

if TYPE_CHECKING:
    from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)


@dataclass
class DeleteOptimizationsResult:
    """Result of a bulk optimization deletion.

    ``affected_cluster_ids`` lets the caller publish a follow-up
    ``taxonomy_changed`` event so warm Phase 0 reconciles the member_count
    on those clusters in the next cycle instead of waiting for the
    debounce window to close.
    """

    deleted: int = 0
    affected_cluster_ids: set[str] = field(default_factory=set)
    affected_project_ids: set[str] = field(default_factory=set)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_SORT_COLUMNS: frozenset[str] = frozenset(
    {
        "created_at",
        "overall_score",
        "task_type",
        "status",
        "duration_ms",
        "strategy_used",
        "intent_label",
        "domain",
    }
)

# All score columns tracked in the distribution report.
_SCORE_COLUMNS: list[str] = [
    "overall_score",
    "score_clarity",
    "score_specificity",
    "score_structure",
    "score_faithfulness",
    "score_conciseness",
]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OptimizationService:
    """Data-access service for the ``optimizations`` table.

    v0.4.13 cycle 8: when ``write_queue`` is supplied,
    ``delete_optimizations`` routes its commit through
    ``write_queue.submit()`` under
    ``operation_label='optimization_bulk_delete'`` so the write
    serializes against every other backend writer through the
    single-writer queue. The legacy ``self._session.commit()`` path is
    retained behind the ``write_queue is None`` guard.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        write_queue: "WriteQueue | None" = None,
    ) -> None:
        self._session = session
        self._write_queue = write_queue

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    async def get_by_id(self, optimization_id: str) -> Optimization | None:
        """Return the Optimization with *optimization_id*, or None if not found."""
        result = await self._session.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        return result.scalar_one_or_none()

    async def get_by_trace_id(self, trace_id: str) -> Optimization | None:
        """Return the Optimization whose *trace_id* matches, or None."""
        result = await self._session.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    async def list_optimizations(
        self,
        offset: int = 0,
        limit: int = 50,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        task_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Return a paginated, filtered, sorted list of optimizations.

        Returns a dict with keys:
            total        — total rows matching the filter (ignoring pagination)
            count        — number of rows in this page
            offset       — requested offset
            items        — list of Optimization ORM objects
            has_more     — whether there are rows beyond this page
            next_offset  — offset to use for the next page, or None
        """
        if sort_by not in VALID_SORT_COLUMNS:
            raise ValueError(
                "Invalid sort column: %s. Must be one of: %s"
                % (sort_by, ", ".join(sorted(VALID_SORT_COLUMNS)))
            )

        # Build base filter predicates
        filters = []
        if task_type is not None:
            filters.append(Optimization.task_type == task_type)
        if status is not None:
            filters.append(Optimization.status == status)

        # Count query
        count_stmt = select(func.count()).select_from(Optimization)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total: int = (await self._session.execute(count_stmt)).scalar_one()

        # Sort expression
        sort_col = getattr(Optimization, sort_by)
        order_expr = desc(sort_col) if sort_order.lower() == "desc" else asc(sort_col)

        # Data query
        data_stmt = select(Optimization).order_by(order_expr).offset(offset).limit(limit)
        if filters:
            data_stmt = data_stmt.where(*filters)

        rows = (await self._session.execute(data_stmt)).scalars().all()

        count = len(rows)
        has_more = (offset + count) < total
        next_offset: int | None = (offset + count) if has_more else None

        logger.debug(
            "list_optimizations: total=%d count=%d offset=%d sort=%s/%s",
            total, count, offset, sort_by, sort_order,
        )

        return {
            "total": total,
            "count": count,
            "offset": offset,
            "items": list(rows),
            "has_more": has_more,
            "next_offset": next_offset,
        }

    # ------------------------------------------------------------------
    # Score distribution
    # ------------------------------------------------------------------

    async def get_score_distribution(
        self,
        exclude_scoring_modes: list[str] | None = None,
    ) -> dict[str, dict[str, float | int]]:
        """Return per-dimension statistics: count, mean, and population stddev.

        Uses SQL aggregates (COUNT, AVG, and the sum-of-squares identity) to
        compute the population standard deviation in a single round-trip:

            stddev = sqrt( E[x²] - (E[x])² )

        Rows where the column is NULL are excluded from each dimension's stats.
        If a column has no non-null rows, stddev is 0.0 and mean is 0.0.

        Args:
            exclude_scoring_modes: If provided, exclude rows with these scoring_mode
                values from the distribution (e.g. ``["heuristic"]`` to keep only
                hybrid/independent scores for z-score normalization).
        """
        col_attrs = [getattr(Optimization, col) for col in _SCORE_COLUMNS]

        # Build aggregate expressions for every score column in one query.
        agg_exprs = []
        for col_attr in col_attrs:
            agg_exprs.extend(
                [
                    func.count(col_attr),          # count of non-null values
                    func.avg(col_attr),             # mean
                    func.avg(col_attr * col_attr),  # E[x²]
                ]
            )

        stmt = select(*agg_exprs)
        if exclude_scoring_modes:
            stmt = stmt.where(
                Optimization.scoring_mode.notin_(exclude_scoring_modes)
            )

        row = (await self._session.execute(stmt)).one()

        distribution: dict[str, dict[str, float | int]] = {}
        for i, col_name in enumerate(_SCORE_COLUMNS):
            base = i * 3
            count_val: int = row[base] or 0
            mean_val: float = float(row[base + 1] or 0.0)
            mean_sq_val: float = float(row[base + 2] or 0.0)

            # Population variance = E[x²] - (E[x])²
            variance = mean_sq_val - mean_val ** 2
            # Guard against tiny negative float due to floating-point precision
            stddev = math.sqrt(max(variance, 0.0)) if count_val > 0 else 0.0

            distribution[col_name] = {
                "count": count_val,
                "mean": mean_val,
                "stddev": stddev,
            }

        return distribution

    # ------------------------------------------------------------------
    # Error counts
    # ------------------------------------------------------------------

    async def get_recent_error_counts(self) -> dict[str, int]:
        """Count failed optimizations in the last hour and last 24 hours."""
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(hours=24)

        last_hour = (
            await self._session.execute(
                select(func.count(Optimization.id)).where(
                    Optimization.status == "failed",
                    Optimization.created_at >= one_hour_ago,
                )
            )
        ).scalar() or 0

        last_24h = (
            await self._session.execute(
                select(func.count(Optimization.id)).where(
                    Optimization.status == "failed",
                    Optimization.created_at >= one_day_ago,
                )
            )
        ).scalar() or 0

        return {"last_hour": last_hour, "last_24h": last_24h}

    # ------------------------------------------------------------------
    # Per-phase average durations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    async def delete_optimizations(
        self,
        ids: list[str],
        *,
        reason: str = "user_request",
    ) -> DeleteOptimizationsResult:
        """Delete optimizations by id and cascade to all dependents.

        Relies on the DB-level ``ondelete="CASCADE"`` rules
        (migration ``a2f6d8e31b09``) to remove ``Feedback``,
        ``OptimizationPattern``, ``RefinementBranch`` and ``RefinementTurn``
        rows atomically. ``PromptTemplate.source_optimization_id`` is
        auto-nulled by its ``ondelete="SET NULL"`` rule — templates are
        immutable forks that outlive their source.

        Cluster-level aggregates (``member_count``, ``dominant_project_id``,
        coherence, learned_phase_weights) are **not** adjusted here; warm
        Phase 0 reconciles them from the live Optimization table on the
        next cycle. Callers that need immediate reconciliation should
        publish a ``taxonomy_changed`` event after awaiting this method.

        Emits one ``optimization_deleted`` event per deleted row with
        payload ``{id, cluster_id, project_id, reason}`` so SSE clients
        (e.g. HistoryPanel) can update in real time.

        Args:
            ids: Optimization ids to delete. Unknown ids are skipped silently
                — the method returns the count of rows actually removed.
            reason: Free-form string propagated on the event payload for
                downstream consumers and audit trails
                (e.g. ``"user_request"``, ``"bulk_reset"``,
                ``"gc_sweep"``).

        Returns:
            ``DeleteOptimizationsResult`` with ``deleted`` count and the set
            of ``affected_cluster_ids`` / ``affected_project_ids``.
        """
        result = DeleteOptimizationsResult()
        if not ids:
            return result

        # Snapshot cluster_id / project_id for each target so we can emit
        # events and report affected clusters after the DELETE fires.
        # Read-side: safe on self._session (which is bound to the read
        # engine when the queue is in use).
        rows = (
            await self._session.execute(
                select(
                    Optimization.id,
                    Optimization.cluster_id,
                    Optimization.project_id,
                ).where(Optimization.id.in_(ids))
            )
        ).all()
        if not rows:
            return result

        for opt_id, cluster_id, project_id in rows:
            if cluster_id:
                result.affected_cluster_ids.add(cluster_id)
            if project_id:
                result.affected_project_ids.add(project_id)

        # ------------------------------------------------------------------
        # v0.4.13 cycle 8: route the DELETE + commit through the
        # write-queue when set, so the bulk delete serializes against
        # every other backend writer through the single-writer queue.
        # operation_label='optimization_bulk_delete'.
        # ------------------------------------------------------------------
        if self._write_queue is not None:
            async def _do_delete(write_db: AsyncSession) -> int:
                deleted = await write_db.execute(
                    delete(Optimization).where(Optimization.id.in_(ids))
                )
                rc = int(deleted.rowcount or 0)
                await write_db.commit()
                return rc

            result.deleted = await self._write_queue.submit(
                _do_delete, operation_label="optimization_bulk_delete",
            )
        else:
            # Legacy: write through self._session directly.
            deleted = await self._session.execute(
                delete(Optimization).where(Optimization.id.in_(ids))
            )
            result.deleted = int(deleted.rowcount or 0)
            await self._session.commit()

        for opt_id, cluster_id, project_id in rows:
            event_bus.publish(
                "optimization_deleted",
                {
                    "id": opt_id,
                    "cluster_id": cluster_id,
                    "project_id": project_id,
                    "reason": reason,
                },
            )

        # Mark affected clusters dirty on the live taxonomy engine so the
        # next warm cycle actually reconciles them (Phase 0 lifecycle
        # phases are dirty-gated — without this the warm path would skip
        # with `decision="no_dirty_clusters"` even after taxonomy_changed
        # fires). The engine import is local to keep the optional service
        # layer free of a hard dependency on the taxonomy subsystem; if
        # the engine singleton isn't initialised yet (tests, CLI scripts,
        # migrations) we just skip the marking.
        try:
            from app.services.taxonomy.engine import get_engine
            try:
                engine = get_engine()
            except RuntimeError:
                engine = None
            if engine is not None:
                for cluster_id in result.affected_cluster_ids:
                    project_id = next(
                        (pid for _, cid, pid in rows if cid == cluster_id),
                        None,
                    )
                    engine.mark_dirty(cluster_id, project_id=project_id)
        except ImportError:
            pass

        # Publish taxonomy_changed so the debounced warm-path runner picks
        # this up cross-process. In-process dirty marking above is the fast
        # path; this event is the safety net for MCP/CLI/test contexts
        # where the engine singleton isn't resident. Callers doing a "reset
        # to fresh" delete can hit /api/taxonomy/reset for immediate,
        # synchronous reconciliation (I-0).
        if result.deleted > 0:
            event_bus.publish(
                "taxonomy_changed",
                {
                    "reason": reason,
                    "trigger": "bulk_delete",
                    "affected_clusters": list(result.affected_cluster_ids),
                    "affected_projects": list(result.affected_project_ids),
                },
            )

        logger.info(
            "Deleted %d optimizations (requested=%d, reason=%s, clusters=%d)",
            result.deleted, len(ids), reason, len(result.affected_cluster_ids),
        )
        return result

    # ------------------------------------------------------------------
    # Per-phase average durations
    # ------------------------------------------------------------------

    async def get_enrichment_profile_effectiveness(
        self, limit: int = 200,
    ) -> dict[str, dict[str, float]]:
        """Aggregate recent completed optimizations by ``enrichment_profile``.

        Surfaces whether the three profiles (``code_aware`` / ``knowledge_work``
        / ``cold_start``) are delivering their hypothesis — lets an operator
        compare scoring outcomes per profile with a single health probe.

        Returns a dict keyed by profile name with per-profile aggregates:

        .. code-block:: python

            {
              "code_aware":      {"count": 150, "avg_overall_score": 7.8,
                                   "avg_improvement_score": 2.1},
              "knowledge_work":  {"count":  42, "avg_overall_score": 7.2, ...},
              "cold_start":      {"count":   7, "avg_overall_score": 6.5, ...},
            }

        Rows without an ``enrichment_profile`` (e.g. pre-v0.3.30 history) are
        excluded from the aggregation.  Rows with NULL ``improvement_score``
        count toward ``count`` and ``avg_overall_score`` but are excluded
        from ``avg_improvement_score``.

        The profile is read from ``context_sources["enrichment_meta"]
        ["enrichment_profile"]`` — the JSON nesting means we aggregate
        Python-side rather than via SQL ``GROUP BY`` (portable across
        SQLite/PostgreSQL without dialect-specific JSON extractors).
        """
        result = await self._session.execute(
            select(
                Optimization.context_sources,
                Optimization.overall_score,
                Optimization.improvement_score,
            )
            .where(
                Optimization.status == "completed",
                Optimization.context_sources.isnot(None),
            )
            .order_by(desc(Optimization.created_at))
            .limit(limit)
        )
        rows = result.all()

        # Per-profile accumulators.
        profile_data: dict[str, dict[str, Any]] = {}
        for context_sources, overall_score, improvement_score in rows:
            if not isinstance(context_sources, dict):
                continue
            meta = context_sources.get("enrichment_meta")
            if not isinstance(meta, dict):
                continue
            profile = meta.get("enrichment_profile")
            if not isinstance(profile, str) or not profile:
                continue

            bucket = profile_data.setdefault(profile, {
                "count": 0,
                "overall_scores": [],
                "improvement_scores": [],
            })
            bucket["count"] += 1
            if isinstance(overall_score, (int, float)):
                bucket["overall_scores"].append(float(overall_score))
            if isinstance(improvement_score, (int, float)):
                bucket["improvement_scores"].append(float(improvement_score))

        # Compute aggregates.
        summary: dict[str, dict[str, float]] = {}
        for profile, bucket in profile_data.items():
            overall = bucket["overall_scores"]
            improvement = bucket["improvement_scores"]
            entry: dict[str, float] = {"count": bucket["count"]}
            if overall:
                entry["avg_overall_score"] = round(sum(overall) / len(overall), 4)
            if improvement:
                entry["avg_improvement_score"] = round(
                    sum(improvement) / len(improvement), 4
                )
            summary[profile] = entry
        return summary

    async def get_avg_duration_by_phase(self, limit: int = 50) -> dict[str, int]:
        """Get average per-phase duration from recent completed optimizations.

        Reads the ``tokens_by_phase`` JSON column which stores phase timing
        data (e.g. ``{"analyze_ms": N, "optimize_ms": N, "score_ms": N}``).
        Also computes total average from ``duration_ms``.
        """
        result = await self._session.execute(
            select(Optimization.tokens_by_phase, Optimization.duration_ms)
            .where(
                Optimization.status == "completed",
                Optimization.tokens_by_phase.isnot(None),
            )
            .order_by(desc(Optimization.created_at))
            .limit(limit)
        )
        rows = result.all()
        if not rows:
            return {}

        totals: dict[str, list[int]] = {}
        total_durations: list[int] = []
        for phase_data, duration_ms in rows:
            if isinstance(phase_data, dict):
                for key, val in phase_data.items():
                    if isinstance(val, (int, float)):
                        totals.setdefault(key, []).append(int(val))
            if duration_ms:
                total_durations.append(duration_ms)

        avg: dict[str, int] = {}
        for key, vals in totals.items():
            avg[key] = round(sum(vals) / len(vals))
        if total_durations:
            avg["total"] = round(sum(total_durations) / len(total_durations))
        return avg
