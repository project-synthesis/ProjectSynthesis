"""Domain management endpoints.

GET /api/domains — list all active domain nodes.
POST /api/domains/{id}/promote — promote a cluster to domain status.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
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
    result = await db.execute(
        select(PromptCluster)
        .where(PromptCluster.state == "domain")
        .order_by(PromptCluster.label)
    )
    return [
        DomainInfo(
            id=d.id,
            label=d.label,
            color_hex=d.color_hex or "#7a7a9e",
            member_count=d.member_count,
            avg_score=d.avg_score,
            source=(d.cluster_metadata or {}).get("source", "seed"),
        )
        for d in result.scalars()
    ]


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
    cluster.state = "domain"
    cluster.domain = cluster.label
    cluster.color_hex = color_hex
    cluster.persistence = 1.0
    cluster.cluster_metadata = {
        "source": "manual",
        "signal_keywords": [],
        "discovered_at": None,
        "proposed_by_snapshot": None,
        "signal_member_count_at_generation": 0,
    }
    await db.commit()

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
