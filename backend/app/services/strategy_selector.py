"""Heuristic fallback for strategy selection.

Maps task_type to a default framework when the LLM strategy call fails.
"""

import logging

logger = logging.getLogger(__name__)


# Mapping of task_type -> (primary_framework, secondary_frameworks, rationale)
TASK_FRAMEWORK_MAP: dict[str, tuple[str, list[str], str]] = {
    "coding": (
        "structured-output",
        ["constraint-injection"],
        "Coding tasks benefit from strict output format specifications and explicit constraints "
        "to reduce ambiguity in generated code.",
    ),
    "analysis": (
        "chain-of-thought",
        ["context-enrichment"],
        "Analysis tasks require step-by-step reasoning. Chain-of-thought helps surface "
        "intermediate logic, while context enrichment ensures all relevant information is considered.",
    ),
    "reasoning": (
        "chain-of-thought",
        ["step-by-step"],
        "Reasoning tasks are best served by explicit chain-of-thought prompting "
        "combined with step-by-step decomposition.",
    ),
    "math": (
        "step-by-step",
        ["chain-of-thought"],
        "Mathematical tasks require explicit step-by-step decomposition to avoid arithmetic "
        "errors and ensure each step is verifiable.",
    ),
    "writing": (
        "CO-STAR",
        ["persona-assignment"],
        "Writing tasks benefit from the full CO-STAR framework (Context, Objective, Style, Tone, "
        "Audience, Response) combined with a clear persona.",
    ),
    "creative": (
        "persona-assignment",
        ["few-shot-scaffolding", "CO-STAR"],
        "Creative tasks benefit from a strong persona combined with structural guidance "
        "and examples to inspire while maintaining quality.",
    ),
    "extraction": (
        "structured-output",
        ["constraint-injection"],
        "Data extraction tasks need precise output format specifications and boundary constraints.",
    ),
    "classification": (
        "few-shot-scaffolding",
        ["structured-output"],
        "Classification tasks perform best with concrete examples showing expected categorization.",
    ),
    "formatting": (
        "structured-output",
        ["role-task-format"],
        "Formatting tasks need explicit output structure and a clear task definition.",
    ),
    "medical": (
        "RISEN",
        ["context-enrichment", "constraint-injection"],
        "Medical tasks require the RISEN framework for role clarity, careful context enrichment, "
        "and explicit safety constraints.",
    ),
    "legal": (
        "RISEN",
        ["context-enrichment", "constraint-injection"],
        "Legal tasks require precise role assignment, thorough context, and explicit constraints "
        "for jurisdictional and regulatory compliance.",
    ),
    "education": (
        "CO-STAR",
        ["step-by-step", "few-shot-scaffolding"],
        "Educational tasks benefit from clear audience-aware structuring (CO-STAR) combined with "
        "pedagogical step-by-step progression and examples.",
    ),
    "general": (
        "role-task-format",
        ["structured-output"],
        "General tasks benefit from clear role + task + format structure without imposing "
        "heavyweight framework scaffolding.",
    ),
    "other": (
        "role-task-format",
        [],
        "Default to lightweight role-task-format for unclassified tasks to avoid "
        "over-engineering with framework-heavy approaches.",
    ),
}


# All task_type values recognised by the heuristic map.
# If the analyzer ever returns a value outside this set, something new has
# been added — log a warning so developers can decide whether to add it.
KNOWN_TASK_TYPES: frozenset[str] = frozenset(TASK_FRAMEWORK_MAP.keys())

# All unique framework names referenced in TASK_FRAMEWORK_MAP (primary + secondary).
# Single source of truth — settings validation and frontend dropdowns derive from this.
KNOWN_FRAMEWORKS: frozenset[str] = frozenset(
    framework
    for primary, secondaries, _ in TASK_FRAMEWORK_MAP.values()
    for framework in [primary, *secondaries]
)


def heuristic_strategy_fallback(task_type: str) -> dict:
    """Return a heuristic strategy based on task type.

    Used as a fallback when the LLM strategy stage fails.
    Unknown task types fall back to 'general' with a warning so the
    gap is visible in logs rather than silently swallowed.
    """
    if task_type not in KNOWN_TASK_TYPES:
        logger.warning(
            "Unknown task_type %r from analyzer — using 'general' heuristic. "
            "Add %r to TASK_FRAMEWORK_MAP in strategy_selector.py if this is a valid category.",
            task_type,
            task_type,
        )
        task_type = "general"

    primary, secondary, rationale = TASK_FRAMEWORK_MAP.get(
        task_type,
        TASK_FRAMEWORK_MAP["general"],
    )

    return {
        "primary_framework": primary,
        "secondary_frameworks": secondary,
        "rationale": f"[Heuristic fallback] {rationale}",
        "approach_notes": f"Apply {primary} framework"
        + (f" with {', '.join(secondary)} as supporting frameworks." if secondary else "."),
    }
