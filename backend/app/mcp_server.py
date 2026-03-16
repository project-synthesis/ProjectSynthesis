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
from app.schemas.pipeline_contracts import AnalysisResult, DimensionScores, ScoreResult
from app.services.heuristic_scorer import HeuristicScorer
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
    ctx: Context = None,
) -> dict:
    """Run the full optimization pipeline on a prompt.

    Returns the optimized prompt with 5-dimension scores and improvement deltas.
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
    if not provider:
        raise ValueError(
            "No LLM provider available. Set ANTHROPIC_API_KEY or install the Claude CLI."
        )

    start = time.monotonic()
    logger.info(
        "synthesis_optimize called: prompt_len=%d strategy=%s repo=%s",
        len(prompt), strategy, repo_full_name,
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
            strategy_override=strategy,
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
    scorer_msg = f"## Prompt A\n\n{prompt}\n\n## Prompt B\n\n{prompt}"

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
    baseline: DimensionScores = score_result.prompt_a_scores
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
    ctx: Context = None,
) -> dict:
    """Assemble the full optimization prompt with context for an external LLM.

    Call synthesis_save_result with the output.
    """
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )

    logger.info(
        "synthesis_prepare_optimization called: prompt_len=%d strategy=%s",
        len(prompt), strategy,
    )

    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")

    # Load strategy instructions
    strategy_name = strategy or "auto"
    try:
        strategy_instructions = strategy_loader.load(strategy_name)
    except FileNotFoundError:
        strategy_instructions = strategy_loader.load("auto")
        strategy_name = "auto"

    # Load scoring rubric excerpt for passthrough
    scoring_rubric = loader.load("scoring.md")
    scoring_excerpt = (
        scoring_rubric[:2000] + "..."
        if len(scoring_rubric) > 2000
        else scoring_rubric
    )

    # Auto-discover workspace roots (zero-config) or fall back to workspace_path
    guidance = await _resolve_workspace_guidance(ctx, workspace_path)

    assembled = loader.render("passthrough.md", {
        "raw_prompt": prompt,
        "strategy_instructions": strategy_instructions,
        "scoring_rubric_excerpt": scoring_excerpt,
        "codebase_guidance": guidance,
        "codebase_context": None,
        "adaptation_state": None,
    })

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

    # Apply bias correction if scores provided
    bias_corrected: dict[str, float] = {}
    heuristic_flags: list[str] = []
    if scores:
        # Coerce string values to floats
        clean_scores: dict[str, float] = {}
        for k, v in scores.items():
            try:
                clean_scores[k] = float(v)
            except (ValueError, TypeError):
                clean_scores[k] = 5.0  # default

        bias_corrected = HeuristicScorer.apply_bias_correction(clean_scores)

        # Run all available heuristic checks
        heuristic_scores: dict[str, float] = {}
        heuristic_scores["structure"] = HeuristicScorer.heuristic_structure(optimized_prompt)
        heuristic_scores["specificity"] = HeuristicScorer.heuristic_specificity(optimized_prompt)
        heuristic_scores["conciseness"] = HeuristicScorer.heuristic_conciseness(optimized_prompt)
        heuristic_scores["clarity"] = HeuristicScorer.heuristic_clarity(optimized_prompt)
        # Note: heuristic_faithfulness requires both original and optimized prompts.
        # Skipped here because passthrough doesn't always have the original.

        heuristic_flags = HeuristicScorer.detect_divergence(
            clean_scores, heuristic_scores
        )

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

        overall = (
            sum(bias_corrected.values()) / max(len(bias_corrected), 1)
            if bias_corrected
            else None
        )

        # Truncate codebase context if provided
        context_snapshot = None
        if codebase_context:
            context_snapshot = codebase_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]

        if opt:
            # Update existing pending record from prepare
            opt.optimized_prompt = optimized_prompt
            opt.task_type = task_type or "unknown"
            opt.strategy_used = strategy_used or opt.strategy_used or "unknown"
            opt.changes_summary = changes_summary or ""
            opt.score_clarity = bias_corrected.get("clarity")
            opt.score_specificity = bias_corrected.get("specificity")
            opt.score_structure = bias_corrected.get("structure")
            opt.score_faithfulness = bias_corrected.get("faithfulness")
            opt.score_conciseness = bias_corrected.get("conciseness")
            opt.overall_score = overall
            opt.model_used = model or "unknown"
            opt.scoring_mode = "self_rated"
            opt.status = "completed"
            if context_snapshot:
                opt.codebase_context_snapshot = context_snapshot
            opt_id = opt.id
        else:
            # No prepare was called — create new record (standalone save)
            opt_id = str(uuid.uuid4())
            opt = Optimization(
                id=opt_id,
                raw_prompt="",
                optimized_prompt=optimized_prompt,
                task_type=task_type or "unknown",
                strategy_used=strategy_used or "unknown",
                changes_summary=changes_summary or "",
                score_clarity=bias_corrected.get("clarity"),
                score_specificity=bias_corrected.get("specificity"),
                score_structure=bias_corrected.get("structure"),
                score_faithfulness=bias_corrected.get("faithfulness"),
                score_conciseness=bias_corrected.get("conciseness"),
                overall_score=overall,
                provider="mcp_passthrough",
                model_used=model or "unknown",
                scoring_mode="self_rated",
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
                            "task_type": task_type or "unknown",
                            "strategy_used": strategy_used or "unknown",
                            "overall_score": overall,
                            "provider": "mcp_passthrough",
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
        "scoring_mode": "self_rated",
        "bias_corrected_scores": bias_corrected,
        "strategy_compliance": strategy_compliance,
        "heuristic_flags": heuristic_flags,
    }


# ---- Entry point ----


def create_mcp_server() -> FastMCP:
    """Return the configured MCP server instance."""
    return mcp


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
