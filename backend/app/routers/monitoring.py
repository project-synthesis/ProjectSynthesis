"""Monitoring data export endpoint for CI/CD consumption.

Provides uptime, cold start latency, and LLM latency percentiles
computed from trace JSONL data.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["monitoring"])

# Cache for percentile computation (avoids repeated disk reads)
_latency_cache: dict[str, Any] | None = None
_latency_cache_time: float = 0.0
_LATENCY_CACHE_TTL = 60.0  # seconds


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class LLMLatencyPercentiles(BaseModel):
    p50_ms: int | None = Field(default=None, description="Median latency in ms.")
    p95_ms: int | None = Field(default=None, description="95th percentile latency in ms.")
    count: int = Field(default=0, description="Number of samples in the computation window.")


class MonitoringResponse(BaseModel):
    uptime_seconds: dict[str, float | None] = Field(
        description="Uptime in seconds per service (null if unknown).",
    )
    cold_start_ms: float | None = Field(
        default=None,
        description="Backend process startup time in ms (lifespan init to ready).",
    )
    llm_latency: dict[str, LLMLatencyPercentiles] = Field(
        default_factory=dict,
        description="Per-phase LLM latency percentiles from trace data.",
    )
    timestamp: str = Field(description="ISO 8601 timestamp of this response.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_service_uptimes(pid_dir: Path) -> dict[str, float | None]:
    """Estimate service uptimes from PID file modification times.

    PID files are created by init.sh at service launch time. Their mtime
    serves as a proxy for when the service started.
    """
    now = time.time()
    uptimes: dict[str, float | None] = {}
    for svc in ("backend", "frontend", "mcp"):
        pid_file = pid_dir / f"{svc}.pid"
        try:
            if pid_file.exists():
                # Verify the process is still running
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)  # signal 0 = check existence
                mtime = pid_file.stat().st_mtime
                uptimes[svc] = round(now - mtime, 1)
            else:
                uptimes[svc] = None
        except (OSError, ValueError):
            uptimes[svc] = None
    return uptimes


def _compute_latency_percentiles(
    traces_dir: Path,
    recent_days: int = 7,
) -> dict[str, LLMLatencyPercentiles]:
    """Compute p50 and p95 latency per phase from trace JSONL files.

    Reads the last `recent_days` of trace files and groups duration_ms
    by phase. Filters out duration_ms <= 0 (phantom/mock traces).
    Uses stdlib statistics.quantiles for percentile computation.
    """
    global _latency_cache, _latency_cache_time  # noqa: PLW0603
    now = time.monotonic()
    if _latency_cache is not None and (now - _latency_cache_time) < _LATENCY_CACHE_TTL:
        return _latency_cache

    durations_by_phase: dict[str, list[int]] = {}
    cutoff = datetime.now(UTC) - timedelta(days=recent_days)

    if not traces_dir.exists():
        return {}

    for path in sorted(traces_dir.glob("traces-*.jsonl")):
        # Parse date from filename
        try:
            date_str = path.stem.replace("traces-", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
            if file_date < cutoff:
                continue
        except ValueError:
            continue

        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                phase = entry.get("phase")
                dur = entry.get("duration_ms")
                if phase and isinstance(dur, (int, float)) and dur > 0:
                    durations_by_phase.setdefault(phase, []).append(int(dur))
        except OSError:
            continue

    result: dict[str, LLMLatencyPercentiles] = {}
    for phase, durations in durations_by_phase.items():
        if len(durations) < 2:
            result[phase] = LLMLatencyPercentiles(
                p50_ms=durations[0] if durations else None,
                p95_ms=durations[0] if durations else None,
                count=len(durations),
            )
            continue

        quantiles = statistics.quantiles(durations, n=20)
        # quantiles(n=20) returns 19 cut points: index 9 = p50, index 18 = p95
        p50 = int(quantiles[9]) if len(quantiles) > 9 else int(statistics.median(durations))
        p95 = int(quantiles[18]) if len(quantiles) > 18 else int(max(durations))
        result[phase] = LLMLatencyPercentiles(
            p50_ms=p50,
            p95_ms=p95,
            count=len(durations),
        )

    _latency_cache = result
    _latency_cache_time = now
    return result


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/monitoring")
async def monitoring(request: Request) -> MonitoringResponse:
    """CI/CD-consumable monitoring data: uptime, cold start, LLM latency."""
    # Backend uptime from in-memory monotonic timestamp
    startup_mono = getattr(request.app.state, "startup_monotonic", None)
    backend_uptime = round(time.monotonic() - startup_mono, 1) if startup_mono else None

    # Service uptimes from PID files
    service_uptimes = _estimate_service_uptimes(DATA_DIR / "pids")
    # Override backend with precise in-memory value
    service_uptimes["backend"] = backend_uptime

    # Cold start
    cold_start = getattr(request.app.state, "cold_start_ms", None)

    # LLM latency percentiles from trace data
    latency = _compute_latency_percentiles(DATA_DIR / "traces")

    return MonitoringResponse(
        uptime_seconds=service_uptimes,
        cold_start_ms=cold_start,
        llm_latency=latency,
        timestamp=datetime.now(UTC).isoformat(),
    )
