"""Framework profiles and performance endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.models.framework_performance import FrameworkPerformance
from app.schemas.auth import AuthenticatedUser
from app.utils.json_fields import parse_json_column

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["framework"])


@router.get(
    "/framework-profiles",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def get_framework_profiles():
    """Return static framework validation profiles."""
    from app.services.framework_profiles import FRAMEWORK_PROFILES

    return FRAMEWORK_PROFILES


@router.get(
    "/framework-performance/{task_type}",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def get_framework_performance(
    task_type: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Get framework performance data for a task type."""
    stmt = select(FrameworkPerformance).where(
        FrameworkPerformance.user_id == current_user.id,
        FrameworkPerformance.task_type == task_type,
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    items = []
    for row in rows:
        avg_scores = parse_json_column(row.avg_scores) if row.avg_scores else None
        issue_freq = parse_json_column(row.issue_frequency) if row.issue_frequency else None
        elasticity = parse_json_column(row.elasticity_snapshot) if row.elasticity_snapshot else None

        items.append({
            "framework": row.framework,
            "avg_scores": avg_scores,
            "user_rating_avg": row.user_rating_avg,
            "issue_frequency": issue_freq,
            "sample_count": row.sample_count,
            "elasticity_snapshot": elasticity,
            "last_updated": row.last_updated.isoformat() if row.last_updated else None,
        })

    return {"task_type": task_type, "frameworks": items}
