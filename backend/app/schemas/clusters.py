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
    breadcrumb: list[str] | None = None


class ClusterNodeCounts(BaseModel):
    """Node state counts embedded in stats response."""
    active: int = 0
    candidate: int = 0
    archived: int = 0
    mature: int = 0
    template: int = 0
    max_depth: int = 0
    leaf_count: int = 0


class QHistoryEntry(BaseModel):
    """Single entry in the quality history."""
    timestamp: str | None = None
    q_system: float | None = None
    operations: int = 0


class ClusterStats(BaseModel):
    q_system: float | None = None
    q_coherence: float | None = None
    q_separation: float | None = None
    q_coverage: float | None = None
    q_dbcv: float | None = None
    total_clusters: int = 0
    nodes: ClusterNodeCounts = ClusterNodeCounts()
    last_warm_path: str | None = None
    last_cold_path: str | None = None
    warm_path_age: float | None = None
    q_history: list[QHistoryEntry] = []
    q_sparkline: list[float] = []
    q_trend: float = 0.0
    q_current: float | None = None
    q_min: float | None = None
    q_max: float | None = None
    q_point_count: int = 0


class ClusterMatchResponse(BaseModel):
    match: dict | None = None


class ReclusterResponse(BaseModel):
    status: str
    reason: str | None = None
    snapshot_id: str | None = None
    q_system: float | None = None
    q_before: float | None = None
    q_after: float | None = None
    accepted: bool = True
    nodes_created: int = 0
    nodes_updated: int = 0
    umap_fitted: bool = False


class ClusterUpdateRequest(BaseModel):
    intent_label: str | None = None
    domain: str | None = None
    state: str | None = None


class SimilarityEdge(BaseModel):
    from_id: str
    to_id: str
    similarity: float


class SimilarityEdgesResponse(BaseModel):
    edges: list[SimilarityEdge]


class InjectionEdge(BaseModel):
    source_id: str  # cluster that provided patterns
    target_id: str  # cluster the optimization was assigned to
    weight: int     # number of injection events


class InjectionEdgesResponse(BaseModel):
    edges: list[InjectionEdge]
