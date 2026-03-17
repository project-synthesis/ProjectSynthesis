"""Standalone MCP server with 4 optimization tools.

Copyright 2025-2026 Project Synthesis contributors.
"""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy import select

from app.config import DATA_DIR, PROMPTS_DIR, settings
from app.database import async_session_factory
from app.models import Optimization
from app.providers.detector import detect_provider
from app.schemas.pipeline_contracts import AnalysisResult, ScoreResult
from app.services.heuristic_scorer import HeuristicScorer
from app.services.passthrough import assemble_passthrough_prompt
from app.services.pipeline import PipelineOrchestrator
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader
from app.services.workspace_intelligence import WorkspaceIntelligence

logger = logging.getLogger(__name__)

# Module-level provider cache — set once by the lifespan, read by tools.
_provider = None

# Shared workspace intelligence instance — caches profiles by root set.
_workspace_intel = WorkspaceIntelligence()


async def _resolve_workspace_guidance(
    ctx: Context | None, workspace_path: str | None
) -> str | None:
    """Resolve workspace guidance: try roots/list first, fall back to workspace_path."""
    roots: list[Path] = []

    # Try MCP roots/list (zero-config)
    if ctx:
        try:
            roots_result = await ctx.session.list_roots()
            for root in roots_result.roots:
                uri = str(root.uri)
                if uri.startswith("file://"):
                    roots.append(Path(uri.removeprefix("file://")))
            if roots:
                logger.debug("Resolved %d workspace roots via MCP roots/list", len(roots))
        except Exception:
            logger.debug("Client does not support roots/list — will try workspace_path fallback")

    # Fallback: explicit workspace_path
    if not roots and workspace_path:
        roots = [Path(workspace_path)]
        logger.debug("Using explicit workspace_path fallback: %s", workspace_path)

    if not roots:
        logger.debug("No workspace roots resolved — skipping guidance injection")
        return None

    profile = _workspace_intel.analyze(roots)
    if profile:
        logger.info("Workspace guidance resolved: %d chars from %d roots", len(profile), len(roots))
    return profile


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Detect the LLM provider once at startup and expose it to tools."""
    global _provider
    # Enable WAL mode for SQLite (same as main.py)
    db_path = DATA_DIR / "synthesis.db"
    if db_path.exists():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
        logger.info("MCP lifespan: SQLite WAL mode enabled")

    _provider = detect_provider()
    if _provider:
        logger.info("MCP lifespan: provider detected — %s", _provider.name)
    else:
        logger.warning("MCP lifespan: no LLM provider available")

    yield {"provider": _provider}

    _provider = None


mcp = FastMCP(
    "synthesis_mcp",
    host="127.0.0.1",
    port=8001,
    streamable_http_path="/mcp",
    lifespan=_mcp_lifespan,
)


# ---- Tool 1: synthesis_optimize ----


@mcp.tool()
async def synthesis_optimize(
    prompt: str,
    strategy: str | None = None,
    repo_full_name: str | None = None,
    workspace_path: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Run the full optimization pipeline on a prompt.

    If a local LLM provider is available, runs the 3-phase pipeline internally.
    If no provider exists, returns an assembled optimization template for your
    IDE's LLM to process — call synthesis_save_result with the output.
    """
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )
    if len(prompt) > 200000:
        raise ValueError(
            "Prompt too long (%d chars). Maximum is 200,000 characters." % len(prompt)
        )

    provider = _provider

    # ---- Passthrough mode: no local provider, delegate to calling agent ----
    if not provider:
        logger.info(
            "synthesis_optimize: no provider — falling back to passthrough mode"
        )
        prefs = PreferencesService(DATA_DIR)
        effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"

        guidance = await _resolve_workspace_guidance(ctx, workspace_path)

        assembled, strategy_name = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=prompt,
            strategy_name=effective_strategy,
            codebase_guidance=guidance,
        )

        trace_id = str(uuid.uuid4())

        # Persist pending record so synthesis_save_result can link to it
        async with async_session_factory() as db:
            pending = Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=prompt,
                status="pending",
                trace_id=trace_id,
                provider="mcp_passthrough",
                strategy_used=strategy_name,
                task_type="general",
            )
            db.add(pending)
            await db.commit()

        return {
            "status": "pending_external",
            "trace_id": trace_id,
            "assembled_prompt": assembled,
            "strategy_used": strategy_name,
            "instructions": (
                "No local LLM provider detected. Process the assembled_prompt "
                "with your LLM, then call synthesis_save_result with the trace_id "
                "and the optimized output. Include optimized_prompt, changes_summary, "
                "task_type, strategy_used, and optionally scores "
                "(clarity, specificity, structure, faithfulness, conciseness — each 1-10)."
            ),
        }

    start = time.monotonic()

    # Resolve strategy: explicit param → user preference → auto
    prefs = PreferencesService(DATA_DIR)
    effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"

    logger.info(
        "synthesis_optimize called: prompt_len=%d strategy=%s repo=%s",
        len(prompt), effective_strategy, repo_full_name,
    )

    # Auto-discover workspace roots (zero-config) or fall back to workspace_path
    guidance = await _resolve_workspace_guidance(ctx, workspace_path)

    async with async_session_factory() as db:
        orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

        result = None
        async for event in orchestrator.run(
            raw_prompt=prompt,
            provider=provider,
            db=db,
            strategy_override=effective_strategy if effective_strategy != "auto" else None,
            codebase_guidance=guidance,
            repo_full_name=repo_full_name,
        ):
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

        # Notify backend event bus via HTTP (MCP runs in a separate process,
        # so pipeline.py's own event_bus.publish goes to a dead bus here)
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    "http://127.0.0.1:8000/api/events/_publish",
                    json={
                        "event_type": "optimization_created",
                        "data": {
                            "id": result.get("id", ""),
                            "task_type": result.get("task_type", ""),
                            "strategy_used": result.get("strategy_used", ""),
                            "overall_score": result.get("overall_score"),
                            "provider": provider.name,
                            "status": "completed",
                        },
                    },
                    timeout=5.0,
                )
        except Exception:
            logger.debug("Failed to notify backend event bus", exc_info=True)

        return {
            "optimization_id": result.get("id", ""),
            "optimized_prompt": result.get("optimized_prompt", ""),
            "task_type": result.get("task_type", ""),
            "strategy_used": result.get("strategy_used", ""),
            "changes_summary": result.get("changes_summary", ""),
            "scores": result.get("optimized_scores", result.get("scores", {})),
            "original_scores": result.get("original_scores", {}),
            "score_deltas": result.get("score_deltas", {}),
            "scoring_mode": "independent",
        }


# ---- Tool 2: synthesis_analyze ----


@mcp.tool()
async def synthesis_analyze(
    prompt: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Analyze a prompt and score it.

    Returns task type, weaknesses, strengths, strategy recommendation,
    and baseline quality scores (5 dimensions). Persists to history as an
    'analyzed' entry. Use the returned optimization_ready params to run
    synthesis_optimize if the analysis suggests improvement is worthwhile.
    """
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )

    provider = _provider
    if not provider:
        raise ValueError(
            "No LLM provider available. Set ANTHROPIC_API_KEY or install the Claude CLI."
        )

    start = time.monotonic()
    logger.info("synthesis_analyze called: prompt_len=%d", len(prompt))

    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()

    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")

    # --- Phase 1: Analyze ---
    system_prompt = loader.load("agent-guidance.md")
    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": strategy_loader.format_available(),
    })

    try:
        analysis: AnalysisResult = await provider.complete_parsed(
            model=prefs.resolve_model("analyzer", prefs_snapshot),
            system_prompt=system_prompt,
            user_message=analyze_msg,
            output_format=AnalysisResult,
            max_tokens=16384,
            effort="medium",
        )
    except Exception as exc:
        logger.error("synthesis_analyze Phase 1 (analyze) failed: %s", exc)
        raise ValueError("Analysis failed: %s" % exc) from exc

    analyze_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "synthesis_analyze Phase 1 complete in %dms: task_type=%s strategy=%s confidence=%.2f",
        analyze_ms, analysis.task_type, analysis.selected_strategy, analysis.confidence,
    )

    # --- Phase 2: Score original prompt ---
    # Send the same prompt as both A and B — scorer evaluates it on its own merits.
    scoring_system = loader.load("scoring.md")
    scorer_msg = (
        f"<prompt-a>\n{prompt}\n</prompt-a>\n\n"
        f"<prompt-b>\n{prompt}\n</prompt-b>"
    )

    try:
        score_result: ScoreResult = await provider.complete_parsed(
            model=prefs.resolve_model("scorer", prefs_snapshot),
            system_prompt=scoring_system,
            user_message=scorer_msg,
            output_format=ScoreResult,
            max_tokens=16384,
            effort="medium",
        )
    except Exception as exc:
        logger.error("synthesis_analyze Phase 2 (score) failed: %s", exc)
        raise ValueError("Scoring failed: %s" % exc) from exc

    # Both A and B are the same prompt — use prompt_a_scores as baseline
    # Apply hybrid scoring for consistency with main pipeline
    from app.services.heuristic_scorer import HeuristicScorer
    from app.services.score_blender import blend_scores

    heur_scores = HeuristicScorer.score_prompt(prompt)
    blended = blend_scores(score_result.prompt_a_scores, heur_scores)
    baseline = blended.to_dimension_scores()
    overall = baseline.overall

    total_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "synthesis_analyze Phase 2 complete: overall=%.1f total_ms=%d",
        overall, total_ms,
    )

    # --- Persist to DB ---
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    async with async_session_factory() as db:
        opt = Optimization(
            id=opt_id,
            raw_prompt=prompt,
            optimized_prompt="",
            task_type=analysis.task_type,
            strategy_used=analysis.selected_strategy,
            changes_summary="",
            score_clarity=baseline.clarity,
            score_specificity=baseline.specificity,
            score_structure=baseline.structure,
            score_faithfulness=baseline.faithfulness,
            score_conciseness=baseline.conciseness,
            overall_score=overall,
            provider=provider.name,
            model_used=prefs.resolve_model("analyzer", prefs_snapshot),
            scoring_mode="baseline",
            status="analyzed",
            trace_id=trace_id,
            duration_ms=total_ms,
        )
        db.add(opt)
        await db.commit()

    logger.info(
        "synthesis_analyze persisted: optimization_id=%s trace_id=%s",
        opt_id, trace_id,
    )

    # --- Notify event bus ---
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                "http://127.0.0.1:8000/api/events/_publish",
                json={
                    "event_type": "optimization_analyzed",
                    "data": {
                        "id": opt_id,
                        "trace_id": trace_id,
                        "task_type": analysis.task_type,
                        "strategy": analysis.selected_strategy,
                        "overall_score": overall,
                        "provider": provider.name,
                        "status": "analyzed",
                    },
                },
                timeout=5.0,
            )
    except Exception:
        logger.debug("Failed to notify backend event bus", exc_info=True)

    # --- Build actionable next steps ---
    next_steps = [
        "Run `synthesis_optimize(prompt=..., strategy='%s')` to improve this prompt"
        % analysis.selected_strategy,
    ]
    # Add weakness-specific suggestions
    for weakness in analysis.weaknesses[:3]:
        next_steps.append("Address: %s" % weakness)

    # Find lowest-scoring dimension for targeted advice
    dim_scores = {
        "clarity": baseline.clarity,
        "specificity": baseline.specificity,
        "structure": baseline.structure,
        "faithfulness": baseline.faithfulness,
        "conciseness": baseline.conciseness,
    }
    weakest_dim = min(dim_scores, key=dim_scores.get)  # type: ignore[arg-type]
    weakest_val = dim_scores[weakest_dim]
    if weakest_val < 7.0:
        next_steps.append(
            "Focus on %s (scored %.1f/10) — this is the biggest opportunity for improvement"
            % (weakest_dim, weakest_val)
        )

    return {
        "optimization_id": opt_id,
        "task_type": analysis.task_type,
        "weaknesses": analysis.weaknesses,
        "strengths": analysis.strengths,
        "selected_strategy": analysis.selected_strategy,
        "strategy_rationale": analysis.strategy_rationale,
        "confidence": analysis.confidence,
        "baseline_scores": dim_scores,
        "overall_score": overall,
        "duration_ms": total_ms,
        "next_steps": next_steps,
        "optimization_ready": {
            "prompt": prompt,
            "strategy": analysis.selected_strategy,
        },
    }


# ---- Tool 3: synthesis_prepare_optimization ----


@mcp.tool()
async def synthesis_prepare_optimization(
    prompt: str,
    strategy: str | None = None,
    max_context_tokens: int = 128000,
    workspace_path: str | None = None,
    repo_full_name: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Assemble the full optimization prompt with context for an external LLM.

    Call synthesis_save_result with the output.
    """
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )

    # Resolve strategy: explicit param → user preference → auto
    prefs = PreferencesService(DATA_DIR)
    effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"

    logger.info(
        "synthesis_prepare_optimization called: prompt_len=%d strategy=%s",
        len(prompt), effective_strategy,
    )

    # Auto-discover workspace roots (zero-config) or fall back to workspace_path
    guidance = await _resolve_workspace_guidance(ctx, workspace_path)

    assembled, strategy_name = assemble_passthrough_prompt(
        prompts_dir=PROMPTS_DIR,
        raw_prompt=prompt,
        strategy_name=effective_strategy,
        codebase_guidance=guidance,
    )

    # Enforce max_context_tokens budget
    estimated_tokens = len(assembled) // 4
    if estimated_tokens > max_context_tokens:
        max_chars = max_context_tokens * 4
        assembled = assembled[:max_chars]
        context_size_tokens = max_context_tokens
    else:
        context_size_tokens = estimated_tokens

    trace_id = str(uuid.uuid4())

    # Store pending optimization with raw_prompt for later save_result linkage
    async with async_session_factory() as db:
        pending = Optimization(
            id=str(uuid.uuid4()),
            raw_prompt=prompt,
            status="pending",
            trace_id=trace_id,
            provider="mcp_passthrough",
            strategy_used=strategy_name,
            task_type="general",
        )
        db.add(pending)
        await db.commit()

    logger.info(
        "synthesis_prepare_optimization completed: trace_id=%s strategy=%s tokens=%d",
        trace_id, strategy_name, context_size_tokens,
    )

    return {
        "trace_id": trace_id,
        "assembled_prompt": assembled,
        "context_size_tokens": context_size_tokens,
        "strategy_requested": strategy_name,
    }


# ---- Tool 4: synthesis_save_result ----


@mcp.tool()
async def synthesis_save_result(
    trace_id: str,
    optimized_prompt: str,
    changes_summary: str | None = None,
    task_type: str | None = None,
    strategy_used: str | None = None,
    scores: dict | None = None,
    model: str | None = None,
    codebase_context: str | None = None,
) -> dict:
    """Persist an optimization result from an external LLM.

    Applies bias correction to self-rated scores.
    Optionally stores IDE-provided codebase context snapshot.
    """
    logger.info("synthesis_save_result called: trace_id=%s model=%s", trace_id, model)

    # Normalize strategy_used — external LLMs often return verbose rationales
    # instead of the short identifier. Match against known strategies.
    if strategy_used:
        strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")
        known = strategy_loader.list_strategies()
        if strategy_used not in known:
            # Try to extract a known strategy name from the verbose string
            normalized = "auto"
            lower = strategy_used.lower()
            for name in known:
                if name != "auto" and name in lower:
                    normalized = name
                    break
            logger.info(
                "Strategy normalized: '%s' → '%s'",
                strategy_used[:80], normalized,
            )
            strategy_used = normalized

    # Check scoring preference
    prefs = PreferencesService(DATA_DIR)
    scoring_enabled = prefs.get("pipeline.enable_scoring")
    if scoring_enabled is None:
        scoring_enabled = True  # default on

    # Determine scoring mode and compute final scores
    clean_scores: dict[str, float] = {}
    heuristic_flags: list[str] = []
    scoring_mode = "skipped" if not scoring_enabled else "heuristic"

    if scores and scoring_enabled:
        # IDE provided self-rated scores — clean and validate
        scoring_mode = "hybrid_passthrough"
        for k, v in scores.items():
            try:
                clean_scores[k] = float(v)
            except (ValueError, TypeError):
                clean_scores[k] = 5.0  # default

    # Persist — look up pending optimization created by prepare, or create new
    async with async_session_factory() as db:
        # Look up pending optimization created by synthesis_prepare_optimization
        result = await db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        opt = result.scalar_one_or_none()

        # Determine strategy compliance by comparing prepare vs save
        strategy_compliance = "unknown"
        if opt and opt.strategy_used and strategy_used:
            if opt.strategy_used == strategy_used:
                strategy_compliance = "matched"
            else:
                strategy_compliance = "partial"
                logger.info(
                    "Strategy mismatch: requested=%s, used=%s",
                    opt.strategy_used,
                    strategy_used,
                )
        elif strategy_used:
            strategy_compliance = "matched"  # no prepare to compare against

        # Compute scores — hybrid blending matching the internal pipeline
        heuristic_scores: dict[str, float] = {}
        final_scores: dict[str, float] = {}
        overall: float | None = None
        original_scores: dict[str, float] | None = None
        deltas: dict[str, float] | None = None

        if scoring_enabled:
            # Compute heuristic scores for the optimized prompt
            heuristic_scores = HeuristicScorer.score_prompt(
                optimized_prompt,
                original=opt.raw_prompt if opt and opt.raw_prompt else None,
            )

            if clean_scores:
                # IDE provided scores — blend with heuristics (same as internal pipeline)
                # Build DimensionScores from IDE's self-rated scores for blending
                from app.schemas.pipeline_contracts import DimensionScores
                from app.services.score_blender import blend_scores

                try:
                    # Apply bias correction BEFORE blending (discount self-rating inflation)
                    corrected = HeuristicScorer.apply_bias_correction(clean_scores)
                    ide_scores_corrected = DimensionScores(
                        clarity=corrected.get("clarity", 5.0),
                        specificity=corrected.get("specificity", 5.0),
                        structure=corrected.get("structure", 5.0),
                        faithfulness=corrected.get("faithfulness", 5.0),
                        conciseness=corrected.get("conciseness", 5.0),
                    )

                    # Fetch historical stats for z-score normalization (non-fatal)
                    historical_stats: dict | None = None
                    try:
                        from app.services.optimization_service import OptimizationService
                        opt_svc = OptimizationService(db)
                        historical_stats = await opt_svc.get_score_distribution(
                            exclude_scoring_modes=["heuristic"],
                        )
                    except Exception:
                        pass

                    # Hybrid blend: bias-corrected IDE scores + heuristics
                    blended = blend_scores(
                        ide_scores_corrected, heuristic_scores, historical_stats,
                    )
                    blended_dims = blended.to_dimension_scores()
                    final_scores = {
                        "clarity": blended_dims.clarity,
                        "specificity": blended_dims.specificity,
                        "structure": blended_dims.structure,
                        "faithfulness": blended_dims.faithfulness,
                        "conciseness": blended_dims.conciseness,
                    }

                    # Divergence flags
                    heuristic_flags = blended.divergence_flags or []

                    scoring_mode = "hybrid_passthrough"

                except Exception as exc:
                    logger.warning("Hybrid blending failed, falling back to heuristic: %s", exc)
                    final_scores = heuristic_scores
                    scoring_mode = "heuristic"
            else:
                # No IDE scores — pure heuristic (same as before)
                final_scores = heuristic_scores
                scoring_mode = "heuristic"

            overall = round(
                sum(final_scores.values()) / max(len(final_scores), 1), 2,
            )

            # Compute original prompt scores + deltas when raw_prompt is available
            if opt and opt.raw_prompt:
                original_heur = HeuristicScorer.score_prompt(opt.raw_prompt)
                original_scores = original_heur
                deltas = {
                    dim: round(final_scores[dim] - original_scores[dim], 2)
                    for dim in final_scores
                    if dim in original_scores
                }

        # Truncate codebase context if provided
        context_snapshot = None
        if codebase_context:
            context_snapshot = codebase_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]

        if opt:
            # Update existing pending record from prepare
            opt.optimized_prompt = optimized_prompt
            opt.task_type = task_type or opt.task_type or "general"
            opt.strategy_used = strategy_used or opt.strategy_used or "auto"
            opt.changes_summary = changes_summary or ""
            opt.score_clarity = final_scores.get("clarity")
            opt.score_specificity = final_scores.get("specificity")
            opt.score_structure = final_scores.get("structure")
            opt.score_faithfulness = final_scores.get("faithfulness")
            opt.score_conciseness = final_scores.get("conciseness")
            opt.overall_score = overall
            opt.original_scores = original_scores
            opt.score_deltas = deltas
            opt.model_used = model or "external"
            opt.scoring_mode = scoring_mode
            opt.status = "completed"
            if context_snapshot:
                opt.codebase_context_snapshot = context_snapshot
            opt_id = opt.id
        else:
            # No prepare was called — create new record (standalone save).
            # No raw_prompt available, so no original_scores or deltas.
            opt_id = str(uuid.uuid4())
            opt = Optimization(
                id=opt_id,
                raw_prompt="",
                optimized_prompt=optimized_prompt,
                task_type=task_type or "general",
                strategy_used=strategy_used or "auto",
                changes_summary=changes_summary or "",
                score_clarity=final_scores.get("clarity"),
                score_specificity=final_scores.get("specificity"),
                score_structure=final_scores.get("structure"),
                score_faithfulness=final_scores.get("faithfulness"),
                score_conciseness=final_scores.get("conciseness"),
                overall_score=overall,
                provider="mcp_passthrough",
                model_used=model or "external",
                scoring_mode=scoring_mode,
                status="completed",
                trace_id=trace_id,
                codebase_context_snapshot=context_snapshot,
            )
            db.add(opt)

        await db.commit()

        # Notify backend event bus via HTTP (MCP runs in a separate process)
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    "http://127.0.0.1:8000/api/events/_publish",
                    json={
                        "event_type": "optimization_created",
                        "data": {
                            "id": opt_id,
                            "trace_id": trace_id,
                            "task_type": opt.task_type,
                            "strategy_used": opt.strategy_used,
                            "overall_score": overall,
                            "provider": opt.provider,
                            "status": "completed",
                        },
                    },
                    timeout=5.0,
                )
        except Exception:
            logger.debug("Failed to notify backend event bus", exc_info=True)

    logger.info(
        "synthesis_save_result completed: optimization_id=%s strategy_compliance=%s flags=%d",
        opt_id, strategy_compliance, len(heuristic_flags),
    )

    return {
        "optimization_id": opt_id,
        "scoring_mode": scoring_mode,
        "scores": {k: round(v, 2) for k, v in final_scores.items()} if final_scores else {},
        "original_scores": original_scores,
        "score_deltas": deltas,
        "overall_score": overall,
        "strategy_compliance": strategy_compliance,
        "heuristic_flags": heuristic_flags,
    }


# ---- Entry point ----


def create_mcp_server() -> FastMCP:
    """Return the configured MCP server instance."""
    return mcp


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
