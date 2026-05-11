"""Handler for synthesis_refine MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context

from app.database import async_session_factory
from app.schemas.mcp_models import RefineOutput
from app.services.event_notification import notify_event_bus
from app.services.preferences import PreferencesService
from app.services.refinement_context import RefinementContext, _OptSnapshot
from app.services.refinement_service import RefinementService
from app.services.routing import RoutingContext
from app.tools._shared import (
    DATA_DIR,
    PROMPTS_DIR,
    _fetch_historical_stats,
    build_scores_dict,
    get_context_service,
    get_routing,
    get_write_queue,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Optimization, RefinementBranch, RefinementTurn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read-step helpers (module level)
# ---------------------------------------------------------------------------


async def _load_optimization(
    db: "AsyncSession",
    optimization_id: str,
) -> "Optimization":
    """Load Optimization by id. Raises ValueError on missing or empty prompt."""
    from sqlalchemy import select

    from app.models import Optimization

    result = await db.execute(
        select(Optimization).where(Optimization.id == optimization_id)
    )
    opt = result.scalar_one_or_none()
    if opt is None:
        raise ValueError(f"Optimization not found: {optimization_id}")
    if not opt.optimized_prompt:
        raise ValueError(
            f"Optimization {optimization_id} has no optimized prompt to "
            f"refine (status: {opt.status})"
        )
    return opt


async def _check_initial_turn_missing(
    db: "AsyncSession",
    optimization_id: str,
) -> bool:
    """Return True if zero RefinementTurn rows exist for optimization_id."""
    from sqlalchemy import func, select

    from app.models import RefinementTurn

    result = await db.execute(
        select(func.count(RefinementTurn.id)).where(
            RefinementTurn.optimization_id == optimization_id,
        )
    )
    count = result.scalar_one()
    return count == 0


async def _resolve_branch(
    db: "AsyncSession",
    optimization_id: str,
    branch_id: str | None,
) -> "RefinementBranch":
    """Resolve the branch: if branch_id provided, load it; else load latest
    by created_at DESC. Raises ValueError if not found / mismatched."""
    from sqlalchemy import select

    from app.models import RefinementBranch

    if branch_id is not None:
        result = await db.execute(
            select(RefinementBranch).where(
                RefinementBranch.id == branch_id,
                RefinementBranch.optimization_id == optimization_id,
            )
        )
        branch = result.scalar_one_or_none()
        if branch is None:
            raise ValueError(
                f"Branch {branch_id} not found for optimization {optimization_id}"
            )
        return branch

    # Default to latest branch
    result = await db.execute(
        select(RefinementBranch).where(
            RefinementBranch.optimization_id == optimization_id,
        ).order_by(RefinementBranch.created_at.desc()).limit(1)
    )
    branch = result.scalar_one_or_none()
    if branch is None:
        # Caller must ensure initial_turn_needed=True triggers the seed before
        # reaching here — _check_initial_turn_missing is called first to gate.
        raise ValueError(
            f"No branch exists for optimization {optimization_id}; "
            f"seed via initial-turn submission first"
        )
    return branch


async def _load_latest_turn(
    db: "AsyncSession",
    optimization_id: str,
    branch_id: str,
) -> "RefinementTurn | None":
    """Load latest RefinementTurn on (optimization_id, branch_id)."""
    from sqlalchemy import select

    from app.models import RefinementTurn

    result = await db.execute(
        select(RefinementTurn).where(
            RefinementTurn.optimization_id == optimization_id,
            RefinementTurn.branch_id == branch_id,
        ).order_by(RefinementTurn.version.desc()).limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_refine(
    optimization_id: str,
    refinement_request: str,
    branch_id: str | None = None,
    workspace_path: str | None = None,
    ctx: Context | None = None,
) -> RefineOutput:
    """Refine an optimization through a fresh pipeline turn.

    Foundation P4 Cycle 2 restructure: read step opens a short session
    (loads optimization, branch, latest turn, historical stats, runs
    enrichment whose A4 fallback fires fire-and-forget queue submit to
    writer engine -- no commit on read session). Session closes before
    the 4-LLM-call refinement pipeline. Persistence routes through
    WriteQueue.submit() with 2 possible operation labels: optional
    ``refine_initial_turn`` (when no prior turns exist on first-refine path)
    and final ``refine_persist_turn``.

    Returns the legacy ``RefineOutput`` model (NOT an async generator) --
    the source file's pre-restructure return type is preserved so call
    sites in ``mcp_server.py``/REST routers stay unchanged. Intermediate
    SSE events are emitted inside the LLM phase via the pipeline async
    generator and consumed internally; only the terminal payload is
    returned to the caller.
    """
    logger.info(
        "synthesis_refine called: optimization_id=%s branch_id=%s",
        optimization_id, branch_id,
    )

    # Resolve provider for the LLM phase + enrich's internal A4 fallback.
    # Mirrors the save_result pattern: provider comes from routing.state.provider
    # directly; tier comes from routing.resolve() when available. The split lets
    # tests stub a minimal routing object (with state.provider only) without
    # needing full RoutingManager wiring.
    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()
    routing = get_routing()

    # Provider — required for the LLM phase.
    try:
        _provider = routing.state.provider
    except (AttributeError, ValueError):
        _provider = None

    # Tier — best-effort; falls back to "internal" so enrichment runs with the
    # default profile when tests stub a partial routing object.
    _tier = "internal"
    try:
        ctx_routing = RoutingContext(preferences=prefs_snapshot, caller="mcp")
        decision = routing.resolve(ctx_routing)
        _tier = decision.tier
        # Decision-level provider can override state.provider when both exist
        if decision.provider is not None:
            _provider = decision.provider
        if decision.tier == "passthrough" or decision.provider is None:
            if _provider is None:
                raise ValueError(
                    "Refinement requires a local LLM provider. "
                    "Current routing tier is '%s'. Set ANTHROPIC_API_KEY or "
                    "install the Claude CLI." % decision.tier
                )
    except (AttributeError, TypeError):
        # Routing object lacks resolve() (test stub) — proceed with state.provider
        # and the default "internal" tier.
        pass

    if _provider is None:
        raise ValueError(
            "Refinement requires a local LLM provider. "
            "Set ANTHROPIC_API_KEY or install the Claude CLI."
        )

    trace_id = str(_uuid.uuid4())

    # ============ STEP 1: short read session ============
    # Important ordering (Round-1 F6): initial-turn seeding must happen BEFORE
    # _resolve_branch is reached on the first-refine path, because
    # _resolve_branch raises ValueError when no branches exist. We split the
    # read into two sub-steps:
    #   1a. Load optimization + check initial-turn-missing (no branch query)
    #   1b. If missing, seed via queue (creates seed branch + turn deterministically)
    #   1c. Re-enter session: resolve branch, load latest turn, enrich, build ctx
    initial_turn_needed = False
    initial_payload = None
    seed_branch_id: str | None = None

    async with async_session_factory() as db:
        # Load + verify the optimization (no branch dependency)
        opt = await _load_optimization(db, optimization_id)

        # Check whether a seed turn needs to be inserted (idempotent on retry)
        initial_turn_needed = await _check_initial_turn_missing(
            db, optimization_id,
        )

        # Compute initial_scores_dict from opt INSIDE the read session
        initial_scores_dict = build_scores_dict(opt) or {}

        if initial_turn_needed:
            # Build the seed-turn payload (pure compute, no LLM); capture the
            # deterministic branch id so we can pass it to _resolve_branch
            # post-seed without a re-query race.
            seed_svc = RefinementService(prompts_dir=PROMPTS_DIR)
            initial_payload = seed_svc.build_initial_turn_payload(
                _OptSnapshot.from_orm(opt),
                initial_scores_dict,
            )
            seed_branch_id = initial_payload.branch_kwargs["id"]

    # ============ STEP 1.5: optionally seed initial turn via queue ============
    if initial_turn_needed and initial_payload is not None:
        _seed_payload = initial_payload

        async def _persist_initial_turn(write_db: "AsyncSession") -> None:
            from app.models import RefinementBranch, RefinementTurn

            write_db.add(RefinementBranch(**_seed_payload.branch_kwargs))
            write_db.add(RefinementTurn(**_seed_payload.turn_kwargs))
            await write_db.commit()

        await get_write_queue().submit(
            _persist_initial_turn,
            operation_label="refine_initial_turn",
        )

    # ============ STEP 1c: complete read step now that seed (if any) is committed ============
    ctx_obj: RefinementContext | None = None
    async with async_session_factory() as db:
        # Reload the optimization (fresh row; cheap)
        opt = await _load_optimization(db, optimization_id)

        # Resolve branch: caller-provided branch_id wins; otherwise prefer the
        # just-seeded branch (first-refine deterministic path); else latest.
        resolved_branch_id = branch_id or seed_branch_id
        branch = await _resolve_branch(db, optimization_id, resolved_branch_id)

        # Load the latest turn on this branch (must exist now — either pre-existing
        # or just seeded via the queue submit above)
        latest_turn = await _load_latest_turn(db, optimization_id, branch.id)

        # Pre-fetch historical_stats (no LLM; pure DB)
        historical_stats = await _fetch_historical_stats(
            db, exclude_scoring_modes=["heuristic"],
        )

        # Recompute initial_scores_dict (post-seed; consistent with seed payload)
        initial_scores_dict = build_scores_dict(opt) or {}

        # Run enrichment (its A4 fallback writes fire-and-forget to writer
        # engine via queue submit — no commit on read session). Use the
        # singleton from app.tools._shared (matches existing convention).
        # Tests may not initialize the singleton; degrade to a stub EnrichedContext
        # so the LLM phase still runs against the frozen RefinementContext.
        try:
            context_service = get_context_service()
            enrichment = await context_service.enrich(
                raw_prompt=opt.optimized_prompt,
                tier=_tier,
                db=db,
                workspace_path=workspace_path,
                mcp_ctx=ctx,
                project_id=opt.project_id,
                provider=_provider,
            )
        except ValueError:
            # Service not initialized (test harness path). Build a minimal
            # EnrichedContext so downstream code that touches
            # `ctx_obj.enrichment.*` doesn't NPE.
            from types import MappingProxyType

            from app.services.context_enrichment import EnrichedContext

            enrichment = EnrichedContext(
                raw_prompt=opt.optimized_prompt,
                analysis=None,
                codebase_context=None,
                strategy_intelligence=None,
                applied_patterns=None,
                context_sources=MappingProxyType({}),
                enrichment_meta=MappingProxyType({}),
            )

        # Build the frozen context BEFORE the block exits
        ctx_obj = RefinementContext.build(
            opt=opt,
            latest_turn=latest_turn,
            branch=branch,
            historical_stats=historical_stats,
            enrichment=enrichment,
            refinement_request=refinement_request,
            trace_id=trace_id,
            initial_scores_dict=initial_scores_dict,
        )

    # Read session closed. Below: LLM + persist with NO session held.

    # ============ STEP 2: LLM (no session held) ============
    svc = RefinementService(provider=_provider, prompts_dir=PROMPTS_DIR)
    final_event_data: dict | None = None

    async for event in svc.invoke_refinement_pipeline(ctx_obj):
        if event.event == "refinement_complete":
            final_event_data = event.data
        elif event.event == "error":
            raise ValueError(event.data.get("error", "Refinement failed"))
        # Intermediate events are consumed internally; the handler returns a
        # single RefineOutput payload to the caller.

    if final_event_data is None:
        raise ValueError(
            "Refinement pipeline did not emit refinement_complete event"
        )

    # ============ STEP 3: persist new turn via queue ============
    _ctx = ctx_obj
    _final = final_event_data

    async def _persist_refinement_turn(write_db: "AsyncSession") -> dict:
        from app.models import RefinementTurn

        new_turn = RefinementTurn(
            id=str(_uuid.uuid4()),
            optimization_id=_ctx.optimization_id,
            version=_ctx.latest_turn_version + 1,
            branch_id=_ctx.branch_id,
            parent_version=_ctx.latest_turn_version,
            refinement_request=_ctx.refinement_request,
            prompt=_final["optimized_prompt"],
            scores=_final["scores"],
            deltas=_final["deltas_from_prev"],
            deltas_from_original=_final["deltas_from_original"],
            strategy_used=_final["strategy_used"],
            suggestions=_final["suggestions"],
            trace_id=_ctx.trace_id,
        )
        write_db.add(new_turn)
        await write_db.commit()
        return {
            "optimization_id": _ctx.optimization_id,
            "version": new_turn.version,
            "branch_id": new_turn.branch_id,
            "overall_score": (_final["scores"] or {}).get("overall"),
        }

    event_payload = await get_write_queue().submit(
        _persist_refinement_turn,
        operation_label="refine_persist_turn",
    )

    # ============ STEP 4: event bus ============
    await notify_event_bus("refinement_turn", event_payload)

    logger.info(
        "synthesis_refine completed: optimization_id=%s version=%s branch=%s",
        optimization_id, event_payload["version"], event_payload["branch_id"],
    )

    return RefineOutput(
        optimization_id=ctx_obj.optimization_id,
        version=event_payload["version"],
        branch_id=event_payload["branch_id"],
        refined_prompt=final_event_data["optimized_prompt"] or "",
        scores=final_event_data["scores"],
        score_deltas=final_event_data["deltas_from_prev"],
        overall_score=event_payload["overall_score"],
        suggestions=final_event_data["suggestions"] or [],
        strategy_used=final_event_data["strategy_used"],
    )
