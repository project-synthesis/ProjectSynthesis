"""Domain management endpoints.

GET /api/domains — list all active domain nodes.
POST /api/domains/{id}/promote — promote a cluster to domain status.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.models import Optimization, PromptCluster
from app.schemas.domains import (
    DissolveEmptyResult,
    DomainInfo,
    RebuildSubDomainsRequest,
    RebuildSubDomainsResult,
)
from app.schemas.sub_domain_readiness import (
    DomainReadinessReport,
    ReadinessHistoryResponse,
)
from app.services.pipeline_constants import (
    DOMAIN_DISCOVERY_MIN_MEMBERS,
    VISIBILITY_THRESHOLD_FRACTION,
)
from app.services.taxonomy import readiness_history as readiness_history_service
from app.services.taxonomy import sub_domain_readiness as readiness_service
from app.services.taxonomy.coloring import compute_max_distance_color

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=list[DomainInfo])
async def list_domains(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[DomainInfo]:
    """List active domain nodes with colors and metadata.

    Hybrid taxonomy: domains are global, projects are views. Pass
    ``project_id`` to filter to domains that have earned visibility for
    that project via adaptive thresholds — absolute member floor
    (``DOMAIN_DISCOVERY_MIN_MEMBERS``) OR proportional share
    (``VISIBILITY_THRESHOLD_FRACTION`` of project optimizations). The
    canonical ``general`` domain is always visible.
    """
    try:
        result = await db.execute(
            select(PromptCluster)
            .where(PromptCluster.state == "domain")
            .order_by(PromptCluster.label)
        )
        from app.services.taxonomy.cluster_meta import read_meta

        all_domains = list(result.scalars())

        # Per-project evidence query — skipped when project_id is absent.
        per_project_counts: dict[str, int] = {}
        per_project_avg: dict[str, float | None] = {}
        project_total = 0
        if project_id is not None:
            evidence_q = await db.execute(
                select(
                    PromptCluster.domain.label("dom"),
                    func.count(Optimization.id).label("cnt"),
                    func.avg(Optimization.overall_score).label("avg_score"),
                )
                .join(PromptCluster, Optimization.cluster_id == PromptCluster.id)
                .where(
                    Optimization.project_id == project_id,
                    PromptCluster.domain.isnot(None),
                )
                .group_by(PromptCluster.domain)
            )
            for row in evidence_q.all():
                label = (row.dom or "").lower()
                if not label:
                    continue
                per_project_counts[label] = int(row.cnt or 0)
                per_project_avg[label] = (
                    float(row.avg_score) if row.avg_score is not None else None
                )
            project_total = sum(per_project_counts.values())

        domains: list[DomainInfo] = []
        for d in all_domains:
            try:
                source = read_meta(d.cluster_metadata)["source"]
            except Exception:
                source = "unknown"

            if project_id is None:
                domains.append(DomainInfo(
                    id=d.id,
                    label=d.label,
                    color_hex=d.color_hex or "#7a7a9e",
                    member_count=d.member_count,
                    avg_score=d.avg_score,
                    source=source,
                    parent_id=d.parent_id,
                ))
                continue

            # Project-scoped view — adaptive visibility.
            label_key = (d.label or "").lower()
            proj_count = per_project_counts.get(label_key, 0)
            is_canonical_general = label_key == "general" and d.parent_id is None

            visible = is_canonical_general
            if not visible and proj_count >= DOMAIN_DISCOVERY_MIN_MEMBERS:
                visible = True
            if (
                not visible
                and project_total > 0
                and proj_count / project_total >= VISIBILITY_THRESHOLD_FRACTION
                and proj_count >= 1
            ):
                visible = True

            if not visible:
                continue

            domains.append(DomainInfo(
                id=d.id,
                label=d.label,
                color_hex=d.color_hex or "#7a7a9e",
                member_count=proj_count,
                avg_score=per_project_avg.get(label_key),
                source=source,
                parent_id=d.parent_id,
                project_member_count=proj_count,
            ))
        return domains
    except OperationalError as exc:
        logger.warning("GET /api/domains DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/domains failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load domains") from exc


@router.get("/readiness", response_model=list[DomainReadinessReport])
async def list_domain_readiness(
    fresh: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[DomainReadinessReport]:
    """Batch domain + sub-domain readiness for every top-level domain.

    Returns reports sorted critical → healthy (stability tier), then by
    emergence gap ascending. Pass ``fresh=true`` to bypass the 30s TTL
    cache and force live recomputation.
    """
    try:
        return await readiness_service.compute_all_domain_readiness(db, fresh=fresh)
    except OperationalError as exc:
        logger.warning("GET /api/domains/readiness DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/domains/readiness failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to compute readiness") from exc


@router.get("/{domain_id}/readiness", response_model=DomainReadinessReport)
async def get_domain_readiness(
    domain_id: str,
    fresh: bool = False,
    db: AsyncSession = Depends(get_db),
) -> DomainReadinessReport:
    """Readiness report for a single top-level domain.

    Combines the dissolution stability view with the sub-domain emergence
    cascade. ``fresh=true`` bypasses the 30s TTL cache.
    """
    domain = await db.get(PromptCluster, domain_id)
    if domain is None:
        raise HTTPException(404, "Domain not found")
    if domain.state != "domain":
        raise HTTPException(422, f"Cluster {domain_id} is not a domain node (state='{domain.state}')")

    try:
        return await readiness_service.compute_domain_readiness(db, domain, fresh=fresh)
    except OperationalError as exc:
        logger.warning("GET /api/domains/%s/readiness DB contention: %s", domain_id, exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/domains/%s/readiness failed: %s", domain_id, exc, exc_info=True)
        raise HTTPException(500, "Failed to compute readiness") from exc


@router.get(
    "/{domain_id}/readiness/history",
    response_model=ReadinessHistoryResponse,
)
async def get_domain_readiness_history(
    domain_id: str,
    window: Literal["24h", "7d", "30d"] = "24h",
    db: AsyncSession = Depends(get_db),
) -> ReadinessHistoryResponse:
    """Time-series readiness history for one domain.

    ``window`` selects ``24h`` (raw points), ``7d``, or ``30d``. Windows at or
    above ``READINESS_HISTORY_BUCKET_THRESHOLD_DAYS`` (7d) are aggregated into
    hourly bucket means so payload size stays bounded (see
    ``services/taxonomy/readiness_history.py``). Reads snapshots written by
    warm-path Phase 5 — returns ``points=[]`` when no snapshots exist yet
    (e.g. a fresh install less than one warm cycle old).

    Errors: ``404`` unknown domain id, ``422`` non-domain cluster or invalid
    window, ``503`` database contention, ``500`` unexpected failure.
    """
    try:
        domain = await db.get(PromptCluster, domain_id)
    except OperationalError as exc:
        logger.warning(
            "GET /api/domains/%s/readiness/history DB contention: %s",
            domain_id, exc,
        )
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    if domain is None:
        raise HTTPException(404, "Domain not found")
    if domain.state != "domain":
        raise HTTPException(
            422,
            f"Cluster {domain_id} is not a domain node (state='{domain.state}')",
        )
    try:
        return await readiness_history_service.query_history(
            domain_id=domain.id,
            domain_label=domain.label,
            window=window,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.error(
            "GET /api/domains/%s/readiness/history failed: %s",
            domain_id, exc, exc_info=True,
        )
        raise HTTPException(500, "Failed to load readiness history") from exc


@router.post("/{domain_id}/promote", response_model=DomainInfo)
async def promote_to_domain(
    domain_id: str,
    db: AsyncSession = Depends(get_db),
) -> DomainInfo:
    """Promote a mature cluster to domain status."""
    cluster = await db.get(PromptCluster, domain_id)
    if not cluster:
        raise HTTPException(404, "Cluster not found")

    if cluster.state == "domain":
        raise HTTPException(422, "Already a domain node")

    if cluster.state not in ("active", "mature"):
        raise HTTPException(
            422,
            f"Cannot promote cluster with state='{cluster.state}' — must be 'active' or 'mature'",
        )

    if cluster.member_count < 5:
        raise HTTPException(
            422,
            f"Cluster has {cluster.member_count} members — minimum 5 required",
        )

    # Check label uniqueness among domains
    existing = await db.scalar(
        select(func.count()).where(
            PromptCluster.state == "domain",
            PromptCluster.label == cluster.label,
        )
    )
    if existing and existing > 0:
        raise HTTPException(409, f"Domain '{cluster.label}' already exists")

    # Compute color
    colors_result = await db.execute(
        select(PromptCluster.color_hex).where(
            PromptCluster.state == "domain",
            PromptCluster.color_hex.isnot(None),
        )
    )
    existing_colors = [row[0] for row in colors_result if row[0]]
    color_hex = compute_max_distance_color(existing_colors)

    # Domain labels must be lowercase
    if cluster.label != cluster.label.lower():
        raise HTTPException(
            422,
            f"Domain labels must be lowercase. Rename cluster to '{cluster.label.lower()}' first.",
        )

    # Promote
    from datetime import datetime, timezone

    from app.services.taxonomy.cluster_meta import write_meta

    cluster.state = "domain"
    cluster.domain = cluster.label
    cluster.color_hex = color_hex
    cluster.persistence = 1.0
    cluster.promoted_at = datetime.now(timezone.utc)
    cluster.parent_id = None  # Domain nodes are roots
    cluster.cluster_metadata = write_meta(
        None,
        source="manual",
        signal_keywords=[],
        discovered_at=None,
        proposed_by_snapshot=None,
        signal_member_count_at_generation=0,
    )
    try:
        await db.commit()
    except OperationalError as exc:
        await db.rollback()
        logger.warning("Domain promote DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        await db.rollback()
        logger.warning("Domain promote failed: %s", exc)
        raise HTTPException(409, f"Domain '{cluster.label}' already exists (concurrent create)") from exc

    logger.info("Cluster %s promoted to domain: label='%s'", domain_id, cluster.label)

    from app.services.event_bus import event_bus
    from app.services.taxonomy.event_logger import get_event_logger

    event_bus.publish("domain_created", {
        "label": cluster.label,
        "color_hex": color_hex,
        "node_id": cluster.id,
        "source": "manual",
    })

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="api",
            op="operator_action",
            decision="domain_created",
            cluster_id=str(cluster.id),
            context={
                "label": cluster.label,
                "color_hex": color_hex,
                "source": "manual"
            }
        )
    except RuntimeError:
        pass

    return DomainInfo(
        id=cluster.id,
        label=cluster.label,
        color_hex=color_hex,
        member_count=cluster.member_count,
        avg_score=cluster.avg_score,
        source="manual",
    )


@router.post(
    "/{domain_id}/rebuild-sub-domains",
    response_model=RebuildSubDomainsResult,
    dependencies=[Depends(RateLimit(lambda: "10/minute"))],
)
async def rebuild_sub_domains(
    domain_id: str,
    request: RebuildSubDomainsRequest,
    db: AsyncSession = Depends(get_db),
) -> RebuildSubDomainsResult:
    """Operator-triggered sub-domain rebuild — see R6 in audit doc.

    Error envelope mirrors ``promote_to_domain``:
        404 — domain_id not found
        422 — node is not a domain (state != "domain") OR
              min_consistency below SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR
        503 — DB OperationalError (transient contention)
        500 — uncaught (logged with traceback)
    """
    from app.services.taxonomy import get_engine
    engine = get_engine()
    try:
        result = await engine.rebuild_sub_domains(
            db, domain_id,
            min_consistency_override=request.min_consistency,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        msg = str(exc)
        msg_lower = msg.lower()
        if "not found" in msg_lower:
            raise HTTPException(404, "Domain not found") from exc
        if "is not a domain" in msg_lower or "not a domain" in msg_lower:
            raise HTTPException(
                422,
                f"Cluster {domain_id} must be a domain node — {msg}",
            ) from exc
        raise HTTPException(422, msg) from exc
    except OperationalError as exc:
        await db.rollback()
        logger.warning("rebuild_sub_domains DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc

    if not request.dry_run and result["created"]:
        try:
            await db.commit()
        except OperationalError as exc:
            await db.rollback()
            logger.warning("rebuild_sub_domains commit failed: %s", exc)
            raise HTTPException(503, "Database busy — retry in a moment") from exc

    return RebuildSubDomainsResult(**result)


@router.post(
    "/{domain_id}/dissolve-empty",
    response_model=DissolveEmptyResult,
    dependencies=[Depends(RateLimit(lambda: "10/minute"))],
)
async def dissolve_empty_domain(
    domain_id: str,
    db: AsyncSession = Depends(get_db),
) -> DissolveEmptyResult:
    """v0.4.11 P1 operator escape hatch — force-dissolve an empty (ghost)
    domain, bypassing the standard 48h dissolution age gate.

    Status mapping (mirrors ``promote_to_domain``):
        200 + ``dissolved=True`` on success
        200 + ``dissolved=False, reason='already_dissolved'`` on idempotent
              re-call (target node is no longer a domain)
        404 ``Domain not found`` if ``domain_id`` doesn't exist
        409 ``not_empty`` if ``member_count > 0``
        409 ``too_young`` if age < ``DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES``
        429 if rate limit exceeded (10/min per IP)
        503 on transient DB ``OperationalError``
    """
    from app.services.taxonomy import get_engine
    engine = get_engine()
    try:
        result = await engine.dissolve_empty_domain(db, domain_id)
    except ValueError as exc:
        msg_lower = str(exc).lower()
        if "not found" in msg_lower:
            raise HTTPException(404, "Domain not found") from exc
        raise HTTPException(422, str(exc)) from exc
    except OperationalError as exc:
        await db.rollback()
        logger.warning("dissolve_empty_domain DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc

    # Map operator-actionable failure reasons to 409 so curl users get
    # canonical REST semantics. The pure engine method always returns the
    # full envelope so service-layer callers can branch on `reason`
    # without exception handling.
    reason = result.get("reason")
    if reason in ("not_empty", "too_young"):
        raise HTTPException(409, reason)

    if result.get("dissolved"):
        try:
            await db.commit()
        except OperationalError as exc:
            await db.rollback()
            logger.warning("dissolve_empty_domain commit failed: %s", exc)
            raise HTTPException(503, "Database busy — retry in a moment") from exc

    return DissolveEmptyResult(**result)
