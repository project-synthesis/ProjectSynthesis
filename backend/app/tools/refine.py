"""Handler for synthesis_refine MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import Context
from sqlalchemy import select

from app.config import PROMPTS_DIR
from app.database import async_session_factory
from app.models import Optimization, RefinementBranch, RefinementTurn
from app.schemas.mcp_models import RefineOutput
from app.services.event_notification import notify_event_bus
from app.services.preferences import PreferencesService
from app.services.refinement_service import RefinementService
from app.services.routing import RoutingContext
from app.tools._shared import DATA_DIR, build_scores_dict, get_context_service, get_routing

logger = logging.getLogger(__name__)


async def handle_refine(
    optimization_id: str,
    refinement_request: str,
    branch_id: str | None = None,
    workspace_path: str | None = None,
    ctx: Context | None = None,
) -> RefineOutput:
    """Iteratively improve an optimized prompt with specific instructions."""
    # Resolve provider via routing
    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()
    routing = get_routing()
    ctx_routing = RoutingContext(preferences=prefs_snapshot, caller="mcp")
    decision = routing.resolve(ctx_routing)

    if decision.tier == "passthrough" or decision.provider is None:
        raise ValueError(
            "Refinement requires a local LLM provider. "
            "Current routing tier is '%s'. Set ANTHROPIC_API_KEY or install the Claude CLI."
            % decision.tier
        )

    provider = decision.provider

    async with async_session_factory() as db:
        # Load the parent optimization
        result = await db.execute(
            select(Optimization).where(Optimization.id == optimization_id)
        )
        opt = result.scalar_one_or_none()
        if not opt:
            raise ValueError(f"Optimization not found: {optimization_id}")

        if not opt.optimized_prompt:
            raise ValueError(
                f"Optimization {optimization_id} has no optimized prompt to refine "
                f"(status: {opt.status})."
            )

        svc = RefinementService(db, provider, PROMPTS_DIR)

        # Check if initial turn exists; if not, create it
        existing_turns = await db.execute(
            select(RefinementTurn).where(
                RefinementTurn.optimization_id == optimization_id
            ).limit(1)
        )
        if not existing_turns.scalar_one_or_none():
            # Seed the initial turn from the parent optimization
            scores_dict = build_scores_dict(opt) or {}
            await svc.create_initial_turn(
                optimization_id=optimization_id,
                prompt=opt.optimized_prompt,
                scores_dict=scores_dict,
                strategy_used=opt.strategy_used or "auto",
            )
            await db.commit()
            logger.info("synthesis_refine: created initial turn for optimization %s", optimization_id)

        # Resolve the branch
        if branch_id:
            branch_result = await db.execute(
                select(RefinementBranch).where(
                    RefinementBranch.id == branch_id,
                    RefinementBranch.optimization_id == optimization_id,
                )
            )
            branch = branch_result.scalar_one_or_none()
            if not branch:
                raise ValueError(f"Branch not found: {branch_id}")
        else:
            # Use latest branch
            branch_result = await db.execute(
                select(RefinementBranch)
                .where(RefinementBranch.optimization_id == optimization_id)
                .order_by(RefinementBranch.created_at.desc())
                .limit(1)
            )
            branch = branch_result.scalar_one_or_none()
            if not branch:
                raise ValueError(f"No branches found for optimization {optimization_id}")

        # Get the latest turn on this branch
        latest_turn_result = await db.execute(
            select(RefinementTurn)
            .where(
                RefinementTurn.optimization_id == optimization_id,
                RefinementTurn.branch_id == branch.id,
            )
            .order_by(RefinementTurn.version.desc())
            .limit(1)
        )
        latest_turn = latest_turn_result.scalar_one_or_none()
        if not latest_turn:
            raise ValueError(f"No turns found on branch {branch.id}")

        # Resolve workspace guidance via unified context enrichment
        context_service = get_context_service()
        enrichment = await context_service.enrich(
            raw_prompt=opt.optimized_prompt,
            tier=decision.tier,
            db=db,
            workspace_path=workspace_path,
            mcp_ctx=ctx,
        )
        # Consume the refinement generator to completion
        logger.info(
            "synthesis_refine: starting turn on branch=%s from_version=%d",
            branch.id, latest_turn.version,
        )

        final_event_data = None
        async for event in svc.create_refinement_turn(
            optimization_id=optimization_id,
            branch_id=branch.id,
            refinement_request=refinement_request,
            codebase_guidance=None,  # workspace guidance folded into codebase_context
            divergence_alerts=enrichment.divergence_alerts if enrichment else None,
        ):
            if event.event == "refinement_complete":
                final_event_data = event.data
            elif event.event == "error":
                error_msg = event.data.get("error", "Refinement failed")
                raise ValueError(error_msg)

        await db.commit()

        # Fetch the newly created turn from DB
        new_turn_result = await db.execute(
            select(RefinementTurn)
            .where(
                RefinementTurn.optimization_id == optimization_id,
                RefinementTurn.branch_id == branch.id,
            )
            .order_by(RefinementTurn.version.desc())
            .limit(1)
        )
        new_turn = new_turn_result.scalar_one_or_none()

        if not new_turn:
            raise ValueError("Refinement completed but no new turn was created.")

        # Extract scores and suggestions
        scores = new_turn.scores if isinstance(new_turn.scores, dict) else None
        score_deltas = new_turn.deltas if isinstance(new_turn.deltas, dict) else None
        overall_score = None
        if scores:
            from app.schemas.pipeline_contracts import DIMENSION_WEIGHTS
            overall_score = round(
                sum(scores.get(d, 5.0) * w for d, w in DIMENSION_WEIGHTS.items()), 2,
            )

        suggestions = []
        if final_event_data and "suggestions" in final_event_data:
            suggestions = final_event_data["suggestions"]

        strategy_used = None
        if final_event_data and "strategy_used" in final_event_data:
            strategy_used = final_event_data["strategy_used"]

    # Notify frontend
    await notify_event_bus("refinement_turn", {
        "optimization_id": optimization_id,
        "version": new_turn.version,
        "branch_id": branch.id,
    })

    logger.info(
        "synthesis_refine completed: optimization_id=%s version=%d branch=%s",
        optimization_id, new_turn.version, branch.id,
    )

    return RefineOutput(
        optimization_id=optimization_id,
        version=new_turn.version,
        branch_id=branch.id,
        refined_prompt=new_turn.prompt or "",
        scores=scores,
        score_deltas=score_deltas,
        overall_score=overall_score,
        suggestions=suggestions,
        strategy_used=strategy_used,
    )
