"""OptimizationService — CRUD, sort/filter, and score distribution for Optimizations."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_SORT_COLUMNS: frozenset[str] = frozenset(
    {
        "created_at",
        "overall_score",
        "task_type",
        "status",
        "duration_ms",
        "strategy_used",
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
    """Data-access service for the ``optimizations`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        if sort_by not in _VALID_SORT_COLUMNS:
            raise ValueError(
                "Invalid sort column: %s. Must be one of: %s"
                % (sort_by, ", ".join(sorted(_VALID_SORT_COLUMNS)))
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

    async def get_score_distribution(self) -> dict[str, dict[str, float | int]]:
        """Return per-dimension statistics: count, mean, and population stddev.

        Uses SQL aggregates (COUNT, AVG, and the sum-of-squares identity) to
        compute the population standard deviation in a single round-trip:

            stddev = sqrt( E[x²] - (E[x])² )

        Rows where the column is NULL are excluded from each dimension's stats.
        If a column has no non-null rows, stddev is 0.0 and mean is 0.0.
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

        row = (await self._session.execute(select(*agg_exprs))).one()

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
