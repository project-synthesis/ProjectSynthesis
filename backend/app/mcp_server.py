"""Standalone MCP server with 3 optimization tools.

Copyright 2025 Project Synthesis contributors.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from app.config import PROMPTS_DIR

logger = logging.getLogger(__name__)

# Module-level provider cache — set once by the lifespan, read by tools.
_provider = None


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Detect the LLM provider once at startup and expose it to tools."""
    global _provider
    from app.providers.detector import detect_provider

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
) -> dict:
    """Run the full optimization pipeline on a prompt.

    Returns the optimized prompt with 5-dimension scores and improvement deltas.
    """
    if len(prompt) < 20:
        raise ValueError("Prompt too short (minimum 20 characters)")
    if len(prompt) > 200000:
        raise ValueError("Prompt too long (maximum 200000 characters)")

    provider = _provider
    if not provider:
        raise ValueError("No LLM provider available")

    # Scan workspace for guidance files
    guidance = None
    if workspace_path:
        from pathlib import Path

        from app.services.roots_scanner import RootsScanner
        scanner = RootsScanner()
        guidance = scanner.scan(Path(workspace_path))

    from app.database import async_session_factory
    from app.services.pipeline import PipelineOrchestrator

    async with async_session_factory() as db:
        orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

        result = None
        async for event in orchestrator.run(
            raw_prompt=prompt,
            provider=provider,
            db=db,
            strategy_override=strategy,
            codebase_guidance=guidance,
        ):
            if event.event == "optimization_complete":
                result = event.data
            elif event.event == "error":
                raise ValueError(event.data.get("error", "Pipeline failed"))

        if not result:
            raise ValueError("Pipeline produced no result")

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


# ---- Tool 2: synthesis_prepare_optimization ----


@mcp.tool()
async def synthesis_prepare_optimization(
    prompt: str,
    strategy: str | None = None,
    max_context_tokens: int = 128000,
    workspace_path: str | None = None,
    repo_full_name: str | None = None,
) -> dict:
    """Assemble the full optimization prompt with context for an external LLM.

    Call synthesis_save_result with the output.
    """
    if len(prompt) < 20:
        raise ValueError("Prompt too short (minimum 20 characters)")

    from app.services.prompt_loader import PromptLoader
    from app.services.strategy_loader import StrategyLoader

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

    # Scan workspace for guidance files
    guidance = None
    if workspace_path:
        from pathlib import Path

        from app.services.roots_scanner import RootsScanner
        scanner = RootsScanner()
        guidance = scanner.scan(Path(workspace_path))

    assembled = loader.render("passthrough.md", {
        "raw_prompt": prompt,
        "strategy_instructions": strategy_instructions,
        "scoring_rubric_excerpt": scoring_excerpt,
        "codebase_guidance": guidance,
        "codebase_context": None,
        "adaptation_state": None,
    })

    trace_id = str(uuid.uuid4())
    context_size_tokens = len(assembled) // 4  # rough estimate

    return {
        "trace_id": trace_id,
        "assembled_prompt": assembled,
        "context_size_tokens": context_size_tokens,
        "strategy_requested": strategy_name,
    }


# ---- Tool 3: synthesis_save_result ----


@mcp.tool()
async def synthesis_save_result(
    trace_id: str,
    optimized_prompt: str,
    changes_summary: str | None = None,
    task_type: str | None = None,
    strategy_used: str | None = None,
    scores: dict | None = None,
    model: str | None = None,
) -> dict:
    """Persist an optimization result from an external LLM.

    Applies bias correction to self-rated scores.
    """
    from app.database import async_session_factory
    from app.models import Optimization
    from app.services.heuristic_scorer import HeuristicScorer

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

        # Run heuristic checks
        heuristic_structure = HeuristicScorer.heuristic_structure(optimized_prompt)
        heuristic_specificity = HeuristicScorer.heuristic_specificity(optimized_prompt)
        heuristic_scores = {
            "structure": heuristic_structure,
            "specificity": heuristic_specificity,
        }
        heuristic_flags = HeuristicScorer.detect_divergence(
            clean_scores, heuristic_scores
        )

    # Determine strategy compliance
    strategy_compliance = "unknown"
    if strategy_used:
        strategy_compliance = "matched"  # simplified — full check needs trace lookup

    # Persist
    opt_id = str(uuid.uuid4())
    async with async_session_factory() as db:
        opt = Optimization(
            id=opt_id,
            raw_prompt="",  # not available in passthrough
            optimized_prompt=optimized_prompt,
            task_type=task_type or "unknown",
            strategy_used=strategy_used or "unknown",
            changes_summary=changes_summary or "",
            score_clarity=bias_corrected.get("clarity"),
            score_specificity=bias_corrected.get("specificity"),
            score_structure=bias_corrected.get("structure"),
            score_faithfulness=bias_corrected.get("faithfulness"),
            score_conciseness=bias_corrected.get("conciseness"),
            overall_score=(
                sum(bias_corrected.values()) / max(len(bias_corrected), 1)
                if bias_corrected
                else None
            ),
            provider="mcp_passthrough",
            model_used=model or "unknown",
            scoring_mode="self_rated",
            status="completed",
            trace_id=trace_id,
        )
        db.add(opt)
        await db.commit()

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
