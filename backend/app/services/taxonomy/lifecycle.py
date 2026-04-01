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
import random
from collections import Counter
from datetime import datetime, timezone
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
) -> PromptCluster | None:
    """Combine two sibling PromptClusters into a single survivor.

    The survivor is the node with more members (node_a wins ties).  The loser
    is marked retired and its families are reassigned to the survivor.

    Merged centroid: weighted mean of the two centroids, re-normalised.
    Merged coherence: weighted average of individual coherences.

    Args:
        db: Async DB session.
        node_a: First candidate for merging.
        node_b: Second candidate for merging.
        warm_path_age: Warm-path age (unused directly; reserved for gate).

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

        # Weighted centroid.
        emb_a = np.frombuffer(node_a.centroid_embedding, dtype=np.float32).copy()
        emb_b = np.frombuffer(node_b.centroid_embedding, dtype=np.float32).copy()
        if total > 0:
            merged_centroid = l2_normalize_1d(
                (emb_a * count_a + emb_b * count_b) / total
            )
        else:
            merged_centroid = l2_normalize_1d((emb_a + emb_b) / 2.0)

        # Weighted coherence average.
        coh_a = node_a.coherence or 0.0
        coh_b = node_b.coherence or 0.0
        if total > 0:
            merged_coherence = (coh_a * count_a + coh_b * count_b) / total
        else:
            merged_coherence = (coh_a + coh_b) / 2.0

        # Update survivor.
        survivor.centroid_embedding = merged_centroid.tobytes()
        survivor.member_count = total
        survivor.coherence = merged_coherence

        # Retire the loser — zero out counters to match attempt_retire().
        loser.state = "archived"
        loser.archived_at = datetime.now(timezone.utc)
        loser.member_count = 0
        loser.scored_count = 0
        loser.avg_score = None

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
        if opt_result.rowcount:
            logger.info(
                "merge: reassigned %d optimizations from '%s' to '%s'",
                opt_result.rowcount, loser.label, survivor.label,
            )

        # Move loser's MetaPatterns to survivor (deduplicate via embedding similarity).
        from app.models import MetaPattern

        loser_patterns = (await db.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == loser.id)
        )).scalars().all()
        if loser_patterns:
            from app.services.embedding_service import EmbeddingService
            from app.services.taxonomy.family_ops import merge_meta_pattern

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
                # Random direction on a sphere, fixed radius
                theta = random.uniform(0, 2 * 3.141592653589793)
                phi = random.uniform(0, 3.141592653589793)
                radius = 2.0
                child.umap_x = parent_node.umap_x + radius * np.sin(phi) * np.cos(theta)
                child.umap_y = parent_node.umap_y + radius * np.sin(phi) * np.sin(theta)
                child.umap_z = parent_node.umap_z + radius * np.cos(phi)
                child.cluster_metadata = {"position_source": "interpolated"}

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


async def attempt_retire(
    db: AsyncSession,
    node: PromptCluster,
    warm_path_age: int,
) -> bool:
    """Retire an idle PromptCluster and redistribute its families.

    Families belonging to *node* are reassigned to the first available
    active sibling (same parent_id, state="active", id != node.id).
    If no sibling exists, retirement is skipped.

    Args:
        db: Async DB session.
        node: The node to retire.
        warm_path_age: Warm-path age (reserved for quality gates).

    Returns:
        True if the node was retired, False if skipped (no siblings).
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
            return False

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
            return False

        # Redistribute families to the first sibling.
        target_sibling = siblings[0]
        families_result = await db.execute(
            select(PromptCluster).where(PromptCluster.parent_id == node.id)
        )
        families = families_result.scalars().all()
        for family in families:
            family.parent_id = target_sibling.id
            target_sibling.member_count = (target_sibling.member_count or 0) + 1

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
        if opt_result.rowcount:
            target_sibling.member_count = (
                (target_sibling.member_count or 0) + opt_result.rowcount
            )
            logger.info(
                "retire: reassigned %d optimizations to '%s' (member_count now %d)",
                opt_result.rowcount, target_sibling.label,
                target_sibling.member_count,
            )

        # Mark node as retired and clear stale metrics.
        # Without clearing, archived clusters show phantom member counts
        # and scores in the "all" filter, confusing the UI.
        node.state = "archived"
        node.archived_at = datetime.now(timezone.utc)
        node.member_count = 0
        node.usage_count = 0
        node.avg_score = None
        node.scored_count = 0

        # Remove from embedding index so hot-path assign_cluster doesn't
        # merge new prompts into this archived cluster.
        try:
            from app.services.taxonomy import get_engine
            _engine = get_engine()
            if _engine and _engine.embedding_index:
                _engine.embedding_index.remove(node.id)
        except Exception:
            pass  # non-fatal — index rebuilt on cold path

        await db.flush()
        logger.info(
            "retire: node '%s' (id=%s) retired; %d families moved to '%s' (id=%s)",
            node.label,
            node.id,
            len(families),
            target_sibling.label,
            target_sibling.id,
        )
        return True

    except Exception:
        logger.exception("attempt_retire: unexpected error — skipping retire")
        return False


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
