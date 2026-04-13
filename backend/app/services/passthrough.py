"""Shared passthrough prompt assembly logic.

Used by both the REST endpoint (POST /api/optimize/passthrough) and the MCP tool
(synthesis_prepare_optimization) to assemble the full optimization prompt for
external LLM processing. Centralizes strategy resolution, scoring rubric loading,
and template rendering.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader

logger = logging.getLogger(__name__)

_SCORING_RUBRIC_MAX_CHARS = 4000


def resolve_strategy(
    strategy_loader: StrategyLoader,
    requested: str | None,
) -> tuple[str, str]:
    """Resolve a strategy name and load its instructions.

    Falls back to "auto" (or the first available strategy) if the requested
    strategy doesn't exist on disk.

    Returns:
        (resolved_name, instructions) tuple.
    """
    name = requested or "auto"
    available = strategy_loader.list_strategies()
    if available and name not in available:
        logger.info("Strategy '%s' not found, falling back to 'auto'", name)
        name = "auto" if "auto" in available else (available[0] if available else name)
    instructions = strategy_loader.load(name)
    return name, instructions


def assemble_passthrough_prompt(
    prompts_dir: Path,
    raw_prompt: str,
    strategy_name: str | None = None,
    codebase_guidance: str | None = None,
    codebase_context: str | None = None,
    adaptation_state: str | None = None,
    analysis_summary: str | None = None,
    applied_patterns: str | None = None,
    divergence_alerts: str | None = None,
) -> tuple[str, str]:
    """Assemble a full passthrough optimization prompt from templates.

    Args:
        prompts_dir: Path to the prompts/ directory.
        raw_prompt: The user's raw prompt to optimize.
        strategy_name: Requested strategy name (None → "auto").
        codebase_guidance: Optional workspace guidance content.
        codebase_context: Optional curated index context.
        adaptation_state: Optional adaptation state content.
        analysis_summary: Optional heuristic analysis results.
        applied_patterns: Optional proven patterns from taxonomy engine.
        divergence_alerts: Optional tech stack divergence alert text.

    Returns:
        (assembled_prompt, resolved_strategy_name) tuple.
    """
    loader = PromptLoader(prompts_dir)
    strategy_loader = StrategyLoader(prompts_dir / "strategies")

    resolved_name, strategy_instructions = resolve_strategy(strategy_loader, strategy_name)

    scoring_rubric = loader.load("scoring.md")
    scoring_excerpt = (
        scoring_rubric[:_SCORING_RUBRIC_MAX_CHARS] + "..."
        if len(scoring_rubric) > _SCORING_RUBRIC_MAX_CHARS
        else scoring_rubric
    )

    assembled = loader.render("passthrough.md", {
        "raw_prompt": raw_prompt,
        "strategy_instructions": strategy_instructions,
        "scoring_rubric_excerpt": scoring_excerpt,
        "codebase_guidance": codebase_guidance,
        "codebase_context": codebase_context,
        "adaptation_state": adaptation_state,
        "analysis_summary": analysis_summary,
        "applied_patterns": applied_patterns,
        "divergence_alerts": divergence_alerts,
    })

    return assembled, resolved_name
