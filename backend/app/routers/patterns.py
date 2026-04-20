"""Pattern graph endpoints.

``GET /api/patterns`` — combined MetaPattern + GlobalPattern view. Optional
``project_id`` filters meta-patterns to those anchored in clusters whose
optimizations belong to the given project (via OptimizationPattern →
Optimization.project_id join). Global patterns are always unioned in because
they are cross-project by construction.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    GlobalPattern,
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PromptCluster,
)
from app.schemas.patterns import (
    GlobalPatternNode,
    MetaPatternNode,
    PatternGraphResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patterns", tags=["patterns"])


_MAX_LIMIT = 200


@router.get("", response_model=PatternGraphResponse)
async def list_patterns(
    project_id: str | None = Query(
        None,
        description="Filter meta-patterns to those owned by this project (Hybrid view).",
    ),
    limit: int = Query(50, ge=1, le=_MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
) -> PatternGraphResponse:
    """Return meta + global patterns for the pattern graph UI.

    * Without ``project_id`` — all meta-patterns (top ``limit`` by
      ``source_count``) across the global taxonomy.
    * With ``project_id`` — meta-patterns anchored to clusters whose
      optimizations are owned by that project. Duplicate pattern texts
      are collapsed (project-local source_count = sum across clusters).

    Global patterns are always returned; they are scoped cross-project by
    design and are the system's durable shared signal.
    """
    try:
        meta_items: list[MetaPatternNode] = []

        if project_id is None:
            # Global view — top N by cluster source_count.
            result = await db.execute(
                select(MetaPattern, PromptCluster)
                .join(PromptCluster, MetaPattern.cluster_id == PromptCluster.id)
                .order_by(MetaPattern.source_count.desc())
                .limit(limit)
            )
            for mp, cluster in result.all():
                meta_items.append(MetaPatternNode(
                    id=mp.id,
                    pattern_text=mp.pattern_text,
                    source_count=mp.source_count,
                    cluster_id=cluster.id,
                    cluster_label=cluster.label,
                    domain=cluster.domain,
                ))
        else:
            # Project-scoped — via OptimizationPattern → Optimization.project_id.
            # Count distinct optimizations per meta_pattern within this project.
            evidence_q = await db.execute(
                select(
                    MetaPattern.id.label("mp_id"),
                    MetaPattern.pattern_text.label("txt"),
                    MetaPattern.cluster_id.label("cluster_id"),
                    PromptCluster.label.label("cluster_label"),
                    PromptCluster.domain.label("domain"),
                    func.count(func.distinct(Optimization.id)).label("proj_count"),
                )
                .join(
                    OptimizationPattern,
                    OptimizationPattern.meta_pattern_id == MetaPattern.id,
                )
                .join(
                    Optimization,
                    OptimizationPattern.optimization_id == Optimization.id,
                )
                .join(
                    PromptCluster,
                    MetaPattern.cluster_id == PromptCluster.id,
                )
                .where(Optimization.project_id == project_id)
                .group_by(MetaPattern.id)
                .order_by(func.count(func.distinct(Optimization.id)).desc())
                .limit(limit)
            )
            # Collapse by pattern_text to avoid showing the same technique
            # multiple times when it was mined from parallel clusters.
            collapsed: dict[str, MetaPatternNode] = {}
            for row in evidence_q.all():
                key = (row.txt or "")[:500]
                if not key:
                    continue
                existing = collapsed.get(key)
                proj_count = int(row.proj_count or 0)
                if existing is None:
                    collapsed[key] = MetaPatternNode(
                        id=row.mp_id,
                        pattern_text=row.txt,
                        source_count=proj_count,
                        cluster_id=row.cluster_id,
                        cluster_label=row.cluster_label,
                        domain=row.domain,
                    )
                else:
                    existing.source_count += proj_count
            meta_items = sorted(
                collapsed.values(),
                key=lambda n: n.source_count,
                reverse=True,
            )

        # Global patterns — always returned (they're cross-project).
        gp_q = await db.execute(
            select(GlobalPattern)
            .where(GlobalPattern.state == "active")
            .order_by(GlobalPattern.cross_project_count.desc())
        )
        global_items = [
            GlobalPatternNode(
                id=gp.id,
                pattern_text=gp.pattern_text,
                source_cluster_ids=list(gp.source_cluster_ids or []),
                source_project_ids=list(gp.source_project_ids or []),
                cross_project_count=gp.cross_project_count,
                avg_cluster_score=gp.avg_cluster_score,
                state=gp.state,
            )
            for gp in gp_q.scalars().all()
        ]

        return PatternGraphResponse(
            project_id=project_id,
            meta_patterns=meta_items,
            global_patterns=global_items,
        )
    except OperationalError as exc:
        logger.warning("GET /api/patterns DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/patterns failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load patterns") from exc
