"""FeedbackService — CRUD and aggregation for the feedbacks table.

v0.4.13 cycle 8: writer paths route through ``self._write_queue`` when
set; ``self._session`` becomes read-side only. Legacy direct-session
writes survive in the ``self._write_queue is None`` branch for
backward-compat with tests + callers that haven't been wired through
the lifespan queue yet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, Optimization, PromptCluster
from app.services.adaptation_tracker import AdaptationTracker
from app.services.taxonomy.cold_path import _parse_quiesce_flag
from app.services.taxonomy.event_logger import get_event_logger

if TYPE_CHECKING:
    from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)

_VALID_RATINGS: frozenset[str] = frozenset({"thumbs_up", "thumbs_down"})


class FeedbackService:
    """Data-access service for the ``feedbacks`` table.

    Persists feedback rows and synchronously drives strategy-affinity
    adaptation via :class:`AdaptationTracker`.

    v0.4.13 cycle 8: when ``write_queue`` is supplied, ``create_feedback``
    routes its commit through ``write_queue.submit()`` under
    ``operation_label='feedback_create'`` so the write serializes against
    every other backend writer. The legacy ``self._session.commit()``
    path is retained behind the ``write_queue is None`` guard.
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

        # Validate parent optimization exists (read-side, fine on self._session).
        result = await self._session.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        opt = result.scalar_one_or_none()
        if opt is None:
            raise ValueError(f"Optimization {optimization_id!r} not found")

        # ------------------------------------------------------------------
        # v0.4.13 cycle 8: route the INSERT + post-insert side-effect
        # writes (AdaptationTracker affinity update + MetaPattern source_count
        # bump) through ``self._write_queue.submit`` when set, so all the
        # writes serialize against every other backend writer through the
        # single-writer queue. Legacy direct-session path retained behind
        # the ``write_queue is None`` branch for tests + callers that
        # haven't yet been wired through the lifespan queue.
        # ------------------------------------------------------------------
        opt_task_type = opt.task_type
        opt_strategy = opt.strategy_used

        if self._write_queue is not None:
            from sqlalchemy import update as sa_update

            from app.models import MetaPattern, OptimizationPattern

            async def _do_create(write_db: AsyncSession) -> Feedback:
                _fb = Feedback(
                    optimization_id=optimization_id,
                    rating=rating,
                    comment=comment,
                )
                write_db.add(_fb)
                await write_db.commit()
                await write_db.refresh(_fb)

                # Adaptation tracker side-effect: same writer session.
                if opt_task_type and opt_strategy:
                    try:
                        tracker = AdaptationTracker(write_db)
                        if await tracker.check_degenerate(opt_task_type, opt_strategy):
                            logger.info(
                                "Skipping affinity update — degenerate feedback "
                                "detected for task_type=%s strategy=%s",
                                opt_task_type, opt_strategy,
                            )
                        else:
                            await tracker.update_affinity(
                                opt_task_type, opt_strategy, rating,
                            )
                    except Exception:
                        logger.exception(
                            "AdaptationTracker.update_affinity failed for "
                            "optimization %s — ignoring",
                            optimization_id,
                        )

                # v0.4.16 P1a Cycle 2: peer-writer SKIP for feedback path.
                # Spec § 3.3 — if the host optimization's cluster is
                # quiesced (cold path mid-refit), skip the MetaPattern
                # upsert. Warm-path Phase 4 catches up via existing
                # reconciliation. Wrapped in try/except so any unexpected
                # query/log failure degrades gracefully (the original
                # contract is "feedback write succeeds even if the boost
                # phase misfires").
                try:
                    cluster_q = await write_db.execute(
                        select(PromptCluster).join(
                            Optimization, Optimization.cluster_id == PromptCluster.id
                        ).where(Optimization.id == optimization_id)
                    )
                    _host_cluster = cluster_q.scalar_one_or_none()
                    if _host_cluster is not None:
                        _qexp = _parse_quiesce_flag(_host_cluster.cluster_metadata)
                        if _qexp is not None:
                            try:
                                get_event_logger().log_decision(
                                    path="cold", op="refit_quiesce_check",
                                    decision="peer_skipped",
                                    context={
                                        "writer_path": "feedback",
                                        "cluster_id": _host_cluster.id,
                                        "expires_at_iso": _qexp.isoformat(),
                                    },
                                )
                            except RuntimeError:
                                pass
                            logger.info(
                                "Peer-writer SKIP (feedback): cluster '%s' is "
                                "quiesced — pattern boost deferred to warm cycle",
                                _host_cluster.label,
                            )
                            return _fb
                except Exception:
                    logger.debug(
                        "Peer-writer SKIP check (feedback) failed (non-fatal)",
                        exc_info=True,
                    )

                # Pattern feedback loop on the same writer session.
                try:
                    pat_q = await write_db.execute(
                        select(OptimizationPattern.meta_pattern_id).where(
                            OptimizationPattern.optimization_id == optimization_id,
                            OptimizationPattern.meta_pattern_id.isnot(None),
                        )
                    )
                    pattern_ids = [r[0] for r in pat_q.all()]
                    if pattern_ids and rating == "thumbs_up":
                        await write_db.execute(
                            sa_update(MetaPattern)
                            .where(MetaPattern.id.in_(pattern_ids))
                            .values(source_count=MetaPattern.source_count + 1)
                        )
                        await write_db.commit()
                        logger.info(
                            "pattern_feedback: optimization=%s rating=%s "
                            "patterns_boosted=%d",
                            optimization_id, rating, len(pattern_ids),
                        )
                    elif pattern_ids:
                        logger.info(
                            "pattern_feedback: optimization=%s rating=%s "
                            "patterns=%d (no boost for thumbs_down)",
                            optimization_id, rating, len(pattern_ids),
                        )
                except Exception:
                    logger.debug(
                        "Pattern feedback tracking failed (non-fatal)",
                        exc_info=True,
                    )
                return _fb

            fb = await self._write_queue.submit(
                _do_create, operation_label="feedback_create",
            )

            logger.info(
                "Feedback created: id=%s optimization_id=%s rating=%s",
                fb.id, optimization_id, rating,
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
        # Legacy: write through self._session directly (write_queue is None).
        # ------------------------------------------------------------------
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

                # Skip affinity update when feedback is degenerate (>90% same
                # rating over 10+ feedbacks). Further updates would only push
                # the rate closer to 1.0/0.0 with no new signal — the strategy
                # is already effectively blocked or proven via get_blocked_strategies().
                if await tracker.check_degenerate(opt.task_type, opt.strategy_used):
                    logger.info(
                        "Skipping affinity update — degenerate feedback detected "
                        "for task_type=%s strategy=%s",
                        opt.task_type, opt.strategy_used,
                    )
                else:
                    await tracker.update_affinity(opt.task_type, opt.strategy_used, rating)
            except Exception:
                logger.exception(
                    "AdaptationTracker.update_affinity failed for optimization %s — ignoring",
                    optimization_id,
                )

        # Phase weight adaptation moved to warm path (score-correlated batch
        # adaptation). Feedback drives strategy affinity only — phase weights
        # adapt toward profiles that correlate with high overall_score values,
        # computed periodically during warm-path refresh.

        # Pattern feedback loop: boost source_count for patterns that
        # contributed to thumbs_up results. This influences future injection
        # ranking and GlobalPattern promotion.
        try:
            from sqlalchemy import update as sa_update

            from app.models import MetaPattern, OptimizationPattern
            pat_q = await self._session.execute(
                select(OptimizationPattern.meta_pattern_id).where(
                    OptimizationPattern.optimization_id == optimization_id,
                    OptimizationPattern.meta_pattern_id.isnot(None),
                )
            )
            pattern_ids = [r[0] for r in pat_q.all()]
            if pattern_ids and rating == "thumbs_up":
                await self._session.execute(
                    sa_update(MetaPattern)
                    .where(MetaPattern.id.in_(pattern_ids))
                    .values(source_count=MetaPattern.source_count + 1)
                )
                logger.info(
                    "pattern_feedback: optimization=%s rating=%s patterns_boosted=%d",
                    optimization_id, rating, len(pattern_ids),
                )
            elif pattern_ids:
                logger.info(
                    "pattern_feedback: optimization=%s rating=%s patterns=%d (no boost for thumbs_down)",
                    optimization_id, rating, len(pattern_ids),
                )
        except Exception:
            logger.debug("Pattern feedback tracking failed (non-fatal)", exc_info=True)

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
