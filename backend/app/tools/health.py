"""Handler for synthesis_health MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import PROMPTS_DIR
from app.database import async_session_factory
from app.models import LinkedRepo, Optimization, PromptCluster, RepoFileIndex, RepoIndexMeta
from app.schemas.mcp_models import HealthOutput, LinkedRepoHealth
from app.services.optimization_service import OptimizationService
from app.services.pipeline_constants import DOMAIN_COUNT_CEILING
from app.services.strategy_loader import StrategyLoader
from app.tools._shared import get_routing

logger = logging.getLogger(__name__)


async def handle_health() -> HealthOutput:
    """Check system capabilities and health."""
    routing = get_routing()

    # Get provider info from routing state
    state = routing.state
    provider_name = state.provider_name if state.provider_name else None
    status = "healthy" if provider_name else "degraded"

    # Determine available tiers
    available_tiers = list(routing.available_tiers)

    # Get strategy list
    try:
        strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")
        strategies = strategy_loader.list_strategies()
    except Exception as exc:
        logger.warning("Could not load strategies for health check: %s", exc)
        strategies = []

    # Get optimization stats from DB
    total_optimizations = 0
    avg_score: float | None = None
    recent_error_rate: float | None = None
    domain_count = 0

    try:
        async with async_session_factory() as db:
            opt_svc = OptimizationService(db)
            # Total count
            result = await opt_svc.list_optimizations(limit=1, offset=0)
            total_optimizations = result["total"]

            # Average score
            if total_optimizations > 0:
                score_result = await db.execute(
                    select(func.avg(Optimization.overall_score)).where(
                        Optimization.status == "completed",
                        Optimization.overall_score.isnot(None),
                    )
                )
                avg_val = score_result.scalar()
                if avg_val is not None:
                    avg_score = round(float(avg_val), 2)

            # Recent error rate (failed / total in last 24h)
            error_counts = await opt_svc.get_recent_error_counts()
            failed_24h = error_counts.get("last_24h", 0)
            if failed_24h > 0:
                total_24h_result = await db.execute(
                    select(func.count(Optimization.id)).where(
                        Optimization.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
                    )
                )
                total_24h = total_24h_result.scalar() or 0
                if total_24h > 0:
                    recent_error_rate = round(failed_24h / total_24h, 3)

            # Domain proliferation metrics
            domain_count = await db.scalar(
                select(func.count()).where(PromptCluster.state == "domain")
            ) or 0
    except Exception as exc:
        logger.warning("Could not fetch optimization stats for health: %s", exc)

    # Linked-repo visibility — mirrors auto_resolve_repo() ordering so the
    # reported repo matches what optimize/match/prepare will actually use.
    linked_repo: LinkedRepoHealth | None = None
    try:
        async with async_session_factory() as db:
            linked = (
                await db.execute(
                    select(LinkedRepo).order_by(LinkedRepo.linked_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if linked is not None:
                branch = linked.branch or linked.default_branch
                meta = (
                    await db.execute(
                        select(RepoIndexMeta)
                        .where(
                            RepoIndexMeta.repo_full_name == linked.full_name,
                            RepoIndexMeta.branch == branch,
                        )
                        .order_by(RepoIndexMeta.indexed_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                files_indexed = 0
                if meta is not None:
                    files_indexed = await db.scalar(
                        select(func.count()).where(
                            RepoFileIndex.repo_full_name == linked.full_name,
                            RepoFileIndex.branch == branch,
                            RepoFileIndex.embedding.isnot(None),
                        )
                    ) or 0
                linked_repo = LinkedRepoHealth(
                    full_name=linked.full_name,
                    branch=branch,
                    language=linked.language,
                    index_status=meta.status if meta else None,
                    index_phase=meta.index_phase if meta else None,
                    files_indexed=files_indexed,
                    synthesis_ready=bool(meta and meta.explore_synthesis),
                )
    except Exception as exc:
        logger.warning("Could not fetch linked-repo state for health: %s", exc)

    return HealthOutput(
        status=status,
        provider=provider_name,
        available_tiers=available_tiers,
        sampling_capable=state.sampling_capable,
        total_optimizations=total_optimizations,
        avg_score=avg_score,
        recent_error_rate=recent_error_rate,
        available_strategies=strategies,
        domain_count=domain_count,
        domain_ceiling=DOMAIN_COUNT_CEILING,
        linked_repo=linked_repo,
    )
