"""Taxonomy Observatory aggregator endpoints.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.schemas.taxonomy_insights import PatternDensityResponse
from app.services.taxonomy_insights import aggregate_pattern_density

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taxonomy", tags=["taxonomy-insights"])

_PERIOD_DAYS = {"24h": 1, "7d": 7, "30d": 30}


@router.get("/pattern-density", response_model=PatternDensityResponse)
async def get_pattern_density(
    period: Literal["24h", "7d", "30d"] = Query("7d"),
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> PatternDensityResponse:
    """Aggregate pattern density per active domain over the given period."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_PERIOD_DAYS[period])
    rows = await aggregate_pattern_density(db, start, end)
    rows.sort(key=lambda r: (-r.meta_pattern_count, -r.cluster_count))
    return PatternDensityResponse(
        rows=rows,
        total_domains=len(rows),
        total_meta_patterns=sum(r.meta_pattern_count for r in rows),
        total_global_patterns=sum(r.global_pattern_count for r in rows),
    )
