"""Tests for FeedbackService — TDD: tests written before implementation."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, Optimization
from app.services.feedback_service import FeedbackService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def opt_id(db_session: AsyncSession) -> str:
    """Insert a sample Optimization and return its id."""
    opt = Optimization(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        raw_prompt="Sample prompt for feedback tests",
        task_type="generation",
        strategy_used="chain_of_thought",
        status="completed",
    )
    db_session.add(opt)
    await db_session.commit()
    await db_session.refresh(opt)
    return opt.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_create_feedback(db_session: AsyncSession, opt_id: str) -> None:
    """create_feedback returns a persisted Feedback with correct fields."""
    svc = FeedbackService(db_session)

    fb = await svc.create_feedback(opt_id, "thumbs_up", comment="Great result!")

    assert fb is not None
    assert isinstance(fb, Feedback)
    assert fb.optimization_id == opt_id
    assert fb.rating == "thumbs_up"
    assert fb.comment == "Great result!"
    assert fb.id is not None
    assert fb.created_at is not None


async def test_create_feedback_invalid_optimization(db_session: AsyncSession) -> None:
    """create_feedback raises ValueError("not found") when optimization does not exist."""
    svc = FeedbackService(db_session)

    with pytest.raises(ValueError, match="not found"):
        await svc.create_feedback("nonexistent-opt-id", "thumbs_up")


async def test_create_feedback_invalid_rating(db_session: AsyncSession, opt_id: str) -> None:
    """create_feedback raises ValueError("Invalid rating") for an unrecognised rating."""
    svc = FeedbackService(db_session)

    with pytest.raises(ValueError, match="Invalid rating"):
        await svc.create_feedback(opt_id, "neutral")


async def test_get_feedback_for_optimization(db_session: AsyncSession, opt_id: str) -> None:
    """get_for_optimization returns the list of Feedback rows ordered newest-first."""
    svc = FeedbackService(db_session)

    await svc.create_feedback(opt_id, "thumbs_up")
    await svc.create_feedback(opt_id, "thumbs_down", comment="Could be better")

    results = await svc.get_for_optimization(opt_id)

    assert isinstance(results, list)
    assert len(results) == 2
    # Most recent first
    assert results[0].created_at >= results[1].created_at


async def test_get_aggregation(db_session: AsyncSession, opt_id: str) -> None:
    """get_aggregation returns correct total, thumbs_up, and thumbs_down counts."""
    svc = FeedbackService(db_session)

    await svc.create_feedback(opt_id, "thumbs_up")
    await svc.create_feedback(opt_id, "thumbs_up")
    await svc.create_feedback(opt_id, "thumbs_down")

    agg = await svc.get_aggregation(opt_id)

    assert agg["total"] == 3
    assert agg["thumbs_up"] == 2
    assert agg["thumbs_down"] == 1
