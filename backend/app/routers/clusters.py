"""Unified cluster API — tree, detail, stats, match, templates, recluster.

Replaces the separate /api/taxonomy/ and /api/patterns/ routers with a single
/api/clusters/ namespace.  Legacy paths receive 301 redirects.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import MetaPattern, Optimization, OptimizationPattern, PromptCluster
from app.schemas.clusters import (
    ClusterDetail,
    ClusterMatchResponse,
    ClusterNode,
    ClusterStats,
    ClusterTreeResponse,
    ClusterUpdateRequest,
    LinkedOptimization,
    MetaPatternItem,
    ReclusterResponse,
)
from app.services.taxonomy import TaxonomyEngine
from app.services.taxonomy import get_engine as get_taxonomy_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clusters"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_engine(request: Request) -> TaxonomyEngine:
    """Retrieve the singleton TaxonomyEngine via the process-wide factory."""
    return get_taxonomy_engine(app=request.app)


class MatchRequest(BaseModel):
    prompt_text: str = Field(
        ..., min_length=10,
        description="Prompt text to match against existing clusters.",
    )


class ClusterListResponse(BaseModel):
    total: int
    count: int
    offset: int
    has_more: bool
    next_offset: int | None = None
    items: list[ClusterNode]


class UpdateClusterResponse(BaseModel):
    id: str
    intent_label: str
    domain: str
    state: str


# ---------------------------------------------------------------------------
# Primary endpoints — /api/clusters/*
# ---------------------------------------------------------------------------

@router.get("/api/clusters/tree")
async def get_cluster_tree(
    request: Request,
    min_persistence: float = 0.0,
    db: AsyncSession = Depends(get_db),
) -> ClusterTreeResponse:
    """Flat node list for 3D topology visualization."""
    try:
        engine = _get_engine(request)
        nodes = await engine.get_tree(db, min_persistence=min_persistence)
        return ClusterTreeResponse(nodes=[ClusterNode(**n) for n in nodes])
    except Exception as exc:
        logger.error("GET /api/clusters/tree failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load cluster tree") from exc


@router.get("/api/clusters/stats")
async def get_cluster_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ClusterStats:
    """System quality metrics and snapshot history."""
    try:
        engine = _get_engine(request)
        data = await engine.get_stats(db)

        # Map total_families -> total_clusters for the unified schema
        total_clusters = data.pop("total_families", 0)
        return ClusterStats(total_clusters=total_clusters, **data)
    except Exception as exc:
        logger.error("GET /api/clusters/stats failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load cluster stats") from exc


@router.get("/api/clusters/templates")
async def get_cluster_templates(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> ClusterListResponse:
    """List clusters with state=template, sorted by avg_score descending."""
    query = (
        select(PromptCluster)
        .where(PromptCluster.state == "template")
        .order_by(PromptCluster.avg_score.desc())
    )
    count_query = select(func.count(PromptCluster.id)).where(
        PromptCluster.state == "template"
    )
    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(query.offset(offset).limit(limit))
    clusters = result.scalars().all()

    items = [
        ClusterNode(
            id=c.id,
            parent_id=c.parent_id,
            label=c.label,
            state=c.state,
            domain=c.domain,
            task_type=c.task_type,
            persistence=c.persistence,
            coherence=c.coherence,
            separation=c.separation,
            stability=c.stability,
            member_count=c.member_count or 0,
            usage_count=c.usage_count or 0,
            avg_score=c.avg_score,
            color_hex=c.color_hex,
            umap_x=c.umap_x,
            umap_y=c.umap_y,
            umap_z=c.umap_z,
            preferred_strategy=c.preferred_strategy,
            created_at=c.created_at,
        )
        for c in clusters
    ]

    return ClusterListResponse(
        total=total,
        count=len(items),
        offset=offset,
        has_more=offset + len(items) < total,
        next_offset=offset + len(items) if offset + len(items) < total else None,
        items=items,
    )


@router.get("/api/clusters/{cluster_id}")
async def get_cluster_detail(
    request: Request,
    cluster_id: str,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> ClusterDetail:
    """Single cluster with children, breadcrumb, meta-patterns, and linked optimizations."""
    try:
        engine = _get_engine(request)
        node = await engine.get_node(cluster_id, db)
        if node is None:
            raise HTTPException(status_code=404, detail="Cluster not found")

        children_raw = node.get("children", [])
        breadcrumb_raw = node.get("breadcrumb", [])

        # Meta-patterns from DB
        meta_result = await db.execute(
            select(MetaPattern)
            .where(MetaPattern.cluster_id == cluster_id)
            .order_by(MetaPattern.source_count.desc())
        )
        meta_patterns = meta_result.scalars().all()

        # Linked optimizations (most recent 20)
        opt_result = await db.execute(
            select(Optimization)
            .join(OptimizationPattern, OptimizationPattern.optimization_id == Optimization.id)
            .where(OptimizationPattern.cluster_id == cluster_id)
            .order_by(Optimization.created_at.desc())
            .limit(20)
        )
        optimizations = opt_result.scalars().all()

        node_data = {k: v for k, v in node.items() if k not in ("children", "breadcrumb")}

        return ClusterDetail(
            **node_data,
            meta_patterns=[
                MetaPatternItem(id=mp.id, pattern_text=mp.pattern_text, source_count=mp.source_count)
                for mp in meta_patterns
            ],
            optimizations=[
                LinkedOptimization(
                    id=o.id,
                    trace_id=o.trace_id,
                    raw_prompt=(o.raw_prompt or "")[:100],
                    intent_label=o.intent_label,
                    overall_score=o.overall_score,
                    strategy_used=o.strategy_used,
                    created_at=o.created_at,
                )
                for o in optimizations
            ],
            children=[ClusterNode(**c) for c in children_raw] if children_raw else None,
            breadcrumb=breadcrumb_raw if isinstance(breadcrumb_raw, list) else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("GET /api/clusters/%s failed: %s", cluster_id, exc, exc_info=True)
        raise HTTPException(500, "Failed to load cluster detail") from exc


@router.patch("/api/clusters/{cluster_id}")
async def update_cluster(
    cluster_id: str,
    body: ClusterUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> UpdateClusterResponse:
    """Update a cluster's label, domain, and/or state."""
    if body.intent_label is None and body.domain is None and body.state is None:
        raise HTTPException(422, "At least one of 'intent_label', 'domain', or 'state' must be provided")

    result = await db.execute(
        select(PromptCluster).where(PromptCluster.id == cluster_id)
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    if body.intent_label is not None:
        old_label = cluster.label
        cluster.label = body.intent_label
        logger.info("Cluster renamed: id=%s '%s' -> '%s'", cluster_id, old_label, body.intent_label)

    if body.domain is not None:
        old_domain = cluster.domain
        cluster.domain = body.domain
        logger.info("Cluster domain changed: id=%s '%s' -> '%s'", cluster_id, old_domain, body.domain)

    if body.state is not None:
        old_state = cluster.state
        cluster.state = body.state
        logger.info("Cluster state changed: id=%s '%s' -> '%s'", cluster_id, old_state, body.state)

    await db.commit()

    return UpdateClusterResponse(
        id=cluster.id,
        intent_label=cluster.label,
        domain=cluster.domain,
        state=cluster.state,
    )


@router.post("/api/clusters/match")
async def match_cluster(
    request: Request,
    body: MatchRequest,
    db: AsyncSession = Depends(get_db),
) -> ClusterMatchResponse:
    """Hierarchical similarity check for auto-suggestion on paste."""
    try:
        engine = _get_engine(request)
        result = await engine.match_prompt(body.prompt_text, db=db)

        if result is None or result.match_level == "none":
            return ClusterMatchResponse()

        # Build match dict for backward compatibility
        match_dict: dict = {}
        cluster = result.cluster
        if cluster:
            match_dict["cluster"] = {
                "id": cluster.id,
                "label": cluster.label,
                "domain": cluster.domain,
                "task_type": cluster.task_type,
                "usage_count": cluster.usage_count,
                "member_count": cluster.member_count,
                "avg_score": cluster.avg_score,
                "created_at": cluster.created_at.isoformat() if cluster.created_at else None,
            }
        match_dict["meta_patterns"] = [
            {"id": mp.id, "pattern_text": mp.pattern_text, "source_count": mp.source_count}
            for mp in result.meta_patterns
        ]
        match_dict["similarity"] = result.similarity

        return ClusterMatchResponse(match=match_dict)
    except Exception as exc:
        logger.error("Cluster match failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Cluster matching failed") from exc


@router.post("/api/clusters/recluster")
async def trigger_recluster(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ReclusterResponse:
    """Manual cold-path trigger — full HDBSCAN + UMAP recomputation."""
    engine = _get_engine(request)
    try:
        result = await engine.run_cold_path(db)
        if result is None:
            return ReclusterResponse(status="skipped", reason="lock held")
        return ReclusterResponse(
            status="completed",
            snapshot_id=result.snapshot_id,
            q_system=result.q_system,
            nodes_created=result.nodes_created,
            nodes_updated=result.nodes_updated,
            umap_fitted=result.umap_fitted,
        )
    except Exception as exc:
        logger.error("Manual recluster failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Recluster failed") from exc


# ---------------------------------------------------------------------------
# Legacy 301 redirects
# ---------------------------------------------------------------------------

# Taxonomy legacy routes

@router.get("/api/taxonomy/tree")
async def legacy_taxonomy_tree(request: Request):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/tree{qs}", status_code=301)


@router.get("/api/taxonomy/stats")
async def legacy_taxonomy_stats(request: Request):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/stats{qs}", status_code=301)


@router.get("/api/taxonomy/node/{node_id}")
async def legacy_taxonomy_node(request: Request, node_id: str):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/{node_id}{qs}", status_code=301)


@router.post("/api/taxonomy/recluster")
async def legacy_taxonomy_recluster(request: Request):
    return RedirectResponse(url="/api/clusters/recluster", status_code=307)


# Patterns legacy routes

@router.get("/api/patterns/families")
async def legacy_patterns_families(request: Request):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/tree{qs}", status_code=301)


@router.get("/api/patterns/families/{family_id}")
async def legacy_patterns_family_detail(request: Request, family_id: str):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/{family_id}{qs}", status_code=301)


@router.patch("/api/patterns/families/{family_id}")
async def legacy_patterns_family_update(request: Request, family_id: str):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/{family_id}{qs}", status_code=307)


@router.post("/api/patterns/match")
async def legacy_patterns_match(request: Request):
    return RedirectResponse(url="/api/clusters/match", status_code=307)


@router.get("/api/patterns/graph")
async def legacy_patterns_graph(request: Request):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/tree{qs}", status_code=301)


@router.get("/api/patterns/stats")
async def legacy_patterns_stats(request: Request):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/stats{qs}", status_code=301)


@router.get("/api/patterns/search")
async def legacy_patterns_search(request: Request):
    qs = f"?{request.query_params}" if request.query_params else ""
    return RedirectResponse(url=f"/api/clusters/tree{qs}", status_code=301)
