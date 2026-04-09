"""Lifecycle operations for the Evolutionary Taxonomy Engine.

Implements the four speculative operations that evolve the taxonomy
during warm-path cycles:

  - emerge  — create a new candidate node from clustered families
  - merge   — combine two sibling nodes into one survivor
  - split   — break a node into child candidate nodes
  - retire  — decommission an idle node and redistribute its families

Each operation is quality-gated via is_non_regressive() from the quality
module.  All DB operations are async.

Reference: Spec Sections 3.1–3.5.
"""

from __future__ import annotations

import logging
import math
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster
from app.providers.base import LLMProvider
from app.services.taxonomy.clustering import compute_pairwise_coherence, l2_normalize_1d
from app.services.taxonomy.coloring import generate_color
from app.services.taxonomy.labeling import generate_label
from app.utils.text_cleanup import parse_domain

logger = logging.getLogger(__name__)


from app.services.taxonomy._constants import _utcnow  # noqa: E402


class GuardrailViolationError(RuntimeError):
    """Raised when a lifecycle operation violates domain stability guardrails.

    This exception should never occur in production — it indicates a code
    regression that bypassed the guardrail checks.
    """
    pass


_GUARDRAIL_VIOLATIONS: dict[str, str] = {
    "retire": "Domain nodes cannot be retired — use manual archival",
    "merge": "Domain nodes cannot be auto-merged — requires approval event",
    "color_assign": "Domain colors are pinned — cold path must skip",
}


def _assert_domain_guardrails(operation: str, node: PromptCluster) -> None:
    """Runtime assertion that domain guardrails are enforced.

    Called at the START of every lifecycle mutation. Raises
    GuardrailViolationError if the operation would violate
    domain stability.
    """
    if node.state != "domain":
        return

    if operation in _GUARDRAIL_VIOLATIONS:
        msg = (
            f"GUARDRAIL VIOLATION: {operation} attempted on domain node "
            f"'{node.label}'. {_GUARDRAIL_VIOLATIONS[operation]}"
        )
        logger.critical(msg)
        raise GuardrailViolationError(msg)


# Priority order for operation scheduling (lower value = higher priority).
_PRIORITY: dict[str, int] = {
    "split": 0,
    "emerge": 1,
    "merge": 2,
    "retire": 3,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_centroid(embeddings: list[np.ndarray]) -> np.ndarray:
    """Mean of embeddings, L2-normalised. Returns float32 1-D array."""
    mat = np.stack(embeddings, axis=0).astype(np.float32)
    mean_vec = mat.mean(axis=0)
    return l2_normalize_1d(mean_vec)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def attempt_emerge(
    db: AsyncSession,
    member_cluster_ids: list[str],
    embeddings: list[np.ndarray],
    warm_path_age: int,
    provider: LLMProvider | None,
    model: str,
) -> PromptCluster | None:
    """Create a new candidate PromptCluster from a cluster of PatternFamilies.

    Steps:
      1. Compute centroid (mean, L2-normalised).
      2. Compute coherence (mean pairwise cosine similarity).
      3. Generate a label from family labels via Haiku.
      4. Generate a placeholder color (UMAP not run yet — use 0,0,0).
      5. Persist PromptCluster (state="active").
      6. Link each family to the new node.

    Args:
        db: Async DB session.
        member_cluster_ids: IDs of PromptCluster rows forming this cluster.
        embeddings: Corresponding embedding vectors (same order).
        warm_path_age: Number of warm-path cycles completed (for quality gates).
        provider: LLM provider for label generation (None → fallback label).
        model: Model ID passed to the labeling module.

    Returns:
        The newly created PromptCluster, or None on failure.
    """
    if not member_cluster_ids or not embeddings:
        logger.warning("attempt_emerge: empty member_cluster_ids or embeddings — skipping")
        return None

    try:
        centroid = _compute_centroid(embeddings)
        coherence = compute_pairwise_coherence(embeddings)

        # Fetch family labels for label generation.
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.id.in_(member_cluster_ids))
        )
        families = result.scalars().all()
        member_texts = [f.label for f in families if f.label]

        label = await generate_label(
            provider=provider,
            member_texts=member_texts,
            model=model,
        )

        # Inherit domain from the majority of member clusters.
        # parse_domain() lowercases to match domain node labels.
        member_domains = [parse_domain(f.domain)[0] for f in families if f.domain]
        domain_counts = Counter(member_domains)
        inherited_domain = domain_counts.most_common(1)[0][0] if domain_counts else "general"

        # Placeholder color — UMAP projection not yet available for new nodes.
        color_hex = generate_color(0.0, 0.0, 0.0)

        node = PromptCluster(
            label=label,
            centroid_embedding=centroid.tobytes(),
            member_count=len(member_cluster_ids),
            coherence=coherence,
            state="active",
            color_hex=color_hex,
            domain=inherited_domain,
        )
        db.add(node)
        await db.flush()  # obtain node.id before linking families

        # Link families to this node.
        for family in families:
            family.parent_id = node.id

        await db.flush()
        logger.info(
            "emerge: created candidate node '%s' (id=%s, members=%d, coherence=%.3f)",
            label,
            node.id,
            node.member_count,
            coherence,
        )
        return node

    except Exception:
        logger.exception("attempt_emerge: unexpected error — skipping emerge")
        return None


async def attempt_merge(
    db: AsyncSession,
    node_a: PromptCluster,
    node_b: PromptCluster,
    warm_path_age: int,
    embedding_svc: object | None = None,
) -> PromptCluster | None:
    """Combine two sibling PromptClusters into a single survivor.

    The survivor is the node with more members (node_a wins ties).  The loser
    is marked retired and its families are reassigned to the survivor.

    Merged centroid: weighted mean of the two centroids, re-normalised.
    Merged coherence: weighted average of individual coherences.
    Merged scored_count/avg_score: combined from both nodes immediately
    (not deferred to warm-path reconciliation).

    Args:
        db: Async DB session.
        node_a: First candidate for merging.
        node_b: Second candidate for merging.
        warm_path_age: Warm-path age (unused directly; reserved for gate).
        embedding_svc: EmbeddingService instance for meta-pattern dedup.
            Falls back to a new instance if not provided.

    Returns:
        The survivor PromptCluster, or None on failure.
    """
    _assert_domain_guardrails("merge", node_a)
    _assert_domain_guardrails("merge", node_b)
    try:
        # Guard: self-merge is a no-op that would double member_count
        if node_a.id == node_b.id:
            logger.warning("attempt_merge: self-merge rejected (id=%s)", node_a.id)
            return None

        count_a = node_a.member_count or 0
        count_b = node_b.member_count or 0
        total = count_a + count_b

        # Determine survivor (more members wins; node_a wins ties).
        if count_b > count_a:
            survivor, loser = node_b, node_a
        else:
            survivor, loser = node_a, node_b

        # Weighted centroid — use weighted_member_sum (score-weighted) rather
        # than raw member_count.  Each centroid is already a score-weighted
        # mean of its members, so the correct blend weight is the total
        # score-weight that produced it (weighted_member_sum), not the
        # unweighted count.  Falls back to member_count for pre-migration
        # clusters where weighted_member_sum is 0.0 or None.
        emb_a = np.frombuffer(node_a.centroid_embedding, dtype=np.float32).copy()
        emb_b = np.frombuffer(node_b.centroid_embedding, dtype=np.float32).copy()
        wms_a = node_a.weighted_member_sum or float(count_a) or 1.0
        wms_b = node_b.weighted_member_sum or float(count_b) or 1.0
        wms_total = wms_a + wms_b
        merged_centroid = l2_normalize_1d(
            (emb_a * wms_a + emb_b * wms_b) / wms_total
        )

        # Weighted coherence average (member_count is fine here — coherence
        # is per-member, not score-dependent).
        coh_a = node_a.coherence or 0.0
        coh_b = node_b.coherence or 0.0
        if total > 0:
            merged_coherence = (coh_a * count_a + coh_b * count_b) / total
        else:
            merged_coherence = (coh_a + coh_b) / 2.0

        # Reconcile scored_count and avg_score from both nodes.
        # Without this, the survivor retains only its own pre-merge
        # scores and the loser's contribution is lost until the next
        # warm-path reconciliation cycle.
        from app.services.taxonomy.family_ops import combine_cluster_scores
        merged_scored, merged_avg = combine_cluster_scores(
            node_a.scored_count or 0, node_a.avg_score,
            node_b.scored_count or 0, node_b.avg_score,
        )

        # Update survivor.
        survivor.centroid_embedding = merged_centroid.tobytes()
        survivor.member_count = total
        survivor.weighted_member_sum = wms_total
        survivor.coherence = merged_coherence
        survivor.scored_count = merged_scored
        survivor.avg_score = merged_avg

        # Retire the loser — zero out ALL counters to prevent phantom data.
        # Must match the fields cleared by _archive_cluster() and attempt_retire().
        loser.state = "archived"
        loser.archived_at = _utcnow()
        loser.member_count = 0
        loser.weighted_member_sum = 0.0
        loser.scored_count = 0
        loser.avg_score = None
        loser.usage_count = 0

        # Reassign loser's families to survivor.
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.parent_id == loser.id)
        )
        loser_families = result.scalars().all()
        for family in loser_families:
            family.parent_id = survivor.id

        # Reassign loser's Optimizations to survivor.
        from sqlalchemy import update

        from app.models import Optimization

        opt_result = await db.execute(
            update(Optimization)
            .where(Optimization.cluster_id == loser.id)
            .values(cluster_id=survivor.id)
        )

        # Synchronize domain for cross-domain merges.  Loser and survivor
        # are usually same-domain siblings, but edge cases (e.g., manual
        # reassignment, domain reclassification) can leave stale domains.
        loser_primary, _ = parse_domain(loser.domain)
        survivor_primary, _ = parse_domain(survivor.domain)
        domain_migrated = 0
        if loser_primary != survivor_primary and survivor.domain:
            _dm = await db.execute(
                update(Optimization)
                .where(
                    Optimization.cluster_id == survivor.id,
                    Optimization.domain == loser.domain,
                )
                .values(domain=survivor.domain)
            )
            domain_migrated = _dm.rowcount

        # Atomically migrate OptimizationPattern join records to survivor.
        # Without this, OP records become stale (pointing to archived loser),
        # causing prompts to vanish from cluster detail views.
        from app.models import OptimizationPattern

        op_result = await db.execute(
            update(OptimizationPattern)
            .where(OptimizationPattern.cluster_id == loser.id)
            .values(cluster_id=survivor.id)
        )

        if opt_result.rowcount:
            logger.info(
                "merge: reassigned %d optimizations + %d OP records from '%s' to '%s'%s",
                opt_result.rowcount, op_result.rowcount, loser.label, survivor.label,
                f" (domain migrated: {domain_migrated})" if domain_migrated else "",
            )

        # Move loser's MetaPatterns to survivor (deduplicate via embedding similarity).
        from app.models import MetaPattern

        loser_patterns = (await db.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == loser.id)
        )).scalars().all()
        if loser_patterns:
            from app.services.taxonomy.family_ops import merge_meta_pattern

            if embedding_svc is None:
                from app.services.embedding_service import EmbeddingService
                embedding_svc = EmbeddingService()
            moved = 0
            for mp in loser_patterns:
                try:
                    await merge_meta_pattern(
                        db, survivor.id, mp.pattern_text, embedding_svc,
                    )
                    moved += 1
                except Exception:
                    pass  # non-fatal per pattern
                await db.delete(mp)
            logger.info("merge: moved %d meta-patterns to survivor", moved)

        # Mark survivor as pattern-stale — merged population needs re-extraction
        from app.services.taxonomy.cluster_meta import write_meta as _wm

        survivor.cluster_metadata = _wm(
            survivor.cluster_metadata, pattern_stale=True,
        )

        await db.flush()
        logger.info(
            "merge: '%s' (id=%s) absorbed '%s' (id=%s) → %d members",
            survivor.label,
            survivor.id,
            loser.label,
            loser.id,
            total,
        )
        return survivor

    except Exception:
        logger.exception("attempt_merge: unexpected error — skipping merge")
        return None


async def attempt_split(
    db: AsyncSession,
    parent_node: PromptCluster,
    child_clusters: list[tuple[list[str], list[np.ndarray]]],
    warm_path_age: int,
    provider: LLMProvider | None,
    model: str,
) -> list[PromptCluster]:
    """Split a parent PromptCluster into child candidate nodes.

    Each element of *child_clusters* is a ``(cluster_ids, embeddings)`` tuple
    produced by the clustering module.  A new candidate child node is created
    for each cluster, inheriting the parent's id as ``parent_id``.  The
    parent's ``member_count`` is decremented by the number of members moved.

    Args:
        db: Async DB session.
        parent_node: The node being split.
        child_clusters: List of ``(cluster_ids, embeddings)`` tuples.
        warm_path_age: Warm-path age (reserved for quality gates).
        provider: LLM provider for child label generation.
        model: Model ID passed to the labeling module.

    Returns:
        List of newly created child PromptCluster objects (may be empty on
        error or if no clusters provided).
    """
    if not child_clusters:
        logger.warning("attempt_split: no child_clusters provided — skipping")
        return []

    created_children: list[PromptCluster] = []
    total_moved = 0

    for cluster_ids, embeddings in child_clusters:
        if not cluster_ids or not embeddings:
            continue
        try:
            centroid = _compute_centroid(embeddings)
            coherence = compute_pairwise_coherence(embeddings)

            result = await db.execute(
                select(PromptCluster).where(PromptCluster.id.in_(cluster_ids))
            )
            families = result.scalars().all()
            member_texts = [f.label for f in families if f.label]

            label = await generate_label(
                provider=provider,
                member_texts=member_texts,
                model=model,
            )
            color_hex = generate_color(0.0, 0.0, 0.0)

            # Inherit domain from the parent: domain-state nodes use their
            # label as the domain value; all other nodes pass the domain field.
            child_domain = (
                parent_node.label
                if parent_node.state == "domain"
                else parent_node.domain
            )

            child = PromptCluster(
                label=label,
                parent_id=parent_node.id,
                centroid_embedding=centroid.tobytes(),
                member_count=len(cluster_ids),
                coherence=coherence,
                state="active",
                color_hex=color_hex,
                domain=child_domain,
            )

            # Interpolate position from parent + radial offset (2.0 units)
            if (
                parent_node.umap_x is not None
                and parent_node.umap_y is not None
                and parent_node.umap_z is not None
            ):
                # Split children positioned near parent (not sibling-weighted) because
                # they emerge from decomposing the parent — proximity to parent preserves
                # the visual continuity of the split operation.
                # Random direction on a sphere, fixed radius
                theta = random.uniform(0, 2 * math.pi)
                phi = random.uniform(0, math.pi)
                radius = 2.0
                child.umap_x = parent_node.umap_x + radius * np.sin(phi) * np.cos(theta)
                child.umap_y = parent_node.umap_y + radius * np.sin(phi) * np.sin(theta)
                child.umap_z = parent_node.umap_z + radius * np.cos(phi)
                from app.services.taxonomy.cluster_meta import write_meta
                child.cluster_metadata = write_meta(child.cluster_metadata, position_source="interpolated")

            db.add(child)
            await db.flush()

            for family in families:
                family.parent_id = child.id

            await db.flush()
            created_children.append(child)
            total_moved += len(cluster_ids)

            logger.info(
                "split: created child '%s' (id=%s, members=%d) under parent '%s'",
                label,
                child.id,
                child.member_count,
                parent_node.label,
            )
        except Exception:
            logger.exception(
                "attempt_split: error creating child cluster — skipping this cluster"
            )

    # Reduce parent's member count by the number of members moved out.
    if total_moved > 0:
        parent_node.member_count = max(0, (parent_node.member_count or 0) - total_moved)
        await db.flush()

    return created_children


@dataclass
class RetireResult:
    """Result of an attempt_retire() call with full context for observability."""

    success: bool
    sibling_target_id: str | None = None
    sibling_label: str | None = None
    families_reparented: int = 0
    optimizations_reassigned: int = 0


async def attempt_retire(
    db: AsyncSession,
    node: PromptCluster,
    warm_path_age: int,
) -> RetireResult:
    """Retire an idle PromptCluster and redistribute its families.

    Families belonging to *node* are reassigned to the first available
    active sibling (same parent_id, state="active", id != node.id).
    If no sibling exists, retirement is skipped.

    Args:
        db: Async DB session.
        node: The node to retire.
        warm_path_age: Warm-path age (reserved for quality gates).

    Returns:
        RetireResult with success flag and context for observability.
    """
    _assert_domain_guardrails("retire", node)
    try:
        # Guard: root nodes (parent_id=None) must never be retired
        if node.parent_id is None:
            logger.info(
                "retire: root node '%s' (id=%s) cannot be retired",
                node.label,
                node.id,
            )
            return RetireResult(success=False)

        # Find active siblings.
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == node.parent_id,
                PromptCluster.state == "active",
                PromptCluster.id != node.id,
            )
        )
        siblings = result.scalars().all()

        if not siblings:
            logger.info(
                "retire: no active siblings for node '%s' (id=%s) — skipping",
                node.label,
                node.id,
            )
            return RetireResult(success=False)

        # Redistribute child clusters (families) to the first sibling.
        # NOTE: Do NOT increment target_sibling.member_count here — member_count
        # tracks Optimization rows (not child clusters).  Only the optimization
        # reassignment below should update member_count.
        target_sibling = siblings[0]
        families_result = await db.execute(
            select(PromptCluster).where(PromptCluster.parent_id == node.id)
        )
        families = families_result.scalars().all()
        families_moved = 0
        for family in families:
            family.parent_id = target_sibling.id
            families_moved += 1
        if families_moved:
            logger.info(
                "retire: re-parented %d child clusters to '%s'",
                families_moved, target_sibling.label,
            )

        # Reassign optimizations that still reference the retiring node.
        # Without this, Optimization.cluster_id becomes a stale pointer
        # to an archived cluster and is never repaired.
        from sqlalchemy import update as sa_update

        from app.models import Optimization

        opt_result = await db.execute(
            sa_update(Optimization)
            .where(Optimization.cluster_id == node.id)
            .values(cluster_id=target_sibling.id)
        )

        # Synchronize domain for cross-domain retirements (same pattern as merge).
        node_primary, _ = parse_domain(node.domain)
        target_primary, _ = parse_domain(target_sibling.domain)
        domain_migrated = 0
        if node_primary != target_primary and target_sibling.domain:
            _dm = await db.execute(
                sa_update(Optimization)
                .where(
                    Optimization.cluster_id == target_sibling.id,
                    Optimization.domain == node.domain,
                )
                .values(domain=target_sibling.domain)
            )
            domain_migrated = _dm.rowcount

        # Atomically migrate OP records (same fix as attempt_merge)
        from app.models import OptimizationPattern
        await db.execute(
            sa_update(OptimizationPattern)
            .where(OptimizationPattern.cluster_id == node.id)
            .values(cluster_id=target_sibling.id)
        )
        if opt_result.rowcount:
            target_sibling.member_count = (
                (target_sibling.member_count or 0) + opt_result.rowcount
            )
            # Transfer weighted_member_sum from retiring node to sibling
            target_sibling.weighted_member_sum = (
                (target_sibling.weighted_member_sum or 0.0)
                + (node.weighted_member_sum or 0.0)
            )
            # Reconcile scored_count and avg_score on the target sibling
            # immediately — don't defer to warm-path reconciliation.
            from app.services.taxonomy.family_ops import combine_cluster_scores
            merged_scored, merged_avg = combine_cluster_scores(
                target_sibling.scored_count or 0, target_sibling.avg_score,
                node.scored_count or 0, node.avg_score,
            )
            target_sibling.scored_count = merged_scored
            target_sibling.avg_score = merged_avg
            logger.info(
                "retire: reassigned %d optimizations to '%s' (member_count now %d)%s",
                opt_result.rowcount, target_sibling.label,
                target_sibling.member_count,
                f" (domain migrated: {domain_migrated})" if domain_migrated else "",
            )

        # Clean up orphaned meta-patterns — archived clusters don't participate
        # in pattern injection or matching, so their patterns are dead weight.
        from app.models import MetaPattern

        orphan_patterns = (await db.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == node.id)
        )).scalars().all()
        for mp in orphan_patterns:
            await db.delete(mp)
        if orphan_patterns:
            logger.info("retire: deleted %d orphaned meta-patterns", len(orphan_patterns))

        # Mark target sibling as pattern-stale (inherited members need re-extraction)
        if opt_result.rowcount:
            from app.services.taxonomy.cluster_meta import write_meta as _wm

            target_sibling.cluster_metadata = _wm(
                target_sibling.cluster_metadata, pattern_stale=True,
            )

        # Mark node as retired and clear stale metrics.
        # Without clearing, archived clusters show phantom member counts
        # and scores in the "all" filter, confusing the UI.
        node.state = "archived"
        node.archived_at = _utcnow()
        node.member_count = 0
        node.weighted_member_sum = 0.0
        node.usage_count = 0
        node.avg_score = None
        node.scored_count = 0

        # NOTE: Embedding index removal is handled by the caller
        # (engine.py _run_warm_path_inner) after this function returns
        # True.  Removing here via get_engine() would be a redundant
        # double-removal and breaks the dependency injection pattern.

        await db.flush()
        logger.info(
            "retire: node '%s' (id=%s) retired; %d families moved to '%s' (id=%s)",
            node.label,
            node.id,
            len(families),
            target_sibling.label,
            target_sibling.id,
        )
        return RetireResult(
            success=True,
            sibling_target_id=target_sibling.id,
            sibling_label=target_sibling.label,
            families_reparented=families_moved,
            optimizations_reassigned=opt_result.rowcount,
        )

    except Exception:
        logger.exception("attempt_retire: unexpected error — skipping retire")
        return RetireResult(success=False)


def prioritize_operations(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort lifecycle operations by execution priority.

    Priority order (lowest value = highest priority):
      split (0) > emerge (1) > merge (2) > retire (3)

    Unknown operation types are sorted to the end (priority = 999).

    Args:
        ops: List of operation dicts, each containing at least a ``"type"`` key.

    Returns:
        New sorted list; original list is not modified.
    """
    return sorted(ops, key=lambda op: _PRIORITY.get(op.get("type", ""), 999))
