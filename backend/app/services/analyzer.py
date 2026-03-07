"""Stage 1: Analyze

Classifies the prompt and identifies optimization opportunities.
Uses claude-haiku for fast, cheap structured JSON extraction.
"""

import asyncio
import json
import logging
from typing import Optional

from app.providers.base import LLMProvider, MODEL_ROUTING
from app.prompts.analyzer_prompt import get_analyzer_prompt
from app.config import settings

logger = logging.getLogger(__name__)


async def run_analyze(
    provider: LLMProvider,
    raw_prompt: str,
    codebase_context: Optional[dict] = None,
) -> dict:
    """Run Stage 1 analysis on the raw prompt.

    Returns:
        dict with keys: task_type, weaknesses, strengths, complexity,
                        recommended_frameworks, codebase_informed
    """
    system_prompt = get_analyzer_prompt()

    user_message = f"Analyze this prompt:\n\n---\n{raw_prompt}\n---"
    if codebase_context:
        user_message += f"\n\nCodebase context:\n{json.dumps(codebase_context, indent=2)}"

    model = MODEL_ROUTING["analyze"]

    try:
        result = await asyncio.wait_for(
            provider.complete_json(system_prompt, user_message, model),
            timeout=settings.ANALYZE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Analyze stage timed out after %ds", settings.ANALYZE_TIMEOUT_SECONDS
        )
        raise  # Propagate to pipeline.py as a stage failure
    except Exception as e:
        logger.error(f"Stage 1 (Analyze) failed: {e}")
        # Return sensible defaults so downstream stages can still run
        result = {
            "task_type": "general",
            "weaknesses": ["Analysis failed - using defaults"],
            "strengths": [],
            "complexity": "moderate",
            "recommended_frameworks": ["CO-STAR"],
            "codebase_informed": codebase_context is not None,
        }

    # Ensure required fields
    result.setdefault("task_type", "general")
    result.setdefault("weaknesses", [])
    result.setdefault("strengths", [])
    result.setdefault("complexity", "moderate")
    result.setdefault("recommended_frameworks", [])
    result.setdefault("codebase_informed", codebase_context is not None)

    return result
