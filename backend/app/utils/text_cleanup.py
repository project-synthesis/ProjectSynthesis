"""Shared text cleanup utilities for LLM output normalization.

Strips meta-commentary artifacts (preambles, code fences, headers) and
separates change rationale from optimized prompt content.  Used by the
sampling pipeline, MCP save_result, and REST passthrough save paths.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import re

__all__ = ["strip_meta_header", "split_prompt_and_changes"]


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


def split_prompt_and_changes(text: str) -> tuple[str, str]:
    """Split an LLM response into optimized prompt and changes summary.

    LLMs in sampling/passthrough mode often merge their rationale (what
    changed and why) into the optimized prompt text.  This function detects
    common section markers and splits them out so ``changes_summary`` is
    separate from ``optimized_prompt``.

    Also strips meta-headers like '# Optimized Prompt'.

    Returns:
        (prompt_text, changes_summary) tuple.
    """
    # Markers ordered from most specific to least — first match wins.
    # Case-insensitive search, split on the line containing the marker.
    change_markers = [
        "## Summary of Changes",
        "## What Changed and Why",
        "## What Changed",
        "## Change Summary",
        "## Changes Made",
        "## Changes",
        "**Summary of Changes**",
        "**What Changed and Why**",
        "**What Changed**",
        "**Changes Made**",
        "**Changes**",
        "**Change Summary**",
        "Changes:",
        "What changed:",
    ]

    for marker in change_markers:
        idx = text.lower().find(marker.lower())
        if idx != -1:
            prompt_part = text[:idx].rstrip()
            changes_part = text[idx + len(marker):].strip()
            # Remove leading markdown decoration from changes
            changes_part = changes_part.lstrip("#").lstrip("*").strip()
            if changes_part:
                return strip_meta_header(prompt_part), changes_part[:500]

    return strip_meta_header(text), "Restructured with added specificity and constraints"
