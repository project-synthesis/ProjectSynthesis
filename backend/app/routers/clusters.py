"""Unified cluster API — tree, detail, stats, match, templates, recluster.

Replaces the separate /api/taxonomy/ and /api/patterns/ routers with a single
/api/clusters/ namespace.  Legacy paths receive 301 redirects.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import MetaPattern, Optimization, OptimizationPattern, PromptCluster
from app.schemas.clusters import (
    ActivityHistoryResponse,
    ActivityResponse,
    ClusterDetail,
    ClusterMatchResponse,
    ClusterNode,
    ClusterStats,
    ClusterTreeResponse,
    ClusterUpdateRequest,
    InjectionEdge,
    InjectionEdgesResponse,
    LinkedOptimization,
    MetaPatternItem,
    ReclusterResponse,
    SimilarityEdge,
    SimilarityEdgesResponse,
    TaxonomyActivityEvent,
)
from app.services.taxonomy import TaxonomyEngine
from app.services.taxonomy import get_engine as get_taxonomy_engine
from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES
from app.services.taxonomy.event_logger import get_event_logger

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
        ..., min_length=10, max_length=8000,
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


@router.get("/api/projects")
async def list_projects(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """List all project nodes for project selection UI."""
    result = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "project")
    )
    projects = result.scalars().all()
    return [
        {
            "id": p.id,
            "label": p.label,
            "member_count": p.member_count or 0,
        }
        for p in projects
    ]


@router.get("/api/clusters/tree")
async def get_cluster_tree(
    request: Request,
    min_persistence: float = Query(0.0, ge=0.0, le=1.0),
    project_id: str | None = Query(None),  # ADR-005 Phase 2A
    db: AsyncSession = Depends(get_db),
) -> ClusterTreeResponse:
    """Flat node list for 3D topology visualization."""
    try:
        db.autoflush = False
        engine = _get_engine(request)
        nodes = await engine.get_tree(db, min_persistence=min_persistence)

        # ADR-005 Phase 2A: filter tree to a single project sub-tree
        if project_id:
            # Collect IDs of the project node + its children + grandchildren
            project_ids = {project_id}
            # First pass: direct children of the project node
            for n in nodes:
                if n.get("parent_id") in project_ids:
                    project_ids.add(n["id"])
            # Second pass: grandchildren (cluster -> domain -> project)
            for n in nodes:
                if n.get("parent_id") in project_ids:
                    project_ids.add(n["id"])
            nodes = [n for n in nodes if n["id"] in project_ids]

        return ClusterTreeResponse(nodes=[ClusterNode(**n) for n in nodes])
    except OperationalError as exc:
        logger.warning("GET /api/clusters/tree DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
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
        db.autoflush = False
        engine = _get_engine(request)
        data = await engine.get_stats(db)

        # Map total_families -> total_clusters for the unified schema
        total_clusters = data.pop("total_families", 0)
        return ClusterStats(total_clusters=total_clusters, **data)
    except OperationalError as exc:
        logger.warning("GET /api/clusters/stats DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/clusters/stats failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load cluster stats") from exc


@router.get("/api/clusters/similarity-edges")
async def get_similarity_edges(
    request: Request,
    threshold: float = Query(0.50, ge=0.0, le=1.0),
    max_edges: int = Query(100, ge=1, le=1000),
) -> SimilarityEdgesResponse:
    """Pairwise cosine similarity edges above threshold for topology overlay."""
    try:
        engine = _get_engine(request)
        pairs = engine.embedding_index.pairwise_similarities(threshold, max_edges)
        return SimilarityEdgesResponse(
            edges=[
                SimilarityEdge(from_id=a, to_id=b, similarity=s)
                for a, b, s in pairs
            ]
        )
    except OperationalError as exc:
        logger.warning("GET /api/clusters/similarity-edges DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/clusters/similarity-edges failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to compute similarity edges") from exc


@router.get("/api/clusters/injection-edges")
async def get_injection_edges(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> InjectionEdgesResponse:
    """Directed injection provenance edges: source cluster → target cluster.

    Aggregates ``OptimizationPattern`` records with ``relationship="injected"``
    and joins with ``Optimization`` to resolve each optimization's assigned
    ``cluster_id`` (the target).  Returns weighted directed edges where weight
    is the number of injection events along that source→target pair.

    Only includes edges where both source and target clusters are non-structural.
    """
    try:
        db.autoflush = False
        stmt = (
            select(
                OptimizationPattern.cluster_id.label("source_id"),
                Optimization.cluster_id.label("target_id"),
                func.count().label("weight"),
            )
            .join(
                Optimization,
                OptimizationPattern.optimization_id == Optimization.id,
            )
            .where(
                OptimizationPattern.relationship == "injected",
                Optimization.cluster_id.isnot(None),
                # Source cluster must still exist and be non-structural
                OptimizationPattern.cluster_id.in_(
                    select(PromptCluster.id).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
                ),
                # Target cluster must still exist and be non-structural
                Optimization.cluster_id.in_(
                    select(PromptCluster.id).where(PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES))
                ),
            )
            .group_by(
                OptimizationPattern.cluster_id,
                Optimization.cluster_id,
            )
        )

        result = await db.execute(stmt)
        rows = result.all()

        edges = [
            InjectionEdge(source_id=row.source_id, target_id=row.target_id, weight=row.weight)
            for row in rows
            if row.source_id != row.target_id  # exclude self-loops
        ]

        return InjectionEdgesResponse(edges=edges)
    except OperationalError as exc:
        logger.warning("GET /api/clusters/injection-edges DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/clusters/injection-edges failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load injection edges") from exc


@router.get("/api/clusters/templates")
async def get_cluster_templates_gone(
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
):
    """Legacy endpoint — removed. Use GET /api/templates instead."""
    raise HTTPException(
        status_code=410,
        detail={"detail": "Endpoint removed. Use GET /api/templates."},
    )


# ---------------------------------------------------------------------------
# Taxonomy activity log — MUST be before {cluster_id} dynamic route
# ---------------------------------------------------------------------------


@router.get("/api/clusters/activity", response_model=ActivityResponse)
async def get_cluster_activity(
    limit: int = Query(50, ge=1, le=200),
    path: str | None = Query(None, pattern="^(hot|warm|cold)$"),
    op: str | None = Query(None),
    errors_only: bool = Query(False),
) -> ActivityResponse:
    """Return recent taxonomy decision events from the in-memory ring buffer."""
    try:
        tel = get_event_logger()
    except RuntimeError:
        return ActivityResponse(events=[], total_in_buffer=0, oldest_ts=None)

    try:
        raw = tel.get_recent(limit=limit, path=path, op=op)
        if errors_only:
            raw = [
                e for e in raw
                if e.get("op") == "error" or e.get("decision") in ("rejected", "failed")
            ]

        events = [TaxonomyActivityEvent(**e) for e in raw]
        return ActivityResponse(
            events=events,
            total_in_buffer=tel.buffer_size,
            oldest_ts=tel.oldest_ts,
        )
    except Exception as exc:
        logger.error("GET /api/clusters/activity failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load activity events") from exc


@router.get("/api/clusters/activity/history", response_model=ActivityHistoryResponse)
async def get_cluster_activity_history(
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ActivityHistoryResponse:
    """Return taxonomy decision events for a specific date from JSONL storage."""
    try:
        tel = get_event_logger()
    except RuntimeError:
        return ActivityHistoryResponse(events=[], total=0, has_more=False)

    try:
        raw = tel.get_history(date=date, limit=limit + 1, offset=offset)
        has_more = len(raw) > limit
        raw = raw[:limit]

        events = [TaxonomyActivityEvent(**e) for e in raw]
        return ActivityHistoryResponse(
            events=events,
            total=offset + len(events) + (1 if has_more else 0),
            has_more=has_more,
        )
    except Exception as exc:
        logger.error("GET /api/clusters/activity/history failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load activity history") from exc


# ---------------------------------------------------------------------------
# Cluster detail (dynamic {cluster_id} — must come after static routes)
# ---------------------------------------------------------------------------


@router.get("/api/clusters/{cluster_id}")
async def get_cluster_detail(
    request: Request,
    cluster_id: str,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(RateLimit(lambda: settings.DEFAULT_RATE_LIMIT)),
) -> ClusterDetail:
    """Single cluster with children, breadcrumb, meta-patterns, and linked optimizations."""
    try:
        # Prevent autoflush during read — avoids 500 when a concurrent
        # recluster/reassign has dirty state in a shared session scope.
        db.autoflush = False

        engine = _get_engine(request)
        node = await engine.get_node(cluster_id, db)
        if node is None:
            raise HTTPException(status_code=404, detail="Cluster not found")

        children_raw = node.get("children", [])
        breadcrumb_raw = node.get("breadcrumb", [])

        # Meta-patterns: aggregate from child clusters for domain nodes,
        # query own patterns for regular clusters
        is_domain_node = node.get("state") == "domain"
        if is_domain_node:
            # Aggregate top patterns across all child clusters in this domain
            meta_result = await db.execute(
                select(MetaPattern)
                .join(PromptCluster, MetaPattern.cluster_id == PromptCluster.id)
                .where(
                    PromptCluster.domain == node.get("label", ""),
                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                )
                .order_by(MetaPattern.source_count.desc())
            )
            all_patterns = meta_result.scalars().all()
            # Deduplicate by pattern_text, summing source_count
            seen: dict[str, MetaPattern] = {}
            for mp in all_patterns:
                key = mp.pattern_text[:500]
                if key in seen:
                    seen[key].source_count += mp.source_count
                else:
                    seen[key] = mp
            meta_patterns = sorted(seen.values(), key=lambda m: m.source_count, reverse=True)[:10]
        else:
            meta_result = await db.execute(
                select(MetaPattern)
                .where(MetaPattern.cluster_id == cluster_id)
                .order_by(MetaPattern.source_count.desc())
            )
            meta_patterns = list(meta_result.scalars().all())

        # Linked optimizations — for project nodes, query by project_id;
        # for clusters/domains, query by cluster_id (hot-path assignment).
        is_project_node = node.get("state") == "project"
        if is_project_node:
            opt_result = await db.execute(
                select(Optimization)
                .where(Optimization.project_id == cluster_id)
                .order_by(Optimization.created_at.desc())
                .limit(50)
            )
        else:
            opt_result = await db.execute(
                select(Optimization)
                .where(Optimization.cluster_id == cluster_id)
                .order_by(Optimization.created_at.desc())
                .limit(50)
            )
        optimizations = opt_result.scalars().all()

        # ADR-005 Phase 2A: per-project member breakdown
        if is_project_node:
            # For project nodes, count optimizations by domain
            project_counts_q = await db.execute(
                select(Optimization.domain, func.count())
                .where(Optimization.project_id == cluster_id)
                .group_by(Optimization.domain)
            )
            member_counts_by_project = {
                (domain or "general"): count
                for domain, count in project_counts_q.all()
            }
        else:
            project_counts_q = await db.execute(
                select(Optimization.project_id, func.count())
                .where(Optimization.cluster_id == cluster_id)
                .group_by(Optimization.project_id)
            )
            member_counts_by_project = {
                (pid or "legacy"): count for pid, count in project_counts_q.all()
            }

        node_data = {k: v for k, v in node.items() if k not in ("children", "breadcrumb")}

        return ClusterDetail(
            **node_data,
            project_ids=list(member_counts_by_project.keys()),
            member_counts_by_project=member_counts_by_project,
            meta_patterns=[
                MetaPatternItem(id=mp.id, pattern_text=mp.pattern_text, source_count=mp.source_count)
                for mp in meta_patterns
            ],
            optimizations=[
                LinkedOptimization(
                    id=o.id,
                    trace_id=o.trace_id or "",
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
    except OperationalError as exc:
        logger.warning("GET /api/clusters/%s DB contention: %s", cluster_id, exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/clusters/%s failed: %s", cluster_id, exc, exc_info=True)
        raise HTTPException(500, "Failed to load cluster detail") from exc


@router.patch("/api/clusters/{cluster_id}")
async def update_cluster(
    cluster_id: str,
    body: ClusterUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UpdateClusterResponse:
    """Update a cluster's label and/or state."""
    if body.intent_label is None and body.state is None:
        raise HTTPException(422, "At least one of 'intent_label' or 'state' must be provided")

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

    if body.state is not None:
        old_state = cluster.state

        # Templates are a separate entity now (ADR: template-architecture).
        # Reject state=template with a redirect hint to the new endpoint.
        # NOTE: Do NOT narrow the Literal on ClusterUpdateRequest.state to exclude
        # "template" — that would cause FastAPI to return 422 before this handler
        # runs, preventing the informative 400 message from reaching the caller.
        if body.state == "template":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Template state no longer exists. "
                    "Use POST /api/clusters/{id}/fork-template."
                ),
            )

        # Quality gate: template promotion requires proven quality + usage
        if body.state == "template" and old_state != "template":
            _score = cluster.avg_score or 0
            _members = cluster.member_count or 0
            _usage = cluster.usage_count or 0
            if _score < 6.0:
                raise HTTPException(
                    422,
                    f"Template promotion requires avg_score >= 6.0 (current: {_score:.1f})",
                )
            if _members < 3 and _usage < 1:
                raise HTTPException(
                    422,
                    f"Template promotion requires 3+ members or 1+ usage (members: {_members}, usage: {_usage})",
                )

        cluster.state = body.state

        # Set promoted_at for state upgrades — always update on template
        # promotion (even mature→template), and on first mature promotion.
        _is_upgrade = (
            (body.state == "template" and old_state != "template")
            or (body.state == "mature" and old_state not in ("mature", "template"))
        )
        if _is_upgrade:
            from datetime import datetime, timezone
            cluster.promoted_at = datetime.now(timezone.utc).replace(tzinfo=None)

        logger.info("Cluster state changed: id=%s '%s' -> '%s'", cluster_id, old_state, body.state)

        # Log taxonomy event for observability
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="api",
                op="state_change",
                cluster_id=cluster_id,
                decision=f"{old_state}_to_{body.state}",
                context={
                    "old_state": old_state,
                    "new_state": body.state,
                    "source": "manual",
                    "avg_score": cluster.avg_score,
                    "member_count": cluster.member_count,
                    "usage_count": cluster.usage_count,
                },
            )
        except RuntimeError:
            pass  # Event logger not initialized — non-fatal

    try:
        await db.commit()
    except OperationalError as exc:
        await db.rollback()
        logger.warning("Cluster update DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        await db.rollback()
        logger.warning("Cluster update failed: %s", exc)
        raise HTTPException(409, "Update conflicts with existing data") from exc

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
    _rate: None = Depends(RateLimit(lambda: "30/minute")),
) -> ClusterMatchResponse:
    """Hierarchical similarity check for live pattern suggestion."""
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
    except OperationalError as exc:
        logger.warning("Cluster match DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
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
        status = "completed" if result.accepted else "rejected"
        return ReclusterResponse(
            status=status,
            reason="quality gate failed — rolled back" if not result.accepted else None,
            snapshot_id=result.snapshot_id,
            q_system=result.q_system,
            q_before=result.q_before,
            q_after=result.q_after,
            accepted=result.accepted,
            nodes_created=result.nodes_created,
            nodes_updated=result.nodes_updated,
            umap_fitted=result.umap_fitted,
        )
    except OperationalError as exc:
        logger.warning("Manual recluster DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("Manual recluster failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Recluster failed") from exc


@router.post("/api/clusters/repair")
async def repair_integrity(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Repair orphaned join records, meta-patterns, and missing coherence."""
    engine = _get_engine(request)
    try:
        result = await engine.repair_data_integrity(db)
        await db.commit()
        return {"status": "completed", **result}
    except OperationalError as exc:
        logger.warning("Data integrity repair DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("Data integrity repair failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Repair failed") from exc


@router.post("/api/clusters/reassign")
async def reassign_clusters(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Replay hot-path cluster assignment for all optimizations.

    Archives existing active clusters and rebuilds them from scratch using
    the current adaptive merge threshold.  Use this after changing threshold
    constants to apply the new logic to existing data.
    """
    engine = _get_engine(request)
    try:
        result = await engine.reassign_all_clusters(db)
        await db.commit()
        return {"status": "completed", **result}
    except OperationalError as exc:
        logger.warning("Cluster reassignment DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("Cluster reassignment failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Cluster reassignment failed") from exc


@router.post("/api/clusters/backfill-scores")
async def backfill_scores(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Recompute avg_score and scored_count for all clusters from member data.

    One-time fix for clusters whose running mean drifted due to the
    member_count/scored_count mismatch or warm-path score clearing.
    """
    _get_engine(request)  # validate engine is available
    try:
        # Reuse the same grouped-query pattern as warm path reconciliation
        from sqlalchemy import func as sa_func

        from app.models import Optimization

        score_q = await db.execute(
            select(
                Optimization.cluster_id,
                sa_func.avg(Optimization.overall_score),
                sa_func.count(Optimization.overall_score),
            ).where(
                Optimization.cluster_id.isnot(None),
                Optimization.overall_score.isnot(None),
            ).group_by(Optimization.cluster_id)
        )
        score_map = {
            row[0]: (round(row[1], 2), row[2])
            for row in score_q.all()
        }

        from app.models import PromptCluster

        cluster_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.in_(["active", "candidate", "mature"])
            )
        )
        updated = 0
        for cluster in cluster_q.scalars().all():
            avg, scored = score_map.get(cluster.id, (None, 0))
            if cluster.avg_score != avg or (cluster.scored_count or 0) != scored:
                cluster.avg_score = avg
                cluster.scored_count = scored
                updated += 1

        await db.commit()
        logger.info("Score backfill completed: %d clusters updated", updated)
        return {"status": "completed", "clusters_updated": updated}
    except OperationalError as exc:
        logger.warning("Score backfill DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("Score backfill failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Score backfill failed") from exc


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
