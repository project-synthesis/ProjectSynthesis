"""Handler for synthesis_feedback MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

from app.database import async_session_factory
from app.schemas.mcp_models import FeedbackOutput
from app.services.event_notification import notify_event_bus
from app.services.feedback_service import FeedbackService
from app.tools._shared import get_write_queue

logger = logging.getLogger(__name__)


async def handle_feedback(
    optimization_id: str,
    rating: str,
    comment: str | None = None,
) -> FeedbackOutput:
    """Submit quality feedback on a completed optimization."""
    # v0.4.13 cycle 9: route DB writes through the MCP-process WriteQueue.
    # Falls back to direct session if the queue is unavailable (test
    # environments without a live lifespan).
    try:
        wq = get_write_queue()
    except ValueError:
        wq = None

    async def _persist(db):  # type: ignore[no-untyped-def]
        svc = FeedbackService(db)
        fb = await svc.create_feedback(
            optimization_id=optimization_id,
            rating=rating,
            comment=comment,
        )
        await db.commit()
        return fb

    if wq is not None:
        feedback = await wq.submit(_persist, operation_label="mcp_feedback")
    else:
        async with async_session_factory() as db:
            feedback = await _persist(db)

    # Strategy affinity is updated synchronously inside create_feedback
    strategy_affinity_updated = True

    # Notify frontend via cross-process event bus
    await notify_event_bus("feedback_submitted", {
        "id": feedback.id,
        "optimization_id": optimization_id,
        "rating": rating,
    })

    logger.info(
        "synthesis_feedback completed: feedback_id=%s optimization_id=%s rating=%s",
        feedback.id, optimization_id, rating,
    )

    return FeedbackOutput(
        feedback_id=feedback.id,
        optimization_id=optimization_id,
        rating=rating,
        strategy_affinity_updated=strategy_affinity_updated,
    )
