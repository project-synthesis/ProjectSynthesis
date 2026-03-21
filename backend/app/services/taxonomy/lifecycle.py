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
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PatternFamily, TaxonomyNode
from app.providers.base import LLMProvider
from app.services.taxonomy.clustering import compute_pairwise_coherence, l2_normalize_1d
from app.services.taxonomy.coloring import generate_color
from app.services.taxonomy.labeling import generate_label

logger = logging.getLogger(__name__)

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
) -> TaxonomyNode | None:
    """Create a new candidate TaxonomyNode from a cluster of PatternFamilies.

    Steps:
      1. Compute centroid (mean, L2-normalised).
      2. Compute coherence (mean pairwise cosine similarity).
      3. Generate a label from family intent_labels via Haiku.
      4. Generate a placeholder color (UMAP not run yet — use 0,0,0).
      5. Persist TaxonomyNode (state="candidate").
      6. Link each family to the new node.

    Args:
        db: Async DB session.
        member_cluster_ids: IDs of PatternFamily rows forming this cluster.
        embeddings: Corresponding embedding vectors (same order).
        warm_path_age: Number of warm-path cycles completed (for quality gates).
        provider: LLM provider for label generation (None → fallback label).
        model: Model ID passed to the labeling module.

    Returns:
        The newly created TaxonomyNode, or None on failure.
    """
    if not member_cluster_ids or not embeddings:
        logger.warning("attempt_emerge: empty member_cluster_ids or embeddings — skipping")
        return None

    try:
        centroid = _compute_centroid(embeddings)
        coherence = compute_pairwise_coherence(embeddings)

        # Fetch family intent_labels for label generation.
        result = await db.execute(
            select(PatternFamily).where(PatternFamily.id.in_(member_cluster_ids))
        )
        families = result.scalars().all()
        member_texts = [f.intent_label for f in families if f.intent_label]

        label = await generate_label(
            provider=provider,
            member_texts=member_texts,
            model=model,
        )

        # Placeholder color — UMAP projection not yet available for new nodes.
        color_hex = generate_color(0.0, 0.0, 0.0)

        node = TaxonomyNode(
            label=label,
            centroid_embedding=centroid.tobytes(),
            member_count=len(member_cluster_ids),
            coherence=coherence,
            state="candidate",
            color_hex=color_hex,
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
    node_a: TaxonomyNode,
    node_b: TaxonomyNode,
    warm_path_age: int,
) -> TaxonomyNode | None:
    """Combine two sibling TaxonomyNodes into a single survivor.

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
        The survivor TaxonomyNode, or None on failure.
    """
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

        # Retire the loser.
        loser.state = "retired"
        loser.retired_at = datetime.now(timezone.utc)

        # Reassign loser's families to survivor.
        result = await db.execute(
            select(PatternFamily).where(PatternFamily.parent_id == loser.id)
        )
        loser_families = result.scalars().all()
        for family in loser_families:
            family.parent_id = survivor.id

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
    parent_node: TaxonomyNode,
    child_clusters: list[tuple[list[str], list[np.ndarray]]],
    warm_path_age: int,
    provider: LLMProvider | None,
    model: str,
) -> list[TaxonomyNode]:
    """Split a parent TaxonomyNode into child candidate nodes.

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
        List of newly created child TaxonomyNode objects (may be empty on
        error or if no clusters provided).
    """
    if not child_clusters:
        logger.warning("attempt_split: no child_clusters provided — skipping")
        return []

    created_children: list[TaxonomyNode] = []
    total_moved = 0

    for cluster_ids, embeddings in child_clusters:
        if not cluster_ids or not embeddings:
            continue
        try:
            centroid = _compute_centroid(embeddings)
            coherence = compute_pairwise_coherence(embeddings)

            result = await db.execute(
                select(PatternFamily).where(PatternFamily.id.in_(cluster_ids))
            )
            families = result.scalars().all()
            member_texts = [f.intent_label for f in families if f.intent_label]

            label = await generate_label(
                provider=provider,
                member_texts=member_texts,
                model=model,
            )
            color_hex = generate_color(0.0, 0.0, 0.0)

            child = TaxonomyNode(
                label=label,
                parent_id=parent_node.id,
                centroid_embedding=centroid.tobytes(),
                member_count=len(cluster_ids),
                coherence=coherence,
                state="candidate",
                color_hex=color_hex,
            )
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
    node: TaxonomyNode,
    warm_path_age: int,
) -> bool:
    """Retire an idle TaxonomyNode and redistribute its families.

    Families belonging to *node* are reassigned to the first available
    confirmed sibling (same parent_id, state="confirmed", id != node.id).
    If no sibling exists, retirement is skipped.

    Args:
        db: Async DB session.
        node: The node to retire.
        warm_path_age: Warm-path age (reserved for quality gates).

    Returns:
        True if the node was retired, False if skipped (no siblings).
    """
    try:
        # Guard: root nodes (parent_id=None) must never be retired
        if node.parent_id is None:
            logger.info(
                "retire: root node '%s' (id=%s) cannot be retired",
                node.label,
                node.id,
            )
            return False

        # Find confirmed siblings.
        result = await db.execute(
            select(TaxonomyNode).where(
                TaxonomyNode.parent_id == node.parent_id,
                TaxonomyNode.state == "confirmed",
                TaxonomyNode.id != node.id,
            )
        )
        siblings = result.scalars().all()

        if not siblings:
            logger.info(
                "retire: no confirmed siblings for node '%s' (id=%s) — skipping",
                node.label,
                node.id,
            )
            return False

        # Redistribute families to the first sibling.
        target_sibling = siblings[0]
        families_result = await db.execute(
            select(PatternFamily).where(PatternFamily.parent_id == node.id)
        )
        families = families_result.scalars().all()
        for family in families:
            family.parent_id = target_sibling.id
            target_sibling.member_count = (target_sibling.member_count or 0) + 1

        # Mark node as retired.
        node.state = "retired"
        node.retired_at = datetime.now(timezone.utc)

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
