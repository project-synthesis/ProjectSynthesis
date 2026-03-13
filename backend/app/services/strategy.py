"""Stage 2: Strategy

Selects the optimal optimization framework combination.
Uses claude-opus for deep reasoning about framework selection.
"""

import logging
from typing import AsyncGenerator, Optional

from app.config import settings
from app.prompts.strategy_prompt import get_strategy_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider
from app.schemas.pipeline_outputs import StrategyOutput
from app.services.cache_service import CacheService, get_cache
from app.services.context_builders import (
    build_analysis_summary,
    build_codebase_summary,
    format_file_contexts,
    format_instructions,
    format_url_contexts,
)
from app.services.stage_runner import extract_json_with_fallback, stream_with_timeout
from app.services.strategy_selector import heuristic_strategy_fallback

logger = logging.getLogger(__name__)

_STRATEGY_CACHE_TTL = 86400  # 24 hours (prompt-specific; was 7 days when keyed only on task_type)

# ── Intent-specific framework hints ────────────────────────────────────────
# Maps explore intent_category to actionable framework recommendations for
# the strategy stage. Each hint names 2-3 frameworks with WHY they fit the
# intent, plus one anti-recommendation where applicable.
_INTENT_FRAMEWORK_HINTS: dict[str, str] = {
    "refactoring": (
        "Favour chain-of-thought (systematic discovery of refactoring opportunities) or "
        "constraint-injection (scope boundaries, safety rules, backward-compat constraints). "
        "Avoid few-shot — refactoring is too context-dependent for generic examples."
    ),
    "api_design": (
        "Favour structured-output (contract definitions, schema specifications) or "
        "constraint-injection (interface rules, versioning constraints, error contract standards). "
        "CO-STAR adds audience clarity for public-facing APIs."
    ),
    "feature_build": (
        "Favour step-by-step (implementation sequence with clear milestones) or "
        "role-task-format (clear executor assignment with acceptance criteria). "
        "Constraint-injection prevents scope creep."
    ),
    "testing": (
        "Favour few-shot-scaffolding (test case examples showing input/expected/assertion pattern) or "
        "structured-output (test specification format). "
        "Constraint-injection defines coverage targets and test isolation requirements."
    ),
    "debugging": (
        "Favour chain-of-thought (hypothesis → evidence → conclusion methodology) or "
        "step-by-step (systematic elimination procedure). "
        "Avoid persona — debugging needs process rigour, not character."
    ),
    "architecture_review": (
        "Favour chain-of-thought (multi-dimensional analysis across coupling, cohesion, layering) or "
        "RISEN (role + structured review steps with explicit end goal). "
        "Context-enrichment grounds the review in domain-specific architectural standards."
    ),
    "performance": (
        "Favour step-by-step (measurement → analysis → optimization sequence) or "
        "structured-output (benchmark result format, profiling checklist). "
        "Constraint-injection defines performance targets and SLA boundaries."
    ),
    "documentation": (
        "Favour CO-STAR (audience-aware writing with clear tone and format) or "
        "role-task-format (clear scope + output format for documentation deliverable). "
        "Context-enrichment grounds documentation in domain conventions and existing style."
    ),
    "migration": (
        "Favour step-by-step (ordered migration phases with rollback checkpoints) or "
        "constraint-injection (compatibility rules, rollback requirements, data integrity constraints). "
        "RISEN adds explicit end-goal clarity for migration success criteria."
    ),
    "security": (
        "Favour constraint-injection (security rules, compliance requirements, threat boundaries) or "
        "chain-of-thought (threat modeling sequence: assets → threats → mitigations). "
        "RISEN adds explicit scope narrowing to prevent audit scope creep."
    ),
}


async def run_strategy(
    provider: LLMProvider,
    raw_prompt: str,
    analysis: dict,
    codebase_context: Optional[dict] = None,
    file_contexts: list[dict] | None = None,
    url_fetched_contexts: list[dict] | None = None,
    instructions: list[str] | None = None,
    model: str | None = None,
    strategy_affinities: dict | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 2 strategy selection.

    Yields:
        ("step_progress", {"step": "strategy", "content": chunk}) for each streamed chunk
        ("strategy", dict) with keys: primary_framework, secondary_frameworks,
                                       rationale, approach_notes
    """
    system_prompt = get_strategy_prompt()

    # Cache check: keyed on prompt content + analysis signature + system prompt
    # so different prompts with the same task_type get distinct strategies,
    # and prompt template changes auto-invalidate cached results.
    cache = get_cache()
    task_type = analysis.get("task_type", "general")
    complexity = analysis.get("complexity", "moderate")
    cache_key = None
    if cache:
        prompt_hash = CacheService.hash_content(raw_prompt)
        analysis_hash = CacheService.hash_content(
            f"{task_type}:{complexity}:"
            f"{','.join(analysis.get('weaknesses', [])[:5])}:"
            f"{','.join(analysis.get('recommended_frameworks', []))}"
        )
        sys_hash = CacheService.hash_content(system_prompt)
        cache_key = CacheService.make_key("strategy_v3", prompt_hash, analysis_hash, sys_hash)

    if cache and cache_key:
        cached = await cache.get(cache_key)
        if cached is not None:
            cached["strategy_source"] = "cached"
            yield ("strategy", cached)
            return

    user_message = (
        f"Raw prompt:\n---\n{raw_prompt}\n---\n\n"
        f"Analysis result:\n{build_analysis_summary(analysis)}"
    )
    if codebase_context:
        codebase_summary = build_codebase_summary(codebase_context)
        if codebase_summary:
            user_message += f"\n\nCodebase intelligence (navigational context):\n{codebase_summary}"

        # Intent-aware framework hint
        intent_cat = codebase_context.get("intent_category", "")
        if intent_cat and intent_cat != "general":
            hint = _INTENT_FRAMEWORK_HINTS.get(intent_cat, "")
            user_message += (
                f"\n\nIntent signal: codebase exploration classified this as '{intent_cat}'. "
            )
            if hint:
                user_message += f"Framework alignment: {hint}"
            else:
                user_message += (
                    "Consider frameworks that align with this intent's primary focus."
                )
    else:
        user_message += (
            "\n\nNo codebase context is available — no repository was linked.\n"
            "Your framework selection must NOT assume any specific programming language,\n"
            "runtime version, framework, library, or project structure.\n"
            "Select frameworks based solely on the task type, complexity, and weaknesses\n"
            "identified in the analysis. If the user's prompt does not mention a specific\n"
            "tech stack, prefer general-purpose frameworks (role-task-format, chain-of-thought,\n"
            "step-by-step, constraint-injection) over domain-specific ones."
        )

    # Inject attached files / URLs / user constraints so strategy selection
    # can account for domain-specific signals (the strategy prompt already
    # instructs the LLM to consider these).
    user_message += format_file_contexts(file_contexts)
    user_message += format_url_contexts(url_fetched_contexts)

    user_message += format_instructions(instructions)

    model = model or MODEL_ROUTING["strategy"]

    stream_ok = False
    full_text = ""
    async for status, text in stream_with_timeout(
        provider, system_prompt, user_message, model,
        settings.STRATEGY_TIMEOUT_SECONDS, "Stage 2 (Strategy)",
    ):
        if status == "chunk":
            yield ("step_progress", {"step": "strategy", "content": text})
        elif status == "done":
            full_text = text  # type: ignore[assignment]
            stream_ok = True
        elif status == "timeout":
            full_text = text or ""  # type: ignore[assignment]

    result = await extract_json_with_fallback(
        provider, system_prompt, user_message, model,
        settings.STRATEGY_TIMEOUT_SECONDS, "Stage 2 (Strategy)",
        full_text, stream_ok,
        quality_key="strategy_source",
        quality_value_success="llm",
        quality_value_fallback_json="llm_json",
        default_result={
            **heuristic_strategy_fallback(analysis.get("task_type", "general")),
            "strategy_source": "heuristic",
        },
        output_type=StrategyOutput,
    )

    # Ensure required fields — derive default from task_type heuristic
    # rather than hardcoding a single framework
    if "primary_framework" not in result or not result["primary_framework"]:
        fallback = heuristic_strategy_fallback(task_type)
        result["primary_framework"] = fallback["primary_framework"]
    result.setdefault("secondary_frameworks", [])
    result.setdefault("rationale", "")
    result.setdefault("approach_notes", "")

    # Cache successful LLM results
    if cache and cache_key and result.get("strategy_source") in ("llm", "llm_json"):
        await cache.set(cache_key, result, ttl_seconds=_STRATEGY_CACHE_TTL)

    # If user has strategy affinities, add soft bias
    if strategy_affinities:
        task = analysis.get("task_type", "")
        affinities = strategy_affinities.get(task, {})
        if affinities:
            result["user_affinities"] = affinities

    yield ("strategy", result)
