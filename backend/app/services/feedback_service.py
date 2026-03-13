"""Feedback CRUD and aggregation.

One feedback per optimization per user (upsert semantics).
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback

logger = logging.getLogger(__name__)


async def upsert_feedback(
    optimization_id: str,
    user_id: str,
    rating: int,
    dimension_overrides: dict | None,
    corrected_issues: list[str] | None,
    comment: str | None,
    db: AsyncSession,
) -> dict:
    """Create or update feedback. Returns {id, created: bool}."""
    stmt = select(Feedback).where(
        Feedback.optimization_id == optimization_id,
        Feedback.user_id == user_id,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.rating = rating
        existing.dimension_overrides = json.dumps(dimension_overrides) if dimension_overrides else None
        existing.corrected_issues = json.dumps(corrected_issues) if corrected_issues else None
        existing.comment = comment
        await db.flush()
        return {"id": existing.id, "created": False}

    fb = Feedback(
        id=str(uuid.uuid4()),
        optimization_id=optimization_id,
        user_id=user_id,
        rating=rating,
        dimension_overrides=json.dumps(dimension_overrides) if dimension_overrides else None,
        corrected_issues=json.dumps(corrected_issues) if corrected_issues else None,
        comment=comment,
    )
    db.add(fb)
    await db.flush()
    return {"id": fb.id, "created": True}


async def get_feedback_for_optimization(
    optimization_id: str,
    user_id: str,
    db: AsyncSession,
) -> dict | None:
    """Get the current user's feedback for an optimization."""
    stmt = select(Feedback).where(
        Feedback.optimization_id == optimization_id,
        Feedback.user_id == user_id,
    )
    result = await db.execute(stmt)
    fb = result.scalar_one_or_none()
    if not fb:
        return None
    return _to_dict(fb)


async def get_feedback_aggregate(
    optimization_id: str,
    db: AsyncSession,
) -> dict:
    """Compute aggregate feedback stats for an optimization."""
    stmt = select(Feedback).where(Feedback.optimization_id == optimization_id)
    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return {"total_ratings": 0, "positive": 0, "negative": 0, "neutral": 0, "avg_dimension_overrides": None}

    feedbacks = [r[0] if isinstance(r, tuple) else r for r in rows]
    positive = sum(1 for f in feedbacks if f.rating > 0)
    negative = sum(1 for f in feedbacks if f.rating < 0)
    neutral = sum(1 for f in feedbacks if f.rating == 0)

    # Average dimension overrides
    all_overrides: dict[str, list[int]] = {}
    for f in feedbacks:
        if f.dimension_overrides:
            try:
                raw = f.dimension_overrides
                overrides = json.loads(raw) if isinstance(raw, str) else raw
                for k, v in overrides.items():
                    all_overrides.setdefault(k, []).append(v)
            except (json.JSONDecodeError, TypeError):
                pass

    avg_overrides = {k: round(sum(v) / len(v), 1) for k, v in all_overrides.items()} if all_overrides else None

    return {
        "total_ratings": len(feedbacks),
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "avg_dimension_overrides": avg_overrides,
    }


async def get_user_feedback_history(
    user_id: str,
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    rating_filter: int | None = None,
) -> dict:
    """Paginated feedback history for a user."""
    stmt = select(Feedback).where(Feedback.user_id == user_id)
    if rating_filter is not None:
        stmt = stmt.where(Feedback.rating == rating_filter)
    stmt = stmt.order_by(Feedback.created_at.desc())

    # Count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Page
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return {
        "total": total,
        "count": len(rows),
        "offset": offset,
        "items": [_to_dict(f) for f in rows],
        "has_more": offset + len(rows) < total,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }


async def get_all_feedbacks_for_user(
    user_id: str,
    db: AsyncSession,
) -> list:
    """Get all feedbacks for adaptation computation. Returns ORM objects."""
    stmt = select(Feedback).where(Feedback.user_id == user_id).order_by(Feedback.created_at)
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _to_dict(fb: Feedback) -> dict:
    """Convert Feedback ORM to response dict."""
    overrides = None
    if fb.dimension_overrides:
        try:
            raw = fb.dimension_overrides
            overrides = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            pass

    issues = None
    if fb.corrected_issues:
        try:
            issues = json.loads(fb.corrected_issues) if isinstance(fb.corrected_issues, str) else fb.corrected_issues
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": fb.id,
        "optimization_id": fb.optimization_id,
        "user_id": fb.user_id,
        "rating": fb.rating,
        "dimension_overrides": overrides,
        "corrected_issues": issues,
        "comment": fb.comment,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
    }
