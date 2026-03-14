"""Static framework validation profiles, correctable issues, and trade-off patterns.

This module defines the domain knowledge that connects frameworks to quality
dimensions, issues to dimension weights, and frameworks to typical trade-off
patterns. All values are tunable constants — no runtime computation.
"""
from __future__ import annotations

DEFAULT_FRAMEWORK_PROFILE: dict = {
    "emphasis": {},
    "de_emphasis": {},
    "entropy_tolerance": 1.0,
}

FRAMEWORK_PROFILES: dict[str, dict] = {
    "chain-of-thought": {
        "emphasis": {"structure_score": 1.3, "clarity_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 0.7,
    },
    "step-by-step": {
        "emphasis": {"structure_score": 1.3, "clarity_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 0.7,
    },
    "persona-assignment": {
        "emphasis": {"faithfulness_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {"structure_score": 0.9},
        "entropy_tolerance": 1.2,
    },
    "CO-STAR": {
        "emphasis": {"clarity_score": 1.2, "faithfulness_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.85},
        "entropy_tolerance": 1.0,
    },
    "RISEN": {
        "emphasis": {"faithfulness_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {},
        "entropy_tolerance": 0.9,
    },
    "structured-output": {
        "emphasis": {"structure_score": 1.3, "specificity_score": 1.2},
        "de_emphasis": {"clarity_score": 0.9},
        "entropy_tolerance": 0.8,
    },
    "constraint-injection": {
        "emphasis": {"specificity_score": 1.3, "faithfulness_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.85},
        "entropy_tolerance": 0.9,
    },
    "few-shot-scaffolding": {
        "emphasis": {"specificity_score": 1.3, "clarity_score": 1.1},
        "de_emphasis": {"conciseness_score": 0.75},
        "entropy_tolerance": 1.1,
    },
    "context-enrichment": {
        "emphasis": {"faithfulness_score": 1.2, "specificity_score": 1.2},
        "de_emphasis": {"conciseness_score": 0.8},
        "entropy_tolerance": 1.0,
    },
    "role-task-format": {
        "emphasis": {"structure_score": 1.2, "clarity_score": 1.1},
        "de_emphasis": {},
        "entropy_tolerance": 1.0,
    },
}


def get_profile(framework: str) -> dict:
    """Return the validation profile for a framework, with fallback to default."""
    return FRAMEWORK_PROFILES.get(framework, DEFAULT_FRAMEWORK_PROFILE)


CORRECTABLE_ISSUES: dict[str, str] = {
    "lost_key_terms": "Lost important terminology or domain language",
    "changed_meaning": "Changed the original intent or meaning",
    "hallucinated_content": "Added claims or details not in the original",
    "lost_examples": "Removed or weakened important examples",
    "too_verbose": "Unnecessarily long or repetitive",
    "too_vague": "Lost specificity or important details",
    "wrong_tone": "Tone doesn't match intended audience",
    "broken_structure": "Formatting, flow, or organization degraded",
}

ISSUE_DIMENSION_MAP: dict[str, dict[str, float]] = {
    "lost_key_terms": {"faithfulness_score": 1.0, "specificity_score": 0.5},
    "changed_meaning": {"faithfulness_score": 1.0},
    "hallucinated_content": {"faithfulness_score": 0.8, "specificity_score": 0.3},
    "lost_examples": {"specificity_score": 1.0, "faithfulness_score": 0.3},
    "too_verbose": {"conciseness_score": 1.0},
    "too_vague": {"specificity_score": 1.0, "clarity_score": 0.3},
    "wrong_tone": {"clarity_score": 1.0},
    "broken_structure": {"structure_score": 1.0},
}

ISSUE_EFFECT_LABELS: dict[str, str] = {
    "lost_key_terms": "term preservation guardrail activated",
    "changed_meaning": "meaning fidelity check activated",
    "hallucinated_content": "addition prevention guardrail activated",
    "lost_examples": "example preservation prioritized",
    "too_verbose": "conciseness priority increased",
    "too_vague": "specificity priority increased",
    "wrong_tone": "tone matching prioritized",
    "broken_structure": "structure preservation prioritized",
}

ISSUE_GUARDRAILS: dict[str, str] = {
    "lost_key_terms": (
        "PRESERVE all domain-specific terminology, acronyms, and technical phrases"
        " from the original. Do not paraphrase specialized language."
    ),
    "changed_meaning": (
        "The optimized prompt must produce the SAME behavioral outcome as the original."
        " Verify intent preservation before restructuring."
    ),
    "hallucinated_content": (
        "Do NOT add requirements, constraints, examples, or claims that are not present"
        " in the original prompt."
    ),
    "lost_examples": (
        "Preserve all examples from the original prompt. If restructuring, ensure"
        " examples remain functionally equivalent."
    ),
    "too_verbose": (
        "Prefer concise formulations. Remove redundancy. Every sentence must add"
        " information not conveyed elsewhere in the prompt."
    ),
    "too_vague": (
        "Maintain or increase specificity. Do not replace concrete details with"
        " abstract generalizations."
    ),
    "wrong_tone": (
        "Match the tone and register of the original prompt. Preserve the relationship"
        " with the intended audience."
    ),
    "broken_structure": (
        "Preserve the organizational structure of the original. If restructuring,"
        " ensure logical flow is maintained or improved."
    ),
}

SCORE_ISSUE_MAP: dict[str, list[str]] = {
    "faithfulness_score": ["changed_meaning", "hallucinated_content"],
    "specificity_score": ["too_vague", "lost_examples"],
    "conciseness_score": ["too_verbose"],
    "clarity_score": ["wrong_tone"],
    "structure_score": ["broken_structure"],
}

FRAMEWORK_TRADE_OFF_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "chain-of-thought": [
        ("structure_score", "conciseness_score"),
        ("clarity_score", "conciseness_score"),
    ],
    "step-by-step": [("structure_score", "conciseness_score")],
    "persona-assignment": [
        ("faithfulness_score", "conciseness_score"),
        ("specificity_score", "structure_score"),
    ],
    "CO-STAR": [
        ("clarity_score", "conciseness_score"),
        ("faithfulness_score", "conciseness_score"),
    ],
    "few-shot-scaffolding": [("specificity_score", "conciseness_score")],
    "structured-output": [("structure_score", "clarity_score")],
    "constraint-injection": [("specificity_score", "conciseness_score")],
}


def is_typical_trade_off(framework: str, gained_dim: str, lost_dim: str) -> bool:
    """Check if a gain/loss pair is a typical trade-off for this framework."""
    patterns = FRAMEWORK_TRADE_OFF_PATTERNS.get(framework, [])
    return (gained_dim, lost_dim) in patterns
