"""Zero-LLM suggestion generator for the passthrough tier.

Produces up to 3 deterministic, actionable suggestions from heuristic
analysis outputs — no LLM calls required.  Output format matches the
LLM-powered ``SuggestionsOutput`` schema exactly: each suggestion is a
dict with ``text`` and ``source`` keys.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["generate_heuristic_suggestions"]


# ---------------------------------------------------------------------------
# Frozen lookup tables — deterministic, no runtime mutation
# ---------------------------------------------------------------------------

# Score-driven: lowest dimension → actionable improvement.
# {dimension_name: suggestion_template}  (uses .format(score=...) at runtime)
_SCORE_SUGGESTIONS: MappingProxyType[str, str] = MappingProxyType({
    "clarity": (
        "Improve clarity (currently {score:.1f}/10) — simplify complex "
        "sentences and replace ambiguous references with concrete terms"
    ),
    "specificity": (
        "Improve specificity (currently {score:.1f}/10) — add concrete "
        "constraints, type annotations, or measurable acceptance criteria"
    ),
    "structure": (
        "Improve structure (currently {score:.1f}/10) — organize content "
        "with markdown headers, numbered steps, or XML section tags"
    ),
    "faithfulness": (
        "Improve faithfulness (currently {score:.1f}/10) — ensure the "
        "optimized prompt preserves all original requirements without "
        "adding unsolicited scope"
    ),
    "conciseness": (
        "Improve conciseness (currently {score:.1f}/10) — remove filler "
        "phrases, redundant qualifiers, and unnecessary preambles"
    ),
})

# Analysis-driven: weakness string → actionable fix.
# Ordered by priority (most impactful first).
_WEAKNESS_SUGGESTIONS: tuple[tuple[str, str], ...] = (
    (
        "vague language reduces precision",
        "Replace vague qualifiers (some, various, better) with concrete "
        "quantities, thresholds, or specific criteria",
    ),
    (
        "lacks constraints — no boundaries for the output",
        "Add explicit constraints: length limits, format requirements, "
        "or boundary conditions the output must satisfy",
    ),
    (
        "no measurable outcome defined",
        "Define the expected output format and success criteria so "
        "results can be objectively evaluated",
    ),
    (
        "target audience unclear",
        "Specify the target audience or persona to calibrate tone, "
        "depth, and assumed knowledge level",
    ),
    (
        "prompt underspecified for task complexity",
        "Expand the prompt with implementation details: edge cases, "
        "error handling, and specific technical requirements",
    ),
    (
        "no examples to anchor expected output",
        "Add 1-2 concrete input/output examples to demonstrate the "
        "expected behavior and format",
    ),
    (
        "scope too broad — consider narrowing focus",
        "Narrow the scope to a single well-defined objective rather "
        "than addressing multiple concerns at once",
    ),
    (
        "insufficient technical context — no language or framework specified",
        "Specify the programming language, framework, and version to "
        "enable targeted, implementation-ready output",
    ),
)

# Strategy-driven: strategy name → technique-specific recommendation.
_STRATEGY_SUGGESTIONS: MappingProxyType[str, str] = MappingProxyType({
    "auto": (
        "Consider specifying an explicit strategy (few-shot, "
        "chain-of-thought, or structured-output) to apply targeted "
        "optimization techniques"
    ),
    "chain-of-thought": (
        "Add explicit reasoning steps with 'First... Then... Finally...' "
        "sequential structure to guide the model through complex logic"
    ),
    "few-shot": (
        "Include 2-3 concrete input/output examples that demonstrate "
        "edge cases alongside the expected happy-path behavior"
    ),
    "meta-prompting": (
        "Add self-verification instructions ('Before responding, "
        "verify that...') and explicit negative constraints "
        "('Do NOT include...')"
    ),
    "role-playing": (
        "Define the expert persona with specific credentials and "
        "domain constraints to focus the model's knowledge and tone"
    ),
    "structured-output": (
        "Specify the exact output schema with field-level types, "
        "constraints, and a concrete example of the expected "
        "JSON/YAML structure"
    ),
})

_STRATEGY_FALLBACK = (
    "Review the optimization strategy and consider whether a more "
    "targeted approach (few-shot examples, explicit constraints, or "
    "step-by-step reasoning) would improve results"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_heuristic_suggestions(
    dimension_scores: dict[str, float],
    weaknesses: list[str],
    strategy_used: str,
) -> list[dict[str, str]]:
    """Generate up to 3 deterministic suggestions without any LLM.

    One suggestion per source type:
    - **score**: targets the lowest-scoring dimension with a specific action.
    - **analysis**: maps the highest-priority detected weakness to a fix.
    - **strategy**: recommends a technique based on the strategy name.

    Args:
        dimension_scores: 5-dimension score dict (clarity, specificity,
            structure, faithfulness, conciseness).  Each value in [1.0, 10.0].
        weaknesses: Weakness strings from ``HeuristicAnalysis``.
        strategy_used: Name of the optimization strategy applied.

    Returns:
        List of 1-3 suggestion dicts with ``text`` and ``source`` keys.
    """
    suggestions: list[dict[str, str]] = []

    # 1. Score-driven — always generated when scores are available
    if dimension_scores:
        min_dim = min(
            (d for d in dimension_scores if d in _SCORE_SUGGESTIONS),
            key=lambda d: dimension_scores[d],
            default=None,
        )
        if min_dim is not None:
            template = _SCORE_SUGGESTIONS[min_dim]
            suggestions.append({
                "text": template.format(score=dimension_scores[min_dim]),
                "source": "score",
            })

    # 2. Analysis-driven — first weakness in priority order
    if weaknesses:
        weakness_set = set(weaknesses)
        for weakness_key, suggestion_text in _WEAKNESS_SUGGESTIONS:
            if weakness_key in weakness_set:
                suggestions.append({
                    "text": suggestion_text,
                    "source": "analysis",
                })
                break

    # 3. Strategy-driven — always generated
    strategy_text = _STRATEGY_SUGGESTIONS.get(
        strategy_used, _STRATEGY_FALLBACK,
    )
    suggestions.append({
        "text": strategy_text,
        "source": "strategy",
    })

    return suggestions
