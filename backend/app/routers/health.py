"""Health check endpoint with pipeline metrics."""

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app._version import __version__
from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.services.optimization_service import OptimizationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


class ScoreHealth(BaseModel):
    last_n_mean: float = Field(description="Mean overall score across recent optimizations.")
    last_n_stddev: float = Field(description="Standard deviation of recent overall scores.")
    count: int = Field(description="Number of optimizations in the sample.")
    clustering_warning: bool = Field(
        description="True if scores cluster suspiciously (low stddev with enough samples).",
    )


class RecentErrors(BaseModel):
    last_hour: int = Field(default=0, description="Number of failed optimizations in the last hour.")
    last_24h: int = Field(default=0, description="Number of failed optimizations in the last 24 hours.")


class HealthResponse(BaseModel):
    status: str = Field(description="'healthy' if a provider is available, 'degraded' otherwise.")
    version: str = Field(description="Application version from version.json.")
    provider: str | None = Field(description="Active LLM provider name, or null if none detected.")
    score_health: ScoreHealth | None = Field(
        default=None, description="Score distribution health metrics, or null if no data.",
    )
    avg_duration_ms: int | None = Field(
        default=None, description="Average pipeline duration in ms across recent optimizations.",
    )
    phase_durations: dict[str, int] = Field(
        default_factory=dict, description="Average duration per pipeline phase in ms.",
    )
    recent_errors: RecentErrors = Field(
        default_factory=RecentErrors, description="Error counts for recent time windows.",
    )
    sampling_capable: bool | None = Field(
        default=None,
        description="Whether the MCP client supports sampling/createMessage. "
        "Null if no MCP session or stale (>30 min).",
    )
    mcp_disconnected: bool = Field(
        default=False,
        description="True when the MCP client appears to have disconnected "
        "(no MCP POST activity within the 5-minute window, but capability was recently detected).",
    )
    available_tiers: list[str] = Field(
        default_factory=lambda: ["passthrough"],
        description="Currently reachable routing tiers.",
    )


@router.get("/health")
async def health_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> HealthResponse:
    """Liveness check with provider, version, and pipeline health metrics."""
    # Provider and routing state (live in-memory)
    routing = getattr(request.app.state, "routing", None)
    if routing:
        provider = routing.state.provider
        provider_name = routing.state.provider_name
        sampling_capable: bool | None = routing.state.sampling_capable
        # Report disconnected when sampling was known AND client is gone.
        # Use `is not None` instead of `is True` so a stale/null
        # sampling_capable after disconnect doesn't mask the state.
        mcp_disconnected = (
            not routing.state.mcp_connected
            and routing.state.sampling_capable is not None
        )
        available_tiers = routing.available_tiers
    else:
        provider = None
        provider_name = None
        sampling_capable = None
        mcp_disconnected = False
        available_tiers = ["passthrough"]

    # Pipeline metrics
    score_health: ScoreHealth | None = None
    avg_duration_ms: int | None = None
    recent_errors = RecentErrors()
    phase_durations: dict[str, int] = {}
    try:
        svc = OptimizationService(db)
        stats = await svc.get_score_distribution()
        overall = stats.get("overall_score", {})
        if overall.get("count", 0) > 0:
            clustering_warning = (
                overall["count"] >= 10 and overall["stddev"] < 0.3
            ) or (
                overall["count"] >= 50 and overall["stddev"] < 0.5
            )
            score_health = ScoreHealth(
                last_n_mean=overall["mean"],
                last_n_stddev=overall["stddev"],
                count=overall["count"],
                clustering_warning=clustering_warning,
            )

        # Average duration from recent optimizations
        result = await svc.list_optimizations(limit=50, sort_by="created_at", sort_order="desc")
        durations = [opt.duration_ms for opt in result["items"] if opt.duration_ms]
        if durations:
            avg_duration_ms = round(sum(durations) / len(durations))

        # Recent error counts
        error_counts = await svc.get_recent_error_counts()
        recent_errors = RecentErrors(**error_counts)

        # Per-phase average durations
        phase_durations = await svc.get_avg_duration_by_phase()
    except Exception:
        logger.debug("Health check metrics collection failed", exc_info=True)

    return HealthResponse(
        status="healthy" if provider else "degraded",
        version=__version__,
        provider=provider_name,
        score_health=score_health,
        avg_duration_ms=avg_duration_ms,
        phase_durations=phase_durations,
        recent_errors=recent_errors,
        sampling_capable=sampling_capable,
        mcp_disconnected=mcp_disconnected,
        available_tiers=available_tiers,
    )
