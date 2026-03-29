"""TaxonomyEngine — hot, warm, and cold path orchestration for the Evolutionary
Taxonomy Engine.

Spec Section 2.3, 2.5, 2.6, 3.5, 4.2, 6.4, 7.3, 7.5, 8.5.

Responsibilities:
  - process_optimization: embed + assign cluster + extract meta-patterns (hot path)
  - run_warm_path: periodic re-clustering with lifecycle (split > emerge > merge > retire)
  - run_cold_path: full HDBSCAN + UMAP refit (the "defrag" operation)
  - map_domain / match_prompt: delegated to matching.py
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR, settings
from app.models import (
    Optimization,
    OptimizationPattern,
    PromptCluster,
    TaxonomySnapshot,
)
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader
from app.services.taxonomy.embedding_index import EmbeddingIndex
from app.services.taxonomy.family_ops import (
    FAMILY_MERGE_THRESHOLD,
    assign_cluster,
    build_breadcrumb,
    extract_meta_patterns,
    merge_meta_pattern,
)
from app.services.taxonomy.matching import (
    PatternMatch,
    TaxonomyMapping,
)
from app.services.taxonomy.matching import (
    map_domain as _map_domain,
)
from app.services.taxonomy.matching import (
    match_prompt as _match_prompt,
)
from app.services.taxonomy.sparkline import compute_sparkline_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — warm path operational limits
# (Matching thresholds imported from matching.py and re-exported for
#  backward compatibility)
# ---------------------------------------------------------------------------

DEADLOCK_BREAKER_THRESHOLD = 5  # consecutive rejected cycles before forcing
SPLIT_COHERENCE_FLOOR = 0.5  # below this coherence, node is a split candidate
SPLIT_MIN_MEMBERS = 6  # minimum members before a node can be split


@dataclass
class WarmPathResult:
    """Return value from run_warm_path()."""

    snapshot_id: str
    q_system: float | None
    operations_attempted: int
    operations_accepted: int
    deadlock_breaker_used: bool


@dataclass
class ColdPathResult:
    """Return value from run_cold_path()."""

    snapshot_id: str
    q_system: float | None
    nodes_created: int
    nodes_updated: int
    umap_fitted: bool


# ---------------------------------------------------------------------------
# TaxonomyEngine
# ---------------------------------------------------------------------------


class TaxonomyEngine:
    """Orchestrates the hot path for the Evolutionary Taxonomy Engine.

    Args:
        embedding_service: EmbeddingService instance (or mock in tests).
        provider: LLM provider for Haiku calls. None disables LLM steps.
            Ignored when *provider_resolver* is set.
        provider_resolver: Callable returning the current LLM provider.
            When set, ``_provider`` resolves lazily on every access so
            hot-reloaded providers (e.g. API key change) are picked up
            automatically.  Falls back to *provider* if not given.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        provider: LLMProvider | None = None,
        provider_resolver: Callable[[], LLMProvider | None] | None = None,
    ) -> None:
        self._embedding = embedding_service or EmbeddingService()
        self._provider_direct: LLMProvider | None = provider
        self._provider_resolver = provider_resolver
        self._prompt_loader = PromptLoader(PROMPTS_DIR)
        self._embedding_index = EmbeddingIndex(dim=384)
        # Lock gates concurrent hot-path writes to shared centroid state.
        self._lock: asyncio.Lock = asyncio.Lock()
        # Separate lock for warm/cold path deduplication (Spec Section 2.6).
        self._warm_path_lock: asyncio.Lock = asyncio.Lock()
        # Deadlock breaker counter (Spec Section 2.5).
        self._consecutive_rejected_cycles: int = 0
        # Warm-path age counter for adaptive epsilon tolerance.
        self._warm_path_age: int = 0
        # Set by deadlock breaker — caller should schedule cold path.
        self._cold_path_needed: bool = False

    @property
    def _provider(self) -> LLMProvider | None:
        """Resolve the current LLM provider, preferring the live resolver."""
        if self._provider_resolver is not None:
            return self._provider_resolver()
        return self._provider_direct

    @property
    def embedding_index(self) -> EmbeddingIndex:
        """In-memory embedding search index for PromptCluster centroids."""
        return self._embedding_index

    # ------------------------------------------------------------------
    # Public hot-path entry point
    # ------------------------------------------------------------------

    async def process_optimization(
        self,
        optimization_id: str,
        db: AsyncSession,
    ) -> None:
        """Full extraction pipeline for a single completed optimization.

        Steps:
          1. Load optimization — skip if not 'completed'.
          2. Idempotency check via OptimizationPattern 'source' record.
          3. Embed raw_prompt.
          4. Find or create PromptCluster via _assign_cluster().
          5. Extract meta-patterns via _extract_meta_patterns().
          6. Merge meta-patterns via _merge_meta_pattern().
          7. Write OptimizationPattern join record and commit.

        Args:
            optimization_id: PK of the Optimization row to process.
            db: Async SQLAlchemy session.
        """
        try:
            result = await db.execute(
                select(Optimization).where(Optimization.id == optimization_id)
            )
            opt = result.scalar_one_or_none()

            if not opt or opt.status != "completed":
                logger.debug(
                    "Skipping taxonomy extraction for %s (status=%s)",
                    optimization_id,
                    opt.status if opt else "not_found",
                )
                return

            # Idempotency: skip if a 'source' OptimizationPattern already exists
            existing = await db.execute(
                select(OptimizationPattern).where(
                    OptimizationPattern.optimization_id == optimization_id,
                    OptimizationPattern.relationship == "source",
                )
            )
            if existing.scalar_one_or_none():
                logger.debug(
                    "Skipping taxonomy extraction for %s (already processed)",
                    optimization_id,
                )
                return

            # 1. Embed raw_prompt
            embedding = await self._embedding.aembed_single(opt.raw_prompt)
            opt.embedding = embedding.astype(np.float32).tobytes()

            # 2. Find or create PromptCluster
            # Use domain_raw (free-text from analyzer) as the canonical domain.
            # The taxonomy engine does NOT constrain domains to a hardcoded list —
            # domains are emergent properties discovered through clustering.
            async with self._lock:
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=opt.intent_label or "general",
                    domain=opt.domain_raw or opt.domain or "general",
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                    embedding_index=self._embedding_index,
                )

            # 3. Extract meta-patterns
            meta_texts = await extract_meta_patterns(
                opt, db, self._provider, self._prompt_loader,
            )

            # 4. Merge meta-patterns
            for text in meta_texts:
                await merge_meta_pattern(db, cluster.id, text, self._embedding)

            # 5. Write join record
            join = OptimizationPattern(
                optimization_id=opt.id,
                cluster_id=cluster.id,
                relationship="source",
            )
            db.add(join)

            await db.commit()
            logger.debug(
                "Taxonomy extraction complete: opt=%s cluster='%s' meta_patterns=%d",
                optimization_id,
                cluster.label,
                len(meta_texts),
            )

            # 6. Publish taxonomy_changed event (Spec Section 6.5)
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("taxonomy_changed", {
                    "optimization_id": optimization_id,
                    "cluster_id": cluster.id,
                    "cluster_label": cluster.label,
                    "meta_patterns_added": len(meta_texts),
                })
            except Exception as evt_exc:
                logger.warning("Failed to publish taxonomy_changed: %s", evt_exc)

        except Exception as exc:
            logger.error(
                "Taxonomy process_optimization failed for %s: %s",
                optimization_id,
                exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Pattern matching — delegated to matching.py
    # ------------------------------------------------------------------

    async def match_prompt(
        self, prompt_text: str, db: AsyncSession,
    ) -> PatternMatch | None:
        """Hierarchical pattern matching for on-paste suggestion.

        Delegates to :func:`matching.match_prompt`.
        """
        return await _match_prompt(prompt_text, db, self._embedding)

    # ------------------------------------------------------------------
    # Warm path (Spec Section 2.3, 2.5, 2.6, 3.5)
    # ------------------------------------------------------------------

    async def run_warm_path(self, db: AsyncSession) -> WarmPathResult | None:
        """Periodic re-clustering with lifecycle operations.

        Checks ``_warm_path_lock`` for deduplication first — if already held
        by another coroutine, returns None (skip).  Otherwise acquires the
        lock and runs lifecycle operations in priority order:
        split > emerge > merge > retire.

        Non-regression: Q_after >= Q_before - epsilon.  If violated, the
        cycle's operations are not committed.  If ALL operations are rejected
        for 5 consecutive cycles, a deadlock breaker forces the single best
        operation through and schedules a cold path.

        Returns:
            WarmPathResult on success, or None if skipped due to lock.
        """
        # Lock deduplication (Spec Section 2.6).
        # In asyncio single-thread model, locked() + acquire is safe —
        # no preemption between check and context manager entry.
        if self._warm_path_lock.locked():
            logger.debug("Warm path skipped — lock already held")
            return None

        async with self._warm_path_lock:
            try:
                return await self._run_warm_path_inner(db)
            except Exception as exc:
                logger.error("Warm path failed: %s", exc, exc_info=True)
                # Return a minimal result so callers don't break
                try:
                    snap = await self._create_warm_snapshot(
                        db, q_system=0.0, operations=[], ops_attempted=0, ops_accepted=0,
                    )
                    snapshot_id = snap.id
                except Exception as snap_exc:
                    logger.error(
                        "Warm path error-recovery snapshot also failed: %s",
                        snap_exc, exc_info=True,
                    )
                    snapshot_id = "error-no-snapshot"
                return WarmPathResult(
                    snapshot_id=snapshot_id,
                    q_system=0.0,
                    operations_attempted=0,
                    operations_accepted=0,
                    deadlock_breaker_used=False,
                )

    async def _run_warm_path_inner(self, db: AsyncSession) -> WarmPathResult:
        """Core warm path logic — called under _warm_path_lock."""
        from app.services.taxonomy.clustering import (
            batch_cluster,
        )
        from app.services.taxonomy.quality import (
            is_non_regressive,
        )

        # 1. Load all active nodes
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "active")
        )
        active_nodes = list(result.scalars().all())

        # 2. Compute Q_before
        q_before = self._compute_q_from_nodes(active_nodes)

        # 3. Gather candidate operations from lifecycle module
        ops_attempted = 0
        ops_accepted = 0
        operations_log: list[dict] = []
        deadlock_breaker_used = False

        # --- Priority 1: Split (Spec Section 3.5) ---
        # Detect split candidates: active nodes with low coherence and enough
        # members to produce viable child clusters.
        for node in active_nodes:
            coherence = node.coherence if node.coherence is not None else 1.0
            if coherence < SPLIT_COHERENCE_FLOOR and (node.member_count or 0) >= SPLIT_MIN_MEMBERS:
                ops_attempted += 1
                # Gather families assigned to this node
                fam_q = await db.execute(
                    select(PromptCluster).where(
                        PromptCluster.parent_id == node.id
                    )
                )
                node_families = list(fam_q.scalars().all())
                if len(node_families) >= SPLIT_MIN_MEMBERS:
                    child_embs = []
                    child_fam_ids = []
                    for f in node_families:
                        try:
                            emb = np.frombuffer(
                                f.centroid_embedding, dtype=np.float32
                            )
                            child_embs.append(emb)
                            child_fam_ids.append(f.id)
                        except (ValueError, TypeError):
                            continue

                    if len(child_embs) >= 6:
                        split_clusters = batch_cluster(
                            child_embs, min_cluster_size=3
                        )
                        if split_clusters.n_clusters >= 2:
                            from app.services.taxonomy.lifecycle import (
                                attempt_split,
                            )

                            child_groups = []
                            for cid in range(split_clusters.n_clusters):
                                mask = split_clusters.labels == cid
                                group_ids = [
                                    child_fam_ids[i]
                                    for i in range(len(child_fam_ids))
                                    if mask[i]
                                ]
                                group_embs = [
                                    child_embs[i]
                                    for i in range(len(child_embs))
                                    if mask[i]
                                ]
                                if group_ids:
                                    child_groups.append((group_ids, group_embs))

                            if len(child_groups) >= 2:
                                children = await attempt_split(
                                    db=db,
                                    parent_node=node,
                                    child_clusters=child_groups,
                                    warm_path_age=self._warm_path_age,
                                    provider=self._provider,
                                    model=settings.MODEL_HAIKU,
                                )
                                if children:
                                    ops_accepted += len(children)
                                    for child in children:
                                        operations_log.append(
                                            {
                                                "type": "split",
                                                "node_id": child.id,
                                            }
                                        )

        # --- Priority 2: Emerge ---
        # Load unassigned families (no cluster_id) for emerge candidates
        fam_result = await db.execute(
            select(PromptCluster).where(PromptCluster.parent_id.is_(None))
        )
        unassigned_families = list(fam_result.scalars().all())

        # Try emerge if enough unassigned families exist
        if len(unassigned_families) >= 3:
            ops_attempted += 1
            emerged = await self._try_emerge_from_families(
                db, unassigned_families, batch_cluster,
            )
            ops_accepted += len(emerged)
            operations_log.extend(emerged)

        # Try merge on active nodes that are close in embedding space
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
                # Find pairs with high similarity for merge candidates
                mat = np.stack(centroids, axis=0).astype(np.float32)
                norms = np.linalg.norm(mat, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1.0, norms)
                mat_norm = mat / norms
                sim = mat_norm @ mat_norm.T
                np.fill_diagonal(sim, -1)

                best_i, best_j = np.unravel_index(np.argmax(sim), sim.shape)
                best_score = float(sim[best_i, best_j])

                if best_score >= FAMILY_MERGE_THRESHOLD:
                    ops_attempted += 1
                    from app.services.taxonomy.lifecycle import attempt_merge

                    merge_node_a = valid_nodes[int(best_i)]
                    merge_node_b = valid_nodes[int(best_j)]
                    merged = await attempt_merge(
                        db=db,
                        node_a=merge_node_a,
                        node_b=merge_node_b,
                        warm_path_age=self._warm_path_age,
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
                        await self._embedding_index.upsert(
                            merged.id, winner_centroid
                        )
                        loser = (
                            merge_node_b
                            if merged.id == merge_node_a.id
                            else merge_node_a
                        )
                        await self._embedding_index.remove(loser.id)

        # Try retire on idle nodes (member_count == 0 and active)
        for node in active_nodes:
            if (node.member_count or 0) == 0:
                ops_attempted += 1
                from app.services.taxonomy.lifecycle import attempt_retire

                retired = await attempt_retire(
                    db=db,
                    node=node,
                    warm_path_age=self._warm_path_age,
                )
                if retired:
                    ops_accepted += 1
                    operations_log.append({"type": "retire", "node_id": node.id})
                    await self._embedding_index.remove(node.id)

        # --- Domain discovery (ADR-004) ---
        # After lifecycle mutations, before quality gate.  Domain nodes have
        # state="domain" so they don't affect Q_system (computed from active
        # nodes only).  Safe to run even if lifecycle ops are later rolled back.
        new_domains = await self._propose_domains(db)
        if new_domains:
            logger.info(
                "Warm path discovered %d new domains: %s",
                len(new_domains), new_domains,
            )

        # --- Risk monitoring (ADR-004 Section 8B) ---
        try:
            await self._monitor_general_health(db)
            stale_domains = await self._check_signal_staleness(db)
            for stale_domain in stale_domains:
                await self._refresh_domain_signals(db, stale_domain)
            await self._suggest_domain_archival(db)
        except Exception as risk_exc:
            logger.warning("Risk monitoring failed (non-fatal): %s", risk_exc)

        # --- Tree integrity check + auto-repair (ADR-004 Risk 5) ---
        try:
            violations = await self.verify_domain_tree_integrity(db)
            if violations:
                repaired = await self._repair_tree_violations(db, violations)
                logger.warning(
                    "Tree integrity: %d violations, %d repaired",
                    len(violations), repaired,
                )
        except Exception as integrity_exc:
            logger.warning("Tree integrity check failed (non-fatal): %s", integrity_exc)

        # 4. Update per-node separation and compute Q_after
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "active")
        )
        active_after = list(result.scalars().all())
        self._update_per_node_separation(active_after)
        q_after = self._compute_q_from_nodes(active_after)

        if ops_accepted > 0 and not is_non_regressive(
            q_before, q_after, self._warm_path_age
        ):
            logger.warning(
                "Warm path quality regression: Q_before=%.4f Q_after=%.4f — "
                "rolling back operations",
                q_before,
                q_after,
            )
            await db.rollback()
            q_after = q_before
            ops_accepted = 0
            operations_log = []
            # Re-query active nodes after rollback so cache isn't stale
            result = await db.execute(
                select(PromptCluster).where(PromptCluster.state == "active")
            )
            active_after = list(result.scalars().all())

        # 5. Deadlock breaker (Spec Section 2.5)
        if ops_attempted > 0 and ops_accepted == 0:
            self._consecutive_rejected_cycles += 1
        else:
            self._consecutive_rejected_cycles = 0

        if self._consecutive_rejected_cycles >= DEADLOCK_BREAKER_THRESHOLD:
            logger.warning(
                "Deadlock breaker triggered after %d consecutive rejected cycles "
                "— forcing best single-dimension operation and scheduling cold path",
                self._consecutive_rejected_cycles,
            )
            deadlock_breaker_used = True
            self._consecutive_rejected_cycles = 0

            # Force the best single-dimension operation through regardless of
            # composite Q_system impact (Spec Section 2.5).
            # Re-run emerge on unassigned families (cheapest constructive op),
            # limited to the first discovered cluster.
            if len(unassigned_families) >= 3:
                emerged = await self._try_emerge_from_families(
                    db, unassigned_families, batch_cluster, max_clusters=1,
                )
                ops_accepted += len(emerged)
                operations_log.extend(emerged)
                if emerged:
                    logger.info(
                        "Deadlock breaker forced emerge: node=%s",
                        emerged[0].get("node_id"),
                    )

            # Signal that a cold-path rebuild is needed (Spec Section 2.5).
            # We do NOT use asyncio.create_task(self.run_cold_path(db)) here
            # because the cold path would receive the same AsyncSession, which
            # is not safe for concurrent use across tasks.  The caller should
            # schedule a cold path with a fresh session.
            self._cold_path_needed = True
            logger.warning(
                "Cold path rebuild needed — caller should invoke "
                "run_cold_path() with a fresh session"
            )

        # 6. Create snapshot
        self._warm_path_age += 1
        snap = await self._create_warm_snapshot(
            db,
            q_system=q_after,
            operations=operations_log,
            ops_attempted=ops_attempted,
            ops_accepted=ops_accepted,
        )

        result = WarmPathResult(
            snapshot_id=snap.id,
            q_system=q_after,
            operations_attempted=ops_attempted,
            operations_accepted=ops_accepted,
            deadlock_breaker_used=deadlock_breaker_used,
        )

        try:
            from app.services.event_bus import event_bus
            event_bus.publish("taxonomy_changed", {
                "trigger": "warm_path",
                "operations_accepted": result.operations_accepted,
                "q_system": result.q_system,
            })
        except Exception as evt_exc:
            logger.warning("Failed to publish taxonomy_changed (warm): %s", evt_exc)

        return result

    # ------------------------------------------------------------------
    # Cold path (Spec Section 2.3, 8.5)
    # ------------------------------------------------------------------

    async def run_cold_path(self, db: AsyncSession) -> ColdPathResult | None:
        """Full HDBSCAN + UMAP refit — the "defrag" operation.

        Acquires the same ``_warm_path_lock`` (cold path is a superset of
        warm path).  Recluster all PromptCluster embeddings, update or create
        PromptClusters, run UMAP 3D projection with Procrustes alignment,
        regenerate OKLab colors, create snapshot.

        Returns:
            ColdPathResult on completion.
        """
        async with self._warm_path_lock:
            try:
                return await self._run_cold_path_inner(db)
            except Exception as exc:
                logger.error("Cold path failed: %s", exc, exc_info=True)
                try:
                    from app.services.taxonomy.snapshot import create_snapshot
                    snap = await create_snapshot(
                        db,
                        trigger="cold_path",
                        q_system=0.0,
                        q_coherence=0.0,
                        q_separation=0.0,
                        q_coverage=0.0,
                    )
                    snapshot_id = snap.id
                except Exception as snap_exc:
                    logger.error(
                        "Cold path error-recovery snapshot also failed: %s",
                        snap_exc, exc_info=True,
                    )
                    snapshot_id = "error-no-snapshot"
                return ColdPathResult(
                    snapshot_id=snapshot_id,
                    q_system=0.0,
                    nodes_created=0,
                    nodes_updated=0,
                    umap_fitted=False,
                )

    async def _run_cold_path_inner(self, db: AsyncSession) -> ColdPathResult:
        """Core cold-path logic — called under _warm_path_lock."""
        from app.services.taxonomy.clustering import (
            batch_cluster,
            compute_pairwise_coherence,
            compute_separation,
            cosine_similarity,
        )
        from app.services.taxonomy.coloring import (
            enforce_minimum_delta_e,
            generate_color,
        )
        from app.services.taxonomy.projection import UMAPProjector, procrustes_align
        from app.services.taxonomy.snapshot import create_snapshot

        nodes_created = 0
        nodes_updated = 0

        # 1. Load all PromptCluster embeddings
        fam_result = await db.execute(select(PromptCluster))
        families = list(fam_result.scalars().all())

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
                    "Skipping cluster '%s' — corrupt centroid", f.label
                )

        # 2. Run HDBSCAN clustering
        if len(embeddings) >= 3:
            cluster_result = batch_cluster(embeddings, min_cluster_size=3)
        else:
            # Not enough data for clustering — still create snapshot
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
                q_system=0.0,
                nodes_created=0,
                nodes_updated=0,
                umap_fitted=False,
            )

        # 3. Create/update PromptClusters from cluster results
        # Load existing active nodes for potential updates
        existing_result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.in_(["active", "candidate"])
            )
        )
        existing_nodes = {n.id: n for n in existing_result.scalars().all()}

        node_embeddings: list[np.ndarray] = []
        all_nodes: list[PromptCluster] = []

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

            centroid = cluster_result.centroids[cid] if cid < len(cluster_result.centroids) else None
            if centroid is None:
                from app.services.taxonomy.clustering import l2_normalize_1d
                centroid = l2_normalize_1d(
                    np.mean(np.stack(cluster_embs, axis=0), axis=0).astype(np.float32)
                )

            coherence = compute_pairwise_coherence(cluster_embs)

            # Try to match existing node by closest centroid
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

                if best_match_id and best_sim >= FAMILY_MERGE_THRESHOLD:
                    matched_node = existing_nodes.pop(best_match_id)

            if matched_node:
                # Update existing node
                matched_node.centroid_embedding = centroid.astype(
                    np.float32
                ).tobytes()
                matched_node.member_count = len(cluster_fam_ids)
                matched_node.coherence = coherence
                matched_node.state = "active"
                nodes_updated += 1
                node = matched_node
            else:
                # Create new node
                from app.services.taxonomy.labeling import generate_label

                member_texts = [
                    f.label
                    for f in valid_families
                    if f.id in set(cluster_fam_ids) and f.label
                ]
                label = await generate_label(
                    provider=self._provider,
                    member_texts=member_texts,
                    model=settings.MODEL_HAIKU,
                )
                node = PromptCluster(
                    label=label,
                    centroid_embedding=centroid.astype(np.float32).tobytes(),
                    member_count=len(cluster_fam_ids),
                    coherence=coherence,
                    state="active",
                    color_hex=generate_color(0.0, 0.0, 0.0),
                )
                db.add(node)
                await db.flush()
                nodes_created += 1

            # Link families to this node (O(k) via dict lookup)
            for fid in cluster_fam_ids:
                fam = family_by_id.get(fid)
                if fam:
                    fam.parent_id = node.id

            node_embeddings.append(centroid)
            all_nodes.append(node)

        # 4. UMAP 3D projection
        umap_fitted = False
        if node_embeddings:
            projector = UMAPProjector()
            positions = projector.fit(node_embeddings)

            # Procrustes alignment against previous positions if available
            old_positions = []
            has_old = True
            for node in all_nodes:
                if node.umap_x is not None and node.umap_y is not None and node.umap_z is not None:
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

        # 5. Regenerate OKLab colors from UMAP positions
        color_pairs: list[tuple[str, str]] = []
        for node in all_nodes:
            if node.state == "domain":
                continue  # Domain colors are pinned at creation time (ADR-004 Guardrail #1)
            if node.umap_x is not None and node.umap_y is not None and node.umap_z is not None:
                new_color = generate_color(node.umap_x, node.umap_y, node.umap_z)
                color_pairs.append((node.id, new_color))

        if color_pairs:
            enforced = enforce_minimum_delta_e(color_pairs)
            node_by_id = {n.id: n for n in all_nodes}
            for node_id, color_hex in enforced:
                if node_id in node_by_id:
                    node_by_id[node_id].color_hex = color_hex

        # 6. Compute per-node separation and update on each node
        #    Each node's separation = min cosine distance to any other node.
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "active")
        )
        active_after = list(result.scalars().all())

        self._update_per_node_separation(active_after)

        # 7. Compute final Q_system (reads node.separation set above)
        q_system = self._compute_q_from_nodes(active_after)

        # 8. Aggregate metrics for snapshot
        node_centroids = []
        for n in active_after:
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                node_centroids.append(c)
            except (ValueError, TypeError):
                continue

        separation = compute_separation(node_centroids) if len(node_centroids) >= 2 else 1.0
        coherences = [n.coherence for n in active_after if n.coherence is not None]
        mean_coherence = float(np.mean(coherences)) if coherences else 0.0

        # 8b. Rebuild embedding index from active centroids (in-memory, no
        # commit needed — reads ORM objects still in the session).
        index_centroids: dict[str, np.ndarray] = {}
        for n in active_after:
            try:
                emb = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                if emb.shape[0] == 384:
                    index_centroids[n.id] = emb
            except (ValueError, TypeError):
                continue
        await self._embedding_index.rebuild(index_centroids)

        # 9. Create snapshot — commits all pending node updates AND the
        # snapshot in a single transaction (matching the warm-path pattern).
        snap = await create_snapshot(
            db,
            trigger="cold_path",
            q_system=q_system,
            q_coherence=mean_coherence,
            q_separation=separation,
            q_coverage=1.0,
            nodes_created=nodes_created,
        )

        # 10. Reset deadlock breaker flag (cold path has run)
        self._cold_path_needed = False

        result = ColdPathResult(
            snapshot_id=snap.id,
            q_system=q_system,
            nodes_created=nodes_created,
            nodes_updated=nodes_updated,
            umap_fitted=umap_fitted,
        )

        # 11. Publish taxonomy_changed event (parity with warm path)
        try:
            from app.services.event_bus import event_bus
            event_bus.publish("taxonomy_changed", {
                "trigger": "cold_path",
                "nodes_created": nodes_created,
                "nodes_updated": nodes_updated,
                "q_system": q_system,
            })
        except Exception as evt_exc:
            logger.warning("Failed to publish taxonomy_changed (cold): %s", evt_exc)

        return result

    # ------------------------------------------------------------------
    # Warm/cold path helpers
    # ------------------------------------------------------------------

    def _compute_q_from_nodes(self, nodes: list[PromptCluster]) -> float:
        """Compute Q_system from a list of PromptCluster rows."""
        from app.services.taxonomy.quality import (
            NodeMetrics,
            QWeights,
            compute_q_system,
        )

        if not nodes:
            return 0.0

        metrics = []
        for n in nodes:
            metrics.append(
                NodeMetrics(
                    coherence=n.coherence if n.coherence is not None else 0.0,
                    separation=n.separation if n.separation is not None else 1.0,
                    state=n.state or "active",
                )
            )

        # DBCV ramp: gate = >=5 active nodes, then ramp linearly over
        # warm_path_age / 20 observations (Spec Section 2.5).
        active_count = sum(1 for m in metrics if m.state == "active")
        if active_count >= 5:
            ramp = min(1.0, max(0.0, self._warm_path_age / 20.0))
        else:
            ramp = 0.0
        weights = QWeights.from_ramp(ramp)

        return compute_q_system(metrics, weights)

    @staticmethod
    def _update_per_node_separation(nodes: list[PromptCluster]) -> None:
        """Set each node's ``separation`` to the minimum cosine distance to any sibling.

        For a single node, separation is 1.0 (no siblings to conflict with).
        Modifies nodes in-place.
        """
        if len(nodes) <= 1:
            for n in nodes:
                n.separation = 1.0
            return

        # Build centroid matrix
        valid: list[tuple[int, np.ndarray]] = []
        for i, n in enumerate(nodes):
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32).copy()
                valid.append((i, c))
            except (ValueError, TypeError):
                n.separation = 1.0  # default for corrupt centroid

        if len(valid) < 2:
            for n in nodes:
                n.separation = 1.0
            return

        mat = np.stack([c for _, c in valid], axis=0).astype(np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        mat_norm = mat / norms
        sim_matrix = mat_norm @ mat_norm.T  # cosine similarity
        dist_matrix = 1.0 - sim_matrix       # cosine distance

        # Fill diagonal with inf so self-distance is ignored
        np.fill_diagonal(dist_matrix, np.inf)

        for j, (orig_idx, _) in enumerate(valid):
            min_dist = float(dist_matrix[j].min())
            # Clamp to [0, 1] for safety
            nodes[orig_idx].separation = max(0.0, min(1.0, min_dist))

    async def _try_emerge_from_families(
        self,
        db: AsyncSession,
        families: list[PromptCluster],
        batch_cluster_fn: object,
        max_clusters: int | None = None,
    ) -> list[dict]:
        """Cluster unassigned families via HDBSCAN and attempt emerge for each.

        Shared by the normal emerge path and the deadlock breaker to avoid
        duplicating the embed → cluster → emerge pipeline.

        Args:
            db: Async DB session (caller-managed transaction).
            families: Unassigned PromptCluster rows (parent_id IS NULL).
            batch_cluster_fn: HDBSCAN clustering callable (typically
                ``clustering.batch_cluster``).
            max_clusters: If set, only attempt emerge for the first N
                discovered clusters (deadlock breaker uses 1).

        Returns:
            List of ``{"type": "emerge", "node_id": ...}`` operation dicts
            for each successfully emerged node.
        """
        from app.services.taxonomy.clustering import ClusterResult
        from app.services.taxonomy.lifecycle import attempt_emerge

        # Extract embeddings and IDs from families
        embeddings: list[np.ndarray] = []
        ids: list[str] = []
        for f in families:
            try:
                emb = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                embeddings.append(emb)
                ids.append(f.id)
            except (ValueError, TypeError):
                continue

        if len(embeddings) < 3:
            return []

        cr: ClusterResult = batch_cluster_fn(embeddings)
        if cr.n_clusters == 0:
            return []

        operations: list[dict] = []
        limit = cr.n_clusters if max_clusters is None else min(max_clusters, cr.n_clusters)

        for cluster_label in range(limit):
            member_mask = cr.labels == cluster_label
            member_ids = [ids[i] for i, m in enumerate(member_mask) if m]
            member_embs = [embeddings[i] for i, m in enumerate(member_mask) if m]

            if len(member_ids) < 2:
                continue

            node = await attempt_emerge(
                db=db,
                member_cluster_ids=member_ids,
                embeddings=member_embs,
                warm_path_age=self._warm_path_age,
                provider=self._provider,
                model=settings.MODEL_HAIKU,
            )
            if node is not None:
                operations.append({"type": "emerge", "node_id": node.id})

        return operations

    # ------------------------------------------------------------------
    # Domain discovery (ADR-004)
    # ------------------------------------------------------------------

    async def _propose_domains(self, db: AsyncSession) -> list[str]:
        """Inspect 'general' domain children and propose new domains.

        Scans active clusters parented under the "general" domain node.
        For each candidate with sufficient members and coherence, inspects
        the ``domain_raw`` field of linked optimizations.  When a single
        parsed primary domain reaches the consistency threshold and no
        domain node with that label already exists, a new domain node is
        created.

        Returns:
            List of newly created domain labels.
        """
        from collections import Counter

        from app.services.pipeline_constants import (
            DOMAIN_COUNT_CEILING,
            DOMAIN_DISCOVERY_CONSISTENCY,
            DOMAIN_DISCOVERY_MIN_COHERENCE,
            DOMAIN_DISCOVERY_MIN_MEMBERS,
        )
        from app.utils.text_cleanup import parse_domain

        # --- Step a: Check domain ceiling ---
        ceiling_q = await db.execute(
            select(func.count()).select_from(PromptCluster).where(
                PromptCluster.state == "domain",
            )
        )
        domain_count = ceiling_q.scalar() or 0

        if domain_count >= DOMAIN_COUNT_CEILING:
            logger.warning(
                "Domain ceiling reached (%d >= %d) — skipping discovery",
                domain_count, DOMAIN_COUNT_CEILING,
            )
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("domain_ceiling_reached", {
                    "domain_count": domain_count,
                    "ceiling": DOMAIN_COUNT_CEILING,
                })
            except Exception:
                pass
            return []

        # --- Step b: Find "general" domain node ---
        gen_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == "general",
            )
        )
        general_node = gen_q.scalar_one_or_none()
        if general_node is None:
            logger.debug("No 'general' domain node — skipping discovery")
            return []

        # --- Step c: Query eligible children ---
        children_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == general_node.id,
                PromptCluster.state.in_(["active", "mature"]),
                PromptCluster.member_count >= DOMAIN_DISCOVERY_MIN_MEMBERS,
                PromptCluster.coherence >= DOMAIN_DISCOVERY_MIN_COHERENCE,
            )
        )
        candidates = list(children_q.scalars().all())
        if not candidates:
            return []

        # --- Step d-f: Gather existing domain labels for dedup ---
        existing_q = await db.execute(
            select(PromptCluster.label).where(
                PromptCluster.state == "domain",
            )
        )
        existing_domains: set[str] = {
            row[0] for row in existing_q.all() if row[0]
        }

        created: list[str] = []

        for candidate in candidates:
            try:
                # Query domain_raw from linked optimizations
                opt_q = await db.execute(
                    select(Optimization.domain_raw).where(
                        Optimization.cluster_id == candidate.id,
                        Optimization.domain_raw.isnot(None),
                    )
                )
                domain_raws = [row[0] for row in opt_q.all() if row[0]]
                if not domain_raws:
                    continue

                # Parse primaries and count
                primaries: Counter[str] = Counter()
                for raw in domain_raws:
                    primary, _ = parse_domain(raw)
                    primaries[primary] += 1

                total = len(domain_raws)
                top_primary, top_count = primaries.most_common(1)[0]

                # Skip "general" — it's not a discovery target
                if top_primary == "general":
                    continue

                # Check consistency threshold
                if top_count / total < DOMAIN_DISCOVERY_CONSISTENCY:
                    continue

                # Skip if domain already exists
                if top_primary in existing_domains:
                    continue

                # Check ceiling again (may have created domains in this loop)
                if (domain_count + len(created)) >= DOMAIN_COUNT_CEILING:
                    break

                # Create the domain node
                await self._create_domain_node(
                    db, top_primary, existing_domains, candidate,
                    general_node_id=general_node.id,
                )
                created.append(top_primary)
                existing_domains.add(top_primary)
            except Exception:
                logger.error(
                    "Domain discovery failed for cluster %s — skipping",
                    candidate.id, exc_info=True,
                )
                continue

        return created

    async def _extract_domain_keywords(
        self, db: AsyncSession, cluster: PromptCluster, top_k: int = 15,
    ) -> list:
        """Extract top TF-IDF keywords from cluster member prompts."""
        from sklearn.feature_extraction.text import TfidfVectorizer

        result = await db.execute(
            select(Optimization.raw_prompt).where(
                Optimization.cluster_id == cluster.id,
            )
        )
        texts = [row[0] for row in result if row[0]]
        if not texts:
            return []

        try:
            vectorizer = TfidfVectorizer(
                max_features=top_k, stop_words="english", ngram_range=(1, 2),
            )
            tfidf = vectorizer.fit_transform(texts)
            feature_names = vectorizer.get_feature_names_out()
            scores = tfidf.mean(axis=0).A1
            ranked = sorted(
                zip(feature_names, scores), key=lambda x: x[1], reverse=True,
            )
            return [[kw, round(float(score), 2)] for kw, score in ranked[:top_k]]
        except Exception:
            logger.warning(
                "TF-IDF extraction failed for cluster %s", cluster.id,
                exc_info=True,
            )
            return []

    async def _create_domain_node(
        self,
        db: AsyncSession,
        label: str,
        existing_domains: set[str],
        seed_cluster: PromptCluster | None = None,
        general_node_id: str | None = None,
    ) -> PromptCluster:
        """Create a new domain node with a maximally distant color.

        Args:
            db: Async DB session.
            label: Domain label (e.g. "marketing").
            existing_domains: Set of existing domain labels (for color computation).
            seed_cluster: The cluster that triggered this domain discovery.
            general_node_id: ID of the "general" domain node (avoids re-query).

        Returns:
            The newly created PromptCluster domain node.
        """
        from datetime import datetime, timezone

        from app.services.taxonomy.coloring import compute_max_distance_color

        # Gather existing domain colors for max-distance computation
        color_q = await db.execute(
            select(PromptCluster.color_hex).where(
                PromptCluster.state == "domain",
                PromptCluster.color_hex.isnot(None),
            )
        )
        existing_colors = [row[0] for row in color_q.all() if row[0]]
        color_hex = compute_max_distance_color(existing_colors)

        # Extract TF-IDF keywords from seed cluster
        keywords: list = []
        signal_member_count = 0
        if seed_cluster is not None:
            keywords = await self._extract_domain_keywords(db, seed_cluster)
            signal_member_count = seed_cluster.member_count or 0

        node = PromptCluster(
            label=label,
            state="domain",
            domain=label,
            task_type="general",
            persistence=1.0,
            color_hex=color_hex,
            centroid_embedding=seed_cluster.centroid_embedding if seed_cluster else None,
            cluster_metadata={
                "source": "discovered",
                "signal_keywords": keywords,
                "discovered_at": datetime.now(timezone.utc).isoformat(),
                "proposed_by_snapshot": None,
                "signal_member_count_at_generation": signal_member_count,
            },
        )
        db.add(node)
        await db.flush()

        logger.info(
            "Created discovered domain node: label=%s color=%s id=%s keywords=%d",
            label, color_hex, node.id, len(keywords),
        )

        # Re-parent matching clusters from "general" to the new domain
        if general_node_id:
            reparented = await self._reparent_to_domain(
                db, node, label, general_node_id,
            )
            if reparented:
                await self._backfill_optimization_domain(db, node)

        try:
            from app.services.event_bus import event_bus
            event_bus.publish("domain_created", {
                "label": label,
                "color_hex": color_hex,
                "node_id": node.id,
                "source": "discovered",
            })
        except Exception:
            pass

        return node

    async def _reparent_to_domain(
        self,
        db: AsyncSession,
        domain_node: PromptCluster,
        label: str,
        general_id: str,
    ) -> int:
        """Re-parent clusters from 'general' to the new domain."""
        from app.utils.text_cleanup import parse_domain

        candidates = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == general_id,
                PromptCluster.state.in_(["active", "mature", "candidate"]),
                PromptCluster.domain == "general",
            )
        )
        reparented = 0
        for cluster in candidates.scalars():
            opts = await db.execute(
                select(Optimization.domain_raw).where(
                    Optimization.cluster_id == cluster.id,
                )
            )
            raw_domains = [row[0] for row in opts if row[0]]
            if not raw_domains:
                continue
            primaries = [parse_domain(d)[0] for d in raw_domains]
            match_count = sum(1 for p in primaries if p == label)
            if match_count / len(primaries) >= 0.6:
                cluster.parent_id = domain_node.id
                cluster.domain = label
                reparented += 1

        if reparented:
            logger.info(
                "Re-parented %d clusters from 'general' to '%s'",
                reparented, label,
            )
        return reparented

    async def _backfill_optimization_domain(
        self, db: AsyncSession, domain_node: PromptCluster,
    ) -> int:
        """Update Optimization.domain for re-parented clusters."""
        from sqlalchemy import update

        result = await db.execute(
            update(Optimization)
            .where(
                Optimization.cluster_id.in_(
                    select(PromptCluster.id).where(
                        PromptCluster.parent_id == domain_node.id,
                    )
                ),
                Optimization.domain == "general",
            )
            .values(domain=domain_node.label)
        )
        if result.rowcount:
            logger.info(
                "Backfilled %d optimizations from 'general' to '%s'",
                result.rowcount, domain_node.label,
            )
        return result.rowcount

    # ------------------------------------------------------------------
    # Risk detection (ADR-004 Section 8B)
    # ------------------------------------------------------------------

    async def _suggest_domain_archival(self, db: AsyncSession) -> list[str]:
        """Identify low-activity discovered domains for potential archival."""
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import and_, or_

        from app.services.pipeline_constants import (
            DOMAIN_ARCHIVAL_IDLE_DAYS,
            DOMAIN_ARCHIVAL_MIN_USAGE,
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=DOMAIN_ARCHIVAL_IDLE_DAYS)
        stale = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                or_(
                    PromptCluster.member_count == 0,
                    and_(
                        or_(
                            PromptCluster.last_used_at < cutoff,
                            PromptCluster.last_used_at.is_(None),
                        ),
                        PromptCluster.usage_count < DOMAIN_ARCHIVAL_MIN_USAGE,
                    ),
                ),
            )
        )
        suggestions = []
        for domain in stale.scalars():
            meta = domain.cluster_metadata or {}
            if meta.get("source") == "seed":
                continue
            suggestions.append(domain.label)
            logger.info(
                "Domain archival suggested: '%s' (members=%d, usage=%d)",
                domain.label, domain.member_count or 0, domain.usage_count or 0,
            )
        if suggestions:
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("domain_archival_suggested", {"labels": suggestions})
            except Exception:
                pass
        return suggestions

    async def _check_signal_staleness(self, db: AsyncSession) -> list[PromptCluster]:
        """Identify domains whose TF-IDF signals need regeneration."""
        from app.services.pipeline_constants import SIGNAL_REFRESH_MEMBER_RATIO

        stale = []
        domains = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "domain")
        )
        for domain in domains.scalars():
            meta = domain.cluster_metadata or {}
            if meta.get("source") == "seed":
                continue
            gen_count = meta.get("signal_member_count_at_generation", 0)
            if gen_count == 0:
                continue
            if (domain.member_count or 0) >= gen_count * SIGNAL_REFRESH_MEMBER_RATIO:
                stale.append(domain)
                logger.info(
                    "Signal staleness detected: domain '%s' generated at %d members, now has %d",
                    domain.label, gen_count, domain.member_count or 0,
                )
        return stale

    async def _refresh_domain_signals(
        self, db: AsyncSession, domain: PromptCluster,
    ) -> None:
        """Regenerate TF-IDF keywords for a domain with stale signals."""
        from datetime import datetime, timezone

        keywords = await self._extract_domain_keywords(db, domain)
        meta = dict(domain.cluster_metadata or {})
        meta["signal_keywords"] = keywords
        meta["signal_generated_at"] = datetime.now(timezone.utc).isoformat()
        meta["signal_member_count_at_generation"] = domain.member_count or 0
        domain.cluster_metadata = meta
        logger.info(
            "Signals refreshed for domain '%s': %d keywords from %d members",
            domain.label, len(keywords), domain.member_count or 0,
        )
        try:
            from app.services.event_bus import event_bus
            event_bus.publish("domain_signals_refreshed", {
                "label": domain.label,
                "keyword_count": len(keywords),
            })
        except Exception:
            pass

    async def _monitor_general_health(self, db: AsyncSession) -> None:
        """Log diagnostic metrics for the 'general' domain."""
        from app.services.pipeline_constants import (
            DOMAIN_DISCOVERY_MIN_COHERENCE,
            DOMAIN_DISCOVERY_MIN_MEMBERS,
        )

        general = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == "general",
            )
        )
        general_node = general.scalar_one_or_none()
        if not general_node:
            return

        child_count = await db.scalar(
            select(func.count()).where(
                PromptCluster.parent_id == general_node.id,
                PromptCluster.state.in_(["active", "mature"]),
            )
        ) or 0

        near_threshold = await db.scalar(
            select(func.count()).where(
                PromptCluster.parent_id == general_node.id,
                PromptCluster.state.in_(["active", "mature"]),
                PromptCluster.member_count >= DOMAIN_DISCOVERY_MIN_MEMBERS - 2,
                PromptCluster.coherence >= DOMAIN_DISCOVERY_MIN_COHERENCE - 0.1,
            )
        ) or 0

        opt_count = await db.scalar(
            select(func.count()).where(Optimization.domain == "general"),
        ) or 0

        logger.info(
            "General domain health: %d child clusters, %d near discovery threshold, "
            "%d total optimizations",
            child_count, near_threshold, opt_count,
        )
        if opt_count > 50 and child_count > 5 and near_threshold == 0:
            logger.warning(
                "General domain stagnation: %d optimizations across %d clusters "
                "but none near threshold. Consider lowering "
                "DOMAIN_DISCOVERY_MIN_MEMBERS (current=%d) or "
                "DOMAIN_DISCOVERY_MIN_COHERENCE (current=%.2f).",
                opt_count, child_count,
                DOMAIN_DISCOVERY_MIN_MEMBERS, DOMAIN_DISCOVERY_MIN_COHERENCE,
            )

    async def _create_warm_snapshot(
        self,
        db: AsyncSession,
        *,
        q_system: float,
        operations: list[dict],
        ops_attempted: int,
        ops_accepted: int,
    ) -> TaxonomySnapshot:
        """Create a warm-path snapshot with current metrics."""
        from app.services.taxonomy.clustering import compute_separation
        from app.services.taxonomy.snapshot import create_snapshot

        # Load active nodes for metrics
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.state == "active")
        )
        active_nodes = list(result.scalars().all())

        # Compute coherence and separation
        coherences = [n.coherence for n in active_nodes if n.coherence is not None]
        mean_coherence = float(np.mean(coherences)) if coherences else 0.0

        centroids = []
        for n in active_nodes:
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                centroids.append(c)
            except (ValueError, TypeError):
                continue

        separation = compute_separation(centroids) if len(centroids) >= 2 else 1.0

        nodes_created = sum(1 for op in operations if op.get("type") == "emerge")
        nodes_merged = sum(1 for op in operations if op.get("type") == "merge")
        nodes_retired = sum(1 for op in operations if op.get("type") == "retire")
        nodes_split = sum(1 for op in operations if op.get("type") == "split")

        return await create_snapshot(
            db,
            trigger="warm_path",
            q_system=q_system,
            q_coherence=mean_coherence,
            q_separation=separation,
            q_coverage=1.0,
            operations=operations,
            nodes_created=nodes_created,
            nodes_retired=nodes_retired,
            nodes_merged=nodes_merged,
            nodes_split=nodes_split,
        )

    # ------------------------------------------------------------------
    # Domain mapping — delegated to matching.py
    # ------------------------------------------------------------------

    async def map_domain(
        self,
        domain_raw: str,
        db: AsyncSession,
        applied_pattern_ids: list[str] | None = None,
    ) -> TaxonomyMapping:
        """Map a free-text domain string to the nearest active PromptCluster.

        Delegates to :func:`matching.map_domain`.
        """
        return await _map_domain(
            domain_raw, db, self._embedding, applied_pattern_ids
        )

    # ------------------------------------------------------------------
    # Read API (Spec Section 6.3)
    # ------------------------------------------------------------------

    async def get_tree(
        self,
        db: AsyncSession,
        min_persistence: float = 0.0,
    ) -> list[dict]:
        # Show all non-archived lifecycle states in the topology view.
        # Mature and template nodes are valid topology members — excluding
        # them would create inconsistency with stats panel node counts.
        query = select(PromptCluster).where(
            PromptCluster.state.in_(["active", "candidate", "mature", "template", "domain"])
        )
        if min_persistence > 0:
            query = query.where(PromptCluster.persistence >= min_persistence)
        result = await db.execute(query)
        nodes = result.scalars().all()
        return [self._node_to_dict(n) for n in nodes]

    async def get_node(
        self,
        node_id: str,
        db: AsyncSession,
    ) -> dict | None:
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.id == node_id)
        )
        node = result.scalar_one_or_none()
        if not node:
            return None
        node_dict = self._node_to_dict(node)
        # Add children
        children_result = await db.execute(
            select(PromptCluster).where(PromptCluster.parent_id == node_id)
        )
        children = children_result.scalars().all()
        node_dict["children"] = [self._node_to_dict(c) for c in children]
        # Add breadcrumb
        node_dict["breadcrumb"] = await build_breadcrumb(db, node)
        return node_dict

    async def get_stats(self, db: AsyncSession) -> dict:
        # Node state counts via GROUP BY (avoids loading full ORM objects + blobs)
        state_result = await db.execute(
            select(PromptCluster.state, func.count(PromptCluster.id)).group_by(
                PromptCluster.state
            )
        )
        state_counts = dict(state_result.all())
        active = state_counts.get("active", 0)
        candidate = state_counts.get("candidate", 0)
        mature = state_counts.get("mature", 0)
        template = state_counts.get("template", 0)
        archived = state_counts.get("archived", 0)

        # max_depth + leaf_count: lightweight projection (id + parent_id + state only)
        tree_result = await db.execute(
            select(PromptCluster.id, PromptCluster.parent_id, PromptCluster.state)
        )
        tree_rows = tree_result.all()
        id_to_parent: dict[str, str | None] = {r.id: r.parent_id for r in tree_rows}

        max_depth = 0
        for node_id in id_to_parent:
            depth = 0
            current_id = node_id
            visited: set[str] = {node_id}
            while True:
                pid = id_to_parent.get(current_id)
                if not pid or pid in visited:
                    break
                visited.add(pid)
                depth += 1
                current_id = pid
            if depth > max_depth:
                max_depth = depth

        active_ids = {r.id for r in tree_rows if r.state != "archived"}
        parent_ids = {r.parent_id for r in tree_rows if r.parent_id}
        leaf_count = sum(1 for nid in active_ids if nid not in parent_ids)

        # Total pattern families (leaf clusters with a parent) via scalar COUNT
        fam_count_result = await db.execute(
            select(func.count(PromptCluster.id)).where(
                PromptCluster.parent_id.isnot(None)
            )
        )
        total_families = fam_count_result.scalar() or 0

        # Recent snapshots (last 30, ascending chronological)
        from app.services.taxonomy.snapshot import get_snapshot_history

        recent = await get_snapshot_history(db, limit=30)
        # recent is newest-first; reverse for chronological sparkline
        snapshots = list(reversed(recent))

        # Latest snapshot metrics (newest = recent[0])
        latest = recent[0] if recent else None
        q_system = latest.q_system if latest else None
        q_coherence = latest.q_coherence if latest else None
        q_separation = latest.q_separation if latest else None
        q_coverage = latest.q_coverage if latest else None
        q_dbcv = latest.q_dbcv if latest else None

        # Sparkline history
        q_values = [s.q_system for s in snapshots]
        sparkline = compute_sparkline_data(q_values)

        q_history = []
        for snap in snapshots:
            try:
                ops = json.loads(snap.operations) if snap.operations else []
            except (ValueError, TypeError):
                ops = []
            q_history.append(
                {
                    "timestamp": snap.created_at.isoformat() if snap.created_at else None,
                    "q_system": snap.q_system,
                    "operations": len(ops),
                }
            )

        # last_warm_path and last_cold_path timestamps (scan newest-first)
        last_warm_path: str | None = None
        last_cold_path: str | None = None
        for snap in recent:  # already newest-first from get_snapshot_history
            if last_warm_path is None and snap.trigger == "warm_path":
                last_warm_path = snap.created_at.isoformat() if snap.created_at else None
            if last_cold_path is None and snap.trigger == "cold_path":
                last_cold_path = snap.created_at.isoformat() if snap.created_at else None
            if last_warm_path is not None and last_cold_path is not None:
                break

        return {
            "q_system": q_system,
            "q_coherence": q_coherence,
            "q_separation": q_separation,
            "q_coverage": q_coverage,
            "q_dbcv": q_dbcv,
            "total_families": total_families,
            "nodes": {
                "active": active,
                "candidate": candidate,
                "mature": mature,
                "template": template,
                "archived": archived,
                "max_depth": max_depth,
                "leaf_count": leaf_count,
            },
            "q_history": q_history,
            "q_sparkline": sparkline.normalized,
            "last_warm_path": last_warm_path,
            "last_cold_path": last_cold_path,
            "warm_path_age": self._warm_path_age,
        }

    async def increment_usage(self, cluster_id: str, db: AsyncSession) -> None:
        """Increment usage on the cluster and propagate up the taxonomy tree.

        Spec Section 7.8 — usage count flows upward so that ancestor
        clusters reflect aggregate activity from their subtree.

        Args:
            cluster_id: ID of the PromptCluster whose patterns were applied.
            db: Async SQLAlchemy session.
        """
        from datetime import datetime, timezone

        cluster = await db.get(PromptCluster, cluster_id)
        if not cluster:
            logger.warning("increment_usage: cluster %s not found", cluster_id)
            return

        now = datetime.now(timezone.utc)
        cluster.usage_count = (cluster.usage_count or 0) + 1
        cluster.last_used_at = now

        # Walk up the taxonomy tree (with cycle guard)
        parent_id = cluster.parent_id
        visited: set[str] = set()
        while parent_id:
            if parent_id in visited:
                logger.warning("increment_usage: cycle detected at node %s", parent_id)
                break
            visited.add(parent_id)
            parent = await db.get(PromptCluster, parent_id)
            if not parent:
                break
            parent.usage_count = (parent.usage_count or 0) + 1
            parent.last_used_at = now
            parent_id = parent.parent_id

        await db.flush()
        logger.debug(
            "Usage incremented: cluster=%s (usage=%d)",
            cluster_id,
            cluster.usage_count,
        )

    # ------------------------------------------------------------------
    # Tree integrity verification and auto-repair
    # ------------------------------------------------------------------

    async def verify_domain_tree_integrity(self, db: AsyncSession) -> list[str]:
        """Post-migration and periodic integrity check.

        Returns a list of violation descriptions. Empty = healthy.
        """
        from sqlalchemy import text

        violations = []

        # 1. Check for duplicate domain labels
        result = await db.execute(
            select(PromptCluster.label, func.count()).where(
                PromptCluster.state == "domain"
            ).group_by(PromptCluster.label)
        )
        for label, count in result:
            if count > 1:
                violations.append(
                    f"Duplicate domain label: '{label}' appears {count} times"
                )

        # 2. Check for orphaned clusters (parent_id points to non-existent node)
        orphans = await db.execute(text("""
            SELECT c.id, c.label, c.parent_id
            FROM prompt_cluster c
            LEFT JOIN prompt_cluster p ON c.parent_id = p.id
            WHERE c.parent_id IS NOT NULL AND p.id IS NULL
        """))
        for row in orphans:
            violations.append(
                f"Orphan cluster: '{row[1]}' (id={row[0]}) references missing parent {row[2]}"
            )

        # 3. Check domain nodes have persistence=1.0
        weak = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.persistence < 1.0,
            )
        )
        for d in weak.scalars():
            violations.append(
                f"Domain node '{d.label}' has persistence={d.persistence} (expected 1.0)"
            )

        # 4. Check for self-referencing parents
        self_refs = await db.execute(
            text("SELECT id, label FROM prompt_cluster WHERE parent_id = id")
        )
        for row in self_refs:
            violations.append(
                f"Self-referencing parent: '{row[1]}' (id={row[0]})"
            )

        # 5. Every non-domain cluster's domain field must match a domain node label
        #    Case-insensitive: analyzer may emit "Backend" while domain label is "backend"
        mismatch_result = await db.execute(text("""
            SELECT c.id, c.label, c.domain
            FROM prompt_cluster c
            WHERE c.state != 'domain'
              AND LOWER(c.domain) NOT IN (SELECT LOWER(label) FROM prompt_cluster WHERE state = 'domain')
              AND c.domain IS NOT NULL
        """))
        for row in mismatch_result:
            violations.append(
                f"Domain mismatch: cluster '{row[1]}' has domain='{row[2]}' "
                f"which is not a domain node"
            )

        if violations:
            for v in violations:
                logger.error("Tree integrity violation: %s", v)
        else:
            logger.info("Domain tree integrity check passed")

        return violations

    async def _repair_tree_violations(
        self, db: AsyncSession, violations: list[str]
    ) -> int:
        """Auto-repair detected violations. Returns count of repairs."""
        from sqlalchemy import text, update

        repaired = 0

        # Repair weak domain persistence
        result = await db.execute(
            update(PromptCluster)
            .where(PromptCluster.state == "domain", PromptCluster.persistence < 1.0)
            .values(persistence=1.0)
        )
        if result.rowcount > 0:
            logger.info(
                "Auto-repaired %d domain nodes with weak persistence", result.rowcount
            )
            repaired += result.rowcount

        # Repair orphaned clusters → re-parent under "general"
        general_result = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.state == "domain", PromptCluster.label == "general"
            )
        )
        general_row = general_result.first()
        if general_row:
            orphan_result = await db.execute(
                text("""
                    UPDATE prompt_cluster
                    SET parent_id = :general_id, domain = 'general'
                    WHERE parent_id IS NOT NULL
                      AND parent_id NOT IN (SELECT id FROM prompt_cluster)
                      AND state != 'domain'
                """),
                {"general_id": general_row[0]},
            )
            if orphan_result.rowcount > 0:
                logger.info(
                    "Auto-repaired %d orphaned clusters → 'general'",
                    orphan_result.rowcount,
                )
                repaired += orphan_result.rowcount

        # Repair domain mismatches → reset to "general" (case-insensitive)
        mismatch_result = await db.execute(text("""
            UPDATE prompt_cluster
            SET domain = 'general'
            WHERE state != 'domain'
              AND LOWER(domain) NOT IN (SELECT LOWER(label) FROM prompt_cluster WHERE state = 'domain')
              AND domain IS NOT NULL
        """))
        if mismatch_result.rowcount > 0:
            logger.info(
                "Auto-repaired %d domain mismatches → 'general'",
                mismatch_result.rowcount,
            )
            repaired += mismatch_result.rowcount

        # NOTE: do NOT commit here — the warm path handles commit/rollback
        # after the Q_system non-regression gate.  Committing prematurely
        # would bypass the quality gate rollback mechanism.
        return repaired

    @staticmethod
    def _node_to_dict(node: PromptCluster) -> dict:
        return {
            "id": node.id,
            "label": node.label,
            "parent_id": node.parent_id,
            "state": node.state,
            "domain": node.domain,
            "task_type": node.task_type,
            "member_count": node.member_count or 0,
            "coherence": node.coherence,
            "separation": node.separation,
            "stability": node.stability,
            "persistence": node.persistence,
            "color_hex": node.color_hex,
            "umap_x": node.umap_x,
            "umap_y": node.umap_y,
            "umap_z": node.umap_z,
            "usage_count": node.usage_count or 0,
            "avg_score": node.avg_score,
            "preferred_strategy": node.preferred_strategy,
            "promoted_at": node.promoted_at.isoformat() if node.promoted_at else None,
            "created_at": node.created_at.isoformat() if node.created_at else None,
        }
