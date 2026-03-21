"""Pydantic response models for taxonomy router endpoints.

Mirrors the frontend TypeScript interfaces in ``frontend/src/lib/api/taxonomy.ts``
and the dict shapes returned by ``TaxonomyEngine.get_tree/get_node/get_stats``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaxonomyNodeResponse(BaseModel):
    """Single taxonomy node — used by both tree and node-detail endpoints."""

    id: str
    label: str | None = None
    parent_id: str | None = None
    state: str  # confirmed | candidate | retired
    member_count: int = 0
    coherence: float | None = None
    separation: float | None = None
    stability: float | None = None
    persistence: float | None = None
    color_hex: str | None = None
    umap_x: float | None = None
    umap_y: float | None = None
    umap_z: float | None = None
    usage_count: int = 0
    created_at: str | None = None

    # Only populated by get_node (node-detail endpoint)
    children: list[TaxonomyNodeResponse] | None = None
    breadcrumb: list[str] | None = None
    family_count: int | None = None


class TaxonomyTreeResponse(BaseModel):
    """Wrapper for the tree endpoint — flat list of nodes."""

    nodes: list[TaxonomyNodeResponse]


class TaxonomyNodeCounts(BaseModel):
    """Node state counts embedded in stats response."""

    confirmed: int = 0
    candidate: int = 0
    retired: int = 0
    max_depth: int = 0
    leaf_count: int = 0


class QHistoryEntry(BaseModel):
    """Single entry in the quality history sparkline."""

    timestamp: str | None = None
    q_system: float | None = None
    operations: int = 0


class TaxonomyStatsResponse(BaseModel):
    """System quality metrics and snapshot history."""

    q_system: float | None = None
    q_coherence: float | None = None
    q_separation: float | None = None
    q_coverage: float | None = None
    q_dbcv: float | None = None
    total_families: int = 0
    nodes: TaxonomyNodeCounts = Field(default_factory=TaxonomyNodeCounts)
    q_history: list[QHistoryEntry] = Field(default_factory=list)
    q_sparkline: list[float] = Field(default_factory=list)
    last_warm_path: str | None = None
    last_cold_path: str | None = None
    warm_path_age: float | None = None


class ReclusterResponse(BaseModel):
    """Response from the manual recluster endpoint."""

    status: str  # completed | skipped
    reason: str | None = None  # populated when status=skipped
    snapshot_id: str | None = None
    q_system: float | None = None
    nodes_created: int | None = None
    nodes_updated: int | None = None
    umap_fitted: bool | None = None
