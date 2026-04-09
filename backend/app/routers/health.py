"""Health check endpoint with pipeline metrics and cross-service probes.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app._version import __version__
from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import PromptCluster
from app.services.optimization_service import OptimizationService
from app.services.pipeline_constants import DOMAIN_COUNT_CEILING

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


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


class ServiceStatus(BaseModel):
    status: str = Field(description="'up', 'down', or 'timeout'.")
    latency_ms: int | None = Field(default=None, description="Response time in ms.")
    error: str | None = Field(default=None, description="Error message if not 'up'.")


class HealthResponse(BaseModel):
    status: str = Field(description="'healthy', 'degraded', or 'unhealthy'.")
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
        description="True when the MCP client appears to have disconnected.",
    )
    available_tiers: list[str] = Field(
        default_factory=lambda: ["passthrough"],
        description="Currently reachable routing tiers.",
    )
    domain_count: int = Field(default=0, description="Number of active domain nodes.")
    domain_ceiling: int = Field(default=DOMAIN_COUNT_CEILING, description="Max domain nodes.")
    project_count: int = Field(default=0, description="Number of project hierarchy nodes.")
    injection_stats: dict[str, int] = Field(
        default_factory=dict, description="Pattern injection provenance counts.",
    )
    global_patterns: dict[str, int] = Field(default_factory=dict)
    # Cross-service probe results (only populated when probes=True)
    services: dict[str, ServiceStatus] | None = Field(
        default=None, description="Live probe results for each service.",
    )
    cross_service: dict[str, ServiceStatus] | None = Field(
        default=None, description="Cross-service connectivity checks.",
    )
    timestamp: str | None = Field(default=None, description="ISO 8601 timestamp.")


# ---------------------------------------------------------------------------
# Service probing
# ---------------------------------------------------------------------------

_PROBE_TIMEOUT = 5.0  # per-probe timeout in seconds
_OVERALL_TIMEOUT = 15.0  # overall deadline for all probes


async def _probe_service(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = _PROBE_TIMEOUT,
) -> ServiceStatus:
    """Probe a service endpoint and return its status."""
    import httpx

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            if method == "POST":
                resp = await asyncio.wait_for(
                    client.post(url, json=payload, timeout=timeout),
                    timeout=timeout + 1.0,
                )
            else:
                resp = await asyncio.wait_for(
                    client.get(url, timeout=timeout),
                    timeout=timeout + 1.0,
                )
        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code < 500:
            return ServiceStatus(status="up", latency_ms=latency)
        return ServiceStatus(
            status="down", latency_ms=latency,
            error=f"HTTP {resp.status_code}",
        )
    except asyncio.TimeoutError:
        latency = int((time.monotonic() - t0) * 1000)
        return ServiceStatus(status="timeout", latency_ms=latency, error="Probe timed out")
    except Exception as exc:
        latency = int((time.monotonic() - t0) * 1000)
        return ServiceStatus(status="down", latency_ms=latency, error=str(exc)[:200])


async def _probe_all_services() -> tuple[dict[str, ServiceStatus], dict[str, ServiceStatus]]:
    """Run all service probes in parallel. Returns (services, cross_service)."""
    # Direct service probes
    backend_probe = _probe_service(
        "http://127.0.0.1:8000/api/health?probes=false",
    )
    frontend_probe = _probe_service("http://127.0.0.1:5199/")
    mcp_probe = _probe_service(
        "http://127.0.0.1:8001/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )

    # Cross-service probes
    fe_to_be_probe = _probe_service(
        "http://127.0.0.1:8000/api/health?probes=false",
    )
    mcp_to_be_probe = _probe_service(
        "http://127.0.0.1:8000/api/events/_publish",
        method="POST",
        payload={"event_type": "_health_check", "data": {}},
    )

    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                backend_probe, frontend_probe, mcp_probe,
                fe_to_be_probe, mcp_to_be_probe,
            ),
            timeout=_OVERALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        timeout_status = ServiceStatus(status="timeout", error="Overall deadline exceeded")
        results = [timeout_status] * 5

    services = {
        "backend": results[0],
        "frontend": results[1],
        "mcp": results[2],
    }
    cross_service = {
        "frontend_to_backend": ServiceStatus(
            status="ok" if results[3].status == "up" else "failed",
            latency_ms=results[3].latency_ms,
            error=results[3].error,
        ),
        "mcp_to_backend": ServiceStatus(
            status="ok" if results[4].status == "up" else "failed",
            latency_ms=results[4].latency_ms,
            error=results[4].error,
        ),
    }
    return services, cross_service


def _compute_overall_status(
    provider: object | None,
    services: dict[str, ServiceStatus] | None,
    cross_service: dict[str, ServiceStatus] | None,
) -> str:
    """Determine overall health status.

    - healthy: provider available AND all services up
    - degraded: services up but cross-link broken, OR no provider
    - unhealthy: any service is down
    """
    if services is None:
        return "healthy" if provider else "degraded"

    any_down = any(s.status != "up" for s in services.values())
    cross_broken = cross_service and any(
        s.status != "ok" for s in cross_service.values()
    )

    if any_down:
        return "unhealthy"
    if cross_broken or not provider:
        return "degraded"
    return "healthy"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/health")
async def health_check(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
    probes: bool = Query(True, description="Run cross-service probes (set false for self-check)."),
) -> HealthResponse:
    """Liveness check with provider, version, pipeline health, and cross-service probes."""
    # Provider and routing state (live in-memory)
    routing = getattr(request.app.state, "routing", None)
    if routing:
        provider = routing.state.provider
        provider_name = routing.state.provider_name
        sampling_capable: bool | None = routing.state.sampling_capable
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

    # Domain proliferation metrics
    domain_count = 0
    project_count = 0
    try:
        domain_count = await db.scalar(
            select(func.count()).where(PromptCluster.state == "domain")
        ) or 0
        project_count = await db.scalar(
            select(func.count()).where(PromptCluster.state == "project")
        ) or 0
    except Exception:
        logger.debug("Health check domain_count query failed", exc_info=True)

    # ADR-005 Phase 2B: global patterns stats
    from app.models import GlobalPattern
    try:
        gp_active = (await db.scalar(
            select(func.count()).where(GlobalPattern.state == "active")
        )) or 0
        gp_demoted = (await db.scalar(
            select(func.count()).where(GlobalPattern.state == "demoted")
        )) or 0
        gp_retired = (await db.scalar(
            select(func.count()).where(GlobalPattern.state == "retired")
        )) or 0
    except Exception:
        gp_active = gp_demoted = gp_retired = 0

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

        result = await svc.list_optimizations(limit=50, sort_by="created_at", sort_order="desc")
        durations = [opt.duration_ms for opt in result["items"] if opt.duration_ms]
        if durations:
            avg_duration_ms = round(sum(durations) / len(durations))

        error_counts = await svc.get_recent_error_counts()
        recent_errors = RecentErrors(**error_counts)

        phase_durations = await svc.get_avg_duration_by_phase()
    except Exception:
        logger.debug("Health check metrics collection failed", exc_info=True)

    # Injection provenance reliability
    injection_stats: dict[str, int] = {}
    try:
        from app.services.pattern_injection import get_injection_stats
        injection_stats = get_injection_stats()
    except Exception:
        pass

    # Cross-service probes (skip when called as self-check to prevent recursion)
    services_result = None
    cross_service_result = None
    if probes:
        try:
            services_result, cross_service_result = await _probe_all_services()
        except Exception as probe_exc:
            logger.warning("Cross-service probes failed: %s", probe_exc)

    # Determine overall status
    overall_status = _compute_overall_status(provider, services_result, cross_service_result)

    # Set 503 status code when unhealthy
    if overall_status == "unhealthy":
        response.status_code = 503

    return HealthResponse(
        status=overall_status,
        version=__version__,
        provider=provider_name,
        score_health=score_health,
        avg_duration_ms=avg_duration_ms,
        phase_durations=phase_durations,
        recent_errors=recent_errors,
        sampling_capable=sampling_capable,
        mcp_disconnected=mcp_disconnected,
        available_tiers=available_tiers,
        domain_count=domain_count,
        domain_ceiling=DOMAIN_COUNT_CEILING,
        project_count=project_count,
        injection_stats=injection_stats,
        global_patterns={
            "active": gp_active,
            "demoted": gp_demoted,
            "retired": gp_retired,
            "total": gp_active + gp_demoted + gp_retired,
        },
        services=services_result,
        cross_service=cross_service_result,
        timestamp=datetime.now(UTC).isoformat(),
    )
