# backend/app/tools/seed.py
"""MCP tool handler for batch seeding."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Literal

from app.config import PROMPTS_DIR
from app.schemas.seed import SeedOutput
from app.services.agent_loader import AgentLoader
from app.services.batch_pipeline import (
    batch_taxonomy_assign,
    bulk_persist,
    estimate_batch_cost,
    run_batch,
)
from app.services.seed_orchestrator import SeedOrchestrator

logger = logging.getLogger(__name__)


async def handle_seed(
    project_description: str | None = None,
    workspace_path: str | None = None,
    repo_full_name: str | None = None,  # Reserved: GitHub explore (populates codebase_context)
    prompt_count: int = 30,
    agents: list[str] | None = None,
    prompts: list[str] | None = None,
    ctx: Any | None = None,  # MCP Context — reserved for future sampling-tier support
    routing: Any | None = None,  # Injected by REST endpoint from request.app.state.routing
) -> SeedOutput:
    """Full batch seeding flow: explore → generate → optimize → persist → taxonomy.

    Routing resolution:
    - REST context: caller passes routing=request.app.state.routing directly.
    - MCP context: falls back to get_routing() from tools/_shared.py.
    This avoids the _shared.py singleton being uninitialized in the backend process.
    """
    batch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    explore_t0 = t0

    # Resolve routing tier
    if routing is None:
        try:
            from app.tools._shared import get_routing
            routing = get_routing()
        except Exception:
            routing = None

    if routing is not None:
        from app.services.routing import RoutingContext
        caller: Literal["mcp", "rest"] = "mcp" if ctx is not None else "rest"
        decision = routing.resolve(RoutingContext(caller=caller))
        tier = decision.tier
        provider = decision.provider
    else:
        tier = "passthrough"
        provider = None

    # Resolve agent count for cost estimation
    agent_count = len(AgentLoader(PROMPTS_DIR / "seed-agents").list_enabled())

    # Cost estimation (done before pipeline so it can be included in seed_started)
    estimated_cost = estimate_batch_cost(
        prompt_count if not prompts else len(prompts),
        agent_count,
        tier,
    )

    # Log seed_started — single authoritative emit
    # (Phase 1 SeedOrchestrator does NOT emit this)
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_started",
            context={
                "batch_id": batch_id,
                "tier": tier,
                "project_description": (project_description or "")[:200],
                "prompt_count_target": prompt_count if not prompts else len(prompts),
                "has_user_prompts": prompts is not None,
                "agent_count": agent_count,
                "estimated_cost_usd": estimated_cost,
            },
        )
    except RuntimeError:
        pass

    # Track completed count for error events (populated as execution proceeds)
    prompts_completed_before_failure = 0

    # Determine prompt source
    if prompts:
        # User-provided prompts — skip generation
        generated_prompts = prompts
        prompts_generated = len(prompts)
        workspace_profile = None
        codebase_context = None

    elif project_description and provider:
        # Generated mode — explore + agents

        # Explore workspace context — degrades gracefully on failure
        workspace_profile = None
        codebase_context = None  # Populated when repo_full_name explore is implemented
        explore_t0 = time.monotonic()
        if workspace_path:
            try:
                from pathlib import Path

                from app.services.workspace_intelligence import WorkspaceIntelligence
                wi = WorkspaceIntelligence()
                workspace_profile = wi.analyze([Path(workspace_path)])
            except Exception as exc:
                logger.warning("Explore failed (continuing without context): %s", exc)
                workspace_profile = None

        # Log explore completion event
        try:
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_explore_complete",
                context={
                    "batch_id": batch_id,
                    "workspace_profile_length": len(workspace_profile or ""),
                    "codebase_context_length": len(codebase_context or ""),
                    "duration_ms": int((time.monotonic() - explore_t0) * 1000),
                },
            )
        except RuntimeError:
            pass

        try:
            orchestrator = SeedOrchestrator(provider=provider)
            gen_result = await orchestrator.generate(
                project_description=project_description,
                batch_id=batch_id,
                workspace_profile=workspace_profile,
                codebase_context=codebase_context,
                agent_names=agents,
                prompt_count=prompt_count,
            )
            generated_prompts = gen_result.prompts
            prompts_generated = len(generated_prompts)
        except Exception as exc:
            logger.error("Seed generation failed: %s", exc, exc_info=True)
            try:
                get_event_logger().log_decision(
                    path="hot", op="seed", decision="seed_failed",
                    context={
                        "batch_id": batch_id,
                        "phase": "generate",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:200],
                        "prompts_completed_before_failure": 0,
                    },
                )
            except RuntimeError:
                pass
            return SeedOutput(
                status="failed",
                batch_id=batch_id,
                tier=tier,
                prompts_generated=0,
                prompts_optimized=0,
                prompts_failed=0,
                estimated_cost_usd=estimated_cost,
                summary=f"Generation failed: {exc}",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

    else:
        return SeedOutput(
            status="failed",
            batch_id=batch_id,
            tier=tier,
            prompts_generated=0,
            prompts_optimized=0,
            prompts_failed=0,
            estimated_cost_usd=estimated_cost,
            summary="Requires project_description with a provider, or user-provided prompts.",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # Determine concurrency based on tier + provider type
    # CLI handles 10 parallel subprocesses; API rate limits cap at 5
    if tier == "internal" and provider is not None:
        max_parallel = 10 if provider.name == "claude_cli" else 5
    elif tier == "sampling":
        max_parallel = 2
    else:
        max_parallel = 1

    # Run batch pipeline — resolve service singletons for enrichment parity
    # with the regular internal pipeline (pattern injection, few-shot, adaptation,
    # domain resolution, z-score normalization).
    from app.database import async_session_factory
    from app.services.embedding_service import EmbeddingService
    from app.services.prompt_loader import PromptLoader

    # Taxonomy engine (singleton, may not be initialized on cold start)
    _taxonomy_engine = None
    try:
        from app.services.taxonomy import get_engine
        _taxonomy_engine = get_engine()
    except Exception:
        logger.debug("Taxonomy engine unavailable for seed enrichment")

    # Domain resolver (singleton, may not be initialized on cold start)
    _domain_resolver = None
    try:
        from app.services.domain_resolver import get_domain_resolver
        _domain_resolver = get_domain_resolver()
    except Exception:
        logger.debug("Domain resolver unavailable for seed enrichment")

    try:
        results = await run_batch(
            prompts=generated_prompts,
            provider=provider,  # type: ignore[arg-type]
            prompt_loader=PromptLoader(PROMPTS_DIR),
            embedding_service=EmbeddingService(),
            max_parallel=max_parallel,
            codebase_context=codebase_context if not prompts else None,
            batch_id=batch_id,
            session_factory=async_session_factory,
            taxonomy_engine=_taxonomy_engine,
            domain_resolver=_domain_resolver,
            tier=tier,
        )
    except Exception as exc:
        logger.error("Seed batch execution failed: %s", exc, exc_info=True)
        try:
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_failed",
                context={
                    "batch_id": batch_id,
                    "phase": "optimize",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:200],
                    "prompts_completed_before_failure": prompts_completed_before_failure,
                },
            )
        except RuntimeError:
            pass
        return SeedOutput(
            status="failed",
            batch_id=batch_id,
            tier=tier,
            prompts_generated=prompts_generated,
            prompts_optimized=0,
            prompts_failed=len(generated_prompts),
            estimated_cost_usd=estimated_cost,
            summary=f"Batch execution failed: {exc}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    prompts_completed_before_failure = sum(
        1 for r in results if r.status == "completed"
    )

    # Bulk persist (async_session_factory already imported above)
    try:
        await bulk_persist(results, async_session_factory, batch_id)
    except Exception as exc:
        logger.error("Seed persist failed: %s", exc, exc_info=True)
        completed = sum(1 for r in results if r.status == "completed")
        try:
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_failed",
                context={
                    "batch_id": batch_id,
                    "phase": "persist",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:200],
                    "prompts_completed_before_failure": completed,
                },
            )
        except RuntimeError:
            pass
        return SeedOutput(
            status="partial",
            batch_id=batch_id,
            tier=tier,
            prompts_generated=prompts_generated,
            prompts_optimized=completed,
            prompts_failed=len(results) - completed,
            estimated_cost_usd=estimated_cost,
            summary=f"Optimized {completed} prompts but persist failed: {exc}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # Taxonomy integration
    try:
        taxonomy_result = await batch_taxonomy_assign(
            results, async_session_factory, batch_id,
        )
    except Exception as exc:
        logger.warning("Taxonomy integration failed (non-fatal): %s", exc)
        taxonomy_result = {"clusters_created": 0, "domains_touched": []}

    # Final summary
    completed = sum(1 for r in results if r.status == "completed")
    failed = sum(1 for r in results if r.status == "failed")
    duration_ms = int((time.monotonic() - t0) * 1000)

    status = "completed" if failed == 0 else "partial"
    summary = (
        f"{completed} prompts optimized"
        f"{f', {failed} failed' if failed else ''}"
        f". {taxonomy_result.get('clusters_created', 0)} clusters created"
        f", domains: {', '.join(taxonomy_result.get('domains_touched', []))}"
    )

    # Log completion event — includes monitoring data
    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_completed",
            context={
                "batch_id": batch_id,
                "total_duration_ms": duration_ms,
                "prompts_generated": prompts_generated,
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "clusters_created": taxonomy_result.get("clusters_created", 0),
                "domains_touched": taxonomy_result.get("domains_touched", []),
                "cost_usd": estimated_cost,
                "tier": tier,
                # Sufficient for: prompts/min = prompts_optimized / (total_duration_ms/60000)
                #                 cost/prompt = cost_usd / prompts_optimized
                #                 failure_rate = prompts_failed / prompts_generated
            },
        )
    except RuntimeError:
        pass

    return SeedOutput(
        status=status,
        batch_id=batch_id,
        tier=tier,
        prompts_generated=prompts_generated,
        prompts_optimized=completed,
        prompts_failed=failed,
        estimated_cost_usd=estimated_cost,
        domains_touched=taxonomy_result.get("domains_touched", []),
        clusters_created=taxonomy_result.get("clusters_created", 0),
        summary=summary,
        duration_ms=duration_ms,
    )
