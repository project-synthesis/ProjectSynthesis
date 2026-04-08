"""Family operations — cluster assignment, meta-pattern extraction/merge,
centroid computation, and breadcrumb walking.

Extracted from engine.py (Task 2.2) to keep engine.py focused on
orchestration (hot/warm/cold paths + read API).

All functions accept explicit dependencies rather than referencing
engine state.  The TaxonomyEngine delegates to these functions from
``process_optimization()`` and ``map_domain()``.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.taxonomy.embedding_index import EmbeddingIndex

import numpy as np
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    MetaPattern,
    Optimization,
    PromptCluster,
)
from app.providers.base import LLMProvider, call_provider_with_retry
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader
from app.services.taxonomy._constants import (
    CROSS_PROJECT_THRESHOLD_BOOST,
    EXCLUDED_STRUCTURAL_STATES,
)
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.projection import interpolate_position
from app.utils.text_cleanup import parse_domain

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — imported from engine for consistency
# ---------------------------------------------------------------------------

# Adaptive merge threshold (replaces static 0.78).
# Empirical analysis: all-MiniLM-L6-v2 pairwise similarity mean=0.27,
# only 4/1711 pairs exceed 0.78 (all duplicates).  Static 0.78 blocks
# all legitimate merges while centroid drift causes mega-cluster snowball.
#
# Formula: BASE + GROWTH_PENALTY * log2(1 + member_count)
# member_count=1 → 0.59, =3 → 0.63, =7 → 0.67, =14 → 0.71, =30 → 0.75
BASE_MERGE_THRESHOLD = 0.55
MERGE_GROWTH_PENALTY = 0.04

# Legacy constant — kept for tests that reference the old static value.
FAMILY_MERGE_THRESHOLD = 0.78

PATTERN_MERGE_THRESHOLD = 0.82

# Task-type mismatch penalty — reduces effective similarity when incoming
# task_type differs from the cluster's.  Soft signal (not a hard block
# like cross-domain prevention) so genuinely related prompts can still
# merge, but mixed-type clusters require higher raw similarity.
TASK_TYPE_MISMATCH_PENALTY = 0.05


def adaptive_merge_threshold(member_count: int) -> float:
    """Compute merge threshold that grows with cluster size.

    Small clusters accept new members easily (base=0.55), while large
    clusters require increasingly similar prompts. Above 15 members,
    quadratic pressure makes merges progressively harder — creating a
    natural equilibrium that prevents centroid-drift mega-clusters.
    """
    base = BASE_MERGE_THRESHOLD + MERGE_GROWTH_PENALTY * math.log2(
        1 + max(member_count, 1)
    )
    # Quadratic size pressure above 15 members
    if member_count > 15:
        size_pressure = 0.02 * ((member_count - 15) / 10) ** 2
        base = min(0.92, base + size_pressure)
    return base

# ---------------------------------------------------------------------------
# Score-weight helpers
# ---------------------------------------------------------------------------


def score_to_centroid_weight(score: float | None) -> float:
    """Convert an optimization score to a centroid influence weight.

    Power-law mapping gives high-scoring prompts outsized centroid influence
    (4.25x range: score 3→0.20 vs score 9→0.85) compared to linear mapping
    (1.37x range).  This ensures cluster centroids track the semantic center
    of quality, not just quantity.

    Used by: hot-path assignment, warm-path reconciliation, cold-path
    reconciliation, and new-cluster creation.  **All call sites must use
    this single formula** to prevent centroid drift on reconciliation.

    Args:
        score: Overall score in [1, 10], or None (defaults to 5.0).

    Returns:
        Weight in [0.2, 1.0].
    """
    return max(0.2, ((score or 5.0) / 10.0) ** 1.5)


# ---------------------------------------------------------------------------
# Score reconciliation helpers
# ---------------------------------------------------------------------------
#
# These functions centralise the running-mean arithmetic for avg_score /
# scored_count so that every mutation path (hot-path assign, merge, retire,
# noise reassignment, leaf split) uses exactly the same formula.


def merge_score_into_cluster(
    cluster: object,
    incoming_score: float | None,
) -> None:
    """Incorporate a single optimization's score into a cluster's running mean.

    Updates ``cluster.avg_score`` and ``cluster.scored_count`` in place.
    No-op if *incoming_score* is None (unscored optimization).

    Used by: ``assign_cluster()`` (hot path) and noise reassignment
    (leaf split).
    """
    if incoming_score is None:
        return
    old_scored: int = cluster.scored_count or 0
    if cluster.avg_score is not None and old_scored > 0:
        cluster.avg_score = round(
            (cluster.avg_score * old_scored + incoming_score) / (old_scored + 1),
            2,
        )
    else:
        cluster.avg_score = round(incoming_score, 2)
    cluster.scored_count = old_scored + 1


def combine_cluster_scores(
    scored_a: int,
    avg_a: float | None,
    scored_b: int,
    avg_b: float | None,
) -> tuple[int, float | None]:
    """Combine scored_count / avg_score from two clusters.

    Returns ``(merged_scored_count, merged_avg_score)``.  Used by
    ``attempt_merge()`` and ``attempt_retire()`` to reconcile scores
    on the survivor / target sibling.
    """
    total_scored = scored_a + scored_b
    if total_scored == 0:
        return (0, None)
    safe_a = avg_a if avg_a is not None else 0.0
    safe_b = avg_b if avg_b is not None else 0.0
    merged_avg = round(
        (safe_a * scored_a + safe_b * scored_b) / total_scored,
        2,
    )
    return (total_scored, merged_avg)


# Warm path operational limits
MAX_META_PATTERNS_PER_EXTRACTION = 5
PROMPT_TRUNCATION_LIMIT = 2000

# ---------------------------------------------------------------------------
# Pydantic schema for extract_meta_patterns structured output
# ---------------------------------------------------------------------------


class _ExtractedPatterns(BaseModel):
    model_config = {"extra": "forbid"}
    patterns: list[str] = PydanticField(
        description=(
            "List of reusable meta-pattern descriptions extracted from the "
            "optimization (max 5)."
        ),
    )


# ---------------------------------------------------------------------------
# Breadcrumb helper
# ---------------------------------------------------------------------------


async def build_breadcrumb(
    db: AsyncSession, node: PromptCluster
) -> list[str]:
    """Walk parent_id chain upward and return labels from root to leaf.

    Args:
        db: Async SQLAlchemy session.
        node: The leaf PromptCluster to start from.

    Returns:
        List of label strings ordered from root to leaf.
    """
    labels: list[str] = []
    current: PromptCluster | None = node
    visited: set[str] = set()  # cycle guard

    while current is not None:
        if current.id in visited:
            logger.warning(
                "Breadcrumb cycle detected at node '%s' (id=%s) — stopping",
                current.label,
                current.id,
            )
            break
        visited.add(current.id)
        labels.append(current.label)

        if current.parent_id is None:
            break

        parent_result = await db.execute(
            select(PromptCluster).where(PromptCluster.id == current.parent_id)
        )
        current = parent_result.scalar_one_or_none()

    # Reverse so list goes root → leaf
    labels.reverse()
    return labels


# ---------------------------------------------------------------------------
# Cluster assignment
# ---------------------------------------------------------------------------


async def _recompute_cluster_task_type(
    db: AsyncSession, cluster: PromptCluster,
) -> None:
    """Recompute cluster task_type as the statistical mode of its members.

    Only updates if the mode has >50% share among members to avoid
    flipping on ambiguous clusters.  Skips domain-state clusters
    (those always keep task_type='general').
    """
    if cluster.state == "domain":
        return

    from sqlalchemy import func as _func

    result = await db.execute(
        select(
            Optimization.task_type,
            _func.count().label("cnt"),
        )
        .where(Optimization.cluster_id == cluster.id)
        .group_by(Optimization.task_type)
        .order_by(_func.count().desc())
    )
    rows = result.all()
    if not rows:
        return

    mode_type, mode_count = rows[0]
    total = sum(r[1] for r in rows)

    # Only update if mode has majority (>50%)
    if mode_type and total > 0 and mode_count / total > 0.5:
        if cluster.task_type != mode_type:
            logger.info(
                "Recomputed cluster '%s' task_type: '%s' → '%s' "
                "(mode=%d/%d members)",
                cluster.label, cluster.task_type, mode_type,
                mode_count, total,
            )
            cluster.task_type = mode_type


async def _get_project_domain_ids(
    db: AsyncSession, project_id: str,
) -> set[str]:
    """Get domain node IDs for a project."""
    result = await db.execute(
        select(PromptCluster.id).where(
            PromptCluster.parent_id == project_id,
            PromptCluster.state == "domain",
        )
    )
    return {row[0] for row in result.all()}


async def _resolve_or_create_domain(
    db: AsyncSession,
    project_id: str | None,
    domain_label: str,
) -> PromptCluster | None:
    """Find or create a domain node under the project for new cluster parenting.

    Search order:
    1. Domain matching *domain_label* under the project
    2. "general" domain under the project
    3. Auto-bootstrap: create a new "general" domain under the project
    """
    if not project_id:
        return None

    # Look for existing domain under this project matching the label
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id == project_id,
            PromptCluster.state == "domain",
            PromptCluster.label == domain_label,
        ).limit(1)
    )
    domain_node = result.scalar_one_or_none()
    if domain_node:
        return domain_node

    # Look for "general" domain under this project
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id == project_id,
            PromptCluster.state == "domain",
            PromptCluster.label == "general",
        ).limit(1)
    )
    general = result.scalar_one_or_none()
    if general:
        return general

    # Auto-bootstrap: create general domain for this project
    new_domain = PromptCluster(
        label="general",
        state="domain",
        domain="general",
        task_type="general",
        member_count=0,
        parent_id=project_id,
    )
    db.add(new_domain)
    await db.flush()
    logger.info(
        "Phase 2A: auto-created 'general' domain under project %s",
        project_id[:8],
    )
    return new_domain


async def assign_cluster(
    db: AsyncSession,
    embedding: np.ndarray,
    label: str,
    domain: str,
    task_type: str,
    overall_score: float | None,
    embedding_index: EmbeddingIndex | None = None,
    project_id: str | None = None,
) -> PromptCluster:
    """Find nearest PromptCluster or create a new one.

    Nearest centroid search with adaptive threshold guard and
    cross-domain merge prevention.  Updates centroid as running mean
    ``(old * n + new) / (n+1)`` on merge.

    ADR-005 Phase 2A: when *project_id* is set, search is two-tiered:
      Tier 1 — only clusters under the project's domain subtree.
      Tier 2 — cross-project fallback with boosted threshold.
    New clusters are parented under the project's domain node.

    Args:
        db: Async SQLAlchemy session.
        embedding: Unit-norm embedding of the raw prompt.
        label: Analyzer intent label.
        domain: Free-text domain string from the analyzer (via domain_raw).
        task_type: Analyzer task type.
        overall_score: Pipeline overall score (may be None).
        embedding_index: Optional embedding index for centroid upsert.
        project_id: Optional project node ID for scoped search.

    Returns:
        Existing (updated) or newly-created PromptCluster.
    """
    _candidates_log: list[dict] = []  # Decision trace

    # ADR-005 Phase 2A: project-scoped candidate loading (Tier 1)
    _candidate_states = ["candidate", "active", "mature", "template"]
    if project_id:
        project_domain_ids = await _get_project_domain_ids(db, project_id)
        if project_domain_ids:
            _cluster_q = await db.execute(
                select(PromptCluster).where(
                    PromptCluster.state.in_(_candidate_states),
                    PromptCluster.parent_id.in_(project_domain_ids),
                )
            )
            clusters = list(_cluster_q.scalars().all())
        else:
            clusters = []
    else:
        _cluster_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.in_(_candidate_states)
            )
        )
        clusters = list(_cluster_q.scalars().all())

    if clusters:
        valid_clusters: list[PromptCluster] = []
        centroids: list[np.ndarray] = []

        for c_row in clusters:
            try:
                c = np.frombuffer(c_row.centroid_embedding, dtype=np.float32)
                if c.shape[0] != embedding.shape[0]:
                    logger.warning(
                        "Skipping cluster '%s' — centroid dim %d != expected %d",
                        c_row.label,
                        c.shape[0],
                        embedding.shape[0],
                    )
                    continue
                centroids.append(c)
                valid_clusters.append(c_row)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping cluster '%s' — corrupt centroid: %s",
                    c_row.label,
                    exc,
                )

        if centroids:
            matches = EmbeddingService.cosine_search(embedding, centroids, top_k=1)
            if matches and matches[0][1] > 0:
                idx, score = matches[0]
                matched = valid_clusters[idx]
                threshold = adaptive_merge_threshold(matched.member_count or 1)

                # Multi-signal assignment score — penalize merges into
                # unhealthy, oversized, or task-mismatched clusters.
                effective_score = score

                # Signal 1: Coherence gate — incoherent clusters shouldn't absorb more
                if matched.coherence is not None and matched.coherence < 0.4:
                    effective_score -= (0.4 - matched.coherence) * 0.3  # max 0.12

                # Signal 2: Output coherence — divergent outputs mean mixed styles
                from app.services.taxonomy.cluster_meta import read_meta as _rm_assign
                _assign_meta = _rm_assign(matched.cluster_metadata)
                _out_coh = _assign_meta.get("output_coherence")
                if _out_coh is not None and _out_coh < 0.35:
                    effective_score -= (0.35 - _out_coh) * 0.4  # max 0.14

                # Signal 3: Task-type mismatch — multiplicative (12% reduction)
                if task_type and matched.task_type and task_type != matched.task_type:
                    effective_score *= 0.88

                _coh_pen = (
                    round((0.4 - (matched.coherence or 1.0)) * 0.3, 4)
                    if matched.coherence is not None and matched.coherence < 0.4
                    else 0.0
                )
                _out_pen = (
                    round((0.35 - (_out_coh or 1.0)) * 0.4, 4)
                    if _out_coh is not None and _out_coh < 0.35
                    else 0.0
                )
                _type_pen = (
                    0.12
                    if (task_type and matched.task_type and task_type != matched.task_type)
                    else 0.0
                )
                _member_ct = matched.member_count or 1
                _size_pressure = round(0.02 * max(0, (_member_ct - 15) / 10) ** 2, 4) if _member_ct > 15 else 0.0
                _candidates_log.append({
                    "id": matched.id,
                    "label": matched.label,
                    "raw_score": round(score, 4),
                    "threshold": round(threshold, 4),
                    "effective_score": round(effective_score, 4),
                    "member_count": matched.member_count or 0,
                    "penalties": {
                        "coherence": _coh_pen,
                        "output_coh": _out_pen,
                        "task_type": _type_pen,
                        "size_pressure": _size_pressure,
                    },
                })

                if effective_score >= threshold:
                    # Cross-domain merge prevention
                    matched_primary, _ = parse_domain(matched.domain)
                    incoming_primary, _ = parse_domain(domain)
                    if matched_primary != incoming_primary:
                        logger.info(
                            "Cross-domain merge prevented: cluster '%s' domain=%s != "
                            "incoming domain=%s (cosine=%.3f). Creating new cluster.",
                            matched.label,
                            matched.domain,
                            domain,
                            score,
                        )
                        if _candidates_log:
                            _candidates_log[-1]["gate"] = "cross_domain"
                        # Fall through to creation
                    else:
                        # Score-weighted centroid: higher-scoring prompts
                        # shift the centroid more than low-scoring ones.
                        # Power-law weight for better score differentiation (4.25x range vs 1.37x).
                        # score 3.0 → 0.20, score 5.0 → 0.35, score 7.0 → 0.59, score 9.0 → 0.85
                        score_weight = score_to_centroid_weight(overall_score)
                        old_centroid = np.frombuffer(
                            matched.centroid_embedding, dtype=np.float32
                        )
                        # Fallback for pre-migration clusters where
                        # weighted_member_sum is 0.0 — use member_count
                        # as the weight denominator.
                        weighted_sum = (
                            getattr(matched, "weighted_member_sum", None)
                            or float(matched.member_count or 1)
                        )
                        new_weighted_sum = weighted_sum + score_weight
                        new_centroid = (
                            old_centroid * weighted_sum
                            + embedding * score_weight
                        ) / new_weighted_sum
                        # Re-normalize to unit norm — running mean drifts
                        # from unit sphere without this (critical for cosine
                        # similarity accuracy on subsequent merges).
                        c_norm = np.linalg.norm(new_centroid)
                        if c_norm > 0:
                            new_centroid = new_centroid / c_norm
                        matched.centroid_embedding = new_centroid.astype(
                            np.float32
                        ).tobytes()
                        matched.member_count = (matched.member_count or 0) + 1
                        matched.weighted_member_sum = new_weighted_sum
                        # NOTE: coherence is intentionally NOT recomputed here.
                        # The warm path reconciliation recomputes it from all
                        # member embeddings.  See engine.py _run_warm_path_inner().

                        # avg_score tracks the running mean over SCORED members
                        # only.  scored_count is the denominator — not
                        # member_count (which includes unscored members and
                        # would dilute the average).
                        merge_score_into_cluster(matched, overall_score)

                        # Update embedding index with new centroid
                        if embedding_index is not None:
                            await embedding_index.upsert(
                                matched.id, new_centroid
                            )

                        # Recompute cluster task_type as majority of members
                        await _recompute_cluster_task_type(db, matched)

                        logger.debug(
                            "Merged into cluster '%s' (cosine=%.3f, effective=%.3f, "
                            "threshold=%.3f, members=%d)",
                            matched.label,
                            score,
                            effective_score,
                            threshold,
                            matched.member_count,
                        )
                        try:
                            get_event_logger().log_decision(
                                path="hot", op="assign", decision="merge_into",
                                cluster_id=matched.id,
                                context={
                                    "candidates": _candidates_log,
                                    "winner_id": matched.id,
                                    "winner_label": matched.label,
                                    "member_count": matched.member_count,
                                    "new_cluster": False,
                                    "prompt_label": label,
                                    "prompt_domain": domain,
                                    "prompt_task_type": task_type,
                                },
                            )
                        except RuntimeError:
                            pass
                        return matched
                else:
                    if _candidates_log:
                        _candidates_log[-1]["gate"] = "below_threshold"
                    logger.debug(
                        "Below adaptive threshold: cluster '%s' "
                        "cosine=%.3f effective=%.3f < threshold=%.3f (members=%d)",
                        matched.label, score, effective_score, threshold,
                        matched.member_count or 0,
                    )

    # ADR-005 Phase 2A: Tier 2 — cross-project fallback with boosted threshold
    if project_id:
        _all_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.in_(_candidate_states)
            )
        )
        all_clusters = list(_all_q.scalars().all())

        # Filter out already-evaluated in-project clusters
        in_project_ids = {c.id for c in clusters}
        cross_project_candidates = [c for c in all_clusters if c.id not in in_project_ids]

        if cross_project_candidates:
            cross_valid: list[PromptCluster] = []
            cross_centroids: list[np.ndarray] = []
            for c_row in cross_project_candidates:
                try:
                    c = np.frombuffer(c_row.centroid_embedding, dtype=np.float32)
                    if c.shape[0] == embedding.shape[0]:
                        cross_centroids.append(c)
                        cross_valid.append(c_row)
                except (ValueError, TypeError):
                    continue

            if cross_centroids:
                cross_matches = EmbeddingService.cosine_search(
                    embedding, cross_centroids, top_k=1
                )
                if cross_matches and cross_matches[0][1] > 0:
                    c_idx, c_score = cross_matches[0]
                    c_matched = cross_valid[c_idx]

                    # Apply same multi-signal penalties as Tier 1
                    from app.services.taxonomy.cluster_meta import read_meta as _rm_assign
                    c_effective = c_score
                    if c_matched.coherence is not None and c_matched.coherence < 0.4:
                        c_effective -= (0.4 - c_matched.coherence) * 0.3
                    _c_meta = _rm_assign(c_matched.cluster_metadata)
                    _c_out_coh = _c_meta.get("output_coherence")
                    if _c_out_coh is not None and _c_out_coh < 0.35:
                        c_effective -= (0.35 - _c_out_coh) * 0.4
                    if task_type and c_matched.task_type and task_type != c_matched.task_type:
                        c_effective *= 0.88

                    c_threshold = (
                        adaptive_merge_threshold(c_matched.member_count or 1)
                        + CROSS_PROJECT_THRESHOLD_BOOST
                    )

                    if c_effective >= c_threshold:
                        # Cross-domain check still applies
                        matched_primary, _ = parse_domain(c_matched.domain)
                        incoming_primary, _ = parse_domain(domain)
                        if matched_primary == incoming_primary:
                            # Cross-project merge accepted — update centroid
                            score_weight = score_to_centroid_weight(overall_score)
                            old_centroid = np.frombuffer(
                                c_matched.centroid_embedding, dtype=np.float32
                            )
                            weighted_sum = (
                                getattr(c_matched, "weighted_member_sum", None)
                                or float(c_matched.member_count or 1)
                            )
                            new_weighted_sum = weighted_sum + score_weight
                            new_centroid = (
                                old_centroid * weighted_sum
                                + embedding * score_weight
                            ) / new_weighted_sum
                            c_norm = np.linalg.norm(new_centroid)
                            if c_norm > 0:
                                new_centroid = new_centroid / c_norm
                            c_matched.centroid_embedding = (
                                new_centroid.astype(np.float32).tobytes()
                            )
                            c_matched.member_count = (c_matched.member_count or 0) + 1
                            c_matched.weighted_member_sum = new_weighted_sum
                            merge_score_into_cluster(c_matched, overall_score)

                            if embedding_index is not None:
                                await embedding_index.upsert(
                                    c_matched.id, new_centroid
                                )
                            await _recompute_cluster_task_type(db, c_matched)

                            logger.info(
                                "Cross-project merge: '%s' (cosine=%.3f, "
                                "boosted_threshold=%.3f)",
                                c_matched.label,
                                c_score,
                                c_threshold,
                            )
                            try:
                                get_event_logger().log_decision(
                                    path="hot",
                                    op="assign",
                                    decision="cross_project_merge",
                                    cluster_id=c_matched.id,
                                    context={
                                        "winner_label": c_matched.label,
                                        "cosine": round(c_score, 4),
                                        "boosted_threshold": round(c_threshold, 4),
                                        "member_count": c_matched.member_count,
                                        "cross_project": True,
                                    },
                                )
                            except RuntimeError:
                                pass
                            return c_matched

    # No match — create new cluster
    # ADR-005 Phase 2A: parent new cluster to project's domain
    if project_id:
        domain_node = await _resolve_or_create_domain(db, project_id, domain)
    else:
        domain_node_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == domain,
            )
        )
        domain_node = domain_node_q.scalar_one_or_none()

    new_cluster = PromptCluster(
        label=label,
        domain=domain,
        task_type=task_type,
        parent_id=domain_node.id if domain_node else None,
        centroid_embedding=embedding.astype(np.float32).tobytes(),
        member_count=1,
        weighted_member_sum=score_to_centroid_weight(overall_score),
        scored_count=1 if overall_score is not None else 0,
        usage_count=0,
        avg_score=overall_score,
    )
    db.add(new_cluster)
    await db.flush()  # populate ID

    # Interpolate UMAP position from positioned siblings (same parent/domain)
    if domain_node is not None:
        sibling_data: list[tuple[np.ndarray, float, float, float]] = []
        for c_row in clusters:
            if (
                c_row.parent_id == domain_node.id
                and c_row.umap_x is not None
                and c_row.umap_y is not None
                and c_row.umap_z is not None
            ):
                try:
                    sib_emb = np.frombuffer(
                        c_row.centroid_embedding, dtype=np.float32
                    )
                    sibling_data.append(
                        (sib_emb, c_row.umap_x, c_row.umap_y, c_row.umap_z)
                    )
                except (ValueError, TypeError) as _sib_exc:
                    logger.warning(
                        "Corrupt sibling centroid for UMAP interpolation, cluster='%s': %s",
                        c_row.label, _sib_exc,
                    )
                    continue

        pos = interpolate_position(embedding, sibling_data)
        if pos is not None:
            new_cluster.umap_x, new_cluster.umap_y, new_cluster.umap_z = pos
            from app.services.taxonomy.cluster_meta import write_meta
            new_cluster.cluster_metadata = write_meta(new_cluster.cluster_metadata, position_source="interpolated")
            logger.debug(
                "Interpolated position for new cluster '%s': (%.2f, %.2f, %.2f)",
                label, pos[0], pos[1], pos[2],
            )

    # Recount domain node's visible members (excludes archived and domain nodes)
    if domain_node is not None:
        from sqlalchemy import func as _func
        count_q = await db.execute(
            select(_func.count()).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
                PromptCluster.domain == domain,
            )
        )
        domain_node.member_count = count_q.scalar() or 0

    # Update embedding index with new centroid
    if embedding_index is not None:
        await embedding_index.upsert(new_cluster.id, embedding)

    logger.debug(
        "Created new PromptCluster: id=%s label='%s' domain=%s parent=%s",
        new_cluster.id,
        label,
        domain,
        domain_node.label if domain_node else None,
    )
    try:
        get_event_logger().log_decision(
            path="hot", op="assign", decision="create_new",
            cluster_id=new_cluster.id,
            context={
                "candidates": _candidates_log,
                "winner_id": None,
                "new_cluster": True,
                "new_label": label,
                "prompt_domain": domain,
                "prompt_task_type": task_type,
                "parent_domain": domain_node.label if domain_node else None,
            },
        )
    except (RuntimeError, NameError):
        pass
    return new_cluster


# ---------------------------------------------------------------------------
# Meta-pattern extraction
# ---------------------------------------------------------------------------


def extract_structural_patterns(
    raw_prompt: str,
    optimized_prompt: str,
) -> list[str]:
    """Extract meta-patterns from structural diff between raw and optimized prompts.

    Zero-LLM alternative to Haiku-based extraction.  Detects formatting
    additions, score dimension improvements, and structural transformations
    using the same regex patterns as :class:`HeuristicScorer`.

    Two detection mechanisms applied in sequence:

    **Mechanism A — Score delta**: Score both prompts on 5 dimensions,
    emit a pattern when improvement crosses a threshold.

    **Mechanism B — Structural regex**: Detect formatting elements
    present in the optimized prompt but absent from the raw prompt.

    Args:
        raw_prompt: Original user prompt text.
        optimized_prompt: Cleaned optimization output.

    Returns:
        List of 1-5 pattern description strings.
    """
    import re

    from app.services.heuristic_scorer import HeuristicScorer

    patterns: list[str] = []

    def _add(text: str) -> None:
        """Append pattern if not a substring duplicate of an existing one."""
        for existing in patterns:
            if text in existing or existing in text:
                return
        if len(patterns) < 5:
            patterns.append(text)

    # --- Mechanism A: Score delta detection ---
    raw_scores = HeuristicScorer.score_prompt(raw_prompt)
    opt_scores = HeuristicScorer.score_prompt(optimized_prompt, original=raw_prompt)

    delta_rules: list[tuple[str, float, str]] = [
        ("structure", 1.5, (
            "Organize prompts with hierarchical headers and numbered "
            "step sequences for clear task decomposition"
        )),
        ("specificity", 2.0, (
            "Add explicit constraints and type-level specifications to "
            "transform vague requests into precise instructions"
        )),
        ("clarity", 1.5, (
            "Simplify sentence structure and eliminate ambiguous "
            "references to improve readability and reduce misinterpretation"
        )),
        ("conciseness", 1.5, (
            "Remove filler phrases and redundant qualifiers to increase "
            "information density without losing essential detail"
        )),
    ]

    for dim, threshold, pattern_text in delta_rules:
        delta = opt_scores.get(dim, 0.0) - raw_scores.get(dim, 0.0)
        if delta >= threshold:
            _add(pattern_text)

    # Faithfulness drop — cautionary pattern
    faith_delta = opt_scores.get("faithfulness", 0.0) - raw_scores.get("faithfulness", 0.0)
    if faith_delta <= -1.5:
        _add(
            "Preserve original intent by anchoring optimizations to the "
            "user's stated requirements and avoiding unsolicited scope expansion"
        )

    # --- Mechanism B: Structural regex detection ---
    re_headers = re.compile(r"(?m)^#{1,6}\s+\S")
    re_lists = re.compile(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S")
    re_xml = re.compile(r"</?[A-Za-z][A-Za-z0-9_-]*\s*/?>")
    re_format = re.compile(r"\b(?:format|schema|json|yaml|xml|csv|markdown)\b", re.IGNORECASE)
    re_examples = re.compile(r"\bfor example\b|\be\.g\.\b|\bsuch as\b|\bexample:", re.IGNORECASE)
    re_modals = re.compile(r"\b(?:must|shall|should)\b", re.IGNORECASE)

    raw_lower = raw_prompt.lower()
    opt_lower = optimized_prompt.lower()

    # Headers added
    if len(re_headers.findall(optimized_prompt)) >= 2 and len(re_headers.findall(raw_prompt)) == 0:
        _add(
            "Use markdown headers to create clear visual hierarchy "
            "and separate distinct sections of the prompt"
        )

    # Lists added
    if len(re_lists.findall(optimized_prompt)) >= 2 and len(re_lists.findall(raw_prompt)) == 0:
        _add(
            "Structure requirements as bulleted or numbered lists to "
            "make individual items scannable and unambiguous"
        )

    # XML tags added
    if len(re_xml.findall(optimized_prompt)) >= 2 and len(re_xml.findall(raw_prompt)) == 0:
        _add(
            "Wrap semantic sections in XML tags to create "
            "machine-parseable boundaries between context, "
            "instructions, and output format"
        )

    # Format keywords added
    if re_format.search(opt_lower) and not re_format.search(raw_lower):
        _add(
            "Specify an explicit output format (JSON schema, YAML "
            "template, or markdown structure) to constrain response shape"
        )

    # Example keywords added
    if re_examples.search(opt_lower) and not re_examples.search(raw_lower):
        _add(
            "Include concrete examples to anchor the expected output "
            "format and reduce interpretation ambiguity"
        )

    # Constraint modals added
    raw_modals = len(re_modals.findall(raw_prompt))
    opt_modals = len(re_modals.findall(optimized_prompt))
    if opt_modals > raw_modals + 1:
        _add(
            "Add modal obligation keywords (must, shall, should) to "
            "enforce non-negotiable requirements"
        )

    # Fallback: always return at least 1 pattern
    if not patterns:
        patterns.append(
            "Apply targeted structural improvements based on the "
            "prompt's weakest quality dimension"
        )

    return patterns


async def extract_meta_patterns(
    opt: Optimization,
    db: AsyncSession,
    provider: LLMProvider | None,
    prompt_loader: PromptLoader,
) -> list[str]:
    """Call Haiku to extract meta-patterns from a completed optimization.

    Renders extract_patterns.md template, calls provider.complete_parsed()
    with _ExtractedPatterns structured output.  Caps at 5 patterns.
    Returns empty list on any error (non-fatal).

    Args:
        opt: Completed Optimization row with prompt text and metadata.
        db: Async SQLAlchemy session (used for taxonomy node lookup).
        provider: LLM provider for Haiku calls. None disables extraction.
        prompt_loader: PromptLoader instance for template rendering.

    Returns:
        List of meta-pattern strings (at most 5).
    """
    if not provider:
        logger.debug("No LLM provider — using structural pattern extraction")
        return extract_structural_patterns(
            raw_prompt=opt.raw_prompt[:PROMPT_TRUNCATION_LIMIT],
            optimized_prompt=(opt.optimized_prompt or "")[:PROMPT_TRUNCATION_LIMIT],
        )

    try:
        # Build taxonomy context string (Spec 7.6)
        taxonomy_context = ""
        if opt.cluster_id:
            try:
                node_result = await db.execute(
                    select(PromptCluster).where(PromptCluster.id == opt.cluster_id)
                )
                tax_node = node_result.scalar_one_or_none()
                if tax_node:
                    breadcrumb = await build_breadcrumb(db, tax_node)
                    taxonomy_context = (
                        f'This prompt belongs to the "{tax_node.label}" pattern cluster '
                        f"({' > '.join(breadcrumb)}).\n"
                    )
            except Exception as ctx_exc:
                logger.warning("Taxonomy context build failed (non-fatal): %s", ctx_exc)

        template = prompt_loader.render(
            "extract_patterns.md",
            {
                "raw_prompt": opt.raw_prompt[:PROMPT_TRUNCATION_LIMIT],
                "optimized_prompt": (opt.optimized_prompt or "")[:PROMPT_TRUNCATION_LIMIT],
                "intent_label": opt.intent_label or "general",
                "domain_raw": opt.domain_raw or opt.domain or "general",
                "strategy_used": opt.strategy_used or "auto",
                "taxonomy_context": taxonomy_context,
            },
        )

        response = await call_provider_with_retry(
            provider,
            model=settings.MODEL_HAIKU,
            system_prompt=(
                "You are a prompt engineering analyst. "
                "Extract reusable meta-patterns."
            ),
            user_message=template,
            output_format=_ExtractedPatterns,
        )

        patterns = [
            str(p) for p in response.patterns if isinstance(p, str)
        ][:MAX_META_PATTERNS_PER_EXTRACTION]
        logger.debug(
            "Haiku returned %d meta-patterns for opt=%s", len(patterns), opt.id
        )
        return patterns

    except Exception as exc:
        logger.warning(
            "Meta-pattern extraction failed (non-fatal) for opt=%s: %s",
            opt.id,
            exc,
        )
        try:
            get_event_logger().log_decision(
                path="hot", op="extract", decision="pattern_extraction_failed",
                optimization_id=opt.id,
                context={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:300],
                },
            )
        except RuntimeError:
            pass
        return []


# ---------------------------------------------------------------------------
# Meta-pattern merge
# ---------------------------------------------------------------------------


async def merge_meta_pattern(
    db: AsyncSession,
    cluster_id: str,
    pattern_text: str,
    embedding_service: EmbeddingService,
) -> bool:
    """Merge a meta-pattern into a cluster — enrich existing or create new.

    Cosine search against existing MetaPatterns for the cluster.  If the
    best match is >= PATTERN_MERGE_THRESHOLD: increment source_count and
    update text if new version is longer.  Otherwise create a new row.

    Args:
        db: Async SQLAlchemy session.
        cluster_id: PromptCluster PK.
        pattern_text: Meta-pattern text extracted by Haiku.
        embedding_service: EmbeddingService for embedding pattern text.

    Returns:
        True if merged into existing pattern, False if new pattern created.
    """
    try:
        result = await db.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == cluster_id)
        )
        existing = result.scalars().all()

        pattern_embedding = await embedding_service.aembed_single(pattern_text)

        if existing:
            embeddings: list[np.ndarray] = []
            for mp in existing:
                if mp.embedding:
                    embeddings.append(
                        np.frombuffer(mp.embedding, dtype=np.float32)
                    )
                else:
                    embeddings.append(
                        np.zeros(embedding_service.dimension, dtype=np.float32)
                    )

            matches = EmbeddingService.cosine_search(
                pattern_embedding, embeddings, top_k=1
            )
            if matches and matches[0][1] >= PATTERN_MERGE_THRESHOLD:
                idx, score = matches[0]
                mp = existing[idx]
                mp.source_count += 1
                if len(pattern_text) > len(mp.pattern_text):
                    mp.pattern_text = pattern_text
                    mp.embedding = pattern_embedding.astype(np.float32).tobytes()
                logger.debug(
                    "Enriched meta-pattern '%s' (cosine=%.3f, count=%d)",
                    mp.pattern_text[:50],
                    score,
                    mp.source_count,
                )
                return True

        # No match — create new MetaPattern
        mp = MetaPattern(
            cluster_id=cluster_id,
            pattern_text=pattern_text,
            embedding=pattern_embedding.astype(np.float32).tobytes(),
            source_count=1,
        )
        db.add(mp)
        logger.debug(
            "Created new MetaPattern for cluster=%s: '%s'",
            cluster_id,
            pattern_text[:50],
        )
        return False

    except Exception as exc:
        logger.warning(
            "Failed to merge meta-pattern into cluster=%s: %s",
            cluster_id,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Pattern centroid computation
# ---------------------------------------------------------------------------


async def compute_pattern_centroid(
    db: AsyncSession, pattern_ids: list[str]
) -> np.ndarray | None:
    """Compute mean centroid of PromptClusters linked via MetaPattern -> PromptCluster.

    Looks up MetaPatterns by ID, gets their cluster_id,
    loads the corresponding PromptCluster centroids, and returns the mean.

    Args:
        db: Async SQLAlchemy session.
        pattern_ids: List of MetaPattern PKs.

    Returns:
        Mean centroid as float32 ndarray, or None if no valid nodes found.
    """
    if not pattern_ids:
        return None

    result = await db.execute(
        select(MetaPattern).where(MetaPattern.id.in_(pattern_ids))
    )
    meta_patterns = result.scalars().all()

    if not meta_patterns:
        return None

    # Collect unique cluster IDs
    cluster_ids = list({mp.cluster_id for mp in meta_patterns if mp.cluster_id})
    if not cluster_ids:
        return None

    # Load clusters referenced by these meta-patterns
    cluster_result = await db.execute(
        select(PromptCluster).where(PromptCluster.id.in_(cluster_ids))
    )
    clusters = cluster_result.scalars().all()

    # Prefer parent (broader topic) centroids when available.
    # Fall back to the cluster's own centroid for root-level clusters
    # so they still contribute to the Bayesian prior.
    parent_ids = list({c.parent_id for c in clusters if c.parent_id})

    # Collect parent centroids
    vecs: list[np.ndarray] = []
    if parent_ids:
        parent_result = await db.execute(
            select(PromptCluster).where(PromptCluster.id.in_(parent_ids))
        )
        for p in parent_result.scalars().all():
            try:
                c = np.frombuffer(p.centroid_embedding, dtype=np.float32)
                vecs.append(c)
            except (ValueError, TypeError) as _pc_exc:
                logger.warning(
                    "Corrupt parent centroid in pattern_centroid, cluster='%s': %s",
                    p.label, _pc_exc,
                )
                continue

    # Root-level clusters (no parent) — use their own centroids
    root_clusters = [c for c in clusters if not c.parent_id]
    for rc in root_clusters:
        try:
            c = np.frombuffer(rc.centroid_embedding, dtype=np.float32)
            vecs.append(c)
        except (ValueError, TypeError) as _rc_exc:
            logger.warning(
                "Corrupt root centroid in pattern_centroid, cluster='%s': %s",
                rc.label, _rc_exc,
            )
            continue

    if not vecs:
        return None

    mean = np.mean(np.stack(vecs, axis=0), axis=0).astype(np.float32)
    norm = np.linalg.norm(mean)
    if norm > 0:
        mean = mean / norm
    return mean
