"""Pydantic schemas for Topic Probe Tier 1 (v0.5.0).

See docs/specs/topic-probe-2026-04-29.md §4.2 for full design.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProbeContext(BaseModel):
    """Phase 1 grounding output, fed into Phase 2 generator."""
    model_config = {"extra": "forbid"}

    topic: str
    scope: str = "**/*"
    intent_hint: Literal["audit", "refactor", "explore", "regression-test"] = "explore"
    repo_full_name: str
    project_id: str | None = None
    project_name: str | None = None
    dominant_stack: list[str] = Field(default_factory=list)
    relevant_files: list[str] = Field(
        default_factory=list,
        description="Top-K paths from RepoIndexQuery.query_curated_context",
    )
    explore_synthesis_excerpt: str | None = None  # cached synthesis, capped at PROBE_CODEBASE_MAX_CHARS
    known_domains: list[str] = Field(default_factory=list)
    existing_clusters_brief: list[dict[str, str]] = Field(default_factory=list)
