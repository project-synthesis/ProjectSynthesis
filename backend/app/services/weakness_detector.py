"""Weakness + strength detection for heuristic prompt analysis.

Pure keyword pattern matching over the raw prompt and its normalized
variants.  Produces short human-readable strings that feed the optimizer
template's weakness/strength block.

Extracted from ``heuristic_analyzer.py`` (Phase 3F).  Public API is
preserved via re-exports in ``heuristic_analyzer.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import re

# Vague quantifier patterns — flagged as imprecise when ≥ 2 occur.
_VAGUE_PATTERNS = re.compile(
    r"\b(some|various|many|a few|several|certain|stuff|things|better|improve)\b",
    re.IGNORECASE,
)

# Triple-backtick fenced-code detection.
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")

# Constraint / requirement keywords — presence indicates output boundaries.
_CONSTRAINT_KEYWORDS: frozenset[str] = frozenset({
    "must", "should", "require", "constraint", "limit", "maximum",
    "minimum", "exactly", "no more than", "at least", "ensure",
})

# Success-criteria keywords — presence indicates measurable outcome.
_OUTCOME_KEYWORDS: frozenset[str] = frozenset({
    "return", "output", "produce", "result", "generate", "create",
    "should return", "expected", "format",
})

# Audience / persona keywords — presence indicates target reader defined.
_AUDIENCE_KEYWORDS: frozenset[str] = frozenset({
    "audience", "persona", "reader", "user", "customer", "developer",
    "beginner", "expert", "stakeholder", "team", "client",
})

# Technical context terms — used for coding-tier weakness + strength hints.
_CODING_TECH_TERMS: frozenset[str] = frozenset({
    "python", "javascript", "typescript", "rust", "go", "java",
    "react", "svelte", "fastapi", "django", "flask", "sql",
})

_STRENGTH_TECH_TERMS: frozenset[str] = frozenset({
    "python", "javascript", "typescript", "react", "svelte",
    "fastapi", "django", "sql", "docker", "kubernetes",
})


def has_code_blocks(raw_prompt: str) -> bool:
    """Return True when the prompt contains a triple-backtick fenced block."""
    return bool(_CODE_BLOCK_RE.search(raw_prompt))


def has_markdown_lists(raw_prompt: str) -> bool:
    """Return True when the prompt contains a dash/asterisk bullet list."""
    return bool(re.search(r"^\s*[-*]\s", raw_prompt, re.MULTILINE))


def has_any_keyword(text_lower: str, keywords: frozenset[str]) -> bool:
    """Substring membership check against a pre-built keyword set."""
    return any(kw in text_lower for kw in keywords)


def detect_weaknesses(
    raw_prompt: str,
    prompt_lower: str,
    words: list[str],
    task_type: str,
    *,
    has_constraints: bool,
    has_outcome: bool,
    has_audience: bool,
) -> list[str]:
    """Surface up to ~8 short weakness strings based on structural signals.

    Every check is defensive — never raise on missing fields; always fall
    through to an empty list if the prompt is too short to evaluate.
    """
    weaknesses: list[str] = []
    word_count = len(words)

    # Vague language
    vague_matches = _VAGUE_PATTERNS.findall(prompt_lower)
    if len(vague_matches) >= 2:
        weaknesses.append("vague language reduces precision")

    # Missing constraints
    if not has_constraints and word_count > 10:
        weaknesses.append("lacks constraints — no boundaries for the output")

    # Missing outcome
    if not has_outcome and word_count > 15:
        weaknesses.append("no measurable outcome defined")

    # Missing audience/persona (writing + creative only)
    if not has_audience and task_type in ("writing", "creative") and word_count > 10:
        weaknesses.append("target audience unclear")

    # Too short for complex task
    if task_type in ("coding", "data", "system") and word_count < 50:
        weaknesses.append("prompt underspecified for task complexity")

    # No examples
    has_examples = "example" in prompt_lower or "e.g." in prompt_lower or "```" in raw_prompt
    if not has_examples and word_count > 20:
        weaknesses.append("no examples to anchor expected output")

    # Broad scope
    if any(w in prompt_lower for w in ("everything", "all aspects", "every part")):
        weaknesses.append("scope too broad — consider narrowing focus")

    # Missing technical context for coding
    if task_type == "coding":
        if not any(t in prompt_lower for t in _CODING_TECH_TERMS):
            weaknesses.append("insufficient technical context — no language or framework specified")

    return weaknesses


def detect_strengths(
    raw_prompt: str,
    prompt_lower: str,
    words: list[str],
    *,
    has_code_blocks: bool,
    has_lists: bool,
    has_constraints: bool,
    has_outcome: bool,
) -> list[str]:
    """Surface short positive strings that inform the optimizer's template."""
    strengths: list[str] = []

    if has_code_blocks:
        strengths.append("includes concrete code examples")
    if has_lists:
        strengths.append("well-organized prompt structure")

    if has_constraints:
        strengths.append("clear constraints defined")

    tech_count = sum(1 for t in _STRENGTH_TECH_TERMS if t in prompt_lower)
    if tech_count >= 2:
        strengths.append("specific technical context provided")

    if has_outcome:
        strengths.append("measurable outcome specified")

    return strengths


__all__ = [
    "detect_strengths",
    "detect_weaknesses",
    "has_any_keyword",
    "has_code_blocks",
    "has_markdown_lists",
]
