"""Stage 3: Optimize (streaming)

Rewrites the prompt using the selected strategy.
Uses claude-opus for creative rewriting at maximum capability.
Streams token by token via SSE step_progress events.
"""

import asyncio
import json
import logging
import re
from typing import AsyncGenerator, Optional

from app.providers.base import LLMProvider, MODEL_ROUTING
from app.prompts.optimizer_prompts import get_optimizer_prompt
from app.config import settings

logger = logging.getLogger(__name__)


def _extract_optimized(full_text: str) -> dict | None:
    """Try three strategies to extract a structured result from streamed text.

    Strategy 1: Entire text is valid JSON.
    Strategy 2: JSON inside ```json ... ``` code fence.
    Strategy 3: First greedy ``{ ... }`` block.

    Returns the parsed dict, or ``None`` if all strategies fail.
    """
    # Strategy 1: raw JSON
    try:
        parsed = json.loads(full_text.strip())
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: ```json ... ``` code fence
    match = re.search(r"```json\s*([\s\S]*?)\s*```", full_text)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: first greedy { ... } block
    match = re.search(r"\{[\s\S]*\}", full_text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return None


async def run_optimize(
    provider: LLMProvider,
    raw_prompt: str,
    analysis: dict,
    strategy: dict,
    codebase_context: Optional[dict] = None,
    retry_constraints: Optional[dict] = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 3 optimization with streaming.

    Yields:
        ("step_progress", {"step": "optimize", "content": "chunk"}) for each token
        ("optimization", {optimized_prompt, changes_made, framework_applied, optimization_notes})

    Args:
        retry_constraints: If provided, includes adjusted constraints for retry attempts
            with keys: min_score_target, previous_score, focus_areas
    """
    task_type = analysis.get("task_type", "general")
    system_prompt = get_optimizer_prompt(task_type)

    user_message = (
        f"Raw prompt to optimize:\n---\n{raw_prompt}\n---\n\n"
        f"Analysis:\n{json.dumps(analysis, indent=2)}\n\n"
        f"Strategy:\n{json.dumps(strategy, indent=2)}"
    )
    if codebase_context:
        user_message += f"\n\nCodebase context:\n{json.dumps(codebase_context, indent=2)}"

    if retry_constraints:
        user_message += (
            f"\n\n--- RETRY WITH ADJUSTED CONSTRAINTS ---\n"
            f"Previous optimization scored {retry_constraints.get('previous_score', 'low')}/10.\n"
            f"Target minimum score: {retry_constraints.get('min_score_target', 7)}/10.\n"
            f"Focus on improving these issues: {json.dumps(retry_constraints.get('focus_areas', []))}\n"
            f"Be MORE specific, structured, and detailed than the previous attempt.\n"
            f"Ensure the optimized prompt is substantially better than the original."
        )

    model = MODEL_ROUTING["optimize"]
    framework_applied = strategy.get("primary_framework", "")

    # Stream the optimization with timeout
    full_text = ""
    stream_failed = False
    try:
        async with asyncio.timeout(settings.OPTIMIZE_TIMEOUT_SECONDS):
            async for chunk in provider.stream(system_prompt, user_message, model):
                full_text += chunk
                yield ("step_progress", {"step": "optimize", "content": chunk})
    except asyncio.TimeoutError:
        logger.warning(
            "Optimize stage streaming timed out after %ds", settings.OPTIMIZE_TIMEOUT_SECONDS
        )
        if not full_text:
            raise  # Nothing accumulated — hard failure
        # Partial text accumulated; fall through to JSON extraction
    except Exception as e:
        logger.error(f"Stage 3 (Optimize) streaming failed: {e}")
        stream_failed = True

    if stream_failed:
        # Non-streaming fallback when streaming itself errors out
        try:
            full_text = await asyncio.wait_for(
                provider.complete(system_prompt, user_message, model),
                timeout=settings.OPTIMIZE_TIMEOUT_SECONDS,
            )
        except Exception as e2:
            logger.error(f"Stage 3 (Optimize) complete() also failed: {e2}")
            full_text = raw_prompt  # Last resort: return original unchanged

    # 3-strategy JSON extraction
    parsed = _extract_optimized(full_text)

    if parsed is None:
        # All 3 strategies failed — attempt complete_json() as a clean non-streaming retry
        logger.warning(
            "Streaming output failed all JSON parse strategies; falling back to complete_json()"
        )
        try:
            parsed = await asyncio.wait_for(
                provider.complete_json(system_prompt, user_message, model),
                timeout=settings.OPTIMIZE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.error(f"Stage 3 (Optimize) complete_json() fallback failed: {e}")
            parsed = None

    if parsed:
        optimized_prompt = parsed.get("optimized_prompt", full_text)
        changes_made = parsed.get("changes_made", [])
        framework_applied = parsed.get("framework_applied", framework_applied)
        optimization_notes = parsed.get("optimization_notes", "")
    else:
        # All extraction strategies exhausted — treat full_text as the optimized prompt
        optimized_prompt = full_text
        changes_made = []
        optimization_notes = ""

    yield ("optimization", {
        "optimized_prompt": optimized_prompt,
        "changes_made": changes_made,
        "framework_applied": framework_applied,
        "optimization_notes": optimization_notes,
    })
