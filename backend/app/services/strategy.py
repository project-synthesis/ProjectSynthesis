"""Stage 2: Strategy

Selects the optimal optimization framework combination.
Uses claude-opus for deep reasoning about framework selection.
"""

import asyncio
import logging
from typing import Optional

from app.prompts.strategy_prompt import get_strategy_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider
from app.services.context_builders import build_analysis_summary, build_codebase_summary
from app.services.strategy_selector import heuristic_strategy_fallback
from app.config import settings

logger = logging.getLogger(__name__)


async def run_strategy(
    provider: LLMProvider,
    raw_prompt: str,
    analysis: dict,
    codebase_context: Optional[dict] = None,
) -> dict:
    """Run Stage 2 strategy selection.

    Returns:
        dict with keys: primary_framework, secondary_frameworks, rationale, approach_notes
    """
    system_prompt = get_strategy_prompt()

    user_message = (
        f"Raw prompt:\n---\n{raw_prompt}\n---\n\n"
        f"Analysis result:\n{build_analysis_summary(analysis)}"
    )
    if codebase_context:
        codebase_summary = build_codebase_summary(codebase_context)
        if codebase_summary:
            user_message += f"\n\nCodebase context:\n{codebase_summary}"

    model = MODEL_ROUTING["strategy"]

    try:
        result = await asyncio.wait_for(
            provider.complete_json(system_prompt, user_message, model),
            timeout=settings.STRATEGY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Strategy stage timed out after %ds", settings.STRATEGY_TIMEOUT_SECONDS
        )
        raise  # Propagate to pipeline.py as a stage failure
    except Exception as e:
        logger.error(f"Stage 2 (Strategy) failed: {e}. Using heuristic fallback.")
        result = heuristic_strategy_fallback(analysis.get("task_type", "general"))

    # Ensure required fields
    result.setdefault("primary_framework", "CO-STAR")
    result.setdefault("secondary_frameworks", [])
    result.setdefault("rationale", "")
    result.setdefault("approach_notes", "")

    return result
