"""Feedback API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.schemas.auth import AuthenticatedUser
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackStatsResponse,
    FeedbackWithAggregate,
)
from app.services.adaptation_engine import load_adaptation, recompute_adaptation_safe
from app.services.feedback_service import (
    get_feedback_aggregate,
    get_feedback_for_optimization,
    get_user_feedback_history,
    upsert_feedback,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feedback"])


@router.post(
    "/api/optimize/{optimization_id}/feedback",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_FEEDBACK))],
)
async def submit_feedback(
    optimization_id: str,
    body: FeedbackCreate,
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    result = await upsert_feedback(
        optimization_id=optimization_id,
        user_id=current_user.id,
        rating=body.rating,
        dimension_overrides=body.dimension_overrides,
        corrected_issues=body.corrected_issues,
        comment=body.comment,
        db=db,
    )
    await db.commit()

    # Trigger background adaptation recomputation
    background_tasks.add_task(
        recompute_adaptation_safe, current_user.id
    )

    return {"id": result["id"], "status": "created" if result["created"] else "updated"}


@router.get(
    "/api/optimize/{optimization_id}/feedback",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def get_feedback(
    optimization_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    feedback = await get_feedback_for_optimization(optimization_id, current_user.id, db)
    aggregate = await get_feedback_aggregate(optimization_id, db)
    return FeedbackWithAggregate(
        feedback=feedback,
        aggregate=aggregate,
    )


@router.get(
    "/api/feedback/history",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def feedback_history(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    offset: int = 0,
    limit: int = 50,
    rating: int | None = None,
):
    return await get_user_feedback_history(current_user.id, db, limit, offset, rating)


@router.get(
    "/api/feedback/stats",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def feedback_stats(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    history = await get_user_feedback_history(current_user.id, db, limit=1000)
    adaptation = await load_adaptation(current_user.id, db)

    feedbacks = history["items"]
    rating_dist = {"positive": 0, "negative": 0, "neutral": 0}
    for fb in feedbacks:
        if fb["rating"] > 0:
            rating_dist["positive"] += 1
        elif fb["rating"] < 0:
            rating_dist["negative"] += 1
        else:
            rating_dist["neutral"] += 1

    return FeedbackStatsResponse(
        total_feedbacks=history["total"],
        rating_distribution=rating_dist,
        avg_override_delta=None,  # computed from overrides vs validator scores
        most_corrected_dimension=None,
        adaptation_state=adaptation,
    )


