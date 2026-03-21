"""Taxonomy tree API endpoints — tree, node detail, stats, recluster.

Spec Section 6.6.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.taxonomy import (
    ReclusterResponse,
    TaxonomyNodeResponse,
    TaxonomyStatsResponse,
    TaxonomyTreeResponse,
)
from app.services.taxonomy import TaxonomyEngine
from app.services.taxonomy import get_engine as get_taxonomy_engine
from app.services.taxonomy.sparkline import SparklineData

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/taxonomy", tags=["taxonomy"])


def _get_engine(request: Request) -> TaxonomyEngine:
    """Retrieve the singleton TaxonomyEngine via the process-wide factory."""
    return get_taxonomy_engine(app=request.app)


@router.get("/tree")
async def get_tree(
    request: Request,
    min_persistence: float = 0.0,
    db: AsyncSession = Depends(get_db),
) -> TaxonomyTreeResponse:
    """Full taxonomy tree for 3D visualization."""
    engine = _get_engine(request)
    nodes = await engine.get_tree(db, min_persistence=min_persistence)
    return TaxonomyTreeResponse(nodes=[TaxonomyNodeResponse(**n) for n in nodes])


@router.get("/node/{node_id}")
async def get_node(
    request: Request,
    node_id: str,
    db: AsyncSession = Depends(get_db),
) -> TaxonomyNodeResponse:
    """Single taxonomy node with children, breadcrumb, and metrics."""
    engine = _get_engine(request)
    node = await engine.get_node(node_id, db)
    if node is None:
        raise HTTPException(status_code=404, detail="Taxonomy node not found")
    # Children are nested dicts — extract without mutating the engine dict
    children_raw = node.get("children")
    node_data = {k: v for k, v in node.items() if k != "children"}
    resp = TaxonomyNodeResponse(**node_data)
    if children_raw is not None:
        resp.children = [TaxonomyNodeResponse(**c) for c in children_raw]
    return resp


@router.get("/stats")
async def get_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TaxonomyStatsResponse:
    """System quality metrics and snapshot history."""
    engine = _get_engine(request)
    data = await engine.get_stats(db)
    # Engine returns SparklineData object for q_sparkline — extract .normalized
    sparkline = data.get("q_sparkline")
    if isinstance(sparkline, SparklineData):
        data["q_sparkline"] = sparkline.normalized
    return TaxonomyStatsResponse(**data)


@router.post("/recluster")
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
