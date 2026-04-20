"""Prompt-context divergence detection (B1/B2) for context enrichment.

Compares tech mentions in the user's prompt against the linked codebase's
tech stack to surface conflicts (e.g. prompt says "MongoDB" but codebase
uses Postgres).  Classifies each conflict as ``migration`` or ``conflict``
based on migration keywords in the prompt — the ``EnrichedContext.divergence_alerts``
renderer turns these into a 4-category intent-classification instruction
block for the optimizer LLM.

Extracted from ``context_enrichment.py`` (Phase 3A).  Public API is preserved
via re-exports in ``context_enrichment.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_TECH_VOCABULARY: dict[str, dict[str, set[str]]] = {
    "database": {
        "postgresql": {"postgresql", "postgres", "psycopg", "asyncpg", "pg_"},
        "mysql": {"mysql", "mariadb", "pymysql", "mysqlclient"},
        "sqlite": {"sqlite", "aiosqlite", "sqlite3"},
        "mongodb": {"mongodb", "pymongo", "motor", "mongosh"},
        "redis": {"redis"},
    },
    "framework": {
        "fastapi": {"fastapi"},
        "django": {"django"},
        "flask": {"flask"},
        "express": {"express", "expressjs"},
        "nextjs": {"nextjs", "next.js"},
        "rails": {"rails", "ruby on rails"},
        "spring": {"spring", "springframework", "spring boot"},
    },
    "language": {
        "python": {"python", "pyproject", "setuptools", ".py"},
        "javascript": {"javascript", "node_modules"},
        "typescript": {"typescript", "tsconfig"},
        "java": {"java", "maven", "gradle"},
        "go": {"golang", "go.mod", "go.sum"},
        "rust": {"rust", "cargo.toml", "rustc"},
        "ruby": {"ruby", "gemfile", "bundler"},
    },
}

# Pairs within the same category that are NOT conflicts
_COMPAT_PAIRS = frozenset({
    ("typescript", "javascript"),  # TS is a superset of JS
    ("javascript", "typescript"),
})

# Technologies that are always additive (no conflict even if different category tech exists)
_ADDITIVE_TECHS = frozenset({
    "redis", "celery", "rabbitmq", "docker", "kubernetes", "nginx",
    "terraform", "prometheus", "grafana", "elasticsearch",
})

_MIGRATION_KEYWORDS = frozenset({
    "migrate", "migration", "upgrade", "switch to", "replace with",
    "move to", "transition to", "port to", "convert to",
})


@dataclass(frozen=True)
class Divergence:
    """A detected tech stack conflict between prompt and codebase context."""

    prompt_tech: str
    codebase_tech: str
    category: str
    severity: str  # "conflict" | "migration"


def _extract_techs(text: str) -> dict[str, set[str]]:
    """Extract technology mentions from text, grouped by category.

    Uses word-boundary-aware matching to avoid false positives
    (e.g., "flask" in "flasks", "go" in "going").
    Multi-word aliases and aliases containing dots/punctuation use
    substring matching (same pattern as _TASK_TYPE_SIGNALS).

    Returns {category: {tech_name, ...}} for each tech found.
    """
    if not text:
        return {}
    text_lower = text.lower()
    found: dict[str, set[str]] = {}
    for category, techs in _TECH_VOCABULARY.items():
        for tech_name, aliases in techs.items():
            for alias in aliases:
                # Multi-word or dotted aliases: substring match
                if " " in alias or "." in alias:
                    matched = alias in text_lower
                else:
                    # Single-word aliases: word boundary match
                    matched = bool(re.search(r"\b" + re.escape(alias) + r"\b", text_lower))
                if matched:
                    found.setdefault(category, set()).add(tech_name)
                    break  # one alias match is enough per tech
    return found


def detect_divergences(
    raw_prompt: str,
    codebase_context: str | None,
) -> list[Divergence]:
    """Compare tech mentions in prompt vs codebase context.

    Returns a list of Divergence objects for any conflicts detected.
    Only runs when codebase_context is available (repo linked + synthesis/curated).
    """
    if not codebase_context:
        return []

    prompt_techs = _extract_techs(raw_prompt)
    codebase_techs = _extract_techs(codebase_context)

    if not prompt_techs or not codebase_techs:
        return []

    # Check for migration keywords in the prompt (NOT codebase — Alembic migrations are noise).
    # Multi-word patterns like "replace...with" are checked with a word-window scan
    # since the user may write "Replace our X layer with Y" (words between "replace" and "with").
    prompt_lower = raw_prompt.lower()
    prompt_words = prompt_lower.split()
    has_migration = any(kw in prompt_lower for kw in _MIGRATION_KEYWORDS)
    _migration_match: str | None = None
    if has_migration:
        _migration_match = next((kw for kw in _MIGRATION_KEYWORDS if kw in prompt_lower), None)
    else:
        # Window-based check for "replace...with" and "rewrite...in" patterns
        for i, w in enumerate(prompt_words):
            if w == "replace":
                if "with" in prompt_words[i + 1 : i + 8]:
                    has_migration = True
                    _migration_match = "replace...with (window)"
                    break
            elif w == "rewrite":
                if "in" in prompt_words[i + 1 : i + 6]:
                    has_migration = True
                    _migration_match = "rewrite...in (window)"
                    break

    logger.debug(
        "divergence_scan: prompt_techs=%s codebase_techs=%s migration=%s match=%s",
        prompt_techs, codebase_techs, has_migration, _migration_match,
    )

    divergences: list[Divergence] = []
    for category, prompt_set in prompt_techs.items():
        codebase_set = codebase_techs.get(category, set())
        if not codebase_set:
            continue  # no codebase tech in this category — can't conflict

        for p_tech in prompt_set:
            # Skip additive technologies
            if p_tech in _ADDITIVE_TECHS:
                continue
            # Skip if the tech IS in the codebase (no conflict)
            if p_tech in codebase_set:
                continue
            # Skip compatible pairs (TS/JS)
            compatible = any(
                (p_tech, c_tech) in _COMPAT_PAIRS for c_tech in codebase_set
            )
            if not compatible:
                # Genuine divergence — determine severity
                severity = "migration" if has_migration else "conflict"
                c_tech = next(iter(codebase_set))
                divergences.append(Divergence(
                    prompt_tech=p_tech,
                    codebase_tech=c_tech,
                    category=category,
                    severity=severity,
                ))

    return divergences


__all__ = ["Divergence", "detect_divergences"]
