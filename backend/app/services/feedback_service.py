"""FeedbackService — CRUD and aggregation for the feedbacks table."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, Optimization
from app.services.adaptation_tracker import AdaptationTracker

logger = logging.getLogger(__name__)

_VALID_RATINGS: frozenset[str] = frozenset({"thumbs_up", "thumbs_down"})


class FeedbackService:
    """Data-access service for the ``feedbacks`` table.

    Persists feedback rows and synchronously drives strategy-affinity
    adaptation via :class:`AdaptationTracker`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create_feedback(
        self,
        optimization_id: str,
        rating: str,
        comment: str | None = None,
    ) -> Feedback:
        """Persist a new Feedback row and update strategy affinities.

        Args:
            optimization_id: ID of the parent Optimization.
            rating: ``"thumbs_up"`` or ``"thumbs_down"``.
            comment: Optional free-text comment.

        Returns:
            The newly created :class:`~app.models.Feedback` instance.

        Raises:
            ValueError: If *rating* is not a recognised value.
            ValueError: If no Optimization with *optimization_id* exists.
        """
        if rating not in _VALID_RATINGS:
            raise ValueError(f"Invalid rating {rating!r}: must be 'thumbs_up' or 'thumbs_down'")

        # Validate parent optimization exists
        result = await self._session.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        opt = result.scalar_one_or_none()
        if opt is None:
            raise ValueError(f"Optimization {optimization_id!r} not found")

        fb = Feedback(
            optimization_id=optimization_id,
            rating=rating,
            comment=comment,
        )
        self._session.add(fb)
        await self._session.commit()
        await self._session.refresh(fb)

        logger.info(
            "Feedback created: id=%s optimization_id=%s rating=%s",
            fb.id, optimization_id, rating,
        )

        # Synchronously call adaptation tracker — non-fatal
        if opt.task_type and opt.strategy_used:
            try:
                tracker = AdaptationTracker(self._session)
                await tracker.update_affinity(opt.task_type, opt.strategy_used, rating)
            except Exception:
                logger.exception(
                    "AdaptationTracker.update_affinity failed for optimization %s — ignoring",
                    optimization_id,
                )

        # Publish real-time event for cross-source notifications
        try:
            from app.services.event_bus import event_bus
            event_bus.publish("feedback_submitted", {
                "optimization_id": optimization_id,
                "rating": rating,
                "feedback_id": fb.id,
            })
        except Exception:
            logger.debug("Event bus publish failed for feedback — ignoring")

        return fb

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_for_optimization(self, optimization_id: str) -> list[Feedback]:
        """Return all Feedback rows for *optimization_id* ordered by created_at desc.

        Args:
            optimization_id: ID of the parent Optimization.

        Returns:
            List of :class:`~app.models.Feedback` instances, newest first.
        """
        result = await self._session.execute(
            select(Feedback)
            .where(Feedback.optimization_id == optimization_id)
            .order_by(Feedback.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_aggregation(self, optimization_id: str) -> dict[str, Any]:
        """Return aggregated feedback counts for *optimization_id*.

        Uses ``func.sum`` + ``case()`` for SQLite compatibility.

        Args:
            optimization_id: ID of the parent Optimization.

        Returns:
            Dict with keys ``total``, ``thumbs_up``, ``thumbs_down``.
        """
        stmt = select(
            func.count(Feedback.id).label("total"),
            func.sum(case((Feedback.rating == "thumbs_up", 1), else_=0)).label("thumbs_up"),
            func.sum(case((Feedback.rating == "thumbs_down", 1), else_=0)).label("thumbs_down"),
        ).where(Feedback.optimization_id == optimization_id)

        row = (await self._session.execute(stmt)).one()

        return {
            "total": row.total or 0,
            "thumbs_up": row.thumbs_up or 0,
            "thumbs_down": row.thumbs_down or 0,
        }
