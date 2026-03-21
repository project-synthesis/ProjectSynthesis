"""Pydantic response models for the unified cluster API."""

from datetime import datetime
from pydantic import BaseModel


class ClusterNode(BaseModel):
    """Single cluster in tree/list responses."""
    id: str
    parent_id: str | None = None
    label: str
    state: str
    domain: str
    task_type: str
    persistence: float | None = None
    coherence: float | None = None
    separation: float | None = None
    stability: float | None = None
    member_count: int = 0
    usage_count: int = 0
    avg_score: float | None = None
    color_hex: str | None = None
    umap_x: float | None = None
    umap_y: float | None = None
    umap_z: float | None = None
    preferred_strategy: str | None = None
    created_at: datetime | None = None


class ClusterTreeResponse(BaseModel):
    nodes: list[ClusterNode]


class MetaPatternItem(BaseModel):
    id: str
    pattern_text: str
    source_count: int


class LinkedOptimization(BaseModel):
    id: str
    trace_id: str
    raw_prompt: str
    intent_label: str | None = None
    overall_score: float | None = None
    strategy_used: str | None = None
    created_at: datetime | None = None


class ClusterDetail(BaseModel):
    """Full cluster detail for Inspector."""
    id: str
    parent_id: str | None = None
    label: str
    state: str
    domain: str
    task_type: str
    member_count: int
    usage_count: int
    avg_score: float | None = None
    coherence: float | None = None
    separation: float | None = None
    preferred_strategy: str | None = None
    promoted_at: datetime | None = None
    meta_patterns: list[MetaPatternItem]
    optimizations: list[LinkedOptimization]
    children: list[ClusterNode] | None = None
    breadcrumb: list[ClusterNode] | None = None


class ClusterStats(BaseModel):
    q_system: float | None = None
    q_coherence: float | None = None
    q_separation: float | None = None
    q_coverage: float | None = None
    q_dbcv: float | None = None
    total_clusters: int = 0
    nodes: dict | None = None
    last_warm_path: datetime | None = None
    last_cold_path: datetime | None = None
    warm_path_age: int = 0
    q_history: list[float] | None = None
    q_sparkline: list[float] | None = None


class ClusterMatchResponse(BaseModel):
    match: dict | None = None


class ReclusterResponse(BaseModel):
    status: str
    snapshot_id: str | None = None
    q_system: float | None = None
    nodes_created: int = 0
    nodes_updated: int = 0
    umap_fitted: bool = False


class ClusterUpdateRequest(BaseModel):
    intent_label: str | None = None
    domain: str | None = None
    state: str | None = None
