"""Feedback endpoints — submit and list feedback."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.services.feedback_service import FeedbackService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["feedback"])


class FeedbackRequest(BaseModel):
    optimization_id: str
    rating: str = Field(..., pattern="^(thumbs_up|thumbs_down)$")
    comment: str | None = None


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.FEEDBACK_RATE_LIMIT)),
):
    svc = FeedbackService(db)
    try:
        fb = await svc.create_feedback(body.optimization_id, body.rating, body.comment)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "id": fb.id,
        "optimization_id": fb.optimization_id,
        "rating": fb.rating,
        "comment": fb.comment,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
    }


@router.get("/feedback")
async def get_feedback(
    optimization_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    svc = FeedbackService(db)
    feedbacks = await svc.get_for_optimization(optimization_id)
    agg = await svc.get_aggregation(optimization_id)
    return {
        "aggregation": agg,
        "items": [
            {
                "id": fb.id, "rating": fb.rating, "comment": fb.comment,
                "created_at": fb.created_at.isoformat() if fb.created_at else None,
            }
            for fb in feedbacks
        ],
    }
