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
    injection_effectiveness: dict | None = Field(
        default=None, description="Score lift: injected vs non-injected optimizations.",
    )
    enrichment_effectiveness: dict | None = Field(
        default=None,
        description=(
            "E2 (phase-e-observability): per-profile aggregates over recent "
            "completed optimizations — {count, avg_overall_score, "
            "avg_improvement_score} keyed by enrichment_profile "
            "(code_aware / knowledge_work / cold_start). Lets operators "
            "confirm or disprove the profile-selection hypothesis with live "
            "data. Null when no rows carry an enrichment_profile."
        ),
    )
    recovery: dict | None = Field(default=None, description="Orphan recovery metrics.")
    classification_agreement: dict | None = Field(
        default=None, description="Heuristic vs LLM classification agreement rates.",
    )
    qualifier_vocab: dict | None = Field(
        default=None, description="Organic qualifier vocabulary cache stats.",
    )
    taxonomy_index_size: int | None = Field(
        default=None,
        description=(
            "Number of cluster centroids currently held in the live "
            "EmbeddingIndex. Null when the taxonomy engine isn't wired "
            "on app.state (e.g. tests without a live engine)."
        ),
    )
    avg_vocab_quality: float | None = Field(
        default=None,
        description=(
            "Rolling-window mean of vocabulary-generation quality scores "
            "(0.0-1.0). Null when no vocabulary has been generated yet."
        ),
    )
    domain_lifecycle: dict | None = Field(
        default=None, description="Domain dissolution lifecycle stats.",
    )
    global_patterns: dict[str, int] = Field(default_factory=dict)
    write_queue: dict | None = Field(
        default=None,
        description=(
            "WriteQueue metrics snapshot — depth, in_flight counts, "
            "p95/p99 latency over the rolling reservoir window, "
            "worker_alive flag, and totals (submitted/completed/failed/"
            "timeout/overload). Null when the queue has not been "
            "initialised on app.state (e.g. tests without a live "
            "lifespan)."
        ),
    )
    cold_path: dict | None = Field(
        default=None,
        description=(
            "v0.4.16 P1a Cycle 2 (spec § 5.5): cold-path lifecycle + "
            "performance metrics. Fields: last_run_at, last_run_duration_ms, "
            "last_run_q_delta, last_run_phases_committed, last_run_status, "
            "peer_skip_count_24h, rejection_count_24h, "
            "phase_failure_count_24h, p95_phase_duration_ms (per-phase "
            "dict). Null when the event ring buffer is empty or "
            "uninitialised."
        ),
    )
    legacy_state_observed: int = Field(
        default=0,
        description=(
            "Diagnostic counter: number of pre-migration 'template' state values "
            "surfaced through /api/clusters/activity since last process restart. "
            "Non-zero values indicate residual legacy events in the ring buffer "
            "or JSONL history."
        ),
    )
    # Cross-service probe results (only populated when probes=True)
    services: dict[str, ServiceStatus] | None = Field(
        default=None, description="Live probe results for each service.",
    )
    cross_service: dict[str, ServiceStatus] | None = Field(
        default=None, description="Cross-service connectivity checks.",
    )
    rate_limit: dict | None = Field(
        default=None, description="Active rate-limit state, or null if clear.",
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
    headers: dict[str, str] | None = None,
    timeout: float = _PROBE_TIMEOUT,
) -> ServiceStatus:
    """Probe a service endpoint and return its status."""
    import httpx

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            if method == "POST":
                resp = await asyncio.wait_for(
                    client.post(url, json=payload, headers=headers, timeout=timeout),
                    timeout=timeout + 1.0,
                )
            else:
                resp = await asyncio.wait_for(
                    client.get(url, headers=headers, timeout=timeout),
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


async def _probe_all_services(
    mcp_connected: bool = True,
) -> tuple[dict[str, ServiceStatus], dict[str, ServiceStatus]]:
    """Run all service probes in parallel. Returns (services, cross_service).

    When *mcp_connected* is False the MCP probe is skipped entirely —
    the Streamable HTTP transport returns 400 without a valid session,
    producing noisy log lines on every health-check cycle.

    **Backend self-probe is skipped**: this code runs inside the backend
    process, so probing ourselves via HTTP is redundant.  More critically,
    with ``pool_size=1`` the self-probe deadlocks — the outer health
    request holds the sole DB connection while the self-probe request
    also needs one, causing a guaranteed pool timeout → false "down".
    The backend is reported as "up" unconditionally (if we can execute
    this function, the backend is serving requests).
    """
    # Backend: we ARE the backend — skip the self-probe to avoid
    # pool_size=1 deadlock (outer request holds the only DB connection).
    backend_status = ServiceStatus(status="up", latency_ms=0)

    # External service probes
    frontend_probe = _probe_service("http://127.0.0.1:5199/")

    # Cross-service probes
    # frontend_to_backend: also skipped (same deadlock as backend self-probe)
    fe_to_be_status = ServiceStatus(status="ok", latency_ms=0)
    mcp_to_be_probe = _probe_service(
        "http://127.0.0.1:8000/api/events/_publish",
        method="POST",
        payload={"event_type": "_health_check", "data": {}},
    )

    if mcp_connected:
        # MCP Streamable HTTP transport requires Accept with both JSON and SSE,
        # otherwise the server returns 406 Not Acceptable.
        mcp_probe = _probe_service(
            "http://127.0.0.1:8001/mcp",
            method="POST",
            payload={"jsonrpc": "2.0", "method": "ping", "id": 1},
            headers={"Accept": "application/json, text/event-stream"},
        )
        try:
            frontend_result, mcp_status, mcp_to_be_result = await asyncio.wait_for(
                asyncio.gather(frontend_probe, mcp_probe, mcp_to_be_probe),
                timeout=_OVERALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            timeout_status = ServiceStatus(status="timeout", error="Overall deadline exceeded")
            frontend_result = mcp_status = mcp_to_be_result = timeout_status
    else:
        # No active MCP session — skip the probe to avoid 400 noise
        try:
            frontend_result, mcp_to_be_result = await asyncio.wait_for(
                asyncio.gather(frontend_probe, mcp_to_be_probe),
                timeout=_OVERALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            timeout_status = ServiceStatus(status="timeout", error="Overall deadline exceeded")
            frontend_result = mcp_to_be_result = timeout_status

        mcp_status = ServiceStatus(status="not_connected", error="No active MCP session")

    services = {
        "backend": backend_status,
        "frontend": frontend_result,
        "mcp": mcp_status,
    }
    cross_service = {
        "frontend_to_backend": fe_to_be_status,
        "mcp_to_backend": ServiceStatus(
            status="ok" if mcp_to_be_result.status == "up" else "failed",
            latency_ms=mcp_to_be_result.latency_ms,
            error=mcp_to_be_result.error,
        ),
    }
    return services, cross_service


_CRITICAL_SERVICES = frozenset({"backend", "frontend"})


# ---------------------------------------------------------------------------
# v0.4.16 P1a Cycle 2: cold-path metrics aggregator
# ---------------------------------------------------------------------------


def _get_cold_path_metrics() -> dict | None:
    """Aggregate the /api/health cold_path block from the event ring buffer
    + the per-phase latency reservoir.

    Spec § 5.5. Returns a dict with the 9 fields required by Cycle 2 tests
    even when the ring buffer is empty (last_run_* fields are None,
    counters are 0, p95 dict has 4 phase keys with None values).
    """
    from datetime import timedelta

    cold_path_block: dict = {
        "last_run_at": None,
        "last_run_duration_ms": None,
        "last_run_q_delta": None,
        "last_run_phases_committed": None,
        "last_run_status": None,
        "peer_skip_count_24h": 0,
        "rejection_count_24h": 0,
        "phase_failure_count_24h": 0,
        "p95_phase_duration_ms": {
            "1_reembed": None,
            "2_reassign": None,
            "3_relabel": None,
            "4_repair": None,
        },
    }

    try:
        from app.services.taxonomy.cold_path import (
            _COLD_PATH_LATENCY_RESERVOIR,
            _get_phase_p95,
            _PHASE_KEYS,
        )
        for phase_key in _PHASE_KEYS:
            cold_path_block["p95_phase_duration_ms"][phase_key] = _get_phase_p95(phase_key)
    except Exception:
        logger.debug("cold_path latency reservoir read failed", exc_info=True)

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        ring = get_event_logger().get_recent(limit=500, path="cold")
    except RuntimeError:
        return cold_path_block
    except Exception:
        logger.debug("cold_path metrics ring buffer read failed", exc_info=True)
        return cold_path_block

    if not ring:
        return cold_path_block

    # Find the most recent cold_path_completed / cold_path_phase_rolled_back
    # to summarize the last run.
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    last_completed: dict | None = None
    last_rolled_back: dict | None = None
    peer_skipped = 0
    rejection_count = 0
    phase_failure_count = 0
    for ev in ring:
        decision = ev.get("decision")
        try:
            ev_ts_str = ev.get("ts")
            ev_ts = (
                datetime.fromisoformat(ev_ts_str.replace("Z", "+00:00"))
                if ev_ts_str else None
            )
        except (TypeError, ValueError):
            ev_ts = None
        if decision == "cold_path_completed" and last_completed is None:
            last_completed = ev
        if decision == "cold_path_phase_rolled_back" and last_rolled_back is None:
            last_rolled_back = ev
        if ev_ts and ev_ts >= cutoff:
            if decision == "peer_skipped":
                peer_skipped += 1
            elif decision == "cold_path_phase_rolled_back":
                ctx = ev.get("context") or {}
                if ctx.get("reason") == "q_regression":
                    rejection_count += 1
                else:
                    phase_failure_count += 1

    cold_path_block["peer_skip_count_24h"] = peer_skipped
    cold_path_block["rejection_count_24h"] = rejection_count
    cold_path_block["phase_failure_count_24h"] = phase_failure_count

    last_run = last_completed or last_rolled_back
    if last_run:
        ctx = last_run.get("context") or {}
        cold_path_block["last_run_at"] = last_run.get("ts")
        cold_path_block["last_run_duration_ms"] = ctx.get("total_duration_ms")
        cold_path_block["last_run_q_delta"] = ctx.get("q_delta")
        cold_path_block["last_run_phases_committed"] = ctx.get("phases_committed")
        if last_run.get("decision") == "cold_path_completed":
            cold_path_block["last_run_status"] = "accepted"
        else:
            ctx_reason = ctx.get("reason")
            if ctx_reason == "q_regression":
                cold_path_block["last_run_status"] = "rejected"
            else:
                cold_path_block["last_run_status"] = "failed"

    return cold_path_block


def _compute_overall_status(
    provider: object | None,
    services: dict[str, ServiceStatus] | None,
    cross_service: dict[str, ServiceStatus] | None,
) -> str:
    """Determine overall health status.

    - healthy: provider available AND all services up
    - degraded: optional service down (MCP), cross-link broken, OR no provider
    - unhealthy: any *critical* service (backend/frontend) is down
    """
    if services is None:
        return "healthy" if provider else "degraded"

    critical_down = any(
        s.status not in ("up", "not_connected")
        for name, s in services.items()
        if name in _CRITICAL_SERVICES
    )
    optional_down = any(
        s.status not in ("up",)
        for name, s in services.items()
        if name not in _CRITICAL_SERVICES
    )
    cross_broken = cross_service and any(
        s.status != "ok" for s in cross_service.values()
    )

    if critical_down:
        return "unhealthy"
    if optional_down or cross_broken or not provider:
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
                count=int(overall["count"]),
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

    # E2: per-profile enrichment effectiveness.  Isolated try/except so a
    # JSON-extractor hiccup on old rows can't collapse the whole endpoint.
    enrichment_effectiveness: dict | None = None
    try:
        profile_stats = await svc.get_enrichment_profile_effectiveness()
        # Keep the field null (not ``{}``) when no profile data exists —
        # matches the frontend pattern for other null-by-default sections.
        enrichment_effectiveness = profile_stats or None
    except Exception:
        logger.debug("Health check enrichment-effectiveness query failed", exc_info=True)

    # Injection provenance reliability
    injection_stats: dict[str, int] = {}
    try:
        from app.services.pattern_injection import get_injection_stats
        injection_stats = get_injection_stats()
    except Exception:
        pass

    # Injection effectiveness — cached on app.state by warm path
    injection_effectiveness: dict | None = getattr(
        request.app.state, "injection_effectiveness", None
    )

    # E1: Classification agreement rates
    agreement_data: dict | None = None
    try:
        from app.services.classification_agreement import get_classification_agreement
        _agr = get_classification_agreement()
        if _agr.total > 0:
            agreement_data = _agr.rates()
    except Exception:
        pass

    # Orphan recovery metrics
    recovery_metrics: dict | None = None
    try:
        from app.services.orphan_recovery import recovery_service
        recovery_metrics = recovery_service.get_metrics()
    except Exception:
        pass

    # Organic qualifier vocabulary stats
    qualifier_vocab_stats: dict | None = None
    try:
        from app.services.domain_signal_loader import get_signal_loader
        _loader = get_signal_loader()
        if _loader:
            qualifier_vocab_stats = _loader.stats()
    except Exception:
        pass

    # Merge engine-side vocab quality scores into qualifier_vocab
    # AND surface them at the top level for quick operator checks (I-1).
    avg_vocab_quality: float | None = None
    taxonomy_index_size: int | None = None
    try:
        _engine = getattr(request.app.state, "taxonomy_engine", None)
        if _engine is not None:
            scores = getattr(_engine, "_vocab_quality_scores", None)
            if scores:
                avg_vocab_quality = round(sum(scores) / len(scores), 4)
                qualifier_vocab_stats = qualifier_vocab_stats or {}
                qualifier_vocab_stats["avg_vocab_quality"] = avg_vocab_quality
            _index = getattr(_engine, "embedding_index", None)
            if _index is not None:
                _size = getattr(_index, "size", None)
                if isinstance(_size, int):
                    taxonomy_index_size = _size
    except Exception:
        logger.debug("Health check taxonomy engine stats failed", exc_info=True)

    # Domain lifecycle stats
    domain_lifecycle_stats: dict | None = None
    try:
        _engine = getattr(request.app.state, "taxonomy_engine", None)
        if _engine:
            domain_lifecycle_stats = getattr(_engine, "_domain_lifecycle_stats", None)
    except Exception:
        pass

    # Diagnostic: legacy 'template' state observations in activity ring buffer
    legacy_state_observed: int = 0
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        legacy_state_observed = get_event_logger().legacy_state_observed
    except RuntimeError:
        pass

    # Cross-service probes (skip when called as self-check to prevent recursion)
    services_result = None
    cross_service_result = None
    if probes:
        try:
            _mcp_connected = routing.state.mcp_connected if routing else False
            services_result, cross_service_result = await _probe_all_services(
                mcp_connected=_mcp_connected,
            )
        except Exception as probe_exc:
            logger.warning("Cross-service probes failed: %s", probe_exc)

    # Determine overall status
    overall_status = _compute_overall_status(provider, services_result, cross_service_result)

    # Set 503 status code when unhealthy
    if overall_status == "unhealthy":
        response.status_code = 503

    # Rate limit state
    from app.services.rate_limit_state import get_rate_limit_store
    rate_limit_state = get_rate_limit_store().get_active()

    # v0.4.13 cycle 9 — write_queue metrics (spec §6.1, 13 fields).
    # ``request.app.state.write_queue`` is populated during lifespan
    # startup; tests that bypass lifespan see ``None`` here.
    write_queue_metrics: dict | None = None
    try:
        wq = getattr(request.app.state, "write_queue", None)
        if wq is not None:
            snap = wq.metrics_snapshot()
            # Surface as plain JSON-friendly dict (dataclass.__dict__).
            write_queue_metrics = {
                "depth": snap.depth,
                "in_flight": snap.in_flight,
                "total_submitted": snap.total_submitted,
                "total_completed": snap.total_completed,
                "total_failed": snap.total_failed,
                "total_timeout": snap.total_timeout,
                "total_overload": snap.total_overload,
                "p95_latency_ms": snap.p95_latency_ms,
                "p99_latency_ms": snap.p99_latency_ms,
                "max_observed_depth": snap.max_observed_depth,
                "worker_alive": snap.worker_alive,
                "metrics_window_seconds": snap.metrics_window_seconds,
                "metrics_sample_count": snap.metrics_sample_count,
            }
    except Exception:
        logger.debug("Health check write_queue metrics failed", exc_info=True)

    # v0.4.16 P1a Cycle 2 (spec § 5.5): cold-path metrics block.
    cold_path_metrics = _get_cold_path_metrics()

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
        injection_effectiveness=injection_effectiveness,
        enrichment_effectiveness=enrichment_effectiveness,
        recovery=recovery_metrics,
        classification_agreement=agreement_data,
        qualifier_vocab=qualifier_vocab_stats,
        taxonomy_index_size=taxonomy_index_size,
        avg_vocab_quality=avg_vocab_quality,
        domain_lifecycle=domain_lifecycle_stats,
        global_patterns={
            "active": gp_active,
            "demoted": gp_demoted,
            "retired": gp_retired,
            "total": gp_active + gp_demoted + gp_retired,
        },
        write_queue=write_queue_metrics,
        cold_path=cold_path_metrics,
        legacy_state_observed=legacy_state_observed,
        services=services_result,
        cross_service=cross_service_result,
        rate_limit=rate_limit_state,
        timestamp=datetime.now(UTC).isoformat(),
    )
