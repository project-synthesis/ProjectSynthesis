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
import time
from collections.abc import Callable
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR, settings
from app.models import (
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PromptCluster,
    TaxonomySnapshot,
)
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader
from app.services.taxonomy._constants import _utcnow
from app.services.taxonomy.cluster_meta import read_meta, write_meta
from app.services.taxonomy.cold_path import ColdPathResult, execute_cold_path
from app.services.taxonomy.embedding_index import EmbeddingIndex
from app.services.taxonomy.family_ops import (
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
from app.services.taxonomy.warm_path import WarmPathResult, execute_warm_path
from app.utils.text_cleanup import parse_domain

logger = logging.getLogger(__name__)

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

    _STATS_CACHE_TTL: float = 30.0  # seconds — stats endpoint TTL

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
        from app.services.taxonomy.transformation_index import TransformationIndex
        self._transformation_index = TransformationIndex(dim=384)
        # Lock gates concurrent hot-path writes to shared centroid state.
        self._lock: asyncio.Lock = asyncio.Lock()
        # Separate lock for warm/cold path deduplication (Spec Section 2.6).
        self._warm_path_lock: asyncio.Lock = asyncio.Lock()
        # Per-phase rejection counters (Spec Section 2.5) — used by warm_path orchestrator.
        self._phase_rejection_counters: dict[str, int] = {}
        # Warm-path age counter for adaptive epsilon tolerance.
        self._warm_path_age: int = 0
        # Set by deadlock breaker — caller should schedule cold path.
        self._cold_path_needed: bool = False
        # Stats cache — monotonic TTL, invalidated on warm/cold path completion.
        self._stats_cache: dict | None = None
        self._stats_cache_time: float = 0.0

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

    @property
    def transformation_index(self):
        """In-memory transformation vector search index."""
        return self._transformation_index

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

            # 1b. Embed optimized_prompt (Phase 1: multi-embedding)
            if opt.optimized_prompt:
                optimized_emb = await self._embedding.aembed_single(opt.optimized_prompt)
                opt.optimized_embedding = optimized_emb.astype(np.float32).tobytes()

                # 1c. Compute transformation vector: direction of improvement
                transform = optimized_emb - embedding  # raw_emb already computed
                t_norm = np.linalg.norm(transform)
                if t_norm > 1e-9:
                    transform = transform / t_norm
                opt.transformation_embedding = transform.astype(np.float32).tobytes()

            # 2. Find or create PromptCluster
            # Use the RESOLVED domain (opt.domain) for cluster assignment — this
            # maps to a known domain node label. The raw analyzer output (domain_raw)
            # is preserved on the Optimization record for warm-path discovery.
            domain_primary, _ = parse_domain(opt.domain or "general")
            async with self._lock:
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=opt.intent_label or "general",
                    domain=domain_primary,
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                    embedding_index=self._embedding_index,
                )

            # Write back the definitive cluster assignment.
            # The pipeline's Phase 1.5 domain mapping sets a preliminary
            # cluster_id (often NULL); this is the canonical assignment.
            old_cluster_id = opt.cluster_id
            if old_cluster_id and old_cluster_id != cluster.id:
                old_cluster_q = await db.execute(
                    select(PromptCluster).where(PromptCluster.id == old_cluster_id)
                )
                old_cluster = old_cluster_q.scalar_one_or_none()
                if old_cluster and old_cluster.state != "archived":
                    old_cluster.member_count = max(0, (old_cluster.member_count or 1) - 1)
                    if opt.overall_score is not None and (old_cluster.scored_count or 0) > 0:
                        old_cluster.scored_count = max(0, old_cluster.scored_count - 1)
                    logger.info(
                        "Decremented old cluster '%s' member_count to %d "
                        "(reassigned to '%s')",
                        old_cluster.label, old_cluster.member_count,
                        cluster.label,
                    )
            opt.cluster_id = cluster.id

            # Update TransformationIndex with this optimization's transformation
            if opt.transformation_embedding and hasattr(self, '_transformation_index'):
                try:
                    transform_vec = np.frombuffer(opt.transformation_embedding, dtype=np.float32)
                    await self._transformation_index.upsert(cluster.id, transform_vec)
                except Exception:
                    pass  # non-fatal

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

    async def run_warm_path(
        self, session_factory: Callable,
    ) -> WarmPathResult | None:
        """Periodic re-clustering with lifecycle operations.

        Checks ``_warm_path_lock`` for deduplication first — if already held
        by another coroutine, returns None (skip).  Otherwise acquires the
        lock and delegates to :func:`execute_warm_path` which runs 7
        sequential phases, each with its own database session.

        Args:
            session_factory: Async context manager factory that yields
                fresh ``AsyncSession`` instances.

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
                return await execute_warm_path(self, session_factory)
            except Exception as exc:
                logger.error("Warm path failed: %s", exc, exc_info=True)
                # Return a minimal result so callers don't break
                try:
                    async with session_factory() as db:
                        snap = await self._create_warm_snapshot(
                            db, q_system=0.0, operations=[],
                            ops_attempted=0, ops_accepted=0,
                        )
                        snapshot_id = snap.id
                        await db.commit()
                except Exception as snap_exc:
                    logger.error(
                        "Warm path error-recovery snapshot also failed: %s",
                        snap_exc, exc_info=True,
                    )
                    snapshot_id = "error-no-snapshot"
                return WarmPathResult(
                    snapshot_id=snapshot_id,
                    q_baseline=None,
                    q_final=0.0,
                    phase_results=[],
                    operations_attempted=0,
                    operations_accepted=0,
                    deadlock_breaker_used=False,
                    deadlock_breaker_phase=None,
                )

    # ------------------------------------------------------------------
    # Backfill: replay hot-path assignment for all optimizations
    # ------------------------------------------------------------------

    async def reassign_all_clusters(
        self, db: AsyncSession,
    ) -> dict:
        """Replay hot-path cluster assignment for every optimization.

        Clears all non-domain, non-archived PromptCluster records and
        re-runs ``assign_cluster()`` for every optimization that has an
        embedding.  This is the correct way to apply a new merge threshold
        to existing data — unlike ``run_cold_path()`` which only
        rearranges the cluster hierarchy without changing
        ``Optimization.cluster_id``.

        Returns:
            Dict with ``reassigned``, ``clusters_before``, ``clusters_after``.
        """
        from sqlalchemy import func as sa_func

        # Count clusters before
        before_q = await db.execute(
            select(sa_func.count()).where(
                PromptCluster.state.notin_(["domain", "archived"]),
            )
        )
        clusters_before = before_q.scalar() or 0

        # Load all optimizations with embeddings
        opt_result = await db.execute(
            select(Optimization)
            .where(Optimization.embedding.isnot(None))
            .order_by(Optimization.created_at.asc())
        )
        optimizations = list(opt_result.scalars().all())
        if not optimizations:
            return {"reassigned": 0, "clusters_before": clusters_before, "clusters_after": 0}

        # Archive all non-domain active clusters — we'll rebuild from scratch
        active_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(["domain", "archived"]),
            )
        )
        now = _utcnow()
        for cluster in active_q.scalars().all():
            cluster.state = "archived"
            cluster.archived_at = now
            cluster.member_count = 0
            cluster.scored_count = 0
            cluster.usage_count = 0
            cluster.avg_score = None

        # Reset embedding index by replacing internal arrays
        if self._embedding_index is not None:
            async with self._embedding_index._lock:
                self._embedding_index._matrix = np.empty(
                    (0, self._embedding_index._dim), dtype=np.float32,
                )
                self._embedding_index._ids = []

        await db.flush()

        # Replay hot-path assignment for each optimization (chronological order)
        reassigned = 0
        for opt in optimizations:
            embedding = np.frombuffer(opt.embedding, dtype=np.float32)
            domain_primary, _ = parse_domain(opt.domain or "general")

            async with self._lock:
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=opt.intent_label or "general",
                    domain=domain_primary,
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                    embedding_index=self._embedding_index,
                )

            opt.cluster_id = cluster.id
            reassigned += 1

        await db.flush()

        # Count clusters after
        after_q = await db.execute(
            select(sa_func.count()).where(
                PromptCluster.state.notin_(["domain", "archived"]),
            )
        )
        clusters_after = after_q.scalar() or 0

        # Rebuild join table, meta-patterns, and coherence
        repair = await self.repair_data_integrity(db)

        logger.info(
            "Reassign complete: %d optimizations, %d→%d clusters",
            reassigned, clusters_before, clusters_after,
        )
        return {
            "reassigned": reassigned,
            "clusters_before": clusters_before,
            "clusters_after": clusters_after,
            **repair,
        }

    # ------------------------------------------------------------------
    # Data integrity repair
    # ------------------------------------------------------------------

    async def repair_data_integrity(self, db: AsyncSession) -> dict:
        """Rebuild optimization_patterns, meta-patterns, and coherence.

        Repairs three classes of integrity issues:

        1. **Orphaned optimization_patterns** — deletes join records
           pointing to archived/missing clusters, recreates ``source``
           records for every optimization with a valid ``cluster_id``.

        2. **Orphaned meta_patterns** — deletes patterns pointing to
           archived/missing clusters, re-extracts structural patterns
           for every optimization and merges into current clusters.

        3. **Missing coherence** — computes pairwise coherence from
           member embeddings for every active cluster with 2+ members.

        Safe to call multiple times (idempotent).
        """
        from sqlalchemy import delete as sa_delete

        from app.services.taxonomy.clustering import compute_pairwise_coherence
        from app.services.taxonomy.family_ops import (
            extract_structural_patterns,
        )

        stats: dict[str, int] = {}

        # --- 1. Rebuild optimization_patterns ---
        # Delete ALL existing (they may be orphaned)
        del_op = await db.execute(sa_delete(OptimizationPattern))
        stats["join_deleted"] = del_op.rowcount

        # Recreate source records for every optimization with a cluster
        opts = (await db.execute(
            select(Optimization).where(
                Optimization.cluster_id.isnot(None),
            )
        )).scalars().all()

        join_created = 0
        for opt in opts:
            # Verify cluster exists and is active
            cluster_q = await db.execute(
                select(PromptCluster.id).where(
                    PromptCluster.id == opt.cluster_id,
                    PromptCluster.state.notin_(["archived"]),
                )
            )
            if cluster_q.scalar_one_or_none():
                db.add(OptimizationPattern(
                    optimization_id=opt.id,
                    cluster_id=opt.cluster_id,
                    relationship="source",
                ))
                join_created += 1
        stats["join_created"] = join_created
        await db.flush()

        # --- 2. Rebuild meta-patterns ---
        # Delete orphaned meta-patterns (pointing to archived/missing clusters)
        del_mp = await db.execute(
            sa_delete(MetaPattern).where(
                MetaPattern.cluster_id.notin_(
                    select(PromptCluster.id).where(
                        PromptCluster.state.notin_(["archived"]),
                    )
                )
            )
        )
        stats["meta_patterns_deleted"] = del_mp.rowcount

        # Re-extract structural patterns for each optimization
        patterns_created = 0
        for opt in opts:
            if not opt.raw_prompt or not opt.optimized_prompt:
                continue
            try:
                pattern_texts = extract_structural_patterns(
                    raw_prompt=opt.raw_prompt[:2000],
                    optimized_prompt=opt.optimized_prompt[:2000],
                )
                for text in pattern_texts:
                    await merge_meta_pattern(
                        db, opt.cluster_id, text, self._embedding,
                    )
                    patterns_created += 1
            except Exception as exc:
                logger.debug(
                    "Pattern extraction failed for opt=%s: %s", opt.id, exc,
                )
        stats["meta_patterns_created"] = patterns_created
        await db.flush()

        # --- 3. Compute coherence ---
        active_clusters = (await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(["domain", "archived"]),
            )
        )).scalars().all()

        coherence_computed = 0
        for cluster in active_clusters:
            emb_rows = (await db.execute(
                select(Optimization.embedding).where(
                    Optimization.cluster_id == cluster.id,
                    Optimization.embedding.isnot(None),
                )
            )).scalars().all()

            if len(emb_rows) < 2:
                cluster.coherence = 1.0 if len(emb_rows) == 1 else 0.0
            else:
                embs = [
                    np.frombuffer(e, dtype=np.float32)
                    for e in emb_rows
                ]
                cluster.coherence = compute_pairwise_coherence(embs)
            coherence_computed += 1

        stats["coherence_computed"] = coherence_computed

        # --- 4. Reconcile member_count from Optimization rows ---
        from sqlalchemy import func as _sa_func

        mc_q = await db.execute(
            select(Optimization.cluster_id, _sa_func.count().label("ct"))
            .where(Optimization.cluster_id.isnot(None))
            .group_by(Optimization.cluster_id)
        )
        mc_map = dict(mc_q.all())
        mc_fixed = 0
        for cluster in active_clusters:
            expected = mc_map.get(cluster.id, 0)
            if cluster.member_count != expected:
                cluster.member_count = expected
                mc_fixed += 1
        stats["member_count_fixed"] = mc_fixed
        await db.flush()

        logger.info(
            "Data integrity repair: join=%d created/%d deleted, "
            "meta=%d created/%d deleted, coherence=%d computed",
            stats["join_created"], stats["join_deleted"],
            stats["meta_patterns_created"], stats["meta_patterns_deleted"],
            stats["coherence_computed"],
        )
        return stats

    # ------------------------------------------------------------------
    # Cold path (Spec Section 2.3, 8.5)
    # ------------------------------------------------------------------

    async def run_cold_path(self, db: AsyncSession) -> ColdPathResult | None:
        """Full HDBSCAN + UMAP refit — the "defrag" operation.

        Acquires the same ``_warm_path_lock`` (cold path is a superset of
        warm path).  Delegates to :func:`execute_cold_path` which reclusters
        all PromptCluster embeddings, updates or creates PromptClusters,
        runs UMAP 3D projection with Procrustes alignment, regenerates
        OKLab colors, and creates a snapshot.

        Returns:
            ColdPathResult on completion.
        """
        async with self._warm_path_lock:
            try:
                return await execute_cold_path(self, db)
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
                    q_before=None,
                    q_after=0.0,
                    accepted=False,
                    nodes_created=0,
                    nodes_updated=0,
                    umap_fitted=False,
                )

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

        # DBCV ramp disabled: DBCV is not yet computed (always 0.0 in
        # compute_q_system).  Ramping its weight adds dead weight that
        # degrades Q_system over time — at age 20+ the ceiling drops to
        # 0.85 even with perfect metrics.  Restore the ramp logic from
        # Spec Section 2.5 when DBCV computation is implemented.
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
        # Coherence is now recomputed from actual member embeddings during
        # the reconciliation step above, so 0.0 values should be rare.
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

        # --- Post-discovery re-parenting sweep ---
        # Check ALL general-parented clusters against existing domain nodes.
        # Clusters created AFTER a domain was established may still be under
        # general if their hot-path assignment didn't find the domain node.
        try:
            domain_nodes_q = await db.execute(
                select(PromptCluster.id, PromptCluster.label).where(
                    PromptCluster.state == "domain",
                    PromptCluster.label != "general",
                )
            )
            domain_lookup = {row[1].lower(): row[0] for row in domain_nodes_q}

            from collections import Counter as _Counter

            general_children_q = await db.execute(
                select(PromptCluster).where(
                    PromptCluster.parent_id == general_node.id,
                    PromptCluster.state.in_(["active", "mature"]),
                )
            )
            general_children = list(general_children_q.scalars().all())
            logger.info(
                "Re-parenting sweep: checking %d general-parented clusters against %d domains",
                len(general_children), len(domain_lookup),
            )
            sweep_reparented = 0
            for cluster in general_children:
                # Get ALL domain_raw values for this cluster's optimizations
                opt_raws_q = await db.execute(
                    select(Optimization.domain_raw).where(
                        Optimization.cluster_id == cluster.id,
                        Optimization.domain_raw.isnot(None),
                    )
                )
                all_raws = [r[0] for r in opt_raws_q.all() if r[0]]
                if not all_raws:
                    continue
                # Parse domain_raw values to extract lowercase primaries
                # before counting — raw values like "Backend: Security" must
                # become "backend" to match domain_lookup keys.
                parsed_counts: _Counter[str] = _Counter()
                for raw in all_raws:
                    primary, _ = parse_domain(raw)
                    parsed_counts[primary] += 1
                total = len(all_raws)
                # Find the top non-general parsed domain
                for top_primary, top_ct in parsed_counts.most_common():
                    if top_primary == "general":
                        continue
                    consistency = top_ct / total
                    if top_primary in domain_lookup and consistency >= DOMAIN_DISCOVERY_CONSISTENCY:
                        target_id = domain_lookup[top_primary]
                        logger.info(
                            "Re-parenting '%s' → '%s' (consistency=%.0f%%, %d/%d members)",
                            cluster.label, top_primary, consistency * 100, top_ct, total,
                        )
                        cluster.parent_id = target_id
                        cluster.domain = top_primary
                        sweep_reparented += 1
                    break  # only check the top non-general candidate
            if sweep_reparented:
                logger.info(
                    "Re-parenting sweep: moved %d clusters from general to their domain",
                    sweep_reparented,
                )
        except Exception as sweep_exc:
            logger.warning("Re-parenting sweep failed (non-fatal): %s", sweep_exc)

        return created

    async def _detect_domain_candidates(self, db: AsyncSession) -> None:
        """Detect near-threshold clusters that may become domains soon."""
        from app.services.pipeline_constants import (
            DOMAIN_DISCOVERY_CANDIDATE_MIN_COHERENCE,
            DOMAIN_DISCOVERY_CANDIDATE_MIN_MEMBERS,
            DOMAIN_DISCOVERY_MIN_MEMBERS,
        )

        general_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == "general",
            )
        )
        general = general_q.scalar_one_or_none()
        if not general:
            return

        near_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == general.id,
                PromptCluster.state.in_(["active", "mature"]),
                PromptCluster.member_count >= DOMAIN_DISCOVERY_CANDIDATE_MIN_MEMBERS,
                PromptCluster.member_count < DOMAIN_DISCOVERY_MIN_MEMBERS,
                PromptCluster.coherence >= DOMAIN_DISCOVERY_CANDIDATE_MIN_COHERENCE,
            )
        )
        for candidate in near_q.scalars():
            logger.info(
                "Domain candidate detected: '%s' (members=%d/%d, coherence=%.2f)",
                candidate.label, candidate.member_count or 0,
                DOMAIN_DISCOVERY_MIN_MEMBERS, candidate.coherence or 0,
            )
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("domain_candidate_detected", {
                    "label": candidate.label,
                    "member_count": candidate.member_count or 0,
                    "threshold": DOMAIN_DISCOVERY_MIN_MEMBERS,
                    "coherence": candidate.coherence or 0,
                })
            except Exception:
                pass

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

        label = label.lower()  # Domain labels are always lowercase
        node = PromptCluster(
            label=label,
            state="domain",
            domain=label,
            task_type="general",
            persistence=1.0,
            color_hex=color_hex,
            centroid_embedding=seed_cluster.centroid_embedding if seed_cluster else None,
            cluster_metadata=write_meta(
                None,
                source="discovered",
                signal_keywords=keywords,
                discovered_at=datetime.now(timezone.utc).isoformat(),
                proposed_by_snapshot=None,
                signal_member_count_at_generation=signal_member_count,
            ),
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
                # Position the new domain node near its children so the
                # topology visualization starts from a meaningful location
                # instead of a random hash-based fallback.
                await self._set_domain_umap_from_children(db, node)

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

    async def _set_domain_umap_from_children(
        self, db: AsyncSession, domain_node: PromptCluster,
    ) -> None:
        """Set a domain node's UMAP position as the centroid of its children.

        Called after domain creation + reparenting so the topology
        visualization starts from a semantically meaningful position
        instead of a hash-based random fallback.  Also called during
        warm-path reconciliation and cold-path refit for domain nodes
        that still lack UMAP coordinates.

        Uses ``domain`` field matching (not ``parent_id``) because the
        cold path can reassign parent_id during HDBSCAN re-clustering,
        leaving tree links stale.
        """
        from sqlalchemy import func as sa_func

        row = (await db.execute(
            select(
                sa_func.avg(PromptCluster.umap_x),
                sa_func.avg(PromptCluster.umap_y),
                sa_func.avg(PromptCluster.umap_z),
                sa_func.count(),
            ).where(
                PromptCluster.domain == domain_node.label,
                PromptCluster.state.notin_(["domain", "archived"]),
                PromptCluster.umap_x.isnot(None),
                PromptCluster.umap_y.isnot(None),
                PromptCluster.umap_z.isnot(None),
            )
        )).one_or_none()

        if row and row[0] is not None:
            domain_node.umap_x = float(row[0])
            domain_node.umap_y = float(row[1])
            domain_node.umap_z = float(row[2])

            # Single-child domains: offset the domain node so it doesn't
            # sit at the exact same UMAP position as its only child.
            # Without this, the force simulation's parent-child spring and
            # UMAP anchor cancel out, rendering both nodes on top of each
            # other in the topology graph.
            # Offset of 1.0 in UMAP space = 10.0 scene units after
            # UMAP_SCALE (frontend TopologyData.ts), which is slightly
            # above the PARENT_REST_LEN (9.0) — enough for the spring
            # to find equilibrium at a visible distance.
            child_count = int(row[3])
            if child_count == 1:
                domain_node.umap_x += 1.0
                domain_node.umap_y += 0.5

            logger.info(
                "Set domain '%s' UMAP from %d children: (%.3f, %.3f, %.3f)",
                domain_node.label, child_count,
                domain_node.umap_x, domain_node.umap_y, domain_node.umap_z,
            )

    # ------------------------------------------------------------------
    # Risk detection (ADR-004 Section 8B)
    # ------------------------------------------------------------------

    async def _suggest_domain_archival(self, db: AsyncSession) -> list[str]:
        """Identify low-activity discovered domains for potential archival."""
        from datetime import timedelta

        from sqlalchemy import and_, or_

        from app.services.pipeline_constants import (
            DOMAIN_ARCHIVAL_IDLE_DAYS,
            DOMAIN_ARCHIVAL_MIN_USAGE,
        )

        cutoff = _utcnow() - timedelta(days=DOMAIN_ARCHIVAL_IDLE_DAYS)
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
            meta = read_meta(domain.cluster_metadata)
            if meta["source"] == "seed":
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
            meta = read_meta(domain.cluster_metadata)
            if meta["source"] == "seed":
                continue
            gen_count = meta["signal_member_count_at_generation"]
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

        keywords = await self._extract_domain_keywords(db, domain)
        domain.cluster_metadata = write_meta(
            domain.cluster_metadata,
            signal_keywords=keywords,
            signal_generated_at=datetime.now(timezone.utc).isoformat(),
            signal_member_count_at_generation=domain.member_count or 0,
        )
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
        from app.services.taxonomy.snapshot import create_snapshot

        # Load non-domain/non-archived nodes for metrics (Fix #10)
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(["domain", "archived"])
            )
        )
        active_nodes = list(result.scalars().all())

        mean_coherence, separation = self._snapshot_metrics(active_nodes)

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
            PromptCluster.state.in_(["active", "candidate", "mature", "template", "domain", "archived"])
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

    @staticmethod
    def _snapshot_metrics(
        active_nodes: list[PromptCluster],
    ) -> tuple[float, float]:
        """Compute mean coherence and mean separation for snapshot creation.

        Shared by warm-path and cold-path snapshot callers to avoid
        duplicated centroid-extraction and metric-computation logic.

        Returns:
            (mean_coherence, mean_separation)
        """
        from app.services.taxonomy.clustering import compute_mean_separation

        coherences = [n.coherence for n in active_nodes if n.coherence is not None]
        mean_coherence = float(np.mean(coherences)) if coherences else 0.0

        centroids: list[np.ndarray] = []
        for n in active_nodes:
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                centroids.append(c)
            except (ValueError, TypeError):
                continue

        separation = compute_mean_separation(centroids) if len(centroids) >= 2 else 1.0
        return mean_coherence, separation

    def _invalidate_stats_cache(self) -> None:
        """Clear the stats cache after a warm or cold path mutation."""
        self._stats_cache = None

    async def get_stats(self, db: AsyncSession) -> dict:
        now = time.monotonic()
        if self._stats_cache is not None and (now - self._stats_cache_time) < self._STATS_CACHE_TTL:
            return self._stats_cache

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

        result = {
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
            # Raw values (0–1 scale) — frontend uses fixedRange=[0,1] for
            # absolute rendering. Normalized values caused tiny fluctuations
            # to look like catastrophic drops.
            "q_sparkline": sparkline.raw_values,
            "q_trend": sparkline.trend,
            "q_current": sparkline.current if sparkline.point_count > 0 else None,
            "q_min": sparkline.min if sparkline.point_count > 0 else None,
            "q_max": sparkline.max if sparkline.point_count > 0 else None,
            "q_point_count": sparkline.point_count,
            "last_warm_path": last_warm_path,
            "last_cold_path": last_cold_path,
            "warm_path_age": self._warm_path_age,
        }

        self._stats_cache = result
        self._stats_cache_time = time.monotonic()

        return result

    async def increment_usage(self, cluster_id: str, db: AsyncSession) -> None:
        """Increment usage on the cluster and propagate up the taxonomy tree.

        Spec Section 7.8 — usage count flows upward so that ancestor
        clusters reflect aggregate activity from their subtree.

        Uses atomic SQL UPDATE (``usage_count = usage_count + 1``) instead of
        Python field mutation to prevent lost writes when multiple optimizations
        complete concurrently and increment the same cluster.

        Args:
            cluster_id: ID of the PromptCluster whose patterns were applied.
            db: Async SQLAlchemy session.
        """
        from sqlalchemy import update as sa_update

        cluster = await db.get(PromptCluster, cluster_id)
        if not cluster:
            logger.warning("increment_usage: cluster %s not found", cluster_id)
            return

        now = _utcnow()

        # Atomic increment on the target cluster
        await db.execute(
            sa_update(PromptCluster)
            .where(PromptCluster.id == cluster_id)
            .values(
                usage_count=PromptCluster.usage_count + 1,
                last_used_at=now,
            )
        )

        # Walk up the taxonomy tree with atomic increments (cycle guard)
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
            await db.execute(
                sa_update(PromptCluster)
                .where(PromptCluster.id == parent_id)
                .values(
                    usage_count=PromptCluster.usage_count + 1,
                    last_used_at=now,
                )
            )
            parent_id = parent.parent_id

        await db.flush()

        # Refresh to get the updated value for logging
        await db.refresh(cluster)
        logger.info(
            "Usage incremented: '%s' (usage=%d, domain=%s)",
            cluster.label, cluster.usage_count, cluster.domain,
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

        # 6. Non-domain clusters must only have domain node parents
        non_domain_parent_q = await db.execute(
            select(PromptCluster.id, PromptCluster.label, PromptCluster.parent_id).where(
                PromptCluster.state != "domain",
                PromptCluster.parent_id.isnot(None),
            )
        )
        domain_ids: set[str] = set(
            (await db.execute(
                select(PromptCluster.id).where(PromptCluster.state == "domain")
            )).scalars().all()
        )
        for row in non_domain_parent_q:
            if row[2] not in domain_ids:  # row[2] = parent_id
                violations.append(
                    f"Non-domain parent: '{row[1]}' (id={row[0][:8]}) "
                    f"has parent {row[2][:8]} which is not a domain node"
                )

        # 7. Archived clusters with active usage AND actual members should not be archived
        # (clusters with usage but 0 members are ghosts — stale usage from before reassignment)
        archived_used_q = await db.execute(
            select(PromptCluster.id, PromptCluster.label, PromptCluster.usage_count).where(
                PromptCluster.state == "archived",
                PromptCluster.usage_count > 0,
                PromptCluster.member_count > 0,
            )
        )
        for row in archived_used_q:
            violations.append(
                f"Archived with usage: '{row[1]}' (id={row[0][:8]}) "
                f"has usage_count={row[2]}"
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

        # Repair self-referencing parents → re-parent under matching domain node
        self_ref_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.parent_id == PromptCluster.id,
            )
        )
        for node in self_ref_q.scalars():
            domain_label = (node.domain or "general").lower().split(":")[0].strip()
            domain_node = (await db.execute(
                select(PromptCluster).where(
                    PromptCluster.state == "domain",
                    func.lower(PromptCluster.label) == domain_label,
                )
            )).scalar()
            if domain_node and domain_node.id != node.id:
                node.parent_id = domain_node.id
                repaired += 1
                logger.info("Repaired self-ref parent: '%s' → domain '%s'", node.label, domain_label)

        # Repair non-domain clusters with non-domain parents
        domain_id_map: dict[str, str] = {}
        for row in (await db.execute(
            select(PromptCluster.id, PromptCluster.label).where(PromptCluster.state == "domain")
        )):
            domain_id_map[row[1].lower()] = row[0]

        non_domain_parent_q2 = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state != "domain",
                PromptCluster.parent_id.isnot(None),
                PromptCluster.parent_id.notin_(list(domain_id_map.values())),
            )
        )
        reparented = 0
        for node in non_domain_parent_q2.scalars():
            domain_label = (node.domain or "general").lower().split(":")[0].strip()
            target = domain_id_map.get(domain_label) or domain_id_map.get("general")
            if target and target != node.parent_id:
                node.parent_id = target
                reparented += 1
        if reparented:
            repaired += reparented
            logger.info("Repaired %d non-domain parent relationships", reparented)

        # Unarchive clusters with active usage AND actual content.
        # Clusters with usage_count > 0 but member_count == 0 are ghosts —
        # their content was reassigned and the usage is stale. Only unarchive
        # when members exist to back the usage data.
        archived_used = (await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "archived",
                PromptCluster.usage_count > 0,
                PromptCluster.member_count > 0,
            )
        )).scalars().all()
        for node in archived_used:
            node.state = "active"
            node.archived_at = None
            repaired += 1
            logger.info(
                "Unarchived cluster with usage: '%s' (usage=%d, members=%d)",
                node.label, node.usage_count, node.member_count,
            )

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
