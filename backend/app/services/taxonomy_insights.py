"""Pattern-density aggregator for the Taxonomy Observatory.

Single function — not worth a class. Read-only query, no caching.
Python-side GlobalPattern containment (<=500 rows x <=30 domains) avoids
SQLite JSON-operator queries for PostgreSQL portability.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GlobalPattern, MetaPattern, OptimizationPattern, PromptCluster
from app.schemas.taxonomy_insights import PatternDensityRow

_ACTIVE_CHILD_STATES = ("active", "mature", "candidate")


async def aggregate_pattern_density(
    db: AsyncSession,
    period_start: datetime,
    period_end: datetime,
) -> list[PatternDensityRow]:
    """Aggregate per-domain pattern density metrics.

    Returns one row per active domain node (state == "domain", archived_at IS NULL).
    """
    # Active domain nodes.
    domains_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.archived_at.is_(None),
        )
    )
    domains = list(domains_q.scalars().all())

    # Pre-load ALL GlobalPattern rows once (<=500 cap).
    gp_q = await db.execute(
        select(GlobalPattern.id, GlobalPattern.source_cluster_ids)
    )
    all_gp: list[tuple[str, set[str]]] = [
        (row[0], set(row[1] or [])) for row in gp_q.all()
    ]

    # In-period injection events globally.
    inj_q = await db.execute(
        select(OptimizationPattern.cluster_id).where(
            OptimizationPattern.relationship.in_(("injected", "global_injected")),
            OptimizationPattern.created_at >= period_start,
            OptimizationPattern.created_at < period_end,
        )
    )
    inj_cluster_ids: list[str] = [r[0] for r in inj_q.all()]
    inj_total_count = len(inj_cluster_ids)

    rows: list[PatternDensityRow] = []
    for domain in domains:
        # Child clusters in active lifecycle states.
        children_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == domain.id,
                PromptCluster.state.in_(_ACTIVE_CHILD_STATES),
            )
        )
        children = list(children_q.scalars().all())
        child_ids = [c.id for c in children]
        child_id_set = set(child_ids)

        # Meta-patterns under this domain (cluster IDs aggregated).
        if child_ids:
            meta_q = await db.execute(
                select(MetaPattern.cluster_id).where(MetaPattern.cluster_id.in_(child_ids))
            )
            meta_cluster_ids = [r[0] for r in meta_q.all()]
        else:
            meta_cluster_ids = []
        meta_pattern_count = len(meta_cluster_ids)

        # Avg score across clusters with >=1 MetaPattern.
        if meta_cluster_ids:
            unique_mc = set(meta_cluster_ids)
            scoring_clusters = [
                c for c in children
                if c.id in unique_mc and c.avg_score is not None
            ]
            if scoring_clusters:
                meta_avg: float | None = (
                    sum(c.avg_score for c in scoring_clusters) / len(scoring_clusters)
                )
            else:
                meta_avg = None
        else:
            meta_avg = None

        # GlobalPattern containment.
        global_pattern_count = sum(
            1 for _gp_id, src_ids in all_gp if src_ids & child_id_set
        )

        # Injection rate.
        domain_injections = sum(1 for cid in inj_cluster_ids if cid in child_id_set)
        injection_rate = (
            domain_injections / inj_total_count if inj_total_count > 0 else 0.0
        )

        rows.append(PatternDensityRow(
            domain_id=domain.id,
            domain_label=domain.label,
            cluster_count=len(children),
            meta_pattern_count=meta_pattern_count,
            meta_pattern_avg_score=meta_avg,
            global_pattern_count=global_pattern_count,
            cross_cluster_injection_rate=injection_rate,
            period_start=period_start,
            period_end=period_end,
        ))

    return rows
