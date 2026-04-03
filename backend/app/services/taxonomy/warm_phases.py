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

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    MetaPattern,
    Optimization,
    PromptCluster,
)
from app.services.taxonomy._constants import (
    MEGA_CLUSTER_MEMBER_FLOOR,
    SPLIT_COHERENCE_FLOOR,
    SPLIT_MIN_MEMBERS,
    _utcnow,
)
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.clustering import (
    batch_cluster,
    blend_embeddings,
    compute_pairwise_coherence,
    cosine_similarity,
)
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.family_ops import (
    adaptive_merge_threshold,
    extract_meta_patterns,
    merge_meta_pattern,
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
    # Cluster IDs that had split attempts (populated by phase_split_emerge).
    # Used by warm_path to persist split_failures metadata outside the
    # speculative transaction on Q-gate rejection (prevents Groundhog Day loop).
    split_attempted_ids: list[str] = field(default_factory=list)


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

        # Batch-load all optimization embeddings + scores grouped by cluster_id.
        all_emb_q = await db.execute(
            select(
                Optimization.cluster_id,
                Optimization.embedding,
                Optimization.overall_score,
                Optimization.optimized_embedding,
            ).where(
                Optimization.cluster_id.isnot(None),
                Optimization.embedding.isnot(None),
            )
        )
        emb_by_cluster: dict[str, list[np.ndarray]] = {}
        score_by_cluster: dict[str, list[float]] = {}
        opt_emb_by_cluster: dict[str, list[np.ndarray]] = {}
        for cid, emb_bytes, opt_score, opt_emb_bytes in all_emb_q.all():
            if emb_bytes is not None:
                try:
                    emb_by_cluster.setdefault(cid, []).append(
                        np.frombuffer(emb_bytes, dtype=np.float32).copy()
                    )
                    score_by_cluster.setdefault(cid, []).append(
                        max(0.1, (opt_score or 5.0) / 10.0)
                    )
                except (ValueError, TypeError):
                    pass
            if opt_emb_bytes is not None:
                try:
                    opt_emb_by_cluster.setdefault(cid, []).append(
                        np.frombuffer(opt_emb_bytes, dtype=np.float32).copy()
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

            # Output coherence: pairwise cosine of optimized_embeddings.
            # A cluster with high raw coherence but low output coherence
            # produces divergent outputs from similar inputs — a split signal.
            opt_embs = opt_emb_by_cluster.get(node.id, [])
            if len(opt_embs) >= 2:
                output_coh = compute_pairwise_coherence(opt_embs)
                node.cluster_metadata = write_meta(
                    node.cluster_metadata, output_coherence=round(output_coh, 4),
                )
            elif len(opt_embs) == 1:
                node.cluster_metadata = write_meta(
                    node.cluster_metadata, output_coherence=1.0,
                )

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
        # Uses score-weighted mean: each member's influence is proportional
        # to max(0.1, score / 10.0), matching the hot-path formula.
        for node in live_nodes:
            member_embs = emb_by_cluster.get(node.id, [])
            member_scores = score_by_cluster.get(node.id, [])

            # Recompute true weighted_member_sum from per-member scores
            if member_scores:
                node.weighted_member_sum = sum(member_scores)

            # Score-weighted centroid recomputation
            if len(member_embs) >= 2 and len(member_scores) == len(member_embs):
                stacked = np.stack(member_embs, axis=0)
                weights = np.array(member_scores, dtype=np.float32).reshape(-1, 1)
                recomputed = (stacked * weights).sum(axis=0) / weights.sum()
                recomputed = recomputed.astype(np.float32)
                c_norm = np.linalg.norm(recomputed)
                if c_norm > 1e-9:
                    node.centroid_embedding = (recomputed / c_norm).tobytes()
            elif len(member_embs) >= 2:
                # Fallback: no per-member scores, use unweighted mean
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
        zombie_ids: list[str] = []

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
                zombie_ids.append(node.id)
                await engine._embedding_index.remove(node.id)
                await engine._transformation_index.remove(node.id)
                await engine._optimized_index.remove(node.id)

        if zombie_ids:
            try:
                get_event_logger().log_decision(
                    path="warm", op="reconcile", decision="zombies_archived",
                    context={"count": len(zombie_ids), "node_ids": zombie_ids},
                )
            except RuntimeError:
                pass

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
    split_attempted_ids: list[str] = []

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
    # Cache: (opt_id, raw_bytes, optimized_bytes | None, transformation_bytes | None)
    _split_emb_cache: dict[str, list[tuple[str, bytes, bytes | None, bytes | None]]] = {}
    if _split_candidate_ids:
        _split_emb_q = await db.execute(
            select(
                Optimization.id,
                Optimization.cluster_id,
                Optimization.embedding,
                Optimization.optimized_embedding,
                Optimization.transformation_embedding,
            ).where(
                Optimization.cluster_id.in_(_split_candidate_ids),
                Optimization.embedding.isnot(None),
            )
        )
        for opt_id, cid, emb_bytes, opt_bytes, trans_bytes in _split_emb_q.all():
            if emb_bytes is not None:
                _split_emb_cache.setdefault(cid, []).append(
                    (opt_id, emb_bytes, opt_bytes, trans_bytes)
                )

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

        # Output coherence split signal: even if raw coherence is acceptable,
        # low output coherence (similar inputs → divergent outputs) suggests
        # the cluster conflates different optimization goals and should split.
        output_coh = read_meta(node.cluster_metadata).get("output_coherence")
        if output_coh is not None and coherence >= dynamic_floor:
            if output_coh >= 0.25:
                continue  # both coherences are healthy — skip
            # Low output coherence: lower the split threshold to trigger a split
            dynamic_floor = max(dynamic_floor - 0.10, 0.20)
            logger.info(
                "Output coherence split signal for '%s': raw=%.3f, output=%.3f — lowered threshold to %.3f",
                node.label, coherence, output_coh, dynamic_floor,
            )

        if coherence >= dynamic_floor:
            continue

        # Cooldown: skip if this cluster already failed to split 3+ times
        # Growth-based reset: if member_count grew 25%+ since last attempt,
        # new data may create sub-structure that wasn't there before.
        node_meta = read_meta(node.cluster_metadata)
        split_failures = node_meta["split_failures"]
        if split_failures >= 3:
            split_attempt_mc = node_meta.get("split_attempt_member_count", 0)
            if split_attempt_mc > 0 and member_count >= split_attempt_mc * 1.25:
                split_failures = 0
                node.cluster_metadata = write_meta(
                    node.cluster_metadata, split_failures=0,
                )
                logger.info(
                    "Split cooldown reset: '%s' grew from %d to %d members",
                    node.label, split_attempt_mc, member_count,
                )
            else:
                continue

        ops_attempted += 1
        split_attempted_ids.append(node.id)
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
                from app.services.taxonomy.split import split_cluster

                result = await split_cluster(node, engine, db, opt_rows)

                if not result.success:
                    # Track failed attempt for cooldown
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        split_failures=split_failures + 1,
                        split_attempt_member_count=member_count,
                    )
                    logger.info(
                        "Leaf split failed for '%s' (attempt %d/3)",
                        node.label, split_failures + 1,
                    )
                else:
                    # Reset failure counter on success
                    node.cluster_metadata = write_meta(
                        node.cluster_metadata,
                        split_failures=0,
                        split_attempt_member_count=0,
                    )
                    embedding_index_mutations += len(result.children) + 1

                    # Protect split children and parent from merge in same cycle
                    split_protected_ids.add(node.id)
                    for ch in result.children:
                        split_protected_ids.add(ch.id)

                    ops_accepted += result.children_created
                    operations_log.append({
                        "type": "leaf_split",
                        "parent_id": node.id,
                        "children": [c.id for c in result.children],
                    })
                    logger.info(
                        "Leaf split complete: '%s' -> %d sub-clusters",
                        node.label, result.children_created,
                    )
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="split", decision="leaf_split",
                            cluster_id=node.id,
                            context={
                                "trigger": "coherence_floor",
                                "coherence": round(coherence, 4),
                                "floor": round(dynamic_floor, 4),
                                "hdbscan_clusters": result.children_created,
                                "noise_count": result.noise_reassigned,
                                "silhouette": getattr(result, 'silhouette', None),
                                "children": [
                                    {
                                        "id": c.id,
                                        "label": c.label,
                                        "members": c.member_count or 0,
                                        "coherence": round(c.coherence or 0.0, 4),
                                    }
                                    for c in result.children
                                ],
                                "fallback": "none",
                            },
                        )
                    except RuntimeError:
                        pass

        # --- Family-based split path ---
        if len(node_families) >= SPLIT_MIN_MEMBERS:
            child_embs_fam = []
            child_blended_fam = []
            child_fam_ids_fam = []
            opt_idx = getattr(engine, "_optimized_index", None)
            trans_idx = getattr(engine, "_transformation_index", None)
            for f in node_families:
                try:
                    emb = np.frombuffer(
                        f.centroid_embedding, dtype=np.float32
                    )
                    opt_vec = opt_idx.get_vector(f.id) if opt_idx else None
                    trans_vec = trans_idx.get_vector(f.id) if trans_idx else None
                    child_embs_fam.append(emb)
                    child_blended_fam.append(blend_embeddings(
                        raw=emb,
                        optimized=opt_vec,
                        transformation=trans_vec,
                    ))
                    child_fam_ids_fam.append(f.id)
                except (ValueError, TypeError):
                    continue

            if len(child_blended_fam) >= 6:
                split_clusters_fam = batch_cluster(
                    child_blended_fam, min_cluster_size=3
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
                                try:
                                    get_event_logger().log_decision(
                                        path="warm", op="split", decision="family_split",
                                        cluster_id=child.id,
                                        context={
                                            "parent_id": node.id,
                                            "parent_label": node.label,
                                            "children_created": len(children),
                                        },
                                    )
                                except RuntimeError:
                                    pass

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
        split_attempted_ids=split_attempted_ids,
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
    # Use blended centroids (raw + optimized + transformation) for the
    # pairwise similarity matrix so merge candidates reflect topic,
    # output quality, and technique direction — not just topic similarity.
    # Exclude split-protected and merge-cooled nodes from merge candidates.
    now_merge = _utcnow()
    opt_idx = getattr(engine, "_optimized_index", None)
    trans_idx = getattr(engine, "_transformation_index", None)
    if len(active_nodes) >= 2:
        centroids = []
        blended_centroids = []
        valid_nodes: list[PromptCluster] = []
        _global_sp_count = 0
        _global_mc_count = 0
        for n in active_nodes:
            if n.id in split_protected_ids:
                _global_sp_count += 1
                continue
            meta_m = read_meta(n.cluster_metadata)
            merge_until_m = meta_m.get("merge_protected_until", "")
            if merge_until_m:
                try:
                    # INVARIANT: merge_protected_until is stored as naive UTC (no tzinfo).
                    # All comparisons use _utcnow() which is also naive UTC.
                    # Do NOT compare with timezone-aware datetimes.
                    if now_merge < datetime.fromisoformat(merge_until_m):
                        _global_mc_count += 1
                        continue
                except (ValueError, TypeError):
                    pass
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                opt_vec = opt_idx.get_vector(n.id) if opt_idx else None
                trans_vec = trans_idx.get_vector(n.id) if trans_idx else None
                centroids.append(c)
                blended_centroids.append(blend_embeddings(
                    raw=c,
                    optimized=opt_vec,
                    transformation=trans_vec,
                ))
                valid_nodes.append(n)
            except (ValueError, TypeError):
                continue

        if _global_sp_count or _global_mc_count:
            try:
                get_event_logger().log_decision(
                    path="warm", op="merge", decision="candidates_filtered",
                    context={"pass": "global", "split_protected": _global_sp_count, "merge_cooled": _global_mc_count},
                )
            except RuntimeError:
                pass

        if len(blended_centroids) >= 2:
            mat = np.stack(blended_centroids, axis=0).astype(np.float32)
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

            # Quality gates: block merge if either cluster is unhealthy.
            # Gate 1: Coherence floor — merging two fragmented clusters
            # creates a worse fragmented cluster.
            merge_blocked = False
            if (
                (merge_node_a.coherence is not None and merge_node_a.coherence < 0.35)
                or (merge_node_b.coherence is not None and merge_node_b.coherence < 0.35)
            ):
                merge_blocked = True
                logger.debug(
                    "Merge blocked: coherence floor — '%s' (%.2f) + '%s' (%.2f)",
                    merge_node_a.label, merge_node_a.coherence or 0,
                    merge_node_b.label, merge_node_b.coherence or 0,
                )

            # Gate 2: Output coherence — block if either has divergent outputs.
            # Ease threshold only when both are high (similar outputs, safe merge).
            a_meta = read_meta(merge_node_a.cluster_metadata)
            b_meta = read_meta(merge_node_b.cluster_metadata)
            a_out_coh = a_meta.get("output_coherence")
            b_out_coh = b_meta.get("output_coherence")
            if not merge_blocked and (
                (a_out_coh is not None and a_out_coh < 0.30)
                or (b_out_coh is not None and b_out_coh < 0.30)
            ):
                merge_blocked = True
                logger.debug(
                    "Merge blocked: low output coherence — '%s' (%.2f) + '%s' (%.2f)",
                    merge_node_a.label, a_out_coh or 0,
                    merge_node_b.label, b_out_coh or 0,
                )
            elif not merge_blocked and (
                a_out_coh is not None and b_out_coh is not None
                and a_out_coh > 0.5 and b_out_coh > 0.5
            ):
                merge_threshold = max(merge_threshold - 0.03, 0.45)

            if merge_blocked:
                # Determine which gate blocked the merge for observability
                _gate = "coherence_floor"
                if (
                    (a_out_coh is not None and a_out_coh < 0.30)
                    or (b_out_coh is not None and b_out_coh < 0.30)
                ):
                    _gate = "output_floor"
                try:
                    get_event_logger().log_decision(
                        path="warm", op="merge", decision="blocked",
                        context={
                            "pair": [merge_node_a.id, merge_node_b.id],
                            "labels": [merge_node_a.label, merge_node_b.label],
                            "similarity": round(best_score, 4),
                            "threshold": round(merge_threshold, 4),
                            "gate": _gate,
                        },
                    )
                except RuntimeError:
                    pass

            if not merge_blocked and best_score >= merge_threshold:
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
                    loser = (
                        merge_node_b
                        if merged.id == merge_node_a.id
                        else merge_node_a
                    )
                    try:
                        get_event_logger().log_decision(
                            path="warm", op="merge", decision="merged",
                            cluster_id=merged.id,
                            context={
                                "pair": [merge_node_a.id, merge_node_b.id],
                                "labels": [merge_node_a.label, merge_node_b.label],
                                "similarity": round(best_score, 4),
                                "threshold": round(merge_threshold, 4),
                                "gate": "passed",
                                "survivor_id": merged.id,
                                "combined_members": merged.member_count or 0,
                            },
                        )
                    except RuntimeError:
                        pass
                    # Update embedding index: upsert winner, remove loser
                    winner_centroid = np.frombuffer(
                        merged.centroid_embedding, dtype=np.float32
                    )
                    await engine._embedding_index.upsert(
                        merged.id, winner_centroid
                    )
                    await engine._embedding_index.remove(loser.id)
                    await engine._transformation_index.remove(loser.id)
                    await engine._optimized_index.remove(loser.id)
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

        # Group by primary domain, excluding split-protected and merge-cooled nodes
        now = _utcnow()
        domain_groups: dict[str, list[PromptCluster]] = {}
        _domain_sp_count = 0
        _domain_mc_count = 0
        for node in current_active:
            if node.id in split_protected_ids:
                _domain_sp_count += 1
                continue
            # Merge cooldown: split children are protected for 30 minutes
            meta = read_meta(node.cluster_metadata)
            merge_until = meta.get("merge_protected_until", "")
            if merge_until:
                try:
                    # INVARIANT: merge_protected_until is stored as naive UTC (no tzinfo).
                    # All comparisons use _utcnow() which is also naive UTC.
                    # Do NOT compare with timezone-aware datetimes.
                    protected_until = datetime.fromisoformat(merge_until)
                    if now < protected_until:
                        _domain_mc_count += 1
                        continue  # still protected
                except (ValueError, TypeError):
                    pass
            primary, _ = parse_domain(node.domain or "general")
            domain_groups.setdefault(primary, []).append(node)

        if _domain_sp_count or _domain_mc_count:
            try:
                get_event_logger().log_decision(
                    path="warm", op="merge", decision="candidates_filtered",
                    context={"pass": "same_domain", "split_protected": _domain_sp_count, "merge_cooled": _domain_mc_count},
                )
            except RuntimeError:
                pass

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
                    # Use blended centroids for consistency with global merge.
                    try:
                        emb_a = np.frombuffer(
                            survivor.centroid_embedding, dtype=np.float32
                        )
                        emb_b = np.frombuffer(
                            loser.centroid_embedding, dtype=np.float32
                        )
                        blend_a = blend_embeddings(
                            raw=emb_a,
                            optimized=opt_idx.get_vector(survivor.id) if opt_idx else None,
                            transformation=trans_idx.get_vector(survivor.id) if trans_idx else None,
                        )
                        blend_b = blend_embeddings(
                            raw=emb_b,
                            optimized=opt_idx.get_vector(loser.id) if opt_idx else None,
                            transformation=trans_idx.get_vector(loser.id) if trans_idx else None,
                        )
                        sim = cosine_similarity(blend_a, blend_b)
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
                            await engine._transformation_index.remove(loser.id)
                            await engine._optimized_index.remove(loser.id)
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
                        # Use blended centroids for consistency with global merge.
                        try:
                            emb_i = np.frombuffer(
                                remaining[i].centroid_embedding,
                                dtype=np.float32,
                            )
                            emb_j = np.frombuffer(
                                remaining[j].centroid_embedding,
                                dtype=np.float32,
                            )
                            blend_i = blend_embeddings(
                                raw=emb_i,
                                optimized=opt_idx.get_vector(remaining[i].id) if opt_idx else None,
                                transformation=trans_idx.get_vector(remaining[i].id) if trans_idx else None,
                            )
                            blend_j = blend_embeddings(
                                raw=emb_j,
                                optimized=opt_idx.get_vector(remaining[j].id) if opt_idx else None,
                                transformation=trans_idx.get_vector(remaining[j].id) if trans_idx else None,
                            )
                            sim = cosine_similarity(blend_i, blend_j)
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
                            # Block merges that would recreate a mega-cluster.
                            # If the combined member count would exceed the
                            # mega-cluster floor AND either cluster's coherence
                            # is below the split floor, the merge would reform
                            # the exact condition that triggered a split.
                            combined_members = (ni.member_count or 0) + (nj.member_count or 0)
                            either_low_coh = (
                                (ni.coherence is not None and ni.coherence < SPLIT_COHERENCE_FLOOR)
                                or (nj.coherence is not None and nj.coherence < SPLIT_COHERENCE_FLOOR)
                            )
                            if combined_members >= MEGA_CLUSTER_MEMBER_FLOOR and either_low_coh:
                                logger.debug(
                                    "Same-domain merge blocked: would recreate mega-cluster "
                                    "'%s' + '%s' (%d combined, low coherence)",
                                    ni.label, nj.label, combined_members,
                                )
                                try:
                                    get_event_logger().log_decision(
                                        path="warm", op="merge", decision="blocked",
                                        context={
                                            "pair": [ni.id, nj.id],
                                            "labels": [ni.label, nj.label],
                                            "similarity": round(sim, 4),
                                            "threshold": round(same_domain_threshold, 4),
                                            "gate": "mega_cluster_prevention",
                                            "combined_members": combined_members,
                                        },
                                    )
                                except RuntimeError:
                                    pass
                                continue
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
                                await engine._transformation_index.remove(small.id)
                                await engine._optimized_index.remove(small.id)
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

            retire_result = await attempt_retire(
                db=db,
                node=node,
                warm_path_age=engine._warm_path_age,
            )
            if retire_result.success:
                ops_accepted += 1
                operations_log.append({"type": "retire", "node_id": node.id})
                await engine._embedding_index.remove(node.id)
                await engine._transformation_index.remove(node.id)
                await engine._optimized_index.remove(node.id)
                embedding_index_mutations += 1
                try:
                    get_event_logger().log_decision(
                        path="warm", op="retire", decision="archived",
                        cluster_id=node.id,
                        context={
                            "node_label": node.label,
                            "member_count_before": node.member_count or 0,
                            "sibling_target_id": retire_result.sibling_target_id,
                            "sibling_label": retire_result.sibling_label,
                            "families_reparented": retire_result.families_reparented,
                            "optimizations_reassigned": retire_result.optimizations_reassigned,
                        },
                    )
                except RuntimeError:
                    pass

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

    refresh_min_members = 3     # matches domain discovery threshold
    refresh_sample_size = 8     # representative sample for re-extraction
    refresh_cooldown_minutes = 10  # min time between re-extractions
    refresh_min_delta = 3       # min member change to override cooldown

    try:
        from app.services.taxonomy.labeling import generate_label

        # Load active non-domain nodes
        nodes_q = await db.execute(
            select(PromptCluster).where(PromptCluster.state.notin_(["domain", "archived"]))
        )
        active_nodes = list(nodes_q.scalars().all())

        # --- Phase A: Collect stale cluster data (sequential DB queries) ---
        now = _utcnow()
        stale_clusters: list[tuple[PromptCluster, list[str], list]] = []  # (node, member_texts, sample_opts)
        for node in active_nodes:
            if node.state == "domain" or (node.member_count or 0) < refresh_min_members:
                continue
            meta = read_meta(node.cluster_metadata)

            # Event-driven: only refresh clusters marked stale by mutation events
            if not meta.get("pattern_stale", True):
                continue  # patterns are fresh

            # Cooldown: don't re-extract if recently refreshed AND small change
            last_refresh = meta.get("label_refreshed_at", "")
            last_pmc = meta.get("pattern_member_count", 0)
            if last_refresh:
                try:
                    refresh_time = datetime.fromisoformat(last_refresh)
                    age_minutes = (now - refresh_time).total_seconds() / 60
                    member_delta = abs((node.member_count or 0) - last_pmc)
                    if age_minutes < refresh_cooldown_minutes and member_delta < refresh_min_delta:
                        continue  # too soon, too little change
                except (ValueError, TypeError):
                    pass  # malformed timestamp, proceed with refresh

            # Gather representative sample of recent members
            sample_q = await db.execute(
                select(Optimization)
                .where(Optimization.cluster_id == node.id)
                .order_by(Optimization.created_at.desc())
                .limit(refresh_sample_size)
            )
            sample_opts = list(sample_q.scalars().all())
            if len(sample_opts) < 3:
                continue

            member_texts = [
                o.intent_label or (o.raw_prompt or "")[:200]
                for o in sample_opts
            ]
            stale_clusters.append((node, member_texts, sample_opts))

        if not stale_clusters:
            # Nothing to refresh — skip flush and event
            pass
        else:
            # --- Phase B: Parallel label generation (LLM calls, no DB) ---
            label_tasks = [
                generate_label(
                    provider=engine._provider,
                    member_texts=sc[1],
                    model=settings.MODEL_HAIKU,
                )
                for sc in stale_clusters
            ]
            labels = await asyncio.gather(*label_tasks, return_exceptions=True)

            # --- Phase C: Apply labels + sequential pattern extraction ---
            for i, (node, member_texts, sample_opts) in enumerate(stale_clusters):
                new_label = labels[i]
                if isinstance(new_label, BaseException):
                    new_label = None
                if new_label and new_label != "Unnamed Cluster":
                    node.label = new_label

                # Fix #15: extract new patterns FIRST, only delete old ones if
                # extraction succeeds. Pattern extraction uses db, so stays sequential.
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
                    pattern_stale=False,
                    label_refreshed_at=_utcnow().isoformat(),
                )
                result.clusters_refreshed += 1
                logger.info(
                    "Refreshed label+patterns for '%s' (members=%d)",
                    node.label, node.member_count,
                )

        if result.clusters_refreshed:
            await db.flush()
            logger.info(
                "Refreshed label+patterns for %d clusters",
                result.clusters_refreshed,
            )
            try:
                get_event_logger().log_decision(
                    path="warm", op="refresh", decision="patterns_refreshed",
                    context={"count": result.clusters_refreshed},
                )
            except RuntimeError:
                pass
    except Exception as refresh_exc:
        logger.warning(
            "Stale label/pattern refresh failed (non-fatal): %s",
            refresh_exc,
        )

    # Decay phase weights toward defaults (prevents overfitting)
    try:
        from app.services.preferences import PreferencesService
        from app.services.taxonomy.fusion import PhaseWeights, decay_toward_defaults

        prefs_svc = PreferencesService()
        prefs = prefs_svc.load()
        phase_weights = prefs.get("phase_weights", {})
        decayed = False
        for phase_name in ["analysis", "optimization", "pattern_injection", "scoring"]:
            if phase_name in phase_weights:
                current = PhaseWeights.from_dict(phase_weights[phase_name])
                updated = decay_toward_defaults(current, phase_name)
                if updated.to_dict() != phase_weights[phase_name]:
                    phase_weights[phase_name] = updated.to_dict()
                    decayed = True
        if decayed:
            prefs_svc.patch({"phase_weights": phase_weights})
    except Exception as decay_exc:
        logger.debug("Phase weight decay failed (non-fatal): %s", decay_exc)

    # Score-correlated phase weight adaptation
    # Queries recent scored optimizations, computes score-weighted optimal
    # profile from above-median results, adapts current weights toward it.
    # Runs AFTER decay so that adaptation (alpha=0.05) dominates over
    # decay (rate=0.01) when there is strong quality signal.
    #
    # Two levels: (1) global adaptation updates preferences as a cross-task
    # regularizer, (2) per-cluster adaptation stores learned_phase_weights on
    # each cluster so future members inherit a proven profile.
    try:
        from app.services.taxonomy.fusion import (
            SCORE_ADAPTATION_LOOKBACK,
            SCORE_ADAPTATION_MIN_SAMPLES,
            adapt_weights,
            compute_score_correlated_target,
        )

        scored_q = await db.execute(
            select(
                Optimization.overall_score,
                Optimization.phase_weights_json,
                Optimization.cluster_id,
            ).where(
                Optimization.overall_score.isnot(None),
                Optimization.phase_weights_json.isnot(None),
                Optimization.status == "completed",
            ).order_by(
                Optimization.created_at.desc(),
            ).limit(SCORE_ADAPTATION_LOOKBACK)
        )
        scored_rows = scored_q.all()

        # --- Global adaptation (existing) ---
        if len(scored_rows) >= SCORE_ADAPTATION_MIN_SAMPLES:
            scored_profiles = [
                (float(row[0]), row[1])
                for row in scored_rows
            ]
            target_profiles = compute_score_correlated_target(scored_profiles)

            if target_profiles:
                prefs_svc_sc = PreferencesService()
                prefs_sc = prefs_svc_sc.load()
                phase_weights_sc = prefs_sc.get("phase_weights", {})
                adapted = False

                for phase_name_sc, target_pw in target_profiles.items():
                    current_dict_sc = phase_weights_sc.get(phase_name_sc, {})
                    current_pw_sc = PhaseWeights.from_dict(current_dict_sc)
                    updated_pw_sc = adapt_weights(current_pw_sc, target_pw)
                    new_dict = updated_pw_sc.to_dict()
                    if new_dict != phase_weights_sc.get(phase_name_sc):
                        phase_weights_sc[phase_name_sc] = new_dict
                        adapted = True

                if adapted:
                    prefs_svc_sc.patch({"phase_weights": phase_weights_sc})
                    logger.info(
                        "Score-correlated adaptation applied from %d scored optimizations",
                        len(scored_rows),
                    )

        # --- Per-cluster adaptation (new) ---
        # Group scored profiles by cluster, compute per-cluster target,
        # and store as learned_phase_weights in cluster_metadata.
        # This closes the learning loop: cluster members snapshot contextual
        # weights -> warm path discovers which profiles correlate with high
        # scores for THAT cluster -> cluster stores learned weights -> new
        # members inherit the cluster's proven profile.
        cluster_groups: dict[str, list[tuple[float, dict]]] = {}
        for row in scored_rows:
            cid = row[2]
            if cid:
                cluster_groups.setdefault(cid, []).append((float(row[0]), row[1]))

        clusters_adapted = 0
        for cid, members in cluster_groups.items():
            if len(members) < SCORE_ADAPTATION_MIN_SAMPLES:
                continue
            cluster_target = compute_score_correlated_target(members)
            if not cluster_target:
                continue
            cluster_q = await db.execute(
                select(PromptCluster).where(PromptCluster.id == cid)
            )
            cluster_node = cluster_q.scalar_one_or_none()
            if cluster_node:
                cluster_node.cluster_metadata = write_meta(
                    cluster_node.cluster_metadata,
                    learned_phase_weights={
                        phase: pw.to_dict() for phase, pw in cluster_target.items()
                    },
                )
                clusters_adapted += 1

        if clusters_adapted:
            await db.flush()
            logger.info(
                "Per-cluster weight adaptation applied to %d clusters",
                clusters_adapted,
            )
    except Exception as sc_exc:
        logger.debug("Score-correlated adaptation failed (non-fatal): %s", sc_exc)

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

    # --- Sub-domain discovery (intra-domain HDBSCAN) ---
    try:
        new_sub_domains = await engine._propose_sub_domains(db)
        if new_sub_domains:
            result.domains_created += len(new_sub_domains)
            logger.info(
                "Warm path discovered %d sub-domains: %s",
                len(new_sub_domains), new_sub_domains,
            )
    except Exception as sub_exc:
        logger.warning(
            "Sub-domain discovery failed (non-fatal): %s", sub_exc
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
    # Reuse last cold-path silhouette score. Warm path lacks the full member
    # embedding matrix needed for silhouette_score (centroids alone produce
    # unique labels which sklearn rejects). The cold path computes the valid
    # silhouette from HDBSCAN's blended embeddings and stores it on the engine.
    q_after = engine._compute_q_from_nodes(
        active_after, silhouette=engine._last_silhouette
    )
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
