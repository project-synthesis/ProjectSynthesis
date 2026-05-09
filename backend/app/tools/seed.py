# backend/app/tools/seed.py
"""synthesis_seed MCP tool — Foundation P3 cycle 13 dispatch shim.

Refactored under Foundation P3 (v0.4.18) to dispatch through
``RunOrchestrator.run('seed_agent', ...)`` instead of the legacy inline
``handle_seed`` orchestration body. The orchestration logic
(``SeedOrchestrator.generate`` → ``run_batch`` → ``bulk_persist`` →
``batch_taxonomy_assign``) now lives in ``SeedAgentGenerator``
(v0.4.18 Cycle 7); this shim is now thin: validate inputs, build a
``RunRequest``, dispatch, and convert the resulting ``RunRow`` back
into a ``SeedOutput`` with the additive ``run_id`` field (spec § 7.2 +
§ 6.3 + § 8.1).

Routing + provider resolution preserved from the legacy handler so the
generator's early-failure gate (no project_description + no prompts +
no provider → ``status='failed'``) behaves identically to the previous
flow. The shim wraps ``RunOrchestrator.run`` in try/except so any
uncaught exception still returns ``SeedOutput(status='failed')`` — the
contract is HTTP 200 (REST) / structured failure (MCP) for any seed
shape, never a raised 5xx / tool-error.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.schemas.runs import RunRequest
from app.schemas.seed import SeedOutput

logger = logging.getLogger(__name__)


def _build_failed_output(
    summary: str,
    *,
    tier: str | None = None,
) -> SeedOutput:
    """Construct a ``SeedOutput`` for the orchestrator-unavailable /
    uncaught-exception path.

    Mirrors ``routers/seed._build_failed_output`` so the MCP tool surface
    matches the REST surface byte-for-byte. Preserves the pre-Foundation
    contract that the seed surface never raises a 5xx / tool error — any
    failure surfaces as a structured ``SeedOutput`` with ``status='failed'``.
    """
    return SeedOutput(
        status="failed",
        batch_id=None,
        tier=tier,
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


def _row_to_seed_output(row: Any, *, fallback_tier: str | None) -> SeedOutput:
    """Convert a ``RunRow`` (mode='seed_agent') into a ``SeedOutput``.

    Mirrors ``routers/seed.seed_taxonomy``'s serialization tail (lines
    127-148). Threads the additive ``run_id`` field from ``RunRow.id``,
    None-guards every JSON-column accessor (failed runs may have NULL
    ``aggregate`` / ``seed_agent_meta`` / ``taxonomy_delta``).
    """
    aggregate = row.aggregate or {}
    seed_meta = row.seed_agent_meta or {}
    taxonomy_delta = row.taxonomy_delta or {}

    completed_at = row.completed_at or row.started_at
    duration_ms = int((completed_at - row.started_at).total_seconds() * 1000)

    return SeedOutput(
        status=row.status,
        batch_id=seed_meta.get("batch_id"),
        tier=seed_meta.get("tier") or fallback_tier,
        prompts_generated=row.prompts_generated or 0,
        prompts_optimized=aggregate.get("prompts_optimized", 0),
        prompts_failed=aggregate.get("prompts_failed", 0),
        estimated_cost_usd=seed_meta.get("estimated_cost_usd"),
        domains_touched=taxonomy_delta.get("domains_touched", []),
        clusters_created=taxonomy_delta.get("clusters_created", 0),
        summary=aggregate.get("summary", ""),
        duration_ms=duration_ms,
        run_id=row.id,
    )


def _resolve_routing_and_tier(
    routing: Any | None,
    ctx: Any | None,
) -> tuple[str, Any | None]:
    """Resolve tier + provider from the routing manager.

    REST callers pass ``routing=request.app.state.routing`` directly; MCP
    callers leave it ``None`` and we fall back to the MCP-process
    ``_shared.get_routing()`` singleton. The caller class (rest vs mcp)
    is inferred from whether ``ctx`` is non-None — mirrors the legacy
    handler's heuristic.
    """
    if routing is None:
        try:
            from app.tools._shared import get_routing
            routing = get_routing()
        except Exception:
            routing = None

    if routing is None:
        return "passthrough", None

    from app.services.routing import RoutingContext
    caller: Literal["mcp", "rest"] = "mcp" if ctx is not None else "rest"
    decision = routing.resolve(RoutingContext(caller=caller))
    return decision.tier, decision.provider


async def handle_seed(
    project_description: str | None = None,
    workspace_path: str | None = None,
    repo_full_name: str | None = None,  # Reserved: GitHub explore (populates codebase_context)
    prompt_count: int = 30,
    agents: list[str] | None = None,
    prompts: list[str] | None = None,
    ctx: Any | None = None,  # MCP Context — used to detect REST vs MCP caller
    routing: Any | None = None,  # Injected by REST endpoint from request.app.state.routing
    context_service: Any | None = None,  # Injected by REST/MCP: ContextEnrichmentService
    write_queue: Any | None = None,  # v0.4.13 cycle 7.5: optional REST-side WriteQueue (no-op post-P3)
) -> SeedOutput:
    """Dispatch seed run through ``RunOrchestrator.run('seed_agent', ...)``.

    Replaces the v0.4.13 inline orchestration (explore → generate →
    run_batch → bulk_persist → batch_taxonomy_assign) — all of that now
    lives in ``SeedAgentGenerator`` (Cycle 7). This shim is responsible
    only for:
      - Resolving routing tier + provider (so the generator's early-failure
        gate behaves identically to the legacy code path)
      - Building the ``RunRequest`` payload
      - Dispatching via the MCP-process RunOrchestrator singleton
      - Converting the resulting ``RunRow`` into a ``SeedOutput`` with
        the additive ``run_id`` field

    Backward-compat:
      - ``write_queue`` parameter retained for callers that still pass it;
        Cycle 13 does not use it (the orchestrator's own queue handles
        persistence). It is silently ignored for shape stability.
      - Any uncaught exception during dispatch surfaces as
        ``SeedOutput(status='failed', run_id=None)`` — the contract is
        HTTP 200 / structured tool result, never a raised 5xx.

    Spec: § 7.2 + § 6.3 backward-compat (additive ``run_id`` only).
    """
    del workspace_path, write_queue  # legacy params; orchestrator owns these

    tier, provider = _resolve_routing_and_tier(routing, ctx)

    # Resolve the orchestrator. If unavailable, surface a structured
    # failure rather than raising — preserves the seed surface contract
    # that no shape of seed input raises a 5xx / tool error.
    try:
        from app.tools._shared import get_run_orchestrator
        orchestrator = get_run_orchestrator()
    except Exception:
        return _build_failed_output(
            "RunOrchestrator unavailable; seed runs are degraded.",
            tier=tier,
        )

    # Build the payload — generator picks up provider/tier/context_service
    # from here. Mirrors routers/seed.seed_taxonomy:97-101.
    payload: dict[str, Any] = {
        "project_description": project_description,
        "repo_full_name": repo_full_name,
        "prompt_count": prompt_count,
        "agents": agents,
        "prompts": prompts,
        "tier": tier,
        "provider": provider,
        "context_service": context_service,
    }
    run_request = RunRequest(mode="seed_agent", payload=payload)

    try:
        run_row = await orchestrator.run("seed_agent", run_request)
    except Exception as exc:  # noqa: BLE001 — preserve HTTP 200 / tool-result contract
        logger.error(
            "synthesis_seed: RunOrchestrator dispatch failed: %s",
            exc,
            exc_info=True,
        )
        return _build_failed_output(
            f"Seed run failed: {type(exc).__name__}: {exc}",
            tier=tier,
        )

    return _row_to_seed_output(run_row, fallback_tier=tier)
