# backend/app/routers/seed.py
"""Batch seed REST endpoints (Foundation P3 cycle 12 shim, v0.4.18).

Refactored from the legacy ``handle_seed`` dispatch to route through
``RunOrchestrator.run('seed_agent', ...)``. Synchronous semantics are
preserved per spec Q3 Path 1 — the POST awaits generator completion and
returns ``SeedOutput`` with the additive ``run_id`` field.

Two new GET endpoints expose the unified ``RunRow`` substrate at the
seed-mode surface:

  - ``GET /api/seed`` — paginated list of ``RunRow WHERE mode='seed_agent'``
  - ``GET /api/seed/{run_id}`` — full ``RunRow`` detail (404 with
    ``detail='run_not_found'`` on miss or mode mismatch)

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 6.3
Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 12
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR
from app.database import get_db
from app.models import RunRow
from app.routers.runs import _serialize_full, _serialize_summary
from app.schemas.runs import RunListResponse, RunRequest, RunResult
from app.schemas.seed import SeedOutput, SeedRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["seed"])


def _build_failed_output(summary: str) -> SeedOutput:
    """Construct a SeedOutput for the orchestrator-unavailable / uncaught-exception path.

    Preserves the pre-Foundation contract that POST /api/seed never raises
    a 5xx — any failure surfaces as HTTP 200 with ``status='failed'`` so
    REST callers (HistoryPanel, Topology view) can handle it uniformly.
    """
    return SeedOutput(
        status="failed",
        batch_id=None,
        tier=None,
        prompts_generated=0,
        prompts_optimized=0,
        prompts_failed=0,
        estimated_cost_usd=None,
        domains_touched=[],
        clusters_created=0,
        summary=summary,
        duration_ms=0,
        run_id=None,
    )


@router.post("/api/seed", response_model=SeedOutput)
async def seed_taxonomy(body: SeedRequest, request: Request) -> SeedOutput:
    """Synchronous seed run (sync semantics preserved per spec Q3 Path 1).

    Persists ``RunRow`` at start, awaits ``RunOrchestrator.run`` to
    completion, returns ``SeedOutput`` with the additive ``run_id`` field.
    Any uncaught exception still surfaces as HTTP 200 with
    ``SeedOutput(status='failed', ...)`` (preserves pre-Foundation contract).

    The router populates ``payload['provider']`` and ``payload['tier']``
    from ``request.app.state.routing`` so the generator's early-failure
    path (no project_description + no prompts + no provider → status='failed')
    behaves identically to the legacy ``handle_seed`` flow.
    """
    orchestrator = getattr(request.app.state, "run_orchestrator", None)
    if orchestrator is None:
        return _build_failed_output(
            "RunOrchestrator unavailable; seed runs are degraded.",
        )

    # Resolve routing tier + provider from app.state for the generator's
    # early-failure gate. Mirrors handle_seed:64-80.
    routing = getattr(request.app.state, "routing", None)
    if routing is not None:
        from app.services.routing import RoutingContext
        decision = routing.resolve(RoutingContext(caller="rest"))
        tier = decision.tier
        provider = decision.provider
    else:
        tier = "passthrough"
        provider = None

    context_service = getattr(request.app.state, "context_service", None)

    payload = body.model_dump()
    payload["tier"] = tier
    payload["provider"] = provider
    payload["context_service"] = context_service

    run_request = RunRequest(mode="seed_agent", payload=payload)

    try:
        # Awaits completion (sync semantics preserved per Q3 Path 1)
        run_row = await orchestrator.run("seed_agent", run_request)
    except Exception as exc:  # noqa: BLE001 — preserve HTTP 200 contract
        logger.error(
            "POST /api/seed: RunOrchestrator dispatch failed: %s",
            exc,
            exc_info=True,
        )
        return _build_failed_output(
            f"Seed run failed: {type(exc).__name__}: {exc}",
        )

    # SeedOutput.status maps directly from RunRow.status (same 4 values:
    # 'completed' | 'partial' | 'failed' | 'running'). With Q3 Path 1's
    # synchronous semantics, 'running' is never observed at this point —
    # RunOrchestrator.run() only returns after the generator reaches a
    # terminal state. The generator (SeedAgentGenerator) is responsible
    # for classifying terminal_status='partial' when prompts_failed > 0.
    # See spec section 5.5 for the classification rules.
    #
    # None-guard every JSON-column accessor — partially-populated failed
    # runs may have aggregate / seed_agent_meta / taxonomy_delta as NULL.
    aggregate = run_row.aggregate or {}
    seed_meta = run_row.seed_agent_meta or {}
    taxonomy_delta = run_row.taxonomy_delta or {}

    completed_at = run_row.completed_at or run_row.started_at
    duration_ms = int((completed_at - run_row.started_at).total_seconds() * 1000)

    return SeedOutput(
        status=run_row.status,
        batch_id=seed_meta.get("batch_id"),
        tier=seed_meta.get("tier") or tier,
        prompts_generated=run_row.prompts_generated or 0,
        prompts_optimized=aggregate.get("prompts_optimized", 0),
        prompts_failed=aggregate.get("prompts_failed", 0),
        estimated_cost_usd=seed_meta.get("estimated_cost_usd"),
        domains_touched=taxonomy_delta.get("domains_touched", []),
        clusters_created=taxonomy_delta.get("clusters_created", 0),
        summary=aggregate.get("summary", ""),
        duration_ms=duration_ms,
        run_id=run_row.id,
    )


@router.get("/api/seed", response_model=RunListResponse)
async def list_seed_runs(
    status: str | None = Query(None, description="Filter by run status."),
    project_id: str | None = Query(
        None, description="Filter by project node id.",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    """Paginated list of seed-mode runs, ordered by ``started_at desc``.

    Only ``RunRow`` rows with ``mode='seed_agent'`` are returned —
    probe-mode rows live under ``GET /api/probes`` (Cycle 11). Both
    modes share the underlying ``run_row`` table.
    """
    base = select(RunRow).where(RunRow.mode == "seed_agent")
    if status is not None:
        base = base.where(RunRow.status == status)
    if project_id is not None:
        base = base.where(RunRow.project_id == project_id)

    total_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(total_q)).scalar_one()

    page_q = (
        base.order_by(RunRow.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(page_q)).scalars().all()
    items = [_serialize_summary(r) for r in rows]

    has_more = offset + len(items) < total
    next_offset = offset + len(items) if has_more else None

    return RunListResponse(
        total=int(total),
        count=len(items),
        offset=offset,
        items=items,
        has_more=has_more,
        next_offset=next_offset,
    )


# NOTE: /api/seed/agents MUST be declared BEFORE /api/seed/{run_id} so
# the static-path route wins ahead of the dynamic-path route. FastAPI
# matches in declaration order — without this ordering, a request to
# /api/seed/agents would be captured by the {run_id} dynamic route and
# return 404 (no row with id='agents').
@router.get("/api/seed/agents")
async def list_seed_agents() -> list[dict]:
    """List available seed agents with metadata.

    NOTE: This endpoint is not in the spec but required for the SeedModal
    agent selector in the Phase 4 frontend.
    """
    from app.services.agent_loader import AgentLoader
    loader = AgentLoader(PROMPTS_DIR / "seed-agents")
    return [
        {
            "name": a.name,
            "description": a.description,
            "task_types": a.task_types,
            "prompts_per_run": a.prompts_per_run,
            "enabled": a.enabled,
        }
        for a in loader.list_enabled()
    ]


@router.get("/api/seed/{run_id}", response_model=RunResult)
async def get_seed_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> RunResult:
    """Full ``RunRow`` detail for a seed-mode run id, or 404.

    Defends against accidentally returning a probe-mode row that happens
    to share the id space — though the orchestrator never reuses ids,
    the explicit ``mode == 'seed_agent'`` guard preserves the surface
    contract that this endpoint only exposes seed runs.
    """
    row = await db.get(RunRow, run_id)
    if row is None or row.mode != "seed_agent":
        raise HTTPException(status_code=404, detail="run_not_found")
    return _serialize_full(row)
