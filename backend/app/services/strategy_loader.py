"""Strategy file discovery, loading, and frontmatter parsing.

Strategy files are Markdown in prompts/strategies/ with YAML frontmatter:

    ---
    tagline: reasoning
    description: Guide the AI through explicit reasoning steps.
    ---

    # Chain of Thought Strategy
    ...

The frontmatter is stripped before injection into optimizer/refiner templates.
The system is fully adaptive — adding/removing .md files is auto-detected.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Regex to extract YAML frontmatter between --- delimiters
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# Validation constraints
_MAX_TAGLINE_LENGTH = 30
_MAX_DESCRIPTION_LENGTH = 200
_MAX_FILE_SIZE = 50_000  # 50KB — strategy files should be concise


class StrategyFrontmatterError(ValueError):
    """Raised when strategy frontmatter is malformed or missing required fields."""


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from markdown content.

    Returns (metadata_dict, body_without_frontmatter).
    If no frontmatter, returns ({}, original_content).

    Handles edge cases:
    - Empty frontmatter block (--- / ---)
    - Values with colons (only splits on first colon)
    - Whitespace-only frontmatter
    - Multi-line values (not supported — logs warning)
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    raw_block = match.group(1).strip()
    if not raw_block:
        # Empty frontmatter block: ---\n---
        return {}, content[match.end():]

    meta: dict[str, str] = {}
    for line in raw_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            logger.warning(
                "Frontmatter line without colon separator, skipping: %r", line,
            )
            continue
        # Partition on first colon only — allows colons in values
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key:
            meta[key] = value

    body = content[match.end():]
    return meta, body


def validate_frontmatter(
    meta: dict[str, str],
    filename: str = "",
) -> list[str]:
    """Validate frontmatter fields. Returns list of warning messages (empty = valid).

    Checks:
    - tagline present and within length limit
    - description present and within length limit
    - no unknown keys (informational only)
    """
    warnings: list[str] = []
    prefix = f"Strategy '{filename}': " if filename else ""

    # Required fields
    if not meta.get("tagline"):
        warnings.append(f"{prefix}missing 'tagline' in frontmatter")
    elif len(meta["tagline"]) > _MAX_TAGLINE_LENGTH:
        warnings.append(
            f"{prefix}tagline too long ({len(meta['tagline'])} chars, "
            f"max {_MAX_TAGLINE_LENGTH})"
        )

    if not meta.get("description"):
        warnings.append(f"{prefix}missing 'description' in frontmatter")
    elif len(meta["description"]) > _MAX_DESCRIPTION_LENGTH:
        warnings.append(
            f"{prefix}description too long ({len(meta['description'])} chars, "
            f"max {_MAX_DESCRIPTION_LENGTH})"
        )

    # Unknown keys (informational — not blocking)
    known_keys = {"tagline", "description"}
    unknown = set(meta.keys()) - known_keys
    if unknown:
        warnings.append(
            f"{prefix}unknown frontmatter keys: {', '.join(sorted(unknown))}"
        )

    return warnings


class StrategyLoader:
    """Discovers and loads strategy files from the strategies directory.

    Fully adaptive: strategies are discovered from disk on each call.
    No hardcoded list — adding/removing .md files changes available strategies.
    """

    def __init__(self, strategies_dir: Path) -> None:
        self.strategies_dir = strategies_dir

    def list_strategies(self) -> list[str]:
        """Return sorted list of available strategy names (without .md extension)."""
        if not self.strategies_dir.exists():
            return []
        try:
            return sorted(p.stem for p in self.strategies_dir.glob("*.md"))
        except OSError as exc:
            logger.error("Failed to list strategies: %s", exc)
            return []

    def load(self, name: str) -> str:
        """Load a strategy file by name, stripping frontmatter.

        Returns the body content (without YAML frontmatter) for injection
        into optimizer/refiner templates. Returns a graceful fallback if
        the file is missing or unreadable.
        """
        path = self.strategies_dir / f"{name}.md"
        if not path.exists():
            available = self.list_strategies()
            if available:
                logger.warning(
                    "Strategy '%s' not found. Available: %s",
                    name, ", ".join(available),
                )
            else:
                logger.warning(
                    "Strategy '%s' not found and no strategy files exist. "
                    "The optimizer will proceed without strategy guidance.",
                    name,
                )
            return (
                "No specific strategy instructions available. "
                "Use your best judgment to optimize the prompt — "
                "focus on clarity, specificity, and structure."
            )

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.error("Failed to read strategy '%s': %s", name, exc)
            return (
                "Strategy file could not be read. "
                "Use your best judgment to optimize the prompt."
            )

        if len(content) > _MAX_FILE_SIZE:
            logger.warning(
                "Strategy '%s' is very large (%d bytes, max %d). "
                "Consider trimming for optimal LLM performance.",
                name, len(content), _MAX_FILE_SIZE,
            )

        _, body = _parse_frontmatter(content)
        body = body.strip()

        if not body:
            logger.warning(
                "Strategy '%s' has frontmatter but no body content.", name,
            )
            return (
                "Strategy file has no instructions. "
                "Use your best judgment to optimize the prompt."
            )

        logger.debug("Loaded strategy %s (%d chars)", name, len(body))
        return body

    def load_metadata(self, name: str) -> dict[str, Any]:
        """Load frontmatter metadata for a strategy.

        Returns dict with keys: name, tagline, description, warnings.
        Falls back to extracting description from first content line if
        no frontmatter. Logs validation warnings but never crashes.
        """
        path = self.strategies_dir / f"{name}.md"
        if not path.exists():
            return {
                "name": name,
                "tagline": "",
                "description": "",
                "warnings": [f"Strategy file '{name}.md' not found"],
            }

        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.error("Failed to read strategy '%s' metadata: %s", name, exc)
            return {
                "name": name,
                "tagline": "",
                "description": "",
                "warnings": [f"Could not read file: {exc}"],
            }

        meta, body = _parse_frontmatter(content)

        # Validate frontmatter
        fm_warnings = validate_frontmatter(meta, filename=name)
        for w in fm_warnings:
            logger.warning(w)

        # Extract values
        tagline = meta.get("tagline", "")
        description = meta.get("description", "")

        # Fallback: extract description from first non-heading, non-empty line
        if not description:
            for line in body.strip().splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    description = stripped
                    break

        return {
            "name": name,
            "tagline": tagline,
            "description": description,
            "warnings": fm_warnings,
        }

    def list_with_metadata(self) -> list[dict[str, Any]]:
        """Return all strategies with their frontmatter metadata."""
        return [self.load_metadata(name) for name in self.list_strategies()]

    def format_available(self) -> str:
        """Format available strategies as a bullet list for the analyzer prompt.

        Includes taglines when available for richer context.
        """
        results = []
        for meta in self.list_with_metadata():
            name = meta["name"]
            tagline = meta.get("tagline", "")
            if tagline:
                results.append(f"- {name} ({tagline})")
            else:
                results.append(f"- {name}")
        return "\n".join(results) if results else "No strategies available."

    def validate(self) -> None:
        """Validate all strategy files. Logs warnings for issues, never crashes."""
        strategies = self.list_strategies()
        if not strategies:
            logger.warning(
                "No strategy files found in %s. "
                "The pipeline will use generic optimization guidance.",
                self.strategies_dir,
            )
            return

        total_warnings = 0
        for name in strategies:
            meta = self.load_metadata(name)
            if meta.get("warnings"):
                total_warnings += len(meta["warnings"])

        if total_warnings:
            logger.warning(
                "Strategy validation: %d strategies, %d warning(s). "
                "Run GET /api/strategies to see details.",
                len(strategies), total_warnings,
            )
        else:
            logger.info(
                "Strategy validation passed: %d strategies, all with valid frontmatter",
                len(strategies),
            )
