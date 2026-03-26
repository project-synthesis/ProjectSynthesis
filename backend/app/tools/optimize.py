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
from app.services.routing import RoutingContext
from app.services.sampling_pipeline import run_sampling_pipeline
from app.tools._shared import (
    DATA_DIR,
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

    context_service = get_context_service()
    async with async_session_factory() as enrich_db:
        enrichment = await context_service.enrich(
            raw_prompt=prompt,
            tier=decision.tier,
            db=enrich_db,
            workspace_path=effective_workspace,
            mcp_ctx=ctx,
            repo_full_name=repo_full_name,
            applied_pattern_ids=applied_pattern_ids,
            preferences_snapshot=prefs_snapshot,
        )

    if decision.tier == "passthrough":
        # Passthrough: assemble template for external LLM processing
        logger.info("synthesis_optimize: tier=passthrough reason=%r", decision.reason)
        assembled, strategy_name = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=prompt,
            strategy_name=effective_strategy,
            codebase_guidance=enrichment.workspace_guidance,
            adaptation_state=enrichment.adaptation_state,
            analysis_summary=enrichment.analysis_summary,
            codebase_context=enrichment.codebase_context,
            applied_patterns=enrichment.applied_patterns,
        )
        trace_id = str(uuid.uuid4())
        async with async_session_factory() as db:
            pending = Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=prompt,
                status="pending",
                trace_id=trace_id,
                provider="mcp_passthrough",
                strategy_used=strategy_name,
                task_type=enrichment.task_type,
                domain=enrichment.domain_value,
                domain_raw=enrichment.domain_value,
                intent_label=enrichment.intent_label,
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

    if decision.tier == "sampling":
        # Sampling pipeline: run via IDE's LLM
        logger.info("synthesis_optimize: tier=sampling reason=%r", decision.reason)
        if not ctx or not hasattr(ctx, "session") or not ctx.session:
            raise ValueError("Sampling tier selected but no MCP session available")
        try:
            result = await run_sampling_pipeline(
                ctx, prompt,
                effective_strategy if effective_strategy != "auto" else None,
                enrichment.workspace_guidance,
                repo_full_name=repo_full_name,
                applied_pattern_ids=applied_pattern_ids,
            )
            return _sampling_result_to_output(result)
        except Exception as exc:
            logger.error("Sampling pipeline failed: %s", exc, exc_info=True)
            error_msg = await _persist_sampling_failure(prompt, effective_strategy, exc)
            return OptimizeOutput(
                status="error",
                pipeline_mode="sampling",
                strategy_used=effective_strategy,
                warnings=[error_msg],
            )

    # Internal pipeline (decision.tier == "internal")
    logger.info("synthesis_optimize: tier=internal provider=%s reason=%r", decision.provider_name, decision.reason)

    start = time.monotonic()

    logger.info(
        "synthesis_optimize called: prompt_len=%d strategy=%s repo=%s",
        len(prompt), effective_strategy, repo_full_name,
    )

    async with async_session_factory() as db:
        orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

        # Forward key pipeline events to the event bus so the web UI
        # shows live progress (phase transitions, scores, model IDs)
        # when the optimization is triggered from the IDE via MCP.
        forward_events = {"status", "score_card", "prompt_preview", "optimization_start", "suggestions"}

        result = None
        async for event in orchestrator.run(
            raw_prompt=prompt,
            provider=decision.provider,
            db=db,
            strategy_override=effective_strategy if effective_strategy != "auto" else None,
            codebase_guidance=enrichment.workspace_guidance,
            codebase_context=enrichment.codebase_context,
            adaptation_state=enrichment.adaptation_state,
            context_sources=enrichment.context_sources_dict,
            repo_full_name=repo_full_name,
            applied_pattern_ids=applied_pattern_ids,
            taxonomy_engine=get_taxonomy_engine(),
        ):
            if event.event in forward_events:
                await notify_event_bus(f"optimization_{event.event}", event.data)
            if event.event == "optimization_complete":
                result = event.data
            elif event.event == "error":
                error_msg = event.data.get("error", "Pipeline failed")
                logger.error("synthesis_optimize pipeline error: %s", error_msg)
                raise ValueError(error_msg)

        if not result:
            raise ValueError(
                "Pipeline completed but produced no result. Check server logs for details."
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "synthesis_optimize completed in %dms: optimization_id=%s strategy=%s",
            elapsed_ms, result.get("id", ""), result.get("strategy_used", ""),
        )

        # Notify backend event bus (MCP runs in a separate process)
        await notify_event_bus("optimization_created", {
            "id": result.get("id", ""),
            "task_type": result.get("task_type", ""),
            "intent_label": result.get("intent_label", "general"),
            "domain": result.get("domain", "general"),
            "domain_raw": result.get("domain_raw", "general"),
            "strategy_used": result.get("strategy_used", ""),
            "overall_score": result.get("overall_score"),
            "provider": decision.provider_name,
            "status": "completed",
        })

        return OptimizeOutput(
            status="completed",
            pipeline_mode="internal",
            optimization_id=result.get("id", ""),
            optimized_prompt=result.get("optimized_prompt", ""),
            task_type=result.get("task_type", ""),
            strategy_used=result.get("strategy_used", ""),
            changes_summary=result.get("changes_summary", ""),
            scores=result.get("optimized_scores", result.get("scores", {})),
            original_scores=result.get("original_scores", {}),
            score_deltas=result.get("score_deltas", {}),
            scoring_mode=result.get("scoring_mode", "independent"),
            suggestions=result.get("suggestions", []),
            warnings=result.get("warnings", []),
            model_used=result.get("model_used"),
            models_by_phase=result.get("models_by_phase"),
            intent_label=result.get("intent_label"),
            domain=result.get("domain"),
            trace_id=result.get("trace_id"),
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
