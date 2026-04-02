"""Cold-path implementation — full HDBSCAN refit + UMAP 3D projection + OKLab
coloring for the Evolutionary Taxonomy Engine.

The cold path is the "defrag" operation: it reclusters all non-domain,
non-archived PromptCluster centroids via HDBSCAN, updates or creates cluster
nodes, runs UMAP 3D projection with Procrustes alignment, regenerates OKLab
colors, reconciles member_count / avg_score / coherence from Optimization
rows, and creates an audit snapshot.

Key improvements over the original engine._run_cold_path_inner():
  - Fix #5:  Archived clusters excluded from HDBSCAN input (original used
             ``state != "domain"`` which includes archived clusters).
  - Fix #6:  Mature/template states included in existing-node matching
             (original used ``state.in_(["active", "candidate"])``).
  - Fix #14: Reset ``split_failures`` metadata on matched nodes after refit.
  - NEW:     Quality gate via ``is_cold_path_non_regressive()`` — bad refits
             are rolled back instead of committed unconditionally.

This module receives ``engine`` and ``db`` as parameters. It NEVER imports
TaxonomyEngine at runtime (TYPE_CHECKING only) to avoid circular imports.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Optimization, PromptCluster
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.clustering import (
    batch_cluster,
    compute_pairwise_coherence,
    cosine_similarity,
    l2_normalize_1d,
)
from app.services.taxonomy.coloring import enforce_minimum_delta_e, generate_color
from app.services.taxonomy.family_ops import adaptive_merge_threshold
from app.services.taxonomy.labeling import generate_label
from app.services.taxonomy.projection import UMAPProjector, procrustes_align
from app.services.taxonomy.quality import is_cold_path_non_regressive
from app.services.taxonomy.snapshot import create_snapshot

if TYPE_CHECKING:
    from app.services.taxonomy.engine import TaxonomyEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ColdPathResult:
    """Return value from execute_cold_path().

    Extended from the original engine.ColdPathResult with quality-gate
    fields (q_before, q_after, accepted) that enable the caller to
    distinguish accepted refits from rolled-back ones.
    """

    snapshot_id: str
    q_before: float | None
    q_after: float | None
    accepted: bool
    nodes_created: int
    nodes_updated: int
    umap_fitted: bool
    q_system: float | None = None  # Backward compat with engine.ColdPathResult

    def __post_init__(self) -> None:
        if self.q_system is None and self.q_after is not None:
            self.q_system = self.q_after


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def execute_cold_path(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> ColdPathResult:
    """Execute the cold path — full HDBSCAN refit + UMAP + OKLab coloring.

    This is the extracted implementation of ``engine._run_cold_path_inner()``,
    with quality-gate protection: if Q_after regresses beyond the cold-path
    epsilon tolerance, the transaction is rolled back and the refit is rejected.

    Args:
        engine: The TaxonomyEngine instance (used for helper methods and state).
        db: AsyncSession — caller manages the outer lock; this function manages
            commit/rollback via the quality gate.

    Returns:
        ColdPathResult with quality metrics and acceptance status.
    """
    nodes_created = 0
    nodes_updated = 0

    # ------------------------------------------------------------------
    # Step 1: Compute Q_before from current active nodes
    # ------------------------------------------------------------------
    q_before_result = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
    )
    q_before_nodes = list(q_before_result.scalars().all())
    q_before = engine._compute_q_from_nodes(q_before_nodes)

    # ------------------------------------------------------------------
    # Step 2: Load non-domain, non-archived clusters for HDBSCAN input
    # Fix #5: Original used `state != "domain"` which included archived
    #         clusters. Archived clusters should not participate in HDBSCAN.
    # ------------------------------------------------------------------
    fam_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(["domain", "archived"])
        )
    )
    families = list(fam_result.scalars().all())

    # Step 3: Extract valid centroid embeddings
    embeddings: list[np.ndarray] = []
    valid_families: list[PromptCluster] = []
    family_by_id: dict[str, PromptCluster] = {}
    for f in families:
        try:
            emb = np.frombuffer(f.centroid_embedding, dtype=np.float32)
            embeddings.append(emb)
            valid_families.append(f)
            family_by_id[f.id] = f
        except (ValueError, TypeError):
            logger.warning(
                "Skipping cluster '%s' -- corrupt centroid", f.label
            )

    # Step 4: Early return if < 3 valid embeddings
    if len(embeddings) < 3:
        snap = await create_snapshot(
            db,
            trigger="cold_path",
            q_system=0.0,
            q_coherence=0.0,
            q_separation=0.0,
            q_coverage=0.0,
            nodes_created=0,
        )
        return ColdPathResult(
            snapshot_id=snap.id,
            q_before=q_before,
            q_after=0.0,
            accepted=True,
            nodes_created=0,
            nodes_updated=0,
            umap_fitted=False,
        )

    # ------------------------------------------------------------------
    # Step 5: Run HDBSCAN clustering
    # ------------------------------------------------------------------
    cluster_result = batch_cluster(embeddings, min_cluster_size=3)

    # ------------------------------------------------------------------
    # Step 6: Load existing nodes for matching
    # Fix #6: Original used `state.in_(["active", "candidate"])` which
    #         missed mature/template nodes. All non-domain, non-archived
    #         nodes should be matchable.
    # ------------------------------------------------------------------
    existing_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.notin_(["domain", "archived"])
        )
    )
    existing_nodes = {n.id: n for n in existing_result.scalars().all()}

    node_embeddings: list[np.ndarray] = []
    all_nodes: list[PromptCluster] = []

    # ------------------------------------------------------------------
    # Steps 7-10: Process each HDBSCAN cluster
    # ------------------------------------------------------------------
    for cid in range(cluster_result.n_clusters):
        mask = cluster_result.labels == cid
        cluster_fam_ids = [
            valid_families[i].id for i in range(len(valid_families)) if mask[i]
        ]
        cluster_embs = [
            embeddings[i] for i in range(len(embeddings)) if mask[i]
        ]

        if not cluster_embs:
            continue

        # Compute centroid for this HDBSCAN cluster
        centroid = (
            cluster_result.centroids[cid]
            if cid < len(cluster_result.centroids)
            else None
        )
        if centroid is None:
            centroid = l2_normalize_1d(
                np.mean(np.stack(cluster_embs, axis=0), axis=0).astype(np.float32)
            )

        coherence = compute_pairwise_coherence(cluster_embs)

        # Step 7: Try to match existing node by cosine similarity >= adaptive threshold
        matched_node = None
        if existing_nodes:
            best_match_id = None
            best_sim = -1.0
            for nid, existing in existing_nodes.items():
                try:
                    ex_emb = np.frombuffer(
                        existing.centroid_embedding, dtype=np.float32
                    )
                    sim = cosine_similarity(centroid, ex_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_match_id = nid
                except (ValueError, TypeError):
                    continue

            if best_match_id:
                candidate = existing_nodes[best_match_id]
                cold_threshold = adaptive_merge_threshold(
                    candidate.member_count or 1,
                )
                if best_sim >= cold_threshold:
                    matched_node = existing_nodes.pop(best_match_id)

        if matched_node:
            # Step 8: Update existing node — preserve higher lifecycle states.
            # Without this guard, a mature/template cluster matched by
            # HDBSCAN would be demoted back to "active".
            matched_node.centroid_embedding = centroid.astype(
                np.float32
            ).tobytes()
            # Do NOT set member_count from HDBSCAN group size --
            # HDBSCAN groups PromptCluster nodes, not Optimizations.
            # member_count is reconciled from Optimization rows below.
            matched_node.coherence = coherence
            if matched_node.state == "candidate":
                matched_node.state = "active"
            # mature, template, active -- keep as-is

            # Fix #14: Reset split_failures metadata after successful refit.
            # After HDBSCAN refit, the cluster's composition has changed so
            # previous split failure history is no longer relevant.
            meta = read_meta(matched_node.cluster_metadata)
            if meta.get("split_failures", 0) > 0:
                matched_node.cluster_metadata = write_meta(
                    matched_node.cluster_metadata,
                    split_failures=0,
                )

            nodes_updated += 1
            node = matched_node
        else:
            # Step 9: Create new node
            member_texts = [
                f.label
                for f in valid_families
                if f.id in set(cluster_fam_ids) and f.label
            ]
            label = await generate_label(
                provider=engine._provider,
                member_texts=member_texts,
                model=settings.MODEL_HAIKU,
            )
            node = PromptCluster(
                label=label,
                centroid_embedding=centroid.astype(np.float32).tobytes(),
                member_count=0,  # reconciled from Optimization rows below
                coherence=coherence,
                state="active",
                color_hex=generate_color(0.0, 0.0, 0.0),
            )
            db.add(node)
            await db.flush()
            nodes_created += 1

        # Step 10: Link families to this node (skip self-references)
        for fid in cluster_fam_ids:
            fam = family_by_id.get(fid)
            if fam and fam.id != node.id:
                fam.parent_id = node.id

        node_embeddings.append(centroid)
        all_nodes.append(node)

    # ------------------------------------------------------------------
    # Step 11: Include leftover unmatched nodes for UMAP coordinates
    # ------------------------------------------------------------------
    # Unmatched nodes that HDBSCAN did not absorb still need UMAP
    # coordinates. Do NOT reassign their optimizations -- HDBSCAN operates
    # on cluster centroids, not individual optimizations.
    for leftover_node in existing_nodes.values():
        if leftover_node.centroid_embedding:
            try:
                emb = np.frombuffer(
                    leftover_node.centroid_embedding, dtype=np.float32,
                ).copy()
                node_embeddings.append(emb)
                all_nodes.append(leftover_node)
            except (ValueError, TypeError):
                pass  # skip corrupt embeddings

    # ------------------------------------------------------------------
    # Step 12: Restore domain->cluster parent_id links
    # ------------------------------------------------------------------
    # HDBSCAN may have set parent_ids to HDBSCAN group leaders or
    # created self-references. Every non-domain cluster must have
    # parent_id pointing to its domain node (looked up by domain field).
    domain_node_map: dict[str, str] = {}  # domain_label -> domain_node_id
    domain_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state == "domain")
    )
    for dn in domain_q.scalars().all():
        domain_node_map[dn.label] = dn.id

    parent_repairs = 0
    for node in all_nodes:
        if node.state == "domain":
            continue
        correct_parent = domain_node_map.get(node.domain)
        if correct_parent and node.parent_id != correct_parent:
            node.parent_id = correct_parent
            parent_repairs += 1
        elif not correct_parent and node.parent_id != domain_node_map.get("general"):
            # Fallback: unknown domain -> general
            node.parent_id = domain_node_map.get("general")
            parent_repairs += 1
    if parent_repairs:
        logger.info(
            "Cold path: repaired %d parent_id links to domain nodes",
            parent_repairs,
        )

    # ------------------------------------------------------------------
    # Step 13: Reconcile member_count from actual Optimization rows
    # ------------------------------------------------------------------
    count_q = await db.execute(
        select(Optimization.cluster_id, sa_func.count().label("ct"))
        .where(Optimization.cluster_id.isnot(None))
        .group_by(Optimization.cluster_id)
    )
    actual_counts = dict(count_q.all())

    # Step 14: Reconcile avg_score and scored_count
    score_q = await db.execute(
        select(
            Optimization.cluster_id,
            sa_func.avg(Optimization.overall_score),
            sa_func.count(Optimization.overall_score),
        )
        .where(
            Optimization.cluster_id.isnot(None),
            Optimization.overall_score.isnot(None),
        )
        .group_by(Optimization.cluster_id)
    )
    score_map = {row[0]: (round(row[1], 2), row[2]) for row in score_q.all()}

    mc_repairs = 0
    for node in all_nodes:
        expected = actual_counts.get(node.id, 0)
        if node.member_count != expected:
            node.member_count = expected
            mc_repairs += 1
        avg, scored = score_map.get(node.id, (None, 0))
        node.avg_score = avg
        node.scored_count = scored

    # Step 15: Reconcile domain node member_counts (child cluster count)
    for dn_label, dn_id in domain_node_map.items():
        dn_q = await db.execute(
            select(PromptCluster).where(PromptCluster.id == dn_id)
        )
        dn = dn_q.scalar_one_or_none()
        if dn:
            child_count = (await db.execute(
                select(sa_func.count()).where(
                    PromptCluster.domain == dn_label,
                    PromptCluster.state.notin_(["domain", "archived"]),
                )
            )).scalar() or 0
            dn.member_count = child_count

    # Recompute weighted_member_sum from score data
    for node in all_nodes:
        score_data = score_map.get(node.id)
        if score_data:
            avg, scored = score_data
            if scored and avg:
                node.weighted_member_sum = scored * max(0.1, avg / 10.0)

    if mc_repairs:
        logger.info(
            "Cold path: reconciled %d member_counts from Optimization rows",
            mc_repairs,
        )

    # ------------------------------------------------------------------
    # Step 16: Recompute per-member coherence from optimization embeddings
    # ------------------------------------------------------------------
    # The HDBSCAN coherence computed above measures centroid-to-centroid
    # similarity within HDBSCAN groups -- NOT the pairwise similarity among
    # individual optimization embeddings within each cluster. Overwrite
    # with the correct per-member value.
    all_opt_emb_q = await db.execute(
        select(Optimization.cluster_id, Optimization.embedding).where(
            Optimization.cluster_id.isnot(None),
            Optimization.embedding.isnot(None),
        )
    )
    cold_emb_by_cluster: dict[str, list[np.ndarray]] = {}
    for cid, emb_bytes in all_opt_emb_q.all():
        if emb_bytes is not None:
            try:
                cold_emb_by_cluster.setdefault(cid, []).append(
                    np.frombuffer(emb_bytes, dtype=np.float32).copy()
                )
            except (ValueError, TypeError):
                pass

    coherence_repairs = 0
    for node in all_nodes:
        member_embs = cold_emb_by_cluster.get(node.id, [])
        if len(member_embs) >= 2:
            node.coherence = compute_pairwise_coherence(member_embs)
            coherence_repairs += 1
        elif len(member_embs) == 1:
            node.coherence = 1.0
        # 0 members: keep HDBSCAN centroid-level coherence as fallback

    if coherence_repairs:
        logger.info(
            "Cold path: recomputed %d per-member coherence values",
            coherence_repairs,
        )
    await db.flush()

    # ------------------------------------------------------------------
    # Step 17: UMAP 3D projection with Procrustes alignment
    # ------------------------------------------------------------------
    umap_fitted = False
    if node_embeddings:
        projector = UMAPProjector()
        positions = projector.fit(node_embeddings)

        # Procrustes alignment against previous positions if available
        old_positions = []
        has_old = True
        for node in all_nodes:
            if (
                node.umap_x is not None
                and node.umap_y is not None
                and node.umap_z is not None
            ):
                old_positions.append(
                    [node.umap_x, node.umap_y, node.umap_z]
                )
            else:
                has_old = False
                break

        if has_old and len(old_positions) == len(positions):
            old_arr = np.array(old_positions, dtype=np.float64)
            positions = procrustes_align(positions, old_arr)

        # Set UMAP coordinates on nodes
        for i, node in enumerate(all_nodes):
            if i < len(positions):
                node.umap_x = float(positions[i, 0])
                node.umap_y = float(positions[i, 1])
                node.umap_z = float(positions[i, 2])
        umap_fitted = True

        # Step 18: Domain node UMAP positioning from children centroids
        try:
            domain_umap_q = await db.execute(
                select(PromptCluster).where(PromptCluster.state == "domain")
            )
            for dnode in domain_umap_q.scalars().all():
                await engine._set_domain_umap_from_children(db, dnode)
        except Exception as dom_umap_exc:
            logger.warning(
                "Domain UMAP centroid failed (non-fatal): %s", dom_umap_exc
            )

    # ------------------------------------------------------------------
    # Step 19: OKLab coloring with minimum deltaE (skip domain nodes)
    # ------------------------------------------------------------------
    color_pairs: list[tuple[str, str]] = []
    for node in all_nodes:
        if node.state == "domain":
            continue  # Domain colors are pinned at creation time
        if (
            node.umap_x is not None
            and node.umap_y is not None
            and node.umap_z is not None
        ):
            new_color = generate_color(node.umap_x, node.umap_y, node.umap_z)
            color_pairs.append((node.id, new_color))

    if color_pairs:
        enforced = enforce_minimum_delta_e(color_pairs)
        node_by_id = {n.id: n for n in all_nodes}
        for node_id, color_hex in enforced:
            if node_id in node_by_id:
                node_by_id[node_id].color_hex = color_hex

    # ------------------------------------------------------------------
    # Step 20: Compute per-node separation
    # ------------------------------------------------------------------
    active_result = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
    )
    active_after = list(active_result.scalars().all())

    engine._update_per_node_separation(active_after)

    # ------------------------------------------------------------------
    # Step 21: Compute Q_after
    # ------------------------------------------------------------------
    q_after = engine._compute_q_from_nodes(active_after)

    # ------------------------------------------------------------------
    # Step 22-24: Quality gate — reject regressive refits
    # ------------------------------------------------------------------
    if not is_cold_path_non_regressive(q_before, q_after):
        # Step 23: Rejected — rollback
        logger.warning(
            "Cold path quality regression: Q_before=%.4f Q_after=%.4f "
            "(epsilon=%.2f) -- rolling back refit",
            q_before,
            q_after,
            0.08,
        )
        await db.rollback()

        # Create a snapshot recording the rejection (after rollback, so
        # this is a fresh transaction)
        snap = await create_snapshot(
            db,
            trigger="cold_path",
            q_system=q_before,
            q_coherence=0.0,
            q_separation=0.0,
            q_coverage=0.0,
            nodes_created=0,
        )
        return ColdPathResult(
            snapshot_id=snap.id,
            q_before=q_before,
            q_after=q_after,
            accepted=False,
            nodes_created=0,
            nodes_updated=0,
            umap_fitted=False,
        )

    # ------------------------------------------------------------------
    # Step 24: Accepted — commit path
    # ------------------------------------------------------------------
    # Aggregate metrics for snapshot
    mean_coherence, separation = engine._snapshot_metrics(active_after)

    # Rebuild embedding index from active centroids
    index_centroids: dict[str, np.ndarray] = {}
    for n in active_after:
        try:
            emb = np.frombuffer(n.centroid_embedding, dtype=np.float32)
            if emb.shape[0] == 384:
                index_centroids[n.id] = emb
        except (ValueError, TypeError):
            continue
    try:
        await engine._embedding_index.rebuild(index_centroids)
        logger.info(
            "Taxonomy embedding index loaded with %d vectors",
            engine._embedding_index.size,
        )
    except Exception as rebuild_exc:
        logger.warning(
            "EmbeddingIndex rebuild failed (non-fatal): %s", rebuild_exc
        )

    # Persist embedding index cache to disk for fast startup recovery
    try:
        from app.config import DATA_DIR

        await engine._embedding_index.save_cache(
            DATA_DIR / "embedding_index.pkl"
        )
    except Exception as cache_exc:
        logger.warning(
            "EmbeddingIndex cache save failed (non-fatal): %s", cache_exc
        )

    # Create snapshot — commits all pending node updates AND the
    # snapshot in a single transaction
    engine._invalidate_stats_cache()
    snap = await create_snapshot(
        db,
        trigger="cold_path",
        q_system=q_after,
        q_coherence=mean_coherence,
        q_separation=separation,
        q_coverage=1.0,
        nodes_created=nodes_created,
    )

    # Step 25: Reset cold_path_needed flag
    engine._cold_path_needed = False

    result = ColdPathResult(
        snapshot_id=snap.id,
        q_before=q_before,
        q_after=q_after,
        accepted=True,
        nodes_created=nodes_created,
        nodes_updated=nodes_updated,
        umap_fitted=umap_fitted,
    )

    # Publish taxonomy_changed event (parity with warm path)
    try:
        from app.services.event_bus import event_bus

        event_bus.publish(
            "taxonomy_changed",
            {
                "trigger": "cold_path",
                "nodes_created": nodes_created,
                "nodes_updated": nodes_updated,
                "q_system": q_after,
            },
        )
    except Exception as evt_exc:
        logger.warning(
            "Failed to publish taxonomy_changed (cold): %s", evt_exc
        )

    return result
