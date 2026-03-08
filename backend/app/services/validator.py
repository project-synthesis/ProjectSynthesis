"""Stage 4: Validate

Scores and verifies the optimized prompt is genuinely better.
Uses claude-sonnet for quality assessment.
Server-side weighted average computation (never trust LLM arithmetic).
"""

import asyncio
import json
import logging
from typing import AsyncGenerator

from app.config import settings
from app.prompts.validator_prompt import get_validator_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider, parse_json_robust
from app.services.context_builders import build_codebase_summary

logger = logging.getLogger(__name__)

# Score weights for overall_score computation
SCORE_WEIGHTS = {
    "clarity_score": 0.20,
    "specificity_score": 0.20,
    "structure_score": 0.15,
    "faithfulness_score": 0.25,
    "conciseness_score": 0.20,
}


def compute_overall_score(scores: dict) -> int:
    """Compute weighted average overall score.

    Weights: clarity 20%, specificity 20%, structure 15%,
             faithfulness 25%, conciseness 20%

    Returns: integer 1-10
    """
    weighted_sum = 0.0
    total_weight = 0.0

    for field, weight in SCORE_WEIGHTS.items():
        value = scores.get(field)
        if value is not None and isinstance(value, (int, float)):
            weighted_sum += value * weight
            total_weight += weight

    if total_weight == 0:
        return 5  # default mid-score

    raw = weighted_sum / total_weight
    return max(1, min(10, round(raw)))


def _default_validation() -> dict:
    """Default validation result used when all providers fail."""
    return {
        "is_improvement": False,
        "validation_quality": "failed",
        "clarity_score": 5,
        "specificity_score": 5,
        "structure_score": 5,
        "faithfulness_score": 5,
        "conciseness_score": 5,
        "verdict": "Validation failed - default scores applied.",
        "issues": ["Validation stage encountered an error"],
    }


async def run_validate(
    provider: LLMProvider,
    original_prompt: str,
    optimized_prompt: str,
    changes_made: list[str],
    codebase_context: dict | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 4 validation.

    Yields:
        ("step_progress", {"step": "validate", "content": chunk}) for each streamed chunk
        ("validation", dict) with canonical shape:
            scores: dict  — all 5 dimension scores + overall_score (authoritative)
            overall_score: int  — convenience copy for direct pipeline access
            is_improvement: bool
            verdict: str
            issues: list[str]

    Individual dimension scores (clarity_score, etc.) live only in the
    ``scores`` sub-dict to avoid duplication. Callers that previously read
    top-level clarity_score should switch to scores["clarity_score"].

    Args:
        codebase_context: When provided, a codebase summary is injected into
            the user message so the LLM can assess codebase accuracy when
            scoring faithfulness_score.
    """
    system_prompt = get_validator_prompt(has_codebase_context=codebase_context is not None)

    user_message = (
        f"Original prompt:\n---\n{original_prompt}\n---\n\n"
        f"Optimized prompt:\n---\n{optimized_prompt}\n---\n\n"
        f"Changes made:\n{json.dumps(changes_made, indent=2)}"
    )

    if codebase_context is not None:
        codebase_summary = build_codebase_summary(codebase_context)
        if codebase_summary:
            user_message += (
                f"\n\nCodebase context (verify optimized prompt references real symbols/APIs):\n"
                f"{codebase_summary[:2500]}"
            )

    model = MODEL_ROUTING["validate"]

    # Stream with background task + queue (same pattern as optimizer.py).
    full_text = ""
    stream_failed = False
    chunk_queue: asyncio.Queue = asyncio.Queue()

    async def _stream_worker() -> None:
        try:
            async for chunk in provider.stream(system_prompt, user_message, model):
                await chunk_queue.put(chunk)
        finally:
            await chunk_queue.put(None)

    stream_task = asyncio.create_task(_stream_worker())
    timeout_handle = asyncio.get_running_loop().call_later(
        settings.VALIDATE_TIMEOUT_SECONDS,
        lambda: stream_task.cancel() if not stream_task.done() else None,
    )

    try:
        while True:
            chunk = await chunk_queue.get()
            if chunk is None:
                break
            full_text += chunk
            yield ("step_progress", {"step": "validate", "content": chunk})
        await stream_task
    except asyncio.CancelledError:
        logger.warning("Validate stage streaming timed out after %ds", settings.VALIDATE_TIMEOUT_SECONDS)
        stream_failed = True
    except Exception as e:
        logger.error(f"Stage 4 (Validate) streaming failed: {e}")
        stream_failed = True
    finally:
        timeout_handle.cancel()
        if not stream_task.done():
            stream_task.cancel()

    # JSON extraction from streamed text
    raw = None
    if not stream_failed and full_text:
        try:
            raw = parse_json_robust(full_text)
        except Exception:
            pass

    # Fallback to complete_json when streaming failed or parse failed
    if not raw:
        try:
            raw = await asyncio.wait_for(
                provider.complete_json(system_prompt, user_message, model),
                timeout=settings.VALIDATE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.error(f"Stage 4 (Validate) failed: {e}")
            raw = _default_validation()

    # Ensure all raw score fields exist and are numeric before computing
    for field in SCORE_WEIGHTS:
        if field not in raw or not isinstance(raw.get(field), (int, float)):
            raw[field] = 5

    # ALWAYS compute overall_score server-side (never trust LLM arithmetic)
    overall_score = compute_overall_score(raw)

    # Canonical scores sub-dict (single authoritative source for all scores)
    scores = {
        "clarity_score": raw["clarity_score"],
        "specificity_score": raw["specificity_score"],
        "structure_score": raw["structure_score"],
        "faithfulness_score": raw["faithfulness_score"],
        "conciseness_score": raw["conciseness_score"],
        "overall_score": overall_score,
    }

    yield ("validation", {
        # Scores live exclusively in the sub-dict — no duplication at top level
        "scores": scores,
        # overall_score is mirrored at top-level as a convenience for pipeline
        # retry logic and direct DB writes without requiring sub-dict access
        "overall_score": overall_score,
        "is_improvement": raw.get("is_improvement", False),
        "verdict": raw.get("verdict", ""),
        "issues": raw.get("issues", []),
        # Pass through validation_quality if set (e.g. "failed" from _default_validation)
        **({"validation_quality": raw["validation_quality"]} if "validation_quality" in raw else {}),
    })
