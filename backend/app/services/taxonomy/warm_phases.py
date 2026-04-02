"""Warm-path phase implementations — individual lifecycle phases for the
Evolutionary Taxonomy Engine warm path.

Each phase function receives the engine instance via dependency injection
(``engine`` parameter typed via ``TYPE_CHECKING`` to avoid circular imports)
and a fresh ``AsyncSession``.  Phases are independently callable and load
their own data from the database.

Phase order:
  0. reconcile  — member count, coherence, score, domain node repair, zombie cleanup
  1. split_emerge — leaf splits (HDBSCAN + k-means fallback), family splits, emerge
  2. merge — global best-pair merge + same-domain label/embedding merge
  3. retire — archive idle nodes with 0 members
  4. refresh — stale label and meta-pattern re-extraction
  5. discover — domain discovery, candidate detection, risk monitoring, tree repair
  6. audit — per-node separation, Q_system, snapshot, deadlock breaker, event

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    MetaPattern,
    Optimization,
    PromptCluster,
)
from app.services.taxonomy._constants import (
    SPLIT_COHERENCE_FLOOR,
    SPLIT_MIN_MEMBERS,
    _utcnow,
)
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.clustering import (
    batch_cluster,
    compute_pairwise_coherence,
    cosine_similarity,
    l2_normalize_1d,
)
from app.services.taxonomy.family_ops import (
    adaptive_merge_threshold,
    extract_meta_patterns,
    merge_meta_pattern,
    merge_score_into_cluster,
)
from app.utils.text_cleanup import parse_domain

if TYPE_CHECKING:
    from app.services.taxonomy.engine import TaxonomyEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PhaseResult:
    """Result from a lifecycle phase that performs speculative mutations."""

    phase: str
    q_before: float
    q_after: float
    accepted: bool
    ops_attempted: int = 0
    ops_accepted: int = 0
    operations: list[dict] = field(default_factory=list)
    embedding_index_mutations: int = 0


@dataclass
class ReconcileResult:
    """Result from Phase 0 — reconciliation."""

    member_counts_fixed: int = 0
    coherence_updated: int = 0
    scores_reconciled: int = 0
    zombies_archived: int = 0


@dataclass
class RefreshResult:
    """Result from Phase 4 — stale label/pattern refresh."""

    clusters_refreshed: int = 0


@dataclass
class DiscoverResult:
    """Result from Phase 5 — domain discovery."""

    domains_created: int = 0
    candidates_detected: int = 0


@dataclass
class AuditResult:
    """Result from Phase 6 — audit, snapshot, and deadlock breaker."""

    snapshot_id: str = "no-snapshot"
    q_final: float | None = None
    deadlock_breaker_used: bool = False
    deadlock_breaker_phase: str | None = None


# ---------------------------------------------------------------------------
# Phase 0 — Reconcile
# ---------------------------------------------------------------------------


async def phase_reconcile(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> ReconcileResult:
    """Reconcile member counts, coherence, scores, domain node repairs, and
    archive zombie clusters.

    Fix #10: queries nodes with ``state.notin_(["domain", "archived"])``
    instead of iterating over a stale ``active_nodes`` list.
    Fix #16: uses fresh query results from its own session.
    """
    result = ReconcileResult()

    # --- Member count + coherence reconciliation ---
    try:
        count_q = await db.execute(
            select(Optimization.cluster_id, func.count().label("ct"))
            .where(Optimization.cluster_id.isnot(None))
            .group_by(Optimization.cluster_id)
        )
        actual_counts = dict(count_q.all())

        # Batch-load all optimization embeddings grouped by cluster_id.
        all_emb_q = await db.execute(
            select(Optimization.cluster_id, Optimization.embedding).where(
                Optimization.cluster_id.isnot(None),
                Optimization.embedding.isnot(None),
            )
        )
        emb_by_cluster: dict[str, list[np.ndarray]] = {}
        for cid, emb_bytes in all_emb_q.all():
            if emb_bytes is not None:
                try:
                    emb_by_cluster.setdefault(cid, []).append(
                        np.frombuffer(emb_bytes, dtype=np.float32).copy()
                    )
                except (ValueError, TypeError):
                    pass

        # Fix #10: query non-domain/non-archived nodes directly instead of
        # relying on a stale active_nodes list from a prior query.
        nodes_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(["domain", "archived"])
            )
        )
        live_nodes = list(nodes_q.scalars().all())

        for node in live_nodes:
            expected = actual_counts.get(node.id, 0)
            if node.member_count != expected:
                node.member_count = expected
                result.member_counts_fixed += 1

            # Always recompute coherence from actual member embeddings.
            if expected >= 2:
                member_embs = emb_by_cluster.get(node.id, [])
                if len(member_embs) >= 2:
                    node.coherence = compute_pairwise_coherence(member_embs)
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        coherence_member_count=expected,
                    )
                    result.coherence_updated += 1
            elif expected == 1:
                node.coherence = 1.0
            elif expected == 0:
                node.coherence = 0.0

        # Reconcile avg_score and scored_count from actual member data.
        score_q = await db.execute(
            select(
                Optimization.cluster_id,
                func.avg(Optimization.overall_score),
                func.count(Optimization.overall_score),
            ).where(
                Optimization.cluster_id.isnot(None),
                Optimization.overall_score.isnot(None),
            ).group_by(Optimization.cluster_id)
        )
        score_map: dict[str, tuple[float, int]] = {
            row[0]: (round(row[1], 2), row[2])
            for row in score_q.all()
        }
        for node in live_nodes:
            avg, scored = score_map.get(node.id, (None, 0))
            if node.avg_score != avg or (node.scored_count or 0) != scored:
                node.avg_score = avg
                node.scored_count = scored
                result.scores_reconciled += 1

        # Recompute weighted_member_sum and centroid from member data.
        # The hot-path running mean can drift; this corrects from ground truth.
        for node in live_nodes:
            score_data = score_map.get(node.id)
            if score_data:
                avg, scored = score_data
                if scored and avg:
                    node.weighted_member_sum = scored * max(0.1, avg / 10.0)

            # Recompute centroid from actual member embeddings
            member_embs = emb_by_cluster.get(node.id, [])
            if len(member_embs) >= 2:
                stacked = np.stack(member_embs, axis=0)
                recomputed = np.mean(stacked, axis=0).astype(np.float32)
                c_norm = np.linalg.norm(recomputed)
                if c_norm > 1e-9:
                    node.centroid_embedding = (recomputed / c_norm).tobytes()

        # Reconcile domain node member_counts and parent_id links.
        domain_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        for domain_node in domain_q.scalars().all():
            # Domain nodes are root -- parent_id must be null.
            if domain_node.parent_id is not None:
                logger.info(
                    "Clearing stale parent_id on domain '%s' (was %s)",
                    domain_node.label, domain_node.parent_id,
                )
                domain_node.parent_id = None
                result.member_counts_fixed += 1

            # Count children by domain field (robust to broken parent_id)
            child_count = (await db.execute(
                select(func.count()).where(
                    PromptCluster.domain == domain_node.label,
                    PromptCluster.state.notin_(["domain", "archived"]),
                )
            )).scalar() or 0
            if domain_node.member_count != child_count:
                domain_node.member_count = child_count
                result.member_counts_fixed += 1

            # Repair self-referencing parent_id links on children.
            self_ref_q = await db.execute(
                select(PromptCluster).where(
                    PromptCluster.domain == domain_node.label,
                    PromptCluster.state.notin_(["domain", "archived"]),
                    PromptCluster.id == PromptCluster.parent_id,
                )
            )
            for child in self_ref_q.scalars().all():
                child.parent_id = domain_node.id
                result.member_counts_fixed += 1

            # Fix domain nodes missing UMAP coordinates
            if (domain_node.umap_x is None
                    or domain_node.umap_y is None
                    or domain_node.umap_z is None):
                await engine._set_domain_umap_from_children(db, domain_node)

        if (result.member_counts_fixed
                or result.coherence_updated
                or result.scores_reconciled):
            logger.info(
                "Reconciled %d member_counts, %d coherence, %d scores",
                result.member_counts_fixed,
                result.coherence_updated,
                result.scores_reconciled,
            )
            await db.flush()
    except Exception as recon_exc:
        logger.warning("Reconciliation failed (non-fatal): %s", recon_exc)

    # --- Zombie cluster cleanup ---
    try:
        # Fix #10: re-query non-domain/non-archived nodes for zombie check
        # instead of iterating over a stale active_nodes list.
        zombie_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(["domain", "archived"]),
            )
        )
        zombie_candidates = list(zombie_q.scalars().all())

        for node in zombie_candidates:
            if (node.member_count or 0) == 0:
                # Verify no optimizations still reference this cluster.
                actual_refs = (await db.execute(
                    select(func.count()).where(
                        Optimization.cluster_id == node.id,
                    )
                )).scalar() or 0
                if actual_refs > 0:
                    node.member_count = actual_refs
                    logger.info(
                        "Zombie guard: '%s' has %d optimization refs "
                        "-- correcting member_count, not archiving",
                        node.label, actual_refs,
                    )
                    continue

                # Clear ALL stale data.
                if node.usage_count and node.usage_count > 0:
                    logger.info(
                        "Clearing stale usage_count=%d on 0-member cluster '%s'",
                        node.usage_count, node.label,
                    )
                node.usage_count = 0
                node.avg_score = None
                node.scored_count = 0
                node.state = "archived"
                node.archived_at = _utcnow()
                result.zombies_archived += 1
                await engine._embedding_index.remove(node.id)

        if result.zombies_archived:
            logger.info(
                "Archived %d zombie clusters (0 members)",
                result.zombies_archived,
            )
            await db.flush()
    except Exception as zombie_exc:
        logger.warning("Zombie cleanup failed (non-fatal): %s", zombie_exc)

    return result


# ---------------------------------------------------------------------------
# Phase 1 — Split + Emerge
# ---------------------------------------------------------------------------


async def phase_split_emerge(
    engine: TaxonomyEngine,
    db: AsyncSession,
    split_protected_ids: set[str],
) -> PhaseResult:
    """Leaf splits (HDBSCAN + k-means fallback), family-based splits, and
    emerge from orphan families.

    Fix #7: exclude domain/archived from emerge query.
    Fix #9: increment ``ops_accepted`` for successful leaf splits.
    Fix #11: use pre-fetched ``_split_emb_cache`` for noise reassignment
        instead of per-noise-point DB queries.
    Fix #12: replace manual cosine with ``cosine_similarity()`` from
        clustering.py at noise reassignment.
    """
    ops_attempted = 0
    ops_accepted = 0
    operations_log: list[dict] = []
    embedding_index_mutations = 0

    # Load active nodes for lifecycle operations.
    # Q_before/Q_after are computed by the orchestrator (_run_speculative_phase),
    # not here — phases focus on mutations, orchestrator handles quality gating.
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
    )
    active_nodes = list(active_q.scalars().all())

    # --- Priority 1: Split ---
    # Pre-fetch all optimization embeddings for split candidates in a
    # single batch query.
    _split_candidate_ids = [
        n.id for n in active_nodes
        if (n.member_count or 0) >= SPLIT_MIN_MEMBERS
    ]
    _split_emb_cache: dict[str, list[tuple[str, bytes]]] = {}
    if _split_candidate_ids:
        _split_emb_q = await db.execute(
            select(
                Optimization.id,
                Optimization.cluster_id,
                Optimization.embedding,
            ).where(
                Optimization.cluster_id.in_(_split_candidate_ids),
                Optimization.embedding.isnot(None),
            )
        )
        for opt_id, cid, emb_bytes in _split_emb_q.all():
            if emb_bytes is not None:
                _split_emb_cache.setdefault(cid, []).append((opt_id, emb_bytes))

    for node in active_nodes:
        member_count = node.member_count or 0
        if member_count < SPLIT_MIN_MEMBERS:
            continue

        # Recompute coherence from actual member embeddings.
        _cached_opt_rows = _split_emb_cache.get(node.id, [])
        try:
            _coh_embs = [
                np.frombuffer(row[1], dtype=np.float32).copy()
                for row in _cached_opt_rows
                if row[1] is not None
            ]
            if len(_coh_embs) >= 2:
                coherence = compute_pairwise_coherence(_coh_embs)
                node.coherence = coherence
                node.cluster_metadata = write_meta(
                    node.cluster_metadata,
                    coherence_member_count=len(_coh_embs),
                )
            else:
                coherence = 1.0
        except Exception:
            logger.debug(
                "Coherence recomputation failed for '%s', using stored value",
                node.label,
                exc_info=True,
            )
            coherence = node.coherence if node.coherence is not None else 1.0

        # Scale: +0.05 per doubling above 6 members
        dynamic_floor = SPLIT_COHERENCE_FLOOR + max(
            0, math.log2(max(member_count, 6) / 6)
        ) * 0.05
        if coherence >= dynamic_floor:
            continue

        # Cooldown: skip if this cluster already failed to split 3+ times
        node_meta = read_meta(node.cluster_metadata)
        split_failures = node_meta["split_failures"]
        if split_failures >= 3:
            continue

        ops_attempted += 1
        logger.info(
            "Split candidate: '%s' (members=%d, coherence=%.3f, threshold=%.3f)",
            node.label, member_count, coherence, dynamic_floor,
        )

        # Gather ACTIVE families assigned to this node
        fam_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == node.id,
                PromptCluster.state.notin_(["domain", "archived"]),
            )
        )
        node_families = list(fam_q.scalars().all())

        # --- Leaf split path ---
        if len(node_families) < SPLIT_MIN_MEMBERS and member_count >= SPLIT_MIN_MEMBERS:
            opt_rows = _cached_opt_rows
            if len(opt_rows) >= SPLIT_MIN_MEMBERS:
                child_embs = []
                child_fam_ids = []
                for opt_id, emb_bytes in opt_rows:
                    try:
                        emb = np.frombuffer(emb_bytes, dtype=np.float32).copy()
                        child_embs.append(emb)
                        child_fam_ids.append(opt_id)
                    except (ValueError, TypeError):
                        continue

                if len(child_embs) >= SPLIT_MIN_MEMBERS:
                    split_clusters = batch_cluster(child_embs, min_cluster_size=3)

                    # Fallback: k-means bisection when HDBSCAN fails
                    if (split_clusters.n_clusters < 2
                            and len(child_embs) >= 2 * SPLIT_MIN_MEMBERS):
                        try:
                            from sklearn.cluster import KMeans

                            emb_stack = np.stack(child_embs, axis=0)
                            km = KMeans(n_clusters=2, n_init=10, random_state=42)
                            km_labels = km.fit_predict(emb_stack)

                            sizes = [int((km_labels == c).sum()) for c in range(2)]
                            if all(s >= 3 for s in sizes):
                                centroids = [
                                    l2_normalize_1d(
                                        km.cluster_centers_[c].astype(np.float32)
                                    )
                                    for c in range(2)
                                ]
                                split_clusters = type(split_clusters)(
                                    labels=km_labels,
                                    centroids=centroids,
                                    n_clusters=2,
                                    persistences=[0.5, 0.5],
                                    noise_count=0,
                                )
                                logger.info(
                                    "HDBSCAN failed, k-means bisection succeeded: %s members",
                                    sizes,
                                )
                        except Exception as km_exc:
                            logger.debug("k-means fallback failed: %s", km_exc)

                    if split_clusters.n_clusters < 2:
                        # Track failed attempt for cooldown
                        node.cluster_metadata = write_meta(
                            node.cluster_metadata,
                            split_failures=split_failures + 1,
                        )
                        logger.info(
                            "Leaf split failed: HDBSCAN found %d clusters (need 2+), attempt %d/3",
                            split_clusters.n_clusters, split_failures + 1,
                        )
                    else:
                        # Reset failure counter on success
                        node.cluster_metadata = write_meta(
                            node.cluster_metadata,
                            split_failures=0,
                        )
                        logger.info(
                            "Leaf split: HDBSCAN found %d sub-clusters from %d optimizations",
                            split_clusters.n_clusters, len(child_embs),
                        )
                        from app.services.taxonomy.coloring import generate_color
                        from app.services.taxonomy.labeling import generate_label

                        parent_domain = node.domain or "general"
                        parent_id_for_children = node.parent_id or node.id
                        new_children = []
                        for cid in range(split_clusters.n_clusters):
                            mask = split_clusters.labels == cid
                            group_opt_ids = [
                                child_fam_ids[i]
                                for i in range(len(child_fam_ids))
                                if mask[i]
                            ]
                            group_embs = [
                                child_embs[i]
                                for i in range(len(child_embs))
                                if mask[i]
                            ]
                            if not group_embs:
                                continue

                            centroid = (
                                split_clusters.centroids[cid]
                                if cid < len(split_clusters.centroids)
                                else None
                            )
                            if centroid is None:
                                centroid = l2_normalize_1d(
                                    np.mean(
                                        np.stack(group_embs), axis=0
                                    ).astype(np.float32)
                                )
                            child_coherence = compute_pairwise_coherence(group_embs)

                            # Generate label from member intent labels
                            opt_labels_q = await db.execute(
                                select(Optimization.intent_label)
                                .where(Optimization.id.in_(group_opt_ids))
                                .limit(10)
                            )
                            member_texts = [
                                r[0] for r in opt_labels_q.all() if r[0]
                            ]
                            label = await generate_label(
                                provider=engine._provider,
                                member_texts=member_texts,
                                model=settings.MODEL_HAIKU,
                            )

                            # Compute avg_score and scored_count from members
                            score_q = await db.execute(
                                select(
                                    func.avg(Optimization.overall_score),
                                    func.count(Optimization.overall_score),
                                ).where(
                                    Optimization.id.in_(group_opt_ids),
                                    Optimization.overall_score.isnot(None),
                                )
                            )
                            score_row = score_q.one()
                            child_avg_score = score_row[0]
                            child_scored_count = score_row[1] or 0
                            if child_avg_score is not None:
                                child_avg_score = round(child_avg_score, 2)

                            child_node = PromptCluster(
                                label=label,
                                centroid_embedding=centroid.astype(
                                    np.float32
                                ).tobytes(),
                                member_count=len(group_opt_ids),
                                scored_count=child_scored_count,
                                avg_score=child_avg_score,
                                coherence=child_coherence,
                                state="active",
                                domain=parent_domain,
                                parent_id=parent_id_for_children,
                                color_hex=generate_color(0.0, 0.0, 0.0),
                            )
                            db.add(child_node)
                            await db.flush()

                            # Reassign optimizations to the new child cluster
                            await db.execute(
                                sa_update(Optimization)
                                .where(Optimization.id.in_(group_opt_ids))
                                .values(cluster_id=child_node.id)
                            )
                            new_children.append(child_node)
                            logger.info(
                                "  Created sub-cluster '%s' (%d members, coherence=%.3f)",
                                label, len(group_opt_ids), child_coherence,
                            )

                        if len(new_children) >= 2:
                            # Archive the original mega-cluster.
                            node.state = "archived"
                            node.archived_at = _utcnow()
                            node.member_count = 0
                            node.scored_count = 0
                            node.usage_count = 0
                            node.avg_score = None
                            await engine._embedding_index.remove(node.id)
                            embedding_index_mutations += 1
                            for child in new_children:
                                centroid_emb = np.frombuffer(
                                    child.centroid_embedding, dtype=np.float32
                                )
                                await engine._embedding_index.upsert(
                                    child.id, centroid_emb
                                )
                                embedding_index_mutations += 1

                            # Fix #11: use pre-fetched _split_emb_cache for
                            # noise reassignment instead of per-noise-point
                            # DB queries.
                            noise_ids = [
                                child_fam_ids[i]
                                for i in range(len(child_fam_ids))
                                if split_clusters.labels[i] == -1
                            ]
                            if noise_ids:
                                # Build lookups from the pre-fetched cache
                                # (Fix #11: batch both embeddings AND scores)
                                noise_id_set = set(noise_ids)
                                noise_emb_lookup: dict[str, bytes] = {}
                                for oid, emb_bytes in _cached_opt_rows:
                                    if oid in noise_id_set:
                                        noise_emb_lookup[oid] = emb_bytes

                                # Batch-fetch scores for noise optimizations
                                noise_score_q = await db.execute(
                                    select(
                                        Optimization.id,
                                        Optimization.overall_score,
                                    ).where(Optimization.id.in_(noise_ids))
                                )
                                noise_score_lookup = {
                                    row[0]: row[1]
                                    for row in noise_score_q.all()
                                }

                                reassigned = 0
                                for nid in noise_ids:
                                    n_bytes = noise_emb_lookup.get(nid)
                                    if not n_bytes:
                                        continue
                                    n_emb = np.frombuffer(
                                        n_bytes, dtype=np.float32
                                    )
                                    best_c, best_s = None, -1.0
                                    for ch in new_children:
                                        c_emb = np.frombuffer(
                                            ch.centroid_embedding,
                                            dtype=np.float32,
                                        )
                                        s = cosine_similarity(n_emb, c_emb)
                                        if s > best_s:
                                            best_s, best_c = s, ch
                                    if best_c:
                                        await db.execute(
                                            sa_update(Optimization)
                                            .where(Optimization.id == nid)
                                            .values(cluster_id=best_c.id)
                                        )
                                        best_c.member_count = (
                                            best_c.member_count or 0
                                        ) + 1
                                        merge_score_into_cluster(
                                            best_c,
                                            noise_score_lookup.get(nid),
                                        )
                                        reassigned += 1
                                logger.info(
                                    "Reassigned %d noise optimizations to nearest sub-clusters",
                                    reassigned,
                                )

                            # Protect split children and parent from
                            # same-domain merge in the same cycle.
                            split_protected_ids.add(node.id)
                            for ch in new_children:
                                split_protected_ids.add(ch.id)

                            # Fix #9: increment ops_accepted for successful
                            # leaf splits (previously only ops_attempted was
                            # incremented).
                            ops_accepted += len(new_children)

                            # Flush (not commit) — the orchestrator decides
                            # whether to commit or rollback via the Q gate.
                            await db.flush()
                            operations_log.append({
                                "type": "leaf_split",
                                "parent_id": node.id,
                                "children": [c.id for c in new_children],
                            })
                            logger.info(
                                "Leaf split complete: '%s' -> %d sub-clusters",
                                node.label, len(new_children),
                            )
                            continue  # skip the family-based split below

        # --- Family-based split path ---
        if len(node_families) >= SPLIT_MIN_MEMBERS:
            child_embs_fam = []
            child_fam_ids_fam = []
            for f in node_families:
                try:
                    emb = np.frombuffer(
                        f.centroid_embedding, dtype=np.float32
                    )
                    child_embs_fam.append(emb)
                    child_fam_ids_fam.append(f.id)
                except (ValueError, TypeError):
                    continue

            if len(child_embs_fam) >= 6:
                split_clusters_fam = batch_cluster(
                    child_embs_fam, min_cluster_size=3
                )
                if split_clusters_fam.n_clusters >= 2:
                    from app.services.taxonomy.lifecycle import attempt_split

                    child_groups = []
                    for cid in range(split_clusters_fam.n_clusters):
                        mask = split_clusters_fam.labels == cid
                        group_ids = [
                            child_fam_ids_fam[i]
                            for i in range(len(child_fam_ids_fam))
                            if mask[i]
                        ]
                        group_embs = [
                            child_embs_fam[i]
                            for i in range(len(child_embs_fam))
                            if mask[i]
                        ]
                        if group_ids:
                            child_groups.append((group_ids, group_embs))

                    if len(child_groups) >= 2:
                        children = await attempt_split(
                            db=db,
                            parent_node=node,
                            child_clusters=child_groups,
                            warm_path_age=engine._warm_path_age,
                            provider=engine._provider,
                            model=settings.MODEL_HAIKU,
                        )
                        if children:
                            ops_accepted += len(children)
                            split_protected_ids.add(node.id)
                            for child in children:
                                split_protected_ids.add(child.id)
                                operations_log.append(
                                    {"type": "split", "node_id": child.id}
                                )

    # --- Priority 2: Emerge ---
    # Fix #7: exclude domain/archived from emerge query (parent_id IS NULL
    # must also exclude domain and archived nodes).
    fam_result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.parent_id.is_(None),
            PromptCluster.state.notin_(["domain", "archived"]),
        )
    )
    unassigned_families = list(fam_result.scalars().all())

    if len(unassigned_families) >= 3:
        ops_attempted += 1
        emerged = await engine._try_emerge_from_families(
            db, unassigned_families, batch_cluster,
        )
        ops_accepted += len(emerged)
        operations_log.extend(emerged)

    return PhaseResult(
        phase="split_emerge",
        q_before=0.0,  # Overwritten by orchestrator
        q_after=0.0,   # Overwritten by orchestrator
        accepted=False, # Set by orchestrator Q gate
        ops_attempted=ops_attempted,
        ops_accepted=ops_accepted,
        operations=operations_log,
        embedding_index_mutations=embedding_index_mutations,
    )


# ---------------------------------------------------------------------------
# Phase 2 — Merge
# ---------------------------------------------------------------------------


async def phase_merge(
    engine: TaxonomyEngine,
    db: AsyncSession,
    split_protected_ids: set[str],
) -> PhaseResult:
    """Global best-pair merge and same-domain label/embedding merge.

    Fix #12: replace manual cosine at label merge and embedding merge
    with ``cosine_similarity()`` from clustering.py.
    """
    ops_attempted = 0
    ops_accepted = 0
    operations_log: list[dict] = []
    embedding_index_mutations = 0

    # Load active nodes
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
    )
    active_nodes = list(active_q.scalars().all())

    # --- Global best-pair merge ---
    # KEEP the matrix-based mat_norm @ mat_norm.T -- it's an optimized batch
    # operation for finding the closest pair across all nodes.
    if len(active_nodes) >= 2:
        centroids = []
        valid_nodes: list[PromptCluster] = []
        for n in active_nodes:
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                centroids.append(c)
                valid_nodes.append(n)
            except (ValueError, TypeError):
                continue

        if len(centroids) >= 2:
            mat = np.stack(centroids, axis=0).astype(np.float32)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            mat_norm = mat / norms
            sim = mat_norm @ mat_norm.T
            np.fill_diagonal(sim, -1)

            best_i, best_j = np.unravel_index(np.argmax(sim), sim.shape)
            best_score = float(sim[best_i, best_j])

            merge_node_a = valid_nodes[int(best_i)]
            merge_node_b = valid_nodes[int(best_j)]
            merge_threshold = adaptive_merge_threshold(
                max(
                    merge_node_a.member_count or 1,
                    merge_node_b.member_count or 1,
                ),
            )
            if best_score >= merge_threshold:
                ops_attempted += 1
                from app.services.taxonomy.lifecycle import attempt_merge

                merged = await attempt_merge(
                    db=db,
                    node_a=merge_node_a,
                    node_b=merge_node_b,
                    warm_path_age=engine._warm_path_age,
                    embedding_svc=engine._embedding,
                )
                if merged:
                    ops_accepted += 1
                    operations_log.append(
                        {"type": "merge", "node_id": merged.id}
                    )
                    # Update embedding index: upsert winner, remove loser
                    winner_centroid = np.frombuffer(
                        merged.centroid_embedding, dtype=np.float32
                    )
                    await engine._embedding_index.upsert(
                        merged.id, winner_centroid
                    )
                    loser = (
                        merge_node_b
                        if merged.id == merge_node_a.id
                        else merge_node_a
                    )
                    await engine._embedding_index.remove(loser.id)
                    embedding_index_mutations += 2

    # --- Same-domain duplicate merge ---
    same_domain_merge_base = 0.65
    label_merge_sanity_base = 0.40
    try:
        from app.services.taxonomy.lifecycle import attempt_merge

        # Reload active nodes (may have changed from global merge)
        current_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
        )
        current_active = list(current_q.scalars().all())

        # Group by primary domain, excluding split-protected nodes
        domain_groups: dict[str, list[PromptCluster]] = {}
        for node in current_active:
            if node.id in split_protected_ids:
                continue
            primary, _ = parse_domain(node.domain or "general")
            domain_groups.setdefault(primary, []).append(node)

        domain_merges = 0
        for domain, siblings in domain_groups.items():
            if len(siblings) < 2:
                continue

            # Signal A: identical labels (one merge per domain per cycle)
            label_merged = False
            label_groups: dict[str, list[PromptCluster]] = {}
            for s in siblings:
                label_groups.setdefault(s.label, []).append(s)
            for label, group in label_groups.items():
                if label_merged:
                    break
                if len(group) < 2:
                    continue
                group.sort(
                    key=lambda n: n.member_count or 0, reverse=True
                )
                survivor = group[0]
                for loser in group[1:]:
                    # Fix #12: use cosine_similarity() from clustering.py
                    try:
                        emb_a = np.frombuffer(
                            survivor.centroid_embedding, dtype=np.float32
                        )
                        emb_b = np.frombuffer(
                            loser.centroid_embedding, dtype=np.float32
                        )
                        sim = cosine_similarity(emb_a, emb_b)
                    except (ValueError, TypeError):
                        sim = 0.0
                    combined_mc = max(
                        survivor.member_count or 0,
                        loser.member_count or 0,
                    )
                    label_merge_sanity = max(
                        label_merge_sanity_base,
                        adaptive_merge_threshold(combined_mc),
                    )
                    if sim >= label_merge_sanity:
                        merged = await attempt_merge(
                            db,
                            survivor,
                            loser,
                            engine._warm_path_age,
                            embedding_svc=engine._embedding,
                        )
                        if merged:
                            domain_merges += 1
                            logger.info(
                                "Same-domain label merge: '%s' absorbed "
                                "duplicate (sim=%.2f, domain=%s)",
                                label, sim, domain,
                            )
                            winner_centroid = np.frombuffer(
                                merged.centroid_embedding, dtype=np.float32
                            )
                            await engine._embedding_index.upsert(
                                merged.id, winner_centroid
                            )
                            await engine._embedding_index.remove(loser.id)
                            embedding_index_mutations += 2
                            label_merged = True
                            break  # one merge per domain per cycle

            # Signal B: high centroid similarity within domain
            remaining = [s for s in siblings if s.state not in ("domain", "archived")]
            if len(remaining) >= 2:
                merged_this_domain = False
                for i in range(len(remaining)):
                    if merged_this_domain:
                        break
                    for j in range(i + 1, len(remaining)):
                        # Fix #12: use cosine_similarity() from clustering.py
                        try:
                            emb_i = np.frombuffer(
                                remaining[i].centroid_embedding,
                                dtype=np.float32,
                            )
                            emb_j = np.frombuffer(
                                remaining[j].centroid_embedding,
                                dtype=np.float32,
                            )
                            sim = cosine_similarity(emb_i, emb_j)
                        except (ValueError, TypeError):
                            continue
                        both_active = (
                            remaining[i].state not in ("domain", "archived")
                            and remaining[j].state not in ("domain", "archived")
                        )
                        combined_mc = max(
                            remaining[i].member_count or 0,
                            remaining[j].member_count or 0,
                        )
                        same_domain_threshold = max(
                            same_domain_merge_base,
                            adaptive_merge_threshold(combined_mc),
                        )
                        if sim >= same_domain_threshold and both_active:
                            ni, nj = remaining[i], remaining[j]
                            big = (
                                ni
                                if (ni.member_count or 0) >= (nj.member_count or 0)
                                else nj
                            )
                            small = nj if big is ni else ni
                            merged = await attempt_merge(
                                db,
                                big,
                                small,
                                engine._warm_path_age,
                                embedding_svc=engine._embedding,
                            )
                            if merged:
                                domain_merges += 1
                                logger.info(
                                    "Same-domain embedding merge: '%s' + '%s' "
                                    "(sim=%.2f, domain=%s)",
                                    big.label, small.label, sim, domain,
                                )
                                winner_centroid = np.frombuffer(
                                    merged.centroid_embedding,
                                    dtype=np.float32,
                                )
                                await engine._embedding_index.upsert(
                                    merged.id, winner_centroid
                                )
                                await engine._embedding_index.remove(small.id)
                                embedding_index_mutations += 2
                                merged_this_domain = True
                                break  # one merge per domain per cycle

        if domain_merges:
            ops_accepted += domain_merges
            ops_attempted += domain_merges
            logger.info(
                "Same-domain merge: %d merges completed", domain_merges
            )
            await db.flush()
    except Exception as merge_exc:
        logger.warning("Same-domain merge failed (non-fatal): %s", merge_exc)

    return PhaseResult(
        phase="merge",
        q_before=0.0,  # Overwritten by orchestrator
        q_after=0.0,   # Overwritten by orchestrator
        accepted=False, # Set by orchestrator Q gate
        ops_attempted=ops_attempted,
        ops_accepted=ops_accepted,
        operations=operations_log,
        embedding_index_mutations=embedding_index_mutations,
    )


# ---------------------------------------------------------------------------
# Phase 3 — Retire
# ---------------------------------------------------------------------------


async def phase_retire(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> PhaseResult:
    """Retire idle nodes with 0 members."""
    ops_attempted = 0
    ops_accepted = 0
    operations_log: list[dict] = []
    embedding_index_mutations = 0

    # Load active nodes
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
    )
    active_nodes = list(active_q.scalars().all())

    for node in active_nodes:
        if (node.member_count or 0) == 0:
            ops_attempted += 1
            from app.services.taxonomy.lifecycle import attempt_retire

            retired = await attempt_retire(
                db=db,
                node=node,
                warm_path_age=engine._warm_path_age,
            )
            if retired:
                ops_accepted += 1
                operations_log.append({"type": "retire", "node_id": node.id})
                await engine._embedding_index.remove(node.id)
                embedding_index_mutations += 1

    return PhaseResult(
        phase="retire",
        q_before=0.0,  # Overwritten by orchestrator
        q_after=0.0,   # Overwritten by orchestrator
        accepted=False, # Set by orchestrator Q gate
        ops_attempted=ops_attempted,
        ops_accepted=ops_accepted,
        operations=operations_log,
        embedding_index_mutations=embedding_index_mutations,
    )


# ---------------------------------------------------------------------------
# Phase 4 — Refresh
# ---------------------------------------------------------------------------


async def phase_refresh(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> RefreshResult:
    """Stale label and meta-pattern refresh.

    Fix #15: extract new patterns FIRST, only delete old ones if extraction
    succeeds.
    """
    result = RefreshResult()

    refresh_growth_factor = 3   # trigger when member_count >= 3x last extraction
    refresh_min_members = 3     # matches domain discovery threshold
    refresh_sample_size = 8     # representative sample for re-extraction

    try:
        from app.services.taxonomy.labeling import generate_label

        # Load active non-domain nodes
        nodes_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
        )
        active_nodes = list(nodes_q.scalars().all())

        for node in active_nodes:
            if node.state == "domain" or (node.member_count or 0) < refresh_min_members:
                continue
            meta = read_meta(node.cluster_metadata)
            last_count = meta["pattern_member_count"]
            if last_count > 0 and node.member_count < last_count * refresh_growth_factor:
                continue  # not stale enough

            # Gather representative sample of recent members
            sample_q = await db.execute(
                select(Optimization)
                .where(Optimization.cluster_id == node.id)
                .order_by(Optimization.created_at.desc())
                .limit(refresh_sample_size)
            )
            sample_opts = sample_q.scalars().all()
            if len(sample_opts) < 3:
                continue

            # Regenerate label from member intent labels
            member_texts = [
                o.intent_label or (o.raw_prompt or "")[:200]
                for o in sample_opts
            ]
            new_label = await generate_label(
                provider=engine._provider,
                member_texts=member_texts,
                model=settings.MODEL_HAIKU,
            )
            if new_label and new_label != "Unnamed Cluster":
                node.label = new_label

            # Fix #15: extract new patterns FIRST, only delete old ones if
            # extraction succeeds.
            new_pattern_texts: list[str] = []
            for opt in sample_opts[:5]:
                try:
                    texts = await extract_meta_patterns(
                        opt, db, engine._provider, engine._prompt_loader,
                    )
                    new_pattern_texts.extend(texts)
                except Exception:
                    pass  # non-fatal per-optimization

            # Only delete old patterns if we successfully extracted new ones
            if new_pattern_texts:
                old_patterns = await db.execute(
                    select(MetaPattern).where(
                        MetaPattern.cluster_id == node.id
                    )
                )
                for old_mp in old_patterns.scalars():
                    await db.delete(old_mp)

                for text in new_pattern_texts:
                    await merge_meta_pattern(
                        db, node.id, text, engine._embedding,
                    )

            # Track extraction state
            node.cluster_metadata = write_meta(
                node.cluster_metadata,
                pattern_member_count=node.member_count,
                label_refreshed_at=_utcnow().isoformat(),
            )
            result.clusters_refreshed += 1
            logger.info(
                "Refreshed label+patterns for '%s' (members: %d->%d, old_count=%d)",
                node.label, last_count, node.member_count, last_count,
            )

        if result.clusters_refreshed:
            await db.flush()
            logger.info(
                "Refreshed label+patterns for %d clusters",
                result.clusters_refreshed,
            )
    except Exception as refresh_exc:
        logger.warning(
            "Stale label/pattern refresh failed (non-fatal): %s",
            refresh_exc,
        )

    # --- Cross-cluster global_source_count computation ---
    # For each MetaPattern, count how many DISTINCT clusters contain a
    # semantically similar pattern (cosine >= 0.82). This enables
    # cross-cluster injection: patterns with high global_source_count
    # are universal techniques that benefit all prompts.
    try:
        all_patterns_q = await db.execute(
            select(MetaPattern).where(
                MetaPattern.embedding.isnot(None),
            )
        )
        all_patterns = list(all_patterns_q.scalars().all())

        if len(all_patterns) >= 2:
            # Build embedding matrix + cluster_id mapping
            pattern_embs: list[np.ndarray] = []
            pattern_cluster_ids: list[str] = []
            valid_patterns: list[MetaPattern] = []
            for mp in all_patterns:
                try:
                    emb = np.frombuffer(mp.embedding, dtype=np.float32).copy()
                    if emb.shape[0] == 384:
                        pattern_embs.append(emb)
                        pattern_cluster_ids.append(mp.cluster_id)
                        valid_patterns.append(mp)
                except (ValueError, TypeError):
                    continue

            if len(pattern_embs) >= 2:
                # Pairwise cosine similarity matrix
                mat = np.stack(pattern_embs, axis=0).astype(np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1.0, norms)
                mat_norm = mat / norms
                sim_matrix = mat_norm @ mat_norm.T

                from app.services.pipeline_constants import (
                    CROSS_CLUSTER_SIMILARITY_THRESHOLD,
                )

                for i, mp in enumerate(valid_patterns):
                    similar_mask = sim_matrix[i] >= CROSS_CLUSTER_SIMILARITY_THRESHOLD
                    similar_cluster_ids = {
                        pattern_cluster_ids[j]
                        for j in range(len(valid_patterns))
                        if similar_mask[j]
                    }
                    mp.global_source_count = len(similar_cluster_ids)

                await db.flush()
                logger.info(
                    "Computed global_source_count for %d meta-patterns",
                    len(valid_patterns),
                )
        elif len(all_patterns) == 1:
            all_patterns[0].global_source_count = 1
            await db.flush()
    except Exception as gsc_exc:
        logger.warning(
            "Global source count computation failed (non-fatal): %s", gsc_exc
        )

    return result


# ---------------------------------------------------------------------------
# Phase 5 — Discover
# ---------------------------------------------------------------------------


async def phase_discover(
    engine: TaxonomyEngine,
    db: AsyncSession,
) -> DiscoverResult:
    """Domain discovery, candidate detection, risk monitoring, and tree
    integrity repair.

    Orchestrates calls to the engine's domain management methods.
    """
    result = DiscoverResult()

    # --- Domain discovery (ADR-004) ---
    new_domains = await engine._propose_domains(db)
    if new_domains:
        result.domains_created = len(new_domains)
        logger.info(
            "Warm path discovered %d new domains: %s",
            len(new_domains), new_domains,
        )

    # --- Candidate domain detection (near-threshold clusters) ---
    try:
        await engine._detect_domain_candidates(db)
    except Exception as cand_exc:
        logger.warning(
            "Candidate detection failed (non-fatal): %s", cand_exc
        )

    # --- Risk monitoring (ADR-004 Section 8B) ---
    try:
        await engine._monitor_general_health(db)
        stale_domains = await engine._check_signal_staleness(db)
        for stale_domain in stale_domains:
            await engine._refresh_domain_signals(db, stale_domain)
        await engine._suggest_domain_archival(db)
    except Exception as risk_exc:
        logger.warning(
            "Risk monitoring failed (non-fatal): %s", risk_exc
        )

    # --- Tree integrity check + auto-repair (ADR-004 Risk 5) ---
    try:
        violations = await engine.verify_domain_tree_integrity(db)
        if violations:
            repaired = await engine._repair_tree_violations(db, violations)
            logger.warning(
                "Tree integrity: %d violations, %d repaired",
                len(violations), repaired,
            )
    except Exception as integrity_exc:
        logger.warning(
            "Tree integrity check failed (non-fatal): %s", integrity_exc
        )

    return result


# ---------------------------------------------------------------------------
# Phase 6 — Audit
# ---------------------------------------------------------------------------


async def phase_audit(
    engine: TaxonomyEngine,
    db: AsyncSession,
    phase_results: list[PhaseResult],
    q_baseline: float | None,
) -> AuditResult:
    """Compute per-node separation, final Q_system, create snapshot, publish events.

    Fix #13: always increment ``engine._warm_path_age`` unconditionally.

    Note: Quality gating and deadlock breaking are handled per-phase by the
    orchestrator (warm_path.py). This function only computes the final metrics,
    creates the audit snapshot, and publishes events.
    """
    from app.services.taxonomy.snapshot import get_latest_snapshot

    result = AuditResult()

    # Gather aggregated stats from all phase results
    total_ops_attempted = sum(pr.ops_attempted for pr in phase_results)
    total_ops_accepted = sum(pr.ops_accepted for pr in phase_results)
    all_operations: list[dict] = []
    for pr in phase_results:
        all_operations.extend(pr.operations)

    # Compute per-node separation and Q_final
    active_q = await db.execute(
        select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
    )
    active_after = list(active_q.scalars().all())
    engine._update_per_node_separation(active_after)
    q_after = engine._compute_q_from_nodes(active_after)
    result.q_final = q_after

    # Invalidate stats cache
    engine._invalidate_stats_cache()

    # Fix #13: always increment _warm_path_age unconditionally.
    engine._warm_path_age += 1

    # Create snapshot (skip on idle cycles with no changes)
    if total_ops_accepted > 0 or total_ops_attempted > 0 or engine._warm_path_age == 1:
        snap = await engine._create_warm_snapshot(
            db,
            q_system=q_after,
            operations=all_operations,
            ops_attempted=total_ops_attempted,
            ops_accepted=total_ops_accepted,
        )
        result.snapshot_id = snap.id
    else:
        latest = await get_latest_snapshot(db)
        result.snapshot_id = latest.id if latest else "no-snapshot"

    # Publish taxonomy_changed when a snapshot was created
    snapshot_created = result.snapshot_id != "no-snapshot" and (
        total_ops_attempted > 0
        or result.deadlock_breaker_used
        or engine._warm_path_age == 1
    )
    if snapshot_created:
        try:
            from app.services.event_bus import event_bus

            event_bus.publish("taxonomy_changed", {
                "trigger": "warm_path",
                "operations_accepted": total_ops_accepted,
                "q_system": q_after,
            })
        except Exception as evt_exc:
            logger.warning(
                "Failed to publish taxonomy_changed (warm): %s", evt_exc
            )

    return result
