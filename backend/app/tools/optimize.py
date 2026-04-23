"""Handler for synthesis_optimize MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import time
import uuid

from mcp.server.fastmcp import Context

from app.config import PROMPTS_DIR, settings
from app.database import async_session_factory
from app.models import Optimization
from app.schemas.mcp_models import OptimizeOutput
from app.services.event_notification import notify_event_bus
from app.services.passthrough import assemble_passthrough_prompt
from app.services.pipeline import PipelineOrchestrator
from app.services.preferences import PreferencesService
from app.services.project_service import resolve_repo_project
from app.services.routing import RoutingContext
from app.tools._shared import (
    DATA_DIR,
    auto_resolve_repo,
    get_context_service,
    get_routing,
    get_taxonomy_engine,
)

logger = logging.getLogger(__name__)


async def handle_optimize(
    prompt: str,
    strategy: str | None,
    repo_full_name: str | None,
    workspace_path: str | None,
    applied_pattern_ids: list[str] | None,
    ctx: Context | None,
) -> OptimizeOutput:
    """Run the full optimization pipeline on a prompt."""
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )
    if len(prompt) > settings.MAX_RAW_PROMPT_CHARS:
        raise ValueError(
            "Prompt too long (%d chars). Maximum is %d characters."
            % (len(prompt), settings.MAX_RAW_PROMPT_CHARS)
        )

    # ---- Auto-resolve repo from linked repo if not provided ----
    effective_repo = await auto_resolve_repo(repo_full_name)

    # ---- Hoist: single PreferencesService + snapshot for all paths ----
    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()
    effective_strategy = strategy or prefs.get("defaults.strategy", prefs_snapshot) or "auto"

    # ---- Routing decision (before enrichment so we know the tier) ----
    routing = get_routing()
    ctx_routing = RoutingContext(preferences=prefs_snapshot, caller="mcp")
    decision = routing.resolve(ctx_routing)

    # ---- Unified context enrichment ----
    # Default workspace_path to PROJECT_ROOT when not provided by the caller.
    from app.config import PROJECT_ROOT
    effective_workspace = workspace_path or str(PROJECT_ROOT)

    # B1: Freeze project_id at pipeline entry (resolve once, not at persist).
    # Resolution: repo chain → Legacy fallback via cached module-level
    # singleton on the MCP-process side.  Done BEFORE enrich() so B7 pattern
    # scoping can honor it.
    _, _effective_project_id = await resolve_repo_project(effective_repo)

    context_service = get_context_service()
    async with async_session_factory() as enrich_db:
        enrichment = await context_service.enrich(
            raw_prompt=prompt,
            tier=decision.tier,
            db=enrich_db,
            workspace_path=effective_workspace,
            mcp_ctx=ctx,
            provider=routing.state.provider,
            repo_full_name=effective_repo,
            applied_pattern_ids=applied_pattern_ids,
            preferences_snapshot=prefs_snapshot,
            project_id=_effective_project_id,
        )

    if decision.tier == "passthrough":
        # Passthrough: assemble template for external LLM processing
        logger.info("synthesis_optimize: tier=passthrough reason=%r", decision.reason)

        # Few-shot retrieval for passthrough (parity with internal/sampling)
        _pt_few_shot: str | None = None
        try:
            from app.services.pattern_injection import (
                format_few_shot_examples,
                retrieve_few_shot_examples,
            )
            async with async_session_factory() as _fs_db:
                _fs_examples = await retrieve_few_shot_examples(
                    raw_prompt=prompt, db=_fs_db, trace_id=str(uuid.uuid4()),
                )
            _pt_few_shot = format_few_shot_examples(_fs_examples)
        except Exception:
            logger.debug("Passthrough few-shot retrieval failed (non-fatal)")

        assembled, strategy_name = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=prompt,
            strategy_name=effective_strategy,
            strategy_intelligence=enrichment.strategy_intelligence,
            analysis_summary=enrichment.analysis_summary,
            codebase_context=enrichment.codebase_context,
            applied_patterns=enrichment.applied_patterns,
            divergence_alerts=enrichment.divergence_alerts,
            few_shot_examples=_pt_few_shot,
        )
        trace_id = str(uuid.uuid4())
        async with async_session_factory() as db:
            pending = Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=prompt,
                status="pending",
                trace_id=trace_id,
                provider="mcp_passthrough",
                routing_tier="passthrough",
                strategy_used=strategy_name,
                task_type=enrichment.task_type,
                domain=enrichment.domain_value,
                domain_raw=enrichment.domain_value,
                intent_label=enrichment.intent_label,
                repo_full_name=effective_repo,
                project_id=_effective_project_id,
                context_sources=enrichment.context_sources_dict,
            )
            db.add(pending)
            await db.commit()
        return OptimizeOutput(
            status="pending_external",
            pipeline_mode="passthrough",
            strategy_used=strategy_name,
            trace_id=trace_id,
            assembled_prompt=assembled,
            instructions=(
                "No local LLM provider detected. Process the assembled_prompt "
                "with your LLM, then call synthesis_save_result with the trace_id "
                "and the optimized output. Include optimized_prompt, changes_summary, "
                "task_type, strategy_used, and optionally scores "
                "(clarity, specificity, structure, faithfulness, conciseness — each 1.0-10.0)."
            ),
        )

    if decision.tier not in ("internal", "sampling"):
        raise ValueError(f"Unsupported tier: {decision.tier}")

    if decision.tier == "sampling":
        logger.info("synthesis_optimize: tier=sampling reason=%r", decision.reason)
        if not ctx or not hasattr(ctx, "session") or not ctx.session:
            raise ValueError("Sampling tier selected but no MCP session available")
        from app.providers.sampling import MCPSamplingProvider
        mcp_provider = MCPSamplingProvider(ctx)
        
        provider = mcp_provider
        provider_instances = None
        if decision.providers_by_phase:
            provider_instances = {}
            for phase, p_tier in decision.providers_by_phase.items():
                if p_tier == "sampling":
                    provider_instances[phase] = mcp_provider
                elif p_tier == "internal" and decision.provider:
                    provider_instances[phase] = decision.provider
    else:
        logger.info("synthesis_optimize: tier=internal provider=%s reason=%r", decision.provider_name, decision.reason)
        provider = decision.provider
        provider_instances = None

    start = time.monotonic()

    logger.info(
        "synthesis_optimize called: prompt_len=%d strategy=%s repo=%s",
        len(prompt), effective_strategy, effective_repo,
    )

    async with async_session_factory() as db:
        orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

        # Forward key pipeline events to the event bus so the web UI
        # shows live progress (phase transitions, scores, model IDs)
        # when the optimization is triggered from the IDE via MCP.
        # Note: events are prefixed with "optimization_" for the event bus,
        # so "status" → "optimization_status", etc.
        forward_events = {"status", "score_card", "prompt_preview", "suggestions"}
        # optimization_start already has the prefix — forward without double-prefixing.
        forward_verbatim = {"optimization_start"}

        pipeline_result: dict | None = None
        # Resolve domain resolver for the internal pipeline
        try:
            from app.tools._shared import get_domain_resolver
            _mcp_domain_resolver = get_domain_resolver()
        except (ValueError, Exception):
            _mcp_domain_resolver = None

        async for event in orchestrator.run(
            raw_prompt=prompt,
            provider=provider,  # type: ignore[arg-type]
            db=db,
            provider_instances=provider_instances,
            strategy_override=effective_strategy if effective_strategy != "auto" else None,
            codebase_context=enrichment.codebase_context,
            strategy_intelligence=enrichment.strategy_intelligence,
            context_sources=enrichment.context_sources_dict,
            repo_full_name=effective_repo,
            project_id=_effective_project_id,
            applied_pattern_ids=applied_pattern_ids,
            taxonomy_engine=get_taxonomy_engine(),
            domain_resolver=_mcp_domain_resolver,
            heuristic_task_type=enrichment.task_type,
            heuristic_domain=enrichment.domain_value,
            divergence_alerts=enrichment.divergence_alerts,
        ):
            if event.event in forward_events:
                await notify_event_bus(f"optimization_{event.event}", event.data)
            elif event.event in forward_verbatim:
                await notify_event_bus(event.event, event.data)
            if event.event == "optimization_complete":
                pipeline_result = event.data
            elif event.event == "error":
                error_msg = event.data.get("error", "Pipeline failed")
                logger.error("synthesis_optimize pipeline error: %s", error_msg)
                raise ValueError(error_msg)

        if not pipeline_result:
            raise ValueError(
                "Pipeline completed but produced no result. Check server logs for details."
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "synthesis_optimize completed in %dms: optimization_id=%s strategy=%s",
            elapsed_ms, pipeline_result.get("id", ""), pipeline_result.get("strategy_used", ""),
        )

        # Notify backend event bus (MCP runs in a separate process)
        await notify_event_bus("optimization_created", {
            "id": pipeline_result.get("id", ""),
            "trace_id": pipeline_result.get("trace_id", ""),
            "task_type": pipeline_result.get("task_type", ""),
            "intent_label": pipeline_result.get("intent_label", "general"),
            "domain": pipeline_result.get("domain", "general"),
            "domain_raw": pipeline_result.get("domain_raw", "general"),
            "strategy_used": pipeline_result.get("strategy_used", ""),
            "overall_score": pipeline_result.get("overall_score"),
            "provider": decision.provider_name if decision.tier == "internal" else ("mcp_sampling" if decision.tier == "sampling" else "unknown"),
            "status": "completed",
        })

        return OptimizeOutput(
            status="completed",
            pipeline_mode=decision.tier,
            optimization_id=pipeline_result.get("id", ""),
            optimized_prompt=pipeline_result.get("optimized_prompt", ""),
            task_type=pipeline_result.get("task_type", ""),
            strategy_used=pipeline_result.get("strategy_used", ""),
            changes_summary=pipeline_result.get("changes_summary", ""),
            scores=pipeline_result.get("optimized_scores", pipeline_result.get("scores", {})),
            original_scores=pipeline_result.get("original_scores", {}),
            score_deltas=pipeline_result.get("score_deltas", {}),
            scoring_mode=pipeline_result.get("scoring_mode", "independent"),
            suggestions=pipeline_result.get("suggestions", []),
            warnings=pipeline_result.get("warnings", []),
            model_used=pipeline_result.get("model_used"),
            models_by_phase=pipeline_result.get("models_by_phase"),
            intent_label=pipeline_result.get("intent_label"),
            domain=pipeline_result.get("domain"),
            trace_id=pipeline_result.get("trace_id"),
        )


async def _persist_sampling_failure(
    prompt: str, strategy: str, exc: Exception,
) -> str:
    """Persist a failed sampling Optimization record and notify event bus."""
    error_msg = f"Sampling pipeline failed: {type(exc).__name__}: {exc}"
    try:
        async with async_session_factory() as db:
            db.add(Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=prompt,
                status="failed",
                provider="mcp_sampling",
                routing_tier="sampling",
                strategy_used=strategy,
                task_type="general",
                changes_summary=error_msg,
            ))
            await db.commit()
    except Exception:
        logger.debug("Failed to persist sampling failure record", exc_info=True)
    await notify_event_bus("optimization_failed", {
        "error": error_msg,
        "provider": "mcp_sampling",
        "pipeline_mode": "sampling",
    })
    return error_msg


def _sampling_result_to_output(result: dict) -> OptimizeOutput:
    """Convert a sampling pipeline result dict to OptimizeOutput."""
    return OptimizeOutput(
        status="completed",
        pipeline_mode="sampling",
        optimization_id=result.get("optimization_id", ""),
        optimized_prompt=result.get("optimized_prompt", ""),
        task_type=result.get("task_type", ""),
        strategy_used=result.get("strategy_used", ""),
        changes_summary=result.get("changes_summary", ""),
        scores=result.get("scores", {}),
        original_scores=result.get("original_scores", {}),
        score_deltas=result.get("score_deltas", {}),
        scoring_mode=result.get("scoring_mode", ""),
        suggestions=result.get("suggestions", []),
        warnings=result.get("warnings", []),
        model_used=result.get("model_used"),
        models_by_phase=result.get("models_by_phase"),
        intent_label=result.get("intent_label"),
        domain=result.get("domain"),
        trace_id=result.get("trace_id"),
    )
