"""Stage 4: Validate

Scores and verifies the optimized prompt is genuinely better.
Uses claude-sonnet for quality assessment.
Server-side weighted average computation (never trust LLM arithmetic).
"""

import json
import logging
from typing import AsyncGenerator

from app.config import settings
from app.prompts.validator_prompt import get_validator_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider
from app.schemas.pipeline_outputs import ValidateOutput
from app.services.context_builders import build_codebase_summary
from app.services.stage_runner import extract_json_with_fallback, stream_with_timeout

logger = logging.getLogger(__name__)

# Score weights for overall_score computation
SCORE_WEIGHTS = {
    "clarity_score": 0.20,
    "specificity_score": 0.20,
    "structure_score": 0.15,
    "faithfulness_score": 0.25,
    "conciseness_score": 0.20,
}


def compute_overall_score(
    scores: dict,
    user_weights: dict[str, float] | None = None,
) -> float | None:
    """Compute weighted average overall score.

    Uses user-adapted weights when available, falls back to defaults.

    Returns: float 1.0-10.0 rounded to 1 decimal place,
             or None if no valid scores are present.
    """
    weights = user_weights if user_weights else SCORE_WEIGHTS
    weighted_sum = 0.0
    total_weight = 0.0

    for field, weight in weights.items():
        value = scores.get(field)
        if value is not None and isinstance(value, (int, float)):
            weighted_sum += value * weight
            total_weight += weight

    if total_weight == 0:
        return None

    raw = weighted_sum / total_weight
    return max(1.0, min(10.0, round(raw, 1)))


def compute_effective_weights(
    user_weights: dict[str, float] | None,
    framework_profile: dict | None,
) -> dict[str, float]:
    """Combine user weights with framework profile multipliers, renormalize to sum=1.0."""
    from app.services.prompt_diff import SCORE_DIMENSIONS

    dims = sorted(SCORE_DIMENSIONS)
    default_w = 1.0 / len(dims)
    if not user_weights:
        base = {d: default_w for d in dims}
    else:
        base = {d: user_weights.get(d, default_w) for d in dims}
    if framework_profile:
        emphasis = framework_profile.get("emphasis", {})
        de_emphasis = framework_profile.get("de_emphasis", {})
        for dim in dims:
            multiplier = emphasis.get(dim, de_emphasis.get(dim, 1.0))
            base[dim] *= multiplier
    total = sum(base.values())
    if total > 0:
        base = {d: w / total for d, w in base.items()}
    return base


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
    instructions: list[str] | None = None,
    model: str | None = None,
    user_weights: dict[str, float] | None = None,
    extra_validation_context: str | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 4 validation.

    Yields:
        ("step_progress", {"step": "validate", "content": chunk}) for each streamed chunk
        ("validation", dict) with canonical shape:
            scores: dict  — all 5 dimension scores + overall_score (authoritative)
            overall_score: float  — convenience copy for direct pipeline access
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
        instructions: User-specified output constraints. When provided, the
            validator checks whether the optimized prompt honors them.
        extra_validation_context: Additional validation instructions (e.g.
            issue verification prompts from adaptation). Appended to the
            user message when provided.
    """
    intent_cat = ""
    if codebase_context is not None:
        intent_cat = codebase_context.get("intent_category", "")

    system_prompt = get_validator_prompt(
        has_codebase_context=codebase_context is not None,
        intent_category=intent_cat,
    )

    user_message = (
        f"Original prompt:\n---\n{original_prompt}\n---\n\n"
        f"Optimized prompt:\n---\n{optimized_prompt}\n---\n\n"
        f"Changes made:\n{json.dumps(changes_made, indent=2)}"
    )

    if codebase_context is not None:
        codebase_summary = build_codebase_summary(codebase_context)
        if codebase_summary:
            user_message += (
                f"\n\nCodebase intelligence (navigational context from explore phase — "
                f"may be partial or stale):\n"
                f"Use this to verify the optimized prompt does not fabricate file paths, "
                f"function names, or API signatures. However, absence from this context "
                f"does NOT mean something doesn't exist — this is partial coverage.\n"
                f"{codebase_summary[:4000]}"
            )
    else:
        user_message += (
            "\n\nNo codebase exploration was performed for this optimization.\n"
            "If the optimized prompt introduces specific tech stacks, file paths, "
            "framework names, or architectural patterns that are NOT present in the "
            "original prompt, penalize faithfulness_score — these are likely hallucinated "
            "and would mislead the executor."
        )

    if instructions:
        constraint_list = "\n".join(f"  - {c}" for c in instructions[:10])
        user_message += (
            f"\n\nUser-specified output constraints (the optimized prompt MUST honor these):\n"
            f"{constraint_list}\n"
            "Verify each constraint is reflected in the optimized prompt. "
            "Missing or violated constraints are faithfulness failures."
        )

    if extra_validation_context:
        user_message += extra_validation_context

    model = model or MODEL_ROUTING["validate"]

    stream_ok = False
    full_text = ""
    async for status, text in stream_with_timeout(
        provider, system_prompt, user_message, model,
        settings.VALIDATE_TIMEOUT_SECONDS, "Stage 4 (Validate)",
    ):
        if status == "chunk":
            yield ("step_progress", {"step": "validate", "content": text})
        elif status == "done":
            full_text = text  # type: ignore[assignment]
            stream_ok = True
        elif status == "timeout":
            full_text = text or ""  # type: ignore[assignment]

    raw = await extract_json_with_fallback(
        provider, system_prompt, user_message, model,
        settings.VALIDATE_TIMEOUT_SECONDS, "Stage 4 (Validate)",
        full_text, stream_ok,
        quality_key="validation_quality",
        quality_value_success=None,  # Don't set on success (validator doesn't use quality flag for success)
        default_result=_default_validation(),
        output_type=ValidateOutput,
    )

    # Ensure all raw score fields exist and are numeric before computing
    for field in SCORE_WEIGHTS:
        if field not in raw or not isinstance(raw.get(field), (int, float)):
            raw[field] = 5

    # ALWAYS compute overall_score server-side (never trust LLM arithmetic)
    overall_score = compute_overall_score(raw, user_weights)

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
