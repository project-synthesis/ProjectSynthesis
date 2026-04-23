"""Weakness + strength detection for heuristic prompt analysis.

Pure keyword pattern matching over the raw prompt and its normalized
variants.  Produces short human-readable strings that feed the optimizer
template's weakness/strength block.

Negation awareness (Phase 4 hardening): keyword matches preceded within
a 3-word window by negators ("not", "no", "without", "skip", "don't",
"doesn't") are excluded from positive flags.  This prevents prompts like
"there are no constraints" from falsely reporting ``has_constraints=True``.

Context-aware density (Phase 4 hardening): the "underspecified for task
complexity" warning cross-references structural signals from
``HeuristicScorer._count_structural_signals`` so short but densely
structured prompts (YAML schemas, XML-heavy specs) are not penalised.

Extracted from ``heuristic_analyzer.py`` (Phase 3F).  Public API is
preserved via re-exports in ``heuristic_analyzer.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Negation awareness (Phase 4 hardening)
# ---------------------------------------------------------------------------

# Negation tokens that, when found within a 3-word window before a keyword
# match, disqualify that match as a positive signal.
_NEGATION_TOKENS: frozenset[str] = frozenset({
    "not", "no", "without", "skip", "don't", "doesn't",
    "never", "nor", "lack", "lacking", "absent",
})


def _is_negated(text_lower: str, keyword: str) -> bool:
    """Return True if *every* occurrence of ``keyword`` in ``text_lower``
    is preceded within a 3-word window by a negation token.

    Multi-word keywords (e.g. "no more than") are matched via substring.
    When a keyword appears multiple times, *all* occurrences must be
    negated for this to return True — a single un-negated mention means
    the keyword genuinely applies.
    """
    start = 0
    found_any = False
    all_negated = True
    while True:
        idx = text_lower.find(keyword, start)
        if idx < 0:
            break
        found_any = True
        # Grab up to 40 chars before the match (covers ~3-4 words comfortably)
        window = text_lower[max(0, idx - 40):idx]
        window_words = window.split()
        # Check the last 3 words in that window for negation tokens
        tail = window_words[-3:] if len(window_words) >= 3 else window_words
        negator = next(
            (w.strip(".,;:!?\"'()") for w in tail
             if w.strip(".,;:!?\"'()") in _NEGATION_TOKENS),
            None,
        )
        if not negator:
            all_negated = False
            break
        logger.debug(
            "negation_detected: keyword=%r negator=%r pos=%d",
            keyword, negator, idx,
        )
        start = idx + len(keyword)
    if not found_any:
        return False  # keyword not present at all — not negated
    return all_negated


def has_keyword_unnegated(
    text_lower: str, keywords: frozenset[str],
) -> bool:
    """Return True when at least one keyword appears WITHOUT preceding negation.

    Drop-in replacement for the old ``has_any_keyword`` with negation
    awareness.  Multi-word keywords use substring search; single-word
    keywords do the same for consistency with the legacy behaviour.
    """
    for kw in keywords:
        if kw in text_lower and not _is_negated(text_lower, kw):
            return True
    return False


def has_code_blocks(raw_prompt: str) -> bool:
    """Return True when the prompt contains a triple-backtick fenced block."""
    return bool(_CODE_BLOCK_RE.search(raw_prompt))


def has_markdown_lists(raw_prompt: str) -> bool:
    """Return True when the prompt contains a dash/asterisk bullet list."""
    return bool(re.search(r"^\s*[-*]\s", raw_prompt, re.MULTILINE))


def has_any_keyword(text_lower: str, keywords: frozenset[str]) -> bool:
    """Substring membership check against a pre-built keyword set.

    .. deprecated:: Phase 4
       Use :func:`has_keyword_unnegated` instead for negation-aware
       matching.  Retained for backward compatibility in callers that
       intentionally want raw presence checking.
    """
    return any(kw in text_lower for kw in keywords)


def _compute_structural_density(raw_prompt: str) -> int:
    """Return a structural density score for context-aware underspec gating.

    Uses ``HeuristicScorer._count_structural_signals`` to measure how much
    organisational structure the prompt contains.  A prompt with multiple
    headers, XML sections, or dense list items is *informatively* rich
    even if it has fewer than 50 words.

    Returns:
        An integer density score (0 = unstructured, 4+ = highly structured).
    """
    try:
        from app.services.heuristic_scorer import HeuristicScorer
        sig = HeuristicScorer._count_structural_signals(raw_prompt)
    except Exception:
        logger.debug("structural_density: scorer unavailable, returning 0")
        return 0

    density = 0
    density += min(sig.get("n_headers", 0), 3)           # up to 3 pts
    density += min(sig.get("n_xml_sections", 0), 3)      # up to 3 pts
    density += min(sig.get("n_list_items", 0) // 2, 2)   # up to 2 pts
    if sig.get("has_format_mention"):
        density += 1
    # Code blocks inside the prompt are high-density artefacts
    if has_code_blocks(raw_prompt):
        density += 2
    logger.debug(
        "structural_density: score=%d headers=%d xml=%d lists=%d code=%s",
        density, sig.get("n_headers", 0), sig.get("n_xml_sections", 0),
        sig.get("n_list_items", 0), has_code_blocks(raw_prompt),
    )
    return density


#: Structural density threshold above which the "underspecified" warning
#: is suppressed even for short prompts.  Value of 3 means ≥ 2 headers +
#: a format mention, or ≥ 1 XML section pair + a code block, etc.
_STRUCTURAL_DENSITY_THRESHOLD: int = 3


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

    # Too short for complex task — suppressed when structural density is high
    # (Phase 4 hardening: context-aware density scoring)
    if task_type in ("coding", "data", "system") and word_count < 50:
        density = _compute_structural_density(raw_prompt)
        if density < _STRUCTURAL_DENSITY_THRESHOLD:
            weaknesses.append("prompt underspecified for task complexity")
        else:
            logger.debug(
                "density_gate_suppressed: underspec warning skipped, "
                "density=%d >= threshold=%d words=%d task_type=%s",
                density, _STRUCTURAL_DENSITY_THRESHOLD, word_count, task_type,
            )

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

    logger.debug(
        "detect_weaknesses: task_type=%s word_count=%d "
        "has_constraints=%s has_outcome=%s count=%d weaknesses=%s",
        task_type, word_count, has_constraints, has_outcome,
        len(weaknesses), weaknesses,
    )
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

    logger.debug(
        "detect_strengths: code=%s lists=%s constraints=%s "
        "outcome=%s tech=%d count=%d",
        has_code_blocks, has_lists, has_constraints,
        has_outcome, tech_count, len(strengths),
    )
    return strengths


__all__ = [
    "detect_strengths",
    "detect_weaknesses",
    "has_any_keyword",
    "has_code_blocks",
    "has_keyword_unnegated",
    "has_markdown_lists",
]
