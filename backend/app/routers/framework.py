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
from app.services.framework_scoring import format_framework_performance

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["framework"])


# No authentication required — static configuration data, no user context needed.
# Rate limited to prevent enumeration/DoS.
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

    return {"task_type": task_type, "frameworks": format_framework_performance(rows)}
