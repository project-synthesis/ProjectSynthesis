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


class FeedbackSubmitResponse(BaseModel):
    id: str = Field(description="Created feedback record ID.")
    optimization_id: str = Field(description="ID of the optimization this feedback is for.")
    rating: str = Field(description="Feedback rating: 'thumbs_up' or 'thumbs_down'.")
    comment: str | None = Field(default=None, description="Optional user comment.")
    created_at: str | None = Field(default=None, description="ISO 8601 creation timestamp.")


class FeedbackAggregation(BaseModel):
    total: int = Field(description="Total feedback count.")
    thumbs_up: int = Field(description="Number of positive ratings.")
    thumbs_down: int = Field(description="Number of negative ratings.")


class FeedbackItem(BaseModel):
    id: str = Field(description="Feedback record ID.")
    rating: str = Field(description="Feedback rating.")
    comment: str | None = Field(default=None, description="Optional user comment.")
    created_at: str | None = Field(default=None, description="ISO 8601 creation timestamp.")


class FeedbackListResponse(BaseModel):
    aggregation: FeedbackAggregation = Field(description="Aggregated feedback counts.")
    items: list[FeedbackItem] = Field(description="Individual feedback entries.")


class FeedbackRequest(BaseModel):
    optimization_id: str = Field(description="ID of the optimization to provide feedback on.")
    rating: str = Field(
        ..., pattern="^(thumbs_up|thumbs_down)$",
        description="Feedback rating: 'thumbs_up' or 'thumbs_down'.",
    )
    comment: str | None = Field(default=None, max_length=2000, description="Optional free-text comment.")


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.FEEDBACK_RATE_LIMIT)),
) -> FeedbackSubmitResponse:
    svc = FeedbackService(db)
    try:
        fb = await svc.create_feedback(body.optimization_id, body.rating, body.comment)
    except ValueError as e:
        logger.warning("Feedback submission failed: %s", e)
        raise HTTPException(status_code=404, detail="Optimization not found.")
    return FeedbackSubmitResponse(
        id=fb.id,
        optimization_id=fb.optimization_id,
        rating=fb.rating,
        comment=fb.comment,
        created_at=fb.created_at.isoformat() if fb.created_at else None,
    )


@router.get("/feedback")
async def get_feedback(
    optimization_id: str = Query(..., description="Optimization ID to fetch feedback for."),
    db: AsyncSession = Depends(get_db),
) -> FeedbackListResponse:
    svc = FeedbackService(db)
    feedbacks = await svc.get_for_optimization(optimization_id)
    agg = await svc.get_aggregation(optimization_id)
    return FeedbackListResponse(
        aggregation=FeedbackAggregation(**agg),
        items=[
            FeedbackItem(
                id=fb.id, rating=fb.rating, comment=fb.comment,
                created_at=fb.created_at.isoformat() if fb.created_at else None,
            )
            for fb in feedbacks
        ],
    )
