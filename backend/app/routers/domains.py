"""Domain management endpoints.

GET /api/domains — list all active domain nodes.
POST /api/domains/{id}/promote — promote a cluster to domain status.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import PromptCluster
from app.schemas.domains import DomainInfo
from app.services.taxonomy.coloring import compute_max_distance_color

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/domains", tags=["domains"])


@router.get("", response_model=list[DomainInfo])
async def list_domains(db: AsyncSession = Depends(get_db)) -> list[DomainInfo]:
    """List all active domain nodes with colors and metadata."""
    try:
        result = await db.execute(
            select(PromptCluster)
            .where(PromptCluster.state == "domain")
            .order_by(PromptCluster.label)
        )
        from app.services.taxonomy.cluster_meta import read_meta

        domains = []
        for d in result.scalars():
            try:
                source = read_meta(d.cluster_metadata)["source"]
            except Exception:
                source = "unknown"
            domains.append(DomainInfo(
                id=d.id,
                label=d.label,
                color_hex=d.color_hex or "#7a7a9e",
                member_count=d.member_count,
                avg_score=d.avg_score,
                source=source,
                parent_id=d.parent_id,
            ))
        return domains
    except OperationalError as exc:
        logger.warning("GET /api/domains DB contention: %s", exc)
        raise HTTPException(503, "Database busy — retry in a moment") from exc
    except Exception as exc:
        logger.error("GET /api/domains failed: %s", exc, exc_info=True)
        raise HTTPException(500, "Failed to load domains") from exc


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
    event_bus.publish("domain_created", {
        "label": cluster.label,
        "color_hex": color_hex,
        "node_id": cluster.id,
        "source": "manual",
    })

    return DomainInfo(
        id=cluster.id,
        label=cluster.label,
        color_hex=color_hex,
        member_count=cluster.member_count,
        avg_score=cluster.avg_score,
        source="manual",
    )
