"""Pydantic models for the patterns graph API."""

from __future__ import annotations

from pydantic import BaseModel


class MetaPatternNode(BaseModel):
    """Project-scoped reusable technique (cluster-anchored)."""

    id: str
    pattern_text: str
    source_count: int
    cluster_id: str | None = None
    cluster_label: str | None = None
    domain: str | None = None


class GlobalPatternNode(BaseModel):
    """Cross-project durable pattern (ADR-005)."""

    id: str
    pattern_text: str
    source_cluster_ids: list[str]
    source_project_ids: list[str]
    cross_project_count: int
    avg_cluster_score: float | None = None
    state: str


class PatternGraphResponse(BaseModel):
    """Combined view for the pattern graph UI.

    Hybrid taxonomy: meta-patterns are scoped to a project when
    ``project_id`` is supplied; global patterns are always returned.
    """

    project_id: str | None = None
    meta_patterns: list[MetaPatternNode]
    global_patterns: list[GlobalPatternNode]
