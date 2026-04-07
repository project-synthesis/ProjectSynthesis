"""Shared text cleanup utilities for LLM output normalization.

Strips meta-commentary artifacts (preambles, code fences, headers) and
separates change rationale from optimized prompt content.  Used by the
sampling pipeline, MCP save_result, and REST passthrough save paths.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import re

__all__ = [
    "strip_meta_header",
    "split_prompt_and_changes",
    "sanitize_optimization_result",
    "title_case_label",
    "validate_intent_label",
    "is_low_quality_label",
    "extract_meaningful_words",
    "parse_domain",
    "LABEL_STOP_WORDS",
]

# Words that should stay uppercase (acronyms, initialisms)
_UPPERCASE_WORDS = frozenset({
    "api", "css", "html", "js", "ts", "sql", "ui", "ux", "cli", "sdk",
    "ssr", "ssg", "jwt", "oauth", "crud", "rest", "graphql", "db", "ai",
    "llm", "mcp", "http", "https", "json", "yaml", "xml", "csv", "pdf",
    "aws", "gcp", "ci", "cd", "devops", "gpu", "cpu", "ram", "ssd",
})


def title_case_label(text: str) -> str:
    """Title-case a short label, preserving known acronyms.

    Examples:
        >>> title_case_label("design auth API service")
        'Design Auth API Service'
        >>> title_case_label("refactor CSS architecture")
        'Refactor CSS Architecture'
    """
    words: list[str] = []
    for w in text.split():
        if w.lower() in _UPPERCASE_WORDS:
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    return " ".join(words)


# ---------------------------------------------------------------------------
# Intent label quality gate
# ---------------------------------------------------------------------------

# Canonical stop-word set for intent label operations — shared across
# heuristic_analyzer, pattern_injection, taxonomy quality, and this module.
# Import as: from app.utils.text_cleanup import LABEL_STOP_WORDS
LABEL_STOP_WORDS = frozenset({
    # Articles & prepositions
    "a", "an", "the", "for", "to", "of", "in", "on", "with", "and",
    "or", "that", "this", "from", "by", "about", "some",
    # Pronouns
    "my", "your", "our", "it", "its", "i", "me", "we", "us", "you",
    "he", "she", "they", "them",
    # Auxiliaries & modals
    "is", "are", "was", "were", "be", "been", "being",
    "please", "can", "could", "would", "help", "need", "want", "like",
    # Filler adverbs
    "just", "also", "very", "really", "so", "but", "if", "how",
    # Generic label tokens (unhelpful in labels/similarity)
    "task", "optimization", "general",
})

_CONVERSATIONAL_STARTS = (
    "i ", "please ", "can ", "could ", "would ", "help ",
    "hi ", "hello ", "hey ",
)


def validate_intent_label(label: str, raw_prompt: str | None = None) -> str:
    """Validate and potentially improve a generated intent label.

    Catches low-quality labels produced by heuristic fallbacks or weak
    LLM outputs and attempts to derive a better label from the raw prompt.

    Rejection criteria:
    - Exact match to "General"/"general"
    - Ends with " optimization" or " task" AND has <=3 words (too generic)
    - Starts with conversational filler ("I ", "Please ", "Can ", etc.)
    - Fewer than 2 words

    On rejection, extracts first 6 meaningful words from raw_prompt as
    fallback. Returns original label if no improvement possible.

    Pure function — no I/O, no async.
    """
    stripped = label.strip()
    if not stripped:
        stripped = "general"

    if not is_low_quality_label(stripped):
        return stripped

    # Attempt to derive a better label from raw_prompt
    if raw_prompt:
        fallback = _extract_label_from_prompt(raw_prompt)
        if fallback and not is_low_quality_label(fallback):
            return fallback

    # No improvement possible — return original (don't make things worse)
    return stripped


def is_low_quality_label(label: str) -> bool:
    """Check if a label matches any low-quality pattern.

    Public API — used by taxonomy engine Tier 2 label upgrade.
    """
    lower = label.lower().strip()

    # Exact "general"
    if lower == "general":
        return True

    words = lower.split()

    # Fewer than 2 words
    if len(words) < 2:
        return True

    # Generic tail patterns with low word count
    if len(words) <= 3 and (
        lower.endswith(" optimization") or lower.endswith(" task")
    ):
        return True

    # Conversational starts
    if any(lower.startswith(prefix) for prefix in _CONVERSATIONAL_STARTS):
        return True

    return False


def extract_meaningful_words(
    text: str,
    max_words: int = 6,
    scan_window: int = 30,
    *,
    exclude: frozenset[str] | None = None,
) -> str | None:
    """Extract up to ``max_words`` meaningful words from text, skipping stop words.

    Shared utility used by both ``validate_intent_label`` (fallback extraction)
    and ``HeuristicAnalyzer._extract_meaningful_words`` / ``_extract_noun_phrase``.

    Args:
        text: Raw text to extract from.
        max_words: Maximum meaningful words to collect.
        scan_window: How many words to scan (trades breadth vs speed).
        exclude: Additional words to exclude beyond ``LABEL_STOP_WORDS``.

    Returns:
        Space-joined meaningful words, or ``None`` if none found.
    """
    stop = LABEL_STOP_WORDS | exclude if exclude else LABEL_STOP_WORDS
    words = text.split()
    meaningful: list[str] = []
    for w in words[:scan_window]:
        cleaned = re.sub(r"[^a-zA-Z0-9]", "", w)
        if not cleaned or cleaned.lower() in stop:
            continue
        meaningful.append(cleaned)
        if len(meaningful) >= max_words:
            break

    return " ".join(meaningful) if meaningful else None


def _extract_label_from_prompt(raw_prompt: str) -> str | None:
    """Extract meaningful words from prompt and title-case them for labeling."""
    try:
        raw = extract_meaningful_words(raw_prompt)
        return title_case_label(raw) if raw else None
    except Exception:
        return None  # fail-safe for malformed input


def strip_meta_header(text: str) -> str:
    """Remove LLM-added preambles, meta-headers, and code fences from the prompt.

    LLMs in sampling/passthrough mode often:
    1. Add a preamble like "Here is the optimized prompt using..."
    2. Prepend a title like '# Optimized Prompt' before the actual content
    3. Wrap the entire prompt in a markdown code fence (```markdown ... ```)

    All are meta-commentary artifacts, not part of the prompt.
    """
    # 0. Strip preamble sentences like "Here is the optimized prompt..."
    text = re.sub(
        r"^(?:here\s+is|below\s+is)[^`\n]*(?:prompt|version)[^`\n]*:?\s*\n+",
        "", text, count=1, flags=re.IGNORECASE,
    )

    # 1. Strip markdown code fence wrapping the entire content.
    #    LLMs sometimes return: ```markdown\n<actual prompt>\n```
    stripped = text.strip()
    if re.match(r"^```(?:markdown|md)?\s*\n", stripped, re.IGNORECASE):
        # Remove opening fence
        stripped = re.sub(r"^```(?:markdown|md)?\s*\n", "", stripped, count=1, flags=re.IGNORECASE)
        # Remove closing fence (at end)
        stripped = re.sub(r"\n```\s*$", "", stripped)
        text = stripped

    # 2. Strip meta-header line
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        first = lines[0].strip().lower().rstrip(":").rstrip()
        meta_headers = [
            "# optimized prompt", "## optimized prompt", "### optimized prompt",
            "# optimized version", "## optimized version", "### optimized version",
            "# improved prompt", "## improved prompt", "### improved prompt",
            "# rewritten prompt", "## rewritten prompt", "### rewritten prompt",
            "# enhanced prompt", "## enhanced prompt", "### enhanced prompt",
        ]
        if any(first == h for h in meta_headers):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)

    # 3. Strip trailing closing fence + orphaned heading markers left by
    #    truncated LLM output (e.g., "```\n\n#" at the end).
    result = "\n".join(lines).rstrip()
    result = re.sub(r"\n```\s*$", "", result)           # trailing ```
    result = re.sub(r"\n#{1,3}\s*$", "", result.rstrip())  # trailing orphaned #/##/###
    return result


_DEFAULT_CHANGES = "Restructured with added specificity and constraints"

# Regex catches markdown headings at any level (#–####), bold markers,
# and plain label variants for changes sections.  Case-insensitive,
# multiline so ^ anchors to line starts.  Optional HR (---) prefix.
_CHANGES_RE = re.compile(
    r"^(?:---\s*\n)?"
    r"(?:"
    # Heading variants: # Changes, ## Changes Made, ### Summary of Changes, etc.
    # Word boundary (?:\s|$) prevents matching "Changelog", "Changed config", etc.
    r"#{1,4}\s+(?:Summary\s+of\s+)?(?:Changes?\s*(?:Made|Summary)?|What\s+Changed(?:\s+and\s+Why)?)(?:\s*$)"
    r"|"
    # Bold variants: **Changes**, **Changes Made**, etc.
    r"\*{2}(?:Summary\s+of\s+)?(?:Changes?\s*(?:Made|Summary)?|What\s+Changed(?:\s+and\s+Why)?)\*{2}"
    r"|"
    # Plain label: "Changes:" or "What changed:"
    r"(?:Changes|What\s+changed)\s*:"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

# Secondary metadata section the LLM may append after the prompt.
_APPLIED_PATTERNS_RE = re.compile(
    r"^(?:---\s*\n)?#{1,4}\s+Applied\s+Patterns",
    re.IGNORECASE | re.MULTILINE,
)


def split_prompt_and_changes(text: str) -> tuple[str, str]:
    """Split an LLM response into optimized prompt and changes summary.

    LLMs often merge their rationale (what changed and why) or
    ``## Applied Patterns`` notes into the optimized prompt text.
    This function detects section markers via regex and splits them
    out so ``changes_summary`` is separate from ``optimized_prompt``.

    Also strips meta-headers like '# Optimized Prompt'.

    Returns:
        (prompt_text, changes_summary) tuple.
    """
    changes_match = _CHANGES_RE.search(text)
    patterns_match = _APPLIED_PATTERNS_RE.search(text)

    # Determine the earliest metadata section — split there.
    split_pos: int | None = None
    changes_text = ""

    if changes_match and patterns_match:
        # Both present — split at whichever comes first
        if changes_match.start() <= patterns_match.start():
            split_pos = changes_match.start()
            changes_text = text[changes_match.end():].strip()
            # Trim Applied Patterns from the tail of changes_text
            ap_tail = _APPLIED_PATTERNS_RE.search(changes_text)
            if ap_tail:
                changes_text = changes_text[:ap_tail.start()].strip()
        else:
            split_pos = patterns_match.start()
            # Changes section is after Applied Patterns
            changes_text = text[changes_match.end():].strip()
    elif changes_match:
        split_pos = changes_match.start()
        changes_text = text[changes_match.end():].strip()
    elif patterns_match:
        split_pos = patterns_match.start()
        # No explicit changes section — just strip the Applied Patterns

    if split_pos is not None:
        prompt_part = text[:split_pos].rstrip()
        # Remove leading markdown decoration from changes
        changes_text = changes_text.lstrip("#").lstrip("*").strip()
        if changes_text:
            return strip_meta_header(prompt_part), changes_text[:500]
        if prompt_part.strip():
            return strip_meta_header(prompt_part), _DEFAULT_CHANGES

    return strip_meta_header(text), _DEFAULT_CHANGES


def sanitize_optimization_result(
    optimized_prompt: str,
    changes_summary: str,
) -> tuple[str, str]:
    """Post-process LLM output to separate leaked metadata sections.

    Even when the LLM returns structured JSON with separate fields, the
    ``optimized_prompt`` value may contain embedded ``## Changes`` or
    ``## Applied Patterns`` sections.  This function strips them and
    merges any extracted changes with the existing ``changes_summary``.

    Applied on ALL pipeline paths (internal, sampling, passthrough) as
    a defense-in-depth measure.

    Returns:
        (cleaned_prompt, changes_summary) tuple.
    """
    cleaned_prompt, extracted_changes = split_prompt_and_changes(optimized_prompt)

    # If we extracted real changes AND the existing summary is
    # empty or just the default placeholder, use the extracted text.
    if extracted_changes and extracted_changes != _DEFAULT_CHANGES:
        if not changes_summary or changes_summary == _DEFAULT_CHANGES:
            changes_summary = extracted_changes

    return cleaned_prompt, changes_summary


def parse_domain(raw: str | None) -> tuple[str, str | None]:
    """Parse a domain string into (primary, qualifier).

    Both primary and qualifier are **lowercased** to match domain node
    labels (which are always lowercase).

    Examples::

        parse_domain("backend")           → ("backend", None)
        parse_domain("Backend: Security") → ("backend", "security")
        parse_domain("REST API design")   → ("rest api design", None)
        parse_domain(None)                → ("general", None)

    Returns ``("general", None)`` for empty/None input.
    """
    if not raw or not raw.strip():
        return ("general", None)
    raw = raw.strip()
    if ":" in raw:
        primary, _, qualifier = raw.partition(":")
        return (primary.strip().lower(), qualifier.strip().lower() or None)
    return (raw.lower(), None)
