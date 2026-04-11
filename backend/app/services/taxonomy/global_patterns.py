"""Global Pattern promotion, validation, and retention.

Phase 4.5 of the warm path (ADR-005 Section 6).  Discovers MetaPatterns
that recur across multiple projects, promotes them to durable GlobalPatterns,
validates existing GlobalPatterns against live cluster health, and enforces
a retention cap to prevent unbounded growth.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from statistics import mean

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GlobalPattern, MetaPattern, Optimization, PromptCluster
from app.services.taxonomy._constants import (
    EXCLUDED_STRUCTURAL_STATES,
    GLOBAL_PATTERN_CAP,
    GLOBAL_PATTERN_DEDUP_COSINE,
    GLOBAL_PATTERN_DEMOTION_SCORE,
    GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS,
    GLOBAL_PATTERN_PROMOTION_MIN_SCORE,
    _utcnow,
)

logger = logging.getLogger(__name__)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two L2-normalised vectors."""
    return float(np.dot(a, b))


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------


async def run_global_pattern_phase(
    db: AsyncSession,
    warm_path_age: float,  # noqa: ARG001 — reserved for future gating
) -> dict[str, int]:
    """Run the full global-pattern lifecycle: promote, validate, cap.

    Returns a stats dict with keys: promoted, updated, demoted,
    re_promoted, retired, evicted.
    """
    stats: dict[str, int] = {
        "promoted": 0,
        "updated": 0,
        "demoted": 0,
        "re_promoted": 0,
        "retired": 0,
        "evicted": 0,
    }

    # Step 1: Discover and promote
    promoted, updated = await _discover_promotion_candidates(db)
    stats["promoted"] = promoted
    stats["updated"] = updated

    # Step 2: Validate existing
    demoted, re_promoted, retired = await _validate_existing_patterns(db)
    stats["demoted"] = demoted
    stats["re_promoted"] = re_promoted
    stats["retired"] = retired

    # Step 3: Enforce retention cap
    evicted = await _enforce_retention_cap(db)
    stats["evicted"] = evicted

    await db.flush()
    return stats


# ------------------------------------------------------------------
# Step 1: Sibling discovery + promotion
# ------------------------------------------------------------------


async def _discover_promotion_candidates(db: AsyncSession) -> tuple[int, int]:
    """Find high-impact MetaPatterns, discover siblings, promote or update.

    Returns (newly_promoted, updated_existing).
    """
    promoted = 0
    updated = 0

    # Find candidates: MetaPatterns with sufficient cross-cluster presence
    cand_stmt = select(MetaPattern).where(
        MetaPattern.global_source_count >= GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS,
        MetaPattern.embedding.isnot(None),
    )
    cand_result = await db.execute(cand_stmt)
    candidates = list(cand_result.scalars().all())

    if not candidates:
        return promoted, updated

    # Load all MetaPatterns with embeddings for sibling search
    all_stmt = select(MetaPattern).where(MetaPattern.embedding.isnot(None))
    all_result = await db.execute(all_stmt)
    all_patterns = list(all_result.scalars().all())

    # Build lookup: id -> (pattern, embedding)
    emb_cache: dict[str, np.ndarray] = {}
    for mp in all_patterns:
        emb_cache[mp.id] = np.frombuffer(mp.embedding, dtype=np.float32).copy()

    # Pre-load existing GlobalPatterns for dedup
    gp_stmt = select(GlobalPattern).where(
        GlobalPattern.state.in_(["active", "demoted"]),
        GlobalPattern.embedding.isnot(None),
    )
    gp_result = await db.execute(gp_stmt)
    existing_gps = list(gp_result.scalars().all())
    gp_embs: list[tuple[GlobalPattern, np.ndarray]] = [
        (gp, np.frombuffer(gp.embedding, dtype=np.float32).copy())
        for gp in existing_gps
    ]

    # Track which GlobalPatterns we've already updated in this pass
    # to avoid double-processing when multiple candidates merge into the same GP
    updated_gp_ids: set[str] = set()

    for candidate in candidates:
        cand_emb = emb_cache.get(candidate.id)
        if cand_emb is None:
            continue

        # Find siblings: MetaPatterns with cosine >= threshold
        siblings = []
        for mp in all_patterns:
            if mp.id == candidate.id:
                continue
            mp_emb = emb_cache.get(mp.id)
            if mp_emb is None:
                continue
            if _cosine(cand_emb, mp_emb) >= GLOBAL_PATTERN_DEDUP_COSINE:
                siblings.append(mp)

        # Collect distinct cluster_ids
        all_cluster_ids: set[str] = {candidate.cluster_id}
        for s in siblings:
            all_cluster_ids.add(s.cluster_id)

        # Collect distinct project_ids from optimizations in those clusters
        all_project_ids: set[str] = set()
        for cid in all_cluster_ids:
            pid_stmt = (
                select(Optimization.project_id)
                .where(
                    Optimization.cluster_id == cid,
                    Optimization.project_id.isnot(None),
                )
                .limit(1)
            )
            pid_result = await db.execute(pid_stmt)
            pid = pid_result.scalar_one_or_none()
            if pid:
                all_project_ids.add(pid)

        # Gate: cluster breadth
        if len(all_cluster_ids) < GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS:
            continue

        # Per-cluster score gate
        avg_scores: list[float] = []
        for cid in all_cluster_ids:
            cl_stmt = select(PromptCluster).where(PromptCluster.id == cid)
            cl_result = await db.execute(cl_stmt)
            cluster = cl_result.scalar_one_or_none()
            if (
                cluster
                and cluster.state not in EXCLUDED_STRUCTURAL_STATES
                and (cluster.avg_score or 0) >= GLOBAL_PATTERN_PROMOTION_MIN_SCORE
            ):
                avg_scores.append(cluster.avg_score)

        if len(avg_scores) < GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS:
            continue

        avg_cluster_score = mean(avg_scores)

        # Dedup against existing GlobalPatterns
        dedup_match: GlobalPattern | None = None
        for gp, gp_emb in gp_embs:
            if _cosine(cand_emb, gp_emb) >= GLOBAL_PATTERN_DEDUP_COSINE:
                dedup_match = gp
                break

        if dedup_match:
            if dedup_match.id in updated_gp_ids:
                continue
            updated_gp_ids.add(dedup_match.id)

            # Update existing — union source lists, refresh metadata
            existing_clusters = set(dedup_match.source_cluster_ids or [])
            existing_projects = set(dedup_match.source_project_ids or [])
            merged_clusters = existing_clusters | all_cluster_ids
            merged_projects = existing_projects | all_project_ids

            dedup_match.source_cluster_ids = list(merged_clusters)
            dedup_match.source_project_ids = list(merged_projects)
            dedup_match.cross_project_count = len(merged_projects)
            dedup_match.global_source_count = len(merged_clusters)
            dedup_match.avg_cluster_score = avg_cluster_score
            dedup_match.last_validated_at = _utcnow()
            # Preserve promoted_at
            updated += 1

            _log_event("promoted", dedup_match.id, {
                "action": "updated_existing",
                "cluster_count": len(merged_clusters),
                "project_count": len(merged_projects),
                "avg_score": round(avg_cluster_score, 2),
            })
        else:
            # Create new GlobalPattern
            gp = GlobalPattern(
                pattern_text=candidate.pattern_text,
                embedding=candidate.embedding,
                source_cluster_ids=list(all_cluster_ids),
                source_project_ids=list(all_project_ids),
                cross_project_count=len(all_project_ids),
                global_source_count=len(all_cluster_ids),
                avg_cluster_score=avg_cluster_score,
                state="active",
            )
            db.add(gp)
            promoted += 1

            # Add to dedup cache so later candidates in this pass can match
            gp_embs.append((gp, cand_emb.copy()))

            _log_event("promoted", gp.id, {
                "action": "new",
                "pattern_text": candidate.pattern_text[:80],
                "cluster_count": len(all_cluster_ids),
                "project_count": len(all_project_ids),
                "avg_score": round(avg_cluster_score, 2),
            })

    return promoted, updated


# ------------------------------------------------------------------
# Step 2: Validate existing patterns
# ------------------------------------------------------------------


async def _validate_existing_patterns(db: AsyncSession) -> tuple[int, int, int]:
    """Recompute scores, demote/re-promote/retire as needed.

    Returns (demoted, re_promoted, retired).
    """
    demoted = 0
    re_promoted = 0
    retired = 0

    stmt = select(GlobalPattern).where(
        GlobalPattern.state.in_(["active", "demoted"]),
    )
    result = await db.execute(stmt)
    patterns = list(result.scalars().all())

    now = _utcnow()

    for gp in patterns:
        source_cids = gp.source_cluster_ids or []

        # Recompute avg_cluster_score from live clusters
        live_scores: list[float] = []
        all_archived = bool(source_cids)  # False if no sources (don't retire empty)

        for cid in source_cids:
            cl_stmt = select(PromptCluster).where(PromptCluster.id == cid)
            cl_result = await db.execute(cl_stmt)
            cluster = cl_result.scalar_one_or_none()

            if cluster is None:
                all_archived = False  # missing cluster ≠ archived
                continue

            if cluster.state != "archived":
                all_archived = False

            if (
                cluster.state not in EXCLUDED_STRUCTURAL_STATES
                and cluster.avg_score is not None
            ):
                live_scores.append(cluster.avg_score)

        gp.avg_cluster_score = mean(live_scores) if live_scores else 0.0

        # Demotion: active with score below threshold
        if gp.avg_cluster_score < GLOBAL_PATTERN_DEMOTION_SCORE and gp.state == "active":
            gp.state = "demoted"
            demoted += 1
            _log_event("demoted", gp.id, {
                "avg_score": round(gp.avg_cluster_score, 2),
                "threshold": GLOBAL_PATTERN_DEMOTION_SCORE,
            })

        # Re-promotion: demoted with score recovered
        elif (
            gp.avg_cluster_score >= GLOBAL_PATTERN_PROMOTION_MIN_SCORE
            and gp.state == "demoted"
        ):
            gp.state = "active"
            re_promoted += 1
            _log_event("re_promoted", gp.id, {
                "avg_score": round(gp.avg_cluster_score, 2),
                "threshold": GLOBAL_PATTERN_PROMOTION_MIN_SCORE,
            })

        # Retirement: all source clusters archived AND stale validation
        # Use elif to prevent double-counting (demotion + retirement in same pass)
        elif (
            all_archived
            and source_cids  # must have at least one source
            and gp.last_validated_at
            and (now - gp.last_validated_at) > timedelta(days=30)
        ):
            gp.state = "retired"
            retired += 1
            _log_event("retired", gp.id, {
                "reason": "all_sources_archived",
                "last_validated_days_ago": (now - gp.last_validated_at).days,
            })
            continue  # skip last_validated_at update for retired patterns

        gp.last_validated_at = now

    return demoted, re_promoted, retired


# ------------------------------------------------------------------
# Step 3: Retention cap enforcement
# ------------------------------------------------------------------


async def _enforce_retention_cap(db: AsyncSession) -> int:
    """Evict excess GlobalPatterns when active+demoted exceeds the cap.

    Eviction order: demoted LRU first, then active LRU.
    Returns number evicted.
    """
    count_stmt = (
        select(func.count())
        .select_from(GlobalPattern)
        .where(GlobalPattern.state.in_(["active", "demoted"]))
    )
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    if total <= GLOBAL_PATTERN_CAP:
        return 0

    excess = total - GLOBAL_PATTERN_CAP
    evicted = 0

    # Evict demoted LRU first
    if excess > 0:
        demoted_stmt = (
            select(GlobalPattern)
            .where(GlobalPattern.state == "demoted")
            .order_by(GlobalPattern.last_validated_at.asc())
            .limit(excess)
        )
        demoted_result = await db.execute(demoted_stmt)
        for gp in demoted_result.scalars().all():
            gp.state = "retired"
            evicted += 1
            excess -= 1
            _log_event("retired", gp.id, {"reason": "evicted", "was_state": "demoted"})

    # Then active LRU if still over
    if excess > 0:
        active_stmt = (
            select(GlobalPattern)
            .where(GlobalPattern.state == "active")
            .order_by(GlobalPattern.last_validated_at.asc())
            .limit(excess)
        )
        active_result = await db.execute(active_stmt)
        for gp in active_result.scalars().all():
            gp.state = "retired"
            evicted += 1
            _log_event("retired", gp.id, {"reason": "evicted", "was_state": "active"})

    if evicted:
        logger.info("GlobalPattern retention cap: evicted %d patterns", evicted)

    return evicted


# ------------------------------------------------------------------
# Event logging helper
# ------------------------------------------------------------------


def _log_event(decision: str, pattern_id: str, context: dict) -> None:
    """Log a global_pattern event via TaxonomyEventLogger (best-effort)."""
    try:
        from app.services.taxonomy.event_logger import get_event_logger

        get_event_logger().log_decision(
            path="warm",
            op="global_pattern",
            decision=decision,
            context={"global_pattern_id": pattern_id, **context},
        )
    except RuntimeError:
        pass
