"""TaxonomyEngine — hot, warm, and cold path orchestration for the Evolutionary
Taxonomy Engine.

Spec Section 2.3, 2.5, 2.6, 3.5, 4.2, 6.4, 7.3, 7.5, 8.5.

Responsibilities:
  - process_optimization: embed + assign family + extract meta-patterns (hot path)
  - run_warm_path: periodic re-clustering with lifecycle (split > emerge > merge > retire)
  - run_cold_path: full HDBSCAN + UMAP refit (the "defrag" operation)
  - map_domain: embed domain_raw, optional Bayesian blend with applied pattern
    centroids, cosine search over confirmed TaxonomyNode centroids.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PROMPTS_DIR, settings
from app.models import (
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PatternFamily,
    TaxonomyNode,
    TaxonomySnapshot,
)
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader
from app.services.taxonomy.sparkline import compute_sparkline_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — cosine similarity thresholds
# ---------------------------------------------------------------------------

FAMILY_MERGE_THRESHOLD = 0.78
PATTERN_MERGE_THRESHOLD = 0.82
DOMAIN_ALIGNMENT_FLOOR = 0.35

# Pattern matching thresholds (Spec Section 7.2, 7.4)
FAMILY_MATCH_THRESHOLD = 0.72
CLUSTER_MATCH_THRESHOLD = 0.60
CANDIDATE_THRESHOLD = 0.80

# Warm path operational limits
DEADLOCK_BREAKER_THRESHOLD = 5  # consecutive rejected cycles before forcing
MAX_META_PATTERNS_PER_EXTRACTION = 5  # max patterns per LLM extraction
SPLIT_COHERENCE_FLOOR = 0.5  # below this coherence, node is a split candidate
SPLIT_MIN_MEMBERS = 6  # minimum members before a node can be split
PROMPT_TRUNCATION_LIMIT = 2000  # max chars for prompts sent to LLM extraction

# ---------------------------------------------------------------------------
# Public data-transfer objects
# ---------------------------------------------------------------------------


@dataclass
class TaxonomyMapping:
    """Result of map_domain — may be fully unmapped (taxonomy_node_id is None)."""

    taxonomy_node_id: str | None
    taxonomy_label: str | None
    taxonomy_breadcrumb: list[str]
    domain_raw: str


@dataclass
class PatternMatch:
    """Result of a pattern similarity search against the knowledge graph."""

    family: PatternFamily | None
    taxonomy_node: TaxonomyNode | None
    meta_patterns: list[MetaPattern]
    similarity: float
    match_level: str  # "family" | "cluster" | "none"


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
# Pydantic schema for _extract_meta_patterns structured output
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
# TaxonomyEngine
# ---------------------------------------------------------------------------


class TaxonomyEngine:
    """Orchestrates the hot path for the Evolutionary Taxonomy Engine.

    Args:
        embedding_service: EmbeddingService instance (or mock in tests).
        provider: LLM provider used for Haiku calls. None disables LLM steps.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self._embedding = embedding_service or EmbeddingService()
        self._provider = provider
        self._prompt_loader = PromptLoader(PROMPTS_DIR)
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
          4. Find or create PatternFamily via _assign_family().
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

            # 2. Find or create PatternFamily
            # Use domain_raw (free-text from analyzer) as the canonical domain.
            # The taxonomy engine does NOT constrain domains to a hardcoded list —
            # domains are emergent properties discovered through clustering.
            async with self._lock:
                family = await self._assign_family(
                    db=db,
                    embedding=embedding,
                    intent_label=opt.intent_label or "general",
                    domain=opt.domain_raw or opt.domain or "general",
                    task_type=opt.task_type or "general",
                    overall_score=opt.overall_score,
                )

            # 3. Extract meta-patterns
            meta_texts = await self._extract_meta_patterns(opt, db)

            # 4. Merge meta-patterns
            for text in meta_texts:
                await self._merge_meta_pattern(db, family.id, text)

            # 5. Write join record
            join = OptimizationPattern(
                optimization_id=opt.id,
                family_id=family.id,
                relationship="source",
            )
            db.add(join)

            await db.commit()
            logger.debug(
                "Taxonomy extraction complete: opt=%s family='%s' meta_patterns=%d",
                optimization_id,
                family.intent_label,
                len(meta_texts),
            )

            # 6. Publish taxonomy_changed event (Spec Section 6.5)
            try:
                from app.services.event_bus import event_bus
                event_bus.publish("taxonomy_changed", {
                    "optimization_id": optimization_id,
                    "family_id": family.id,
                    "family_label": family.intent_label,
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
    # Pattern matching (Spec Section 7.2, 7.4, 7.7, 7.9)
    # ------------------------------------------------------------------

    async def match_prompt(
        self, prompt_text: str, db: AsyncSession,
    ) -> PatternMatch | None:
        """Hierarchical pattern matching for on-paste suggestion.

        Reference: Spec Section 7.2, 7.4, 7.7, 7.9

        Cascade search:
        1. Embed prompt
        2. Search leaf families -- if cosine >= family_threshold -> family match
        3. If no leaf match, search parent clusters -- if cosine >= cluster_threshold -> cluster match
        4. No match at any level -> return None

        Cold-start: candidate families use strict 0.80 threshold (Spec 7.4).
        Thresholds adapt per-cluster coherence (Spec 7.9).
        """
        from app.services.taxonomy.quality import suggestion_threshold

        # 1. Embed the prompt text
        query_emb = await self._embedding.aembed_single(prompt_text)

        # ------------------------------------------------------------------
        # Level 1: Family-level search
        # ------------------------------------------------------------------
        result = await db.execute(
            select(PatternFamily).where(
                PatternFamily.taxonomy_node_id.isnot(None)
            )
        )
        families = list(result.scalars().all())

        if families:
            # Build family centroids and load their parent nodes
            valid_families: list[PatternFamily] = []
            centroids: list[np.ndarray] = []
            node_ids: set[str] = set()

            for f in families:
                try:
                    c = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                    if c.shape[0] != query_emb.shape[0]:
                        continue
                    centroids.append(c)
                    valid_families.append(f)
                    if f.taxonomy_node_id:
                        node_ids.add(f.taxonomy_node_id)
                except (ValueError, TypeError):
                    continue

            # Pre-load all referenced taxonomy nodes
            node_map: dict[str, TaxonomyNode] = {}
            if node_ids:
                node_result = await db.execute(
                    select(TaxonomyNode).where(TaxonomyNode.id.in_(list(node_ids)))
                )
                for n in node_result.scalars().all():
                    node_map[n.id] = n

            if centroids:
                # Search all family centroids
                matches = EmbeddingService.cosine_search(
                    query_emb, centroids, top_k=len(centroids)
                )

                for idx, score in matches:
                    family = valid_families[idx]
                    node = node_map.get(family.taxonomy_node_id) if family.taxonomy_node_id else None

                    # Determine threshold based on node state (Spec 7.4)
                    if node and node.state == "candidate":
                        threshold = CANDIDATE_THRESHOLD
                    elif node:
                        coherence = node.coherence if node.coherence is not None else 0.0
                        threshold = suggestion_threshold(
                            base=FAMILY_MATCH_THRESHOLD, coherence=coherence
                        )
                    else:
                        threshold = FAMILY_MATCH_THRESHOLD

                    if score >= threshold:
                        # Load meta-patterns for this family
                        mp_result = await db.execute(
                            select(MetaPattern).where(
                                MetaPattern.family_id == family.id
                            )
                        )
                        meta_patterns = list(mp_result.scalars().all())

                        return PatternMatch(
                            family=family,
                            taxonomy_node=node,
                            meta_patterns=meta_patterns,
                            similarity=score,
                            match_level="family",
                        )

        # ------------------------------------------------------------------
        # Level 2: Cluster-level fallback
        # ------------------------------------------------------------------
        node_result = await db.execute(
            select(TaxonomyNode).where(
                TaxonomyNode.state.in_(["confirmed", "candidate"])
            )
        )
        all_nodes = list(node_result.scalars().all())

        if all_nodes:
            valid_nodes: list[TaxonomyNode] = []
            node_centroids: list[np.ndarray] = []

            for n in all_nodes:
                try:
                    c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                    if c.shape[0] != query_emb.shape[0]:
                        continue
                    node_centroids.append(c)
                    valid_nodes.append(n)
                except (ValueError, TypeError):
                    continue

            if node_centroids:
                matches = EmbeddingService.cosine_search(
                    query_emb, node_centroids, top_k=len(node_centroids)
                )

                for idx, score in matches:
                    node = valid_nodes[idx]
                    coherence = node.coherence if node.coherence is not None else 0.0
                    # Spec 7.4 strict CANDIDATE_THRESHOLD applies only at family
                    # level. Cluster-level uses adaptive threshold for all node
                    # states — the match is more general (parent cluster context).
                    threshold = suggestion_threshold(
                        base=CLUSTER_MATCH_THRESHOLD, coherence=coherence
                    )

                    if score >= threshold:
                        # Aggregate meta-patterns from top-3 child families
                        # ranked by cosine similarity to query (Spec 7.7)
                        child_fam_result = await db.execute(
                            select(PatternFamily)
                            .where(PatternFamily.taxonomy_node_id == node.id)
                        )
                        candidate_families = list(child_fam_result.scalars().all())

                        # Also include families from child nodes
                        child_node_result = await db.execute(
                            select(TaxonomyNode).where(
                                TaxonomyNode.parent_id == node.id
                            )
                        )
                        child_nodes = list(child_node_result.scalars().all())
                        child_node_ids = [cn.id for cn in child_nodes]

                        if child_node_ids:
                            child_child_fam_result = await db.execute(
                                select(PatternFamily)
                                .where(
                                    PatternFamily.taxonomy_node_id.in_(child_node_ids)
                                )
                            )
                            candidate_families.extend(
                                child_child_fam_result.scalars().all()
                            )

                        # Rank all candidate families by cosine similarity
                        # to the query embedding and take top-3
                        scored_families: list[tuple[PatternFamily, float]] = []
                        for fam in candidate_families:
                            try:
                                fc = np.frombuffer(
                                    fam.centroid_embedding, dtype=np.float32
                                )
                                if fc.shape[0] != query_emb.shape[0]:
                                    continue
                                norm_fc = np.linalg.norm(fc)
                                norm_q = np.linalg.norm(query_emb)
                                if norm_fc > 0 and norm_q > 0:
                                    sim = float(
                                        np.dot(query_emb, fc) / (norm_q * norm_fc)
                                    )
                                    scored_families.append((fam, sim))
                            except (ValueError, TypeError):
                                continue

                        scored_families.sort(key=lambda x: x[1], reverse=True)
                        top_families = [f for f, _ in scored_families[:3]]

                        # Gather meta-patterns from these families
                        family_ids = [f.id for f in top_families]
                        if family_ids:
                            mp_result = await db.execute(
                                select(MetaPattern).where(
                                    MetaPattern.family_id.in_(family_ids)
                                )
                            )
                            all_meta_patterns = list(mp_result.scalars().all())
                        else:
                            all_meta_patterns = []

                        # Deduplicate at cosine 0.82
                        deduped = self._deduplicate_meta_patterns(all_meta_patterns)

                        return PatternMatch(
                            family=None,
                            taxonomy_node=node,
                            meta_patterns=deduped,
                            similarity=score,
                            match_level="cluster",
                        )

        # ------------------------------------------------------------------
        # No match at any level
        # ------------------------------------------------------------------
        return PatternMatch(
            family=None,
            taxonomy_node=None,
            meta_patterns=[],
            similarity=0.0,
            match_level="none",
        )

    def _deduplicate_meta_patterns(
        self, patterns: list[MetaPattern]
    ) -> list[MetaPattern]:
        """Deduplicate meta-patterns by cosine similarity at PATTERN_MERGE_THRESHOLD.

        Keeps the first occurrence (by order) and drops near-duplicates.
        Patterns without embeddings are always kept.
        """
        if len(patterns) <= 1:
            return patterns

        deduped: list[MetaPattern] = []
        deduped_embeddings: list[np.ndarray] = []

        for mp in patterns:
            if not mp.embedding:
                deduped.append(mp)
                continue

            try:
                emb = np.frombuffer(mp.embedding, dtype=np.float32)
            except (ValueError, TypeError):
                deduped.append(mp)
                continue

            # Check against already-kept patterns
            is_duplicate = False
            for kept_emb in deduped_embeddings:
                if emb.shape != kept_emb.shape:
                    continue
                norm_a = np.linalg.norm(emb)
                norm_b = np.linalg.norm(kept_emb)
                if norm_a > 0 and norm_b > 0:
                    sim = float(np.dot(emb, kept_emb) / (norm_a * norm_b))
                    if sim >= PATTERN_MERGE_THRESHOLD:
                        is_duplicate = True
                        break

            if not is_duplicate:
                deduped.append(mp)
                deduped_embeddings.append(emb)

        return deduped

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
                snap = await self._create_warm_snapshot(
                    db, q_system=0.0, operations=[], ops_attempted=0, ops_accepted=0,
                )
                return WarmPathResult(
                    snapshot_id=snap.id,
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

        # 1. Load all confirmed nodes
        result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
        )
        confirmed_nodes = list(result.scalars().all())

        # 2. Compute Q_before
        q_before = self._compute_q_from_nodes(confirmed_nodes)

        # 3. Gather candidate operations from lifecycle module
        ops_attempted = 0
        ops_accepted = 0
        operations_log: list[dict] = []
        deadlock_breaker_used = False

        # --- Priority 1: Split (Spec Section 3.5) ---
        # Detect split candidates: confirmed nodes with low coherence and enough
        # members to produce viable child clusters.
        for node in confirmed_nodes:
            coherence = node.coherence if node.coherence is not None else 1.0
            if coherence < SPLIT_COHERENCE_FLOOR and (node.member_count or 0) >= SPLIT_MIN_MEMBERS:
                ops_attempted += 1
                # Gather families assigned to this node
                fam_q = await db.execute(
                    select(PatternFamily).where(
                        PatternFamily.taxonomy_node_id == node.id
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
        # Load unassigned families (no taxonomy_node_id) for emerge candidates
        fam_result = await db.execute(
            select(PatternFamily).where(PatternFamily.taxonomy_node_id.is_(None))
        )
        unassigned_families = list(fam_result.scalars().all())

        # Try emerge if enough unassigned families exist
        if len(unassigned_families) >= 3:
            ops_attempted += 1
            embeddings = []
            family_ids = []
            for f in unassigned_families:
                try:
                    emb = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                    embeddings.append(emb)
                    family_ids.append(f.id)
                except (ValueError, TypeError):
                    continue

            if len(embeddings) >= 3:
                cluster_result = batch_cluster(embeddings, min_cluster_size=3)
                if cluster_result.n_clusters > 0:
                    from app.services.taxonomy.lifecycle import attempt_emerge

                    for cid in range(cluster_result.n_clusters):
                        mask = cluster_result.labels == cid
                        cluster_fam_ids = [
                            family_ids[i] for i in range(len(family_ids)) if mask[i]
                        ]
                        cluster_embs = [
                            embeddings[i] for i in range(len(embeddings)) if mask[i]
                        ]
                        if cluster_fam_ids:
                            node = await attempt_emerge(
                                db=db,
                                member_family_ids=cluster_fam_ids,
                                embeddings=cluster_embs,
                                warm_path_age=self._warm_path_age,
                                provider=self._provider,
                                model=settings.MODEL_HAIKU,
                            )
                            if node:
                                ops_accepted += 1
                                operations_log.append(
                                    {"type": "emerge", "node_id": node.id}
                                )

        # Try merge on confirmed nodes that are close in embedding space
        if len(confirmed_nodes) >= 2:
            centroids = []
            valid_nodes: list[TaxonomyNode] = []
            for n in confirmed_nodes:
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

                    merged = await attempt_merge(
                        db=db,
                        node_a=valid_nodes[int(best_i)],
                        node_b=valid_nodes[int(best_j)],
                        warm_path_age=self._warm_path_age,
                    )
                    if merged:
                        ops_accepted += 1
                        operations_log.append(
                            {"type": "merge", "node_id": merged.id}
                        )

        # Try retire on idle nodes (member_count == 0 and confirmed)
        for node in confirmed_nodes:
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

        # 4. Update per-node separation and compute Q_after
        result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
        )
        confirmed_after = list(result.scalars().all())
        self._update_per_node_separation(confirmed_after)
        q_after = self._compute_q_from_nodes(confirmed_after)

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
            # Re-query confirmed nodes after rollback so cache isn't stale
            result = await db.execute(
                select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
            )
            confirmed_after = list(result.scalars().all())

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
            # Re-run emerge on unassigned families (cheapest constructive op).
            if len(unassigned_families) >= 3:
                from app.services.taxonomy.lifecycle import attempt_emerge

                force_embs = []
                force_ids = []
                for f in unassigned_families:
                    try:
                        emb = np.frombuffer(
                            f.centroid_embedding, dtype=np.float32
                        )
                        force_embs.append(emb)
                        force_ids.append(f.id)
                    except (ValueError, TypeError):
                        continue

                if len(force_embs) >= 3:
                    cluster_result = batch_cluster(
                        force_embs, min_cluster_size=3
                    )
                    if cluster_result.n_clusters > 0:
                        mask = cluster_result.labels == 0
                        forced_fam_ids = [
                            force_ids[i]
                            for i in range(len(force_ids))
                            if mask[i]
                        ]
                        forced_embs = [
                            force_embs[i]
                            for i in range(len(force_embs))
                            if mask[i]
                        ]
                        if forced_fam_ids:
                            node = await attempt_emerge(
                                db=db,
                                member_family_ids=forced_fam_ids,
                                embeddings=forced_embs,
                                warm_path_age=self._warm_path_age,
                                provider=self._provider,
                                model=settings.MODEL_HAIKU,
                            )
                            if node:
                                ops_accepted += 1
                                operations_log.append(
                                    {"type": "emerge", "node_id": node.id}
                                )
                                logger.info(
                                    "Deadlock breaker forced emerge: node=%s",
                                    node.id,
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
        warm path).  Recluster all PatternFamily embeddings, update or create
        TaxonomyNodes, run UMAP 3D projection with Procrustes alignment,
        regenerate OKLab colors, create snapshot.

        Returns:
            ColdPathResult on completion.
        """
        async with self._warm_path_lock:
            try:
                return await self._run_cold_path_inner(db)
            except Exception as exc:
                logger.error("Cold path failed: %s", exc, exc_info=True)
                from app.services.taxonomy.snapshot import create_snapshot

                snap = await create_snapshot(
                    db,
                    trigger="cold_path",
                    q_system=0.0,
                    q_coherence=0.0,
                    q_separation=0.0,
                    q_coverage=0.0,
                )
                return ColdPathResult(
                    snapshot_id=snap.id,
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
        )
        from app.services.taxonomy.coloring import (
            enforce_minimum_delta_e,
            generate_color,
        )
        from app.services.taxonomy.projection import UMAPProjector, procrustes_align
        from app.services.taxonomy.snapshot import create_snapshot

        nodes_created = 0
        nodes_updated = 0

        # 1. Load all PatternFamily embeddings
        fam_result = await db.execute(select(PatternFamily))
        families = list(fam_result.scalars().all())

        embeddings: list[np.ndarray] = []
        valid_families: list[PatternFamily] = []
        family_by_id: dict[str, PatternFamily] = {}
        for f in families:
            try:
                emb = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                embeddings.append(emb)
                valid_families.append(f)
                family_by_id[f.id] = f
            except (ValueError, TypeError):
                logger.warning(
                    "Skipping family '%s' — corrupt centroid", f.intent_label
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

        # 3. Create/update TaxonomyNodes from cluster results
        # Load existing confirmed nodes for potential updates
        existing_result = await db.execute(
            select(TaxonomyNode).where(
                TaxonomyNode.state.in_(["confirmed", "candidate"])
            )
        )
        existing_nodes = {n.id: n for n in existing_result.scalars().all()}

        node_embeddings: list[np.ndarray] = []
        all_nodes: list[TaxonomyNode] = []

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
                centroid = np.mean(
                    np.stack(cluster_embs, axis=0), axis=0
                ).astype(np.float32)
                norm = np.linalg.norm(centroid)
                if norm > 0:
                    centroid = centroid / norm

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
                        sim = float(
                            np.dot(centroid, ex_emb)
                            / (np.linalg.norm(centroid) * np.linalg.norm(ex_emb) + 1e-9)
                        )
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
                matched_node.state = "confirmed"
                nodes_updated += 1
                node = matched_node
            else:
                # Create new node
                from app.services.taxonomy.labeling import generate_label

                member_texts = [
                    f.intent_label
                    for f in valid_families
                    if f.id in set(cluster_fam_ids) and f.intent_label
                ]
                label = await generate_label(
                    provider=self._provider,
                    member_texts=member_texts,
                    model=settings.MODEL_HAIKU,
                )
                node = TaxonomyNode(
                    label=label,
                    centroid_embedding=centroid.astype(np.float32).tobytes(),
                    member_count=len(cluster_fam_ids),
                    coherence=coherence,
                    state="confirmed",
                    color_hex=generate_color(0.0, 0.0, 0.0),
                )
                db.add(node)
                await db.flush()
                nodes_created += 1

            # Link families to this node (O(k) via dict lookup)
            for fid in cluster_fam_ids:
                fam = family_by_id.get(fid)
                if fam:
                    fam.taxonomy_node_id = node.id

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
            select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
        )
        confirmed_after = list(result.scalars().all())

        self._update_per_node_separation(confirmed_after)

        # 7. Compute final Q_system (reads node.separation set above)
        q_system = self._compute_q_from_nodes(confirmed_after)

        # 8. Aggregate metrics for snapshot
        node_centroids = []
        for n in confirmed_after:
            try:
                c = np.frombuffer(n.centroid_embedding, dtype=np.float32)
                node_centroids.append(c)
            except (ValueError, TypeError):
                continue

        separation = compute_separation(node_centroids) if len(node_centroids) >= 2 else 1.0
        coherences = [n.coherence for n in confirmed_after if n.coherence is not None]
        mean_coherence = float(np.mean(coherences)) if coherences else 0.0

        await db.commit()

        # 9. Create snapshot
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

        return ColdPathResult(
            snapshot_id=snap.id,
            q_system=q_system,
            nodes_created=nodes_created,
            nodes_updated=nodes_updated,
            umap_fitted=umap_fitted,
        )

    # ------------------------------------------------------------------
    # Warm/cold path helpers
    # ------------------------------------------------------------------

    def _compute_q_from_nodes(self, nodes: list[TaxonomyNode]) -> float:
        """Compute Q_system from a list of TaxonomyNode rows."""
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
                    state=n.state or "confirmed",
                )
            )

        # DBCV ramp: gate = ≥5 confirmed nodes, then ramp linearly over
        # warm_path_age / 20 observations (Spec Section 2.5).
        confirmed_count = sum(1 for m in metrics if m.state == "confirmed")
        if confirmed_count >= 5:
            ramp = min(1.0, max(0.0, self._warm_path_age / 20.0))
        else:
            ramp = 0.0
        weights = QWeights.from_ramp(ramp)

        return compute_q_system(metrics, weights)

    @staticmethod
    def _update_per_node_separation(nodes: list[TaxonomyNode]) -> None:
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

        # Load confirmed nodes for metrics
        result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
        )
        confirmed = list(result.scalars().all())

        # Compute coherence and separation
        coherences = [n.coherence for n in confirmed if n.coherence is not None]
        mean_coherence = float(np.mean(coherences)) if coherences else 0.0

        centroids = []
        for n in confirmed:
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
    # Domain mapping
    # ------------------------------------------------------------------

    async def map_domain(
        self,
        domain_raw: str,
        db: AsyncSession,
        applied_pattern_ids: list[str] | None = None,
    ) -> TaxonomyMapping:
        """Map a free-text domain string to the nearest confirmed TaxonomyNode.

        If applied_pattern_ids are provided, compute a pattern centroid and
        blend 70 % analyzer embedding + 30 % pattern centroid (Bayesian prior).

        Args:
            domain_raw: Raw domain string from the analyzer phase.
            db: Async SQLAlchemy session.
            applied_pattern_ids: Optional list of MetaPattern IDs applied to
                this optimization — used to inject a pattern-based prior.

        Returns:
            TaxonomyMapping.  taxonomy_node_id is None when no confirmed node
            has cosine similarity ≥ DOMAIN_ALIGNMENT_FLOOR.
        """
        # Embed domain_raw
        query_emb = await self._embedding.aembed_single(domain_raw)

        # Optional 70/30 Bayesian blend with pattern centroid
        if applied_pattern_ids:
            pattern_centroid = await self._compute_pattern_centroid(
                db, applied_pattern_ids
            )
            if pattern_centroid is not None:
                # 70 % analyzer, 30 % pattern prior
                blended = 0.7 * query_emb + 0.3 * pattern_centroid
                norm = np.linalg.norm(blended)
                if norm > 0:
                    query_emb = blended / norm

        # Load confirmed TaxonomyNode centroids
        result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.state == "confirmed")
        )
        nodes = result.scalars().all()

        if not nodes:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        # Build centroid list, skip corrupt rows
        valid_nodes: list[TaxonomyNode] = []
        centroids: list[np.ndarray] = []
        for node in nodes:
            try:
                c = np.frombuffer(node.centroid_embedding, dtype=np.float32)
                if c.shape[0] != query_emb.shape[0]:
                    logger.warning(
                        "TaxonomyNode '%s' centroid dim %d != query dim %d — skipped",
                        node.label,
                        c.shape[0],
                        query_emb.shape[0],
                    )
                    continue
                centroids.append(c)
                valid_nodes.append(node)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "TaxonomyNode '%s' has corrupt centroid: %s — skipped",
                    node.label,
                    exc,
                )

        if not centroids:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        # Nearest centroid search
        matches = EmbeddingService.cosine_search(query_emb, centroids, top_k=1)
        if not matches:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        idx, score = matches[0]
        if score < DOMAIN_ALIGNMENT_FLOOR:
            return TaxonomyMapping(
                taxonomy_node_id=None,
                taxonomy_label=None,
                taxonomy_breadcrumb=[],
                domain_raw=domain_raw,
            )

        best_node = valid_nodes[idx]
        breadcrumb = await self._build_breadcrumb(db, best_node)

        return TaxonomyMapping(
            taxonomy_node_id=best_node.id,
            taxonomy_label=best_node.label,
            taxonomy_breadcrumb=breadcrumb,
            domain_raw=domain_raw,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _assign_family(
        self,
        db: AsyncSession,
        embedding: np.ndarray,
        intent_label: str,
        domain: str,
        task_type: str,
        overall_score: float | None,
    ) -> PatternFamily:
        """Find nearest PatternFamily or create a new one.

        Nearest centroid search with FAMILY_MERGE_THRESHOLD guard and
        cross-domain merge prevention.  Updates centroid as running mean
        ``(old * n + new) / (n+1)`` on merge.

        Args:
            db: Async SQLAlchemy session.
            embedding: Unit-norm embedding of the raw prompt.
            intent_label: Analyzer intent label.
            domain: Free-text domain string from the analyzer (via domain_raw).
            task_type: Analyzer task type.
            overall_score: Pipeline overall score (may be None).

        Returns:
            Existing (updated) or newly-created PatternFamily.
        """

        result = await db.execute(select(PatternFamily))
        families = result.scalars().all()

        if families:
            valid_families: list[PatternFamily] = []
            centroids: list[np.ndarray] = []

            for f in families:
                try:
                    c = np.frombuffer(f.centroid_embedding, dtype=np.float32)
                    if c.shape[0] != embedding.shape[0]:
                        logger.warning(
                            "Skipping family '%s' — centroid dim %d != expected %d",
                            f.intent_label,
                            c.shape[0],
                            embedding.shape[0],
                        )
                        continue
                    centroids.append(c)
                    valid_families.append(f)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "Skipping family '%s' — corrupt centroid: %s",
                        f.intent_label,
                        exc,
                    )

            if centroids:
                matches = EmbeddingService.cosine_search(embedding, centroids, top_k=1)
                if matches and matches[0][1] >= FAMILY_MERGE_THRESHOLD:
                    idx, score = matches[0]
                    family = valid_families[idx]

                    # Cross-domain merge prevention
                    if family.domain != domain:
                        logger.info(
                            "Cross-domain merge prevented: family '%s' domain=%s != "
                            "incoming domain=%s (cosine=%.3f). Creating new family.",
                            family.intent_label,
                            family.domain,
                            domain,
                            score,
                        )
                        # Fall through to creation
                    else:
                        # Merge: update centroid as running mean, re-normalize
                        old_centroid = np.frombuffer(
                            family.centroid_embedding, dtype=np.float32
                        )
                        new_centroid = (old_centroid * family.member_count + embedding) / (
                            family.member_count + 1
                        )
                        # Re-normalize to unit norm — running mean drifts
                        # from unit sphere without this (critical for cosine
                        # similarity accuracy on subsequent merges).
                        c_norm = np.linalg.norm(new_centroid)
                        if c_norm > 0:
                            new_centroid = new_centroid / c_norm
                        family.centroid_embedding = new_centroid.astype(
                            np.float32
                        ).tobytes()
                        family.member_count += 1

                        # avg_score tracks the running mean over members that
                        # have a score.  Members with overall_score=None are
                        # excluded intentionally — we cannot average with None.
                        # When the first scored member arrives, avg_score is
                        # seeded with that single score.
                        if overall_score is not None and family.avg_score is not None:
                            family.avg_score = round(
                                (
                                    family.avg_score * (family.member_count - 1)
                                    + overall_score
                                )
                                / family.member_count,
                                2,
                            )
                        elif overall_score is not None:
                            family.avg_score = overall_score

                        logger.debug(
                            "Merged into family '%s' (cosine=%.3f, members=%d)",
                            family.intent_label,
                            score,
                            family.member_count,
                        )
                        return family

        # No match — create new family
        family = PatternFamily(
            intent_label=intent_label,
            domain=domain,
            task_type=task_type,
            centroid_embedding=embedding.astype(np.float32).tobytes(),
            member_count=1,
            usage_count=0,
            avg_score=overall_score,
        )
        db.add(family)
        await db.flush()  # populate ID
        logger.debug(
            "Created new PatternFamily: id=%s label='%s' domain=%s",
            family.id,
            intent_label,
            domain,
        )
        return family

    async def _extract_meta_patterns(
        self, opt: Optimization, db: AsyncSession
    ) -> list[str]:
        """Call Haiku to extract meta-patterns from a completed optimization.

        Renders extract_patterns.md template, calls provider.complete_parsed()
        with _ExtractedPatterns structured output.  Caps at 5 patterns.
        Returns empty list on any error (non-fatal).

        Args:
            opt: Completed Optimization row with prompt text and metadata.
            db: Async SQLAlchemy session (used for taxonomy node lookup).

        Returns:
            List of meta-pattern strings (at most 5).
        """
        if not self._provider:
            logger.debug("No LLM provider — skipping meta-pattern extraction")
            return []

        try:
            # Build taxonomy context string (Spec 7.6)
            taxonomy_context = ""
            if opt.taxonomy_node_id:
                try:
                    node_result = await db.execute(
                        select(TaxonomyNode).where(TaxonomyNode.id == opt.taxonomy_node_id)
                    )
                    tax_node = node_result.scalar_one_or_none()
                    if tax_node:
                        breadcrumb = await self._build_breadcrumb(db, tax_node)
                        taxonomy_context = (
                            f'This prompt belongs to the "{tax_node.label}" pattern cluster '
                            f"({' > '.join(breadcrumb)}).\n"
                        )
                except Exception:
                    pass  # Non-fatal — empty context is fine

            template = self._prompt_loader.render(
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

            response = await self._provider.complete_parsed(
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
            return []

    async def _merge_meta_pattern(
        self, db: AsyncSession, family_id: str, pattern_text: str
    ) -> bool:
        """Merge a meta-pattern into a family — enrich existing or create new.

        Cosine search against existing MetaPatterns for the family.  If the
        best match is ≥ PATTERN_MERGE_THRESHOLD: increment source_count and
        update text if new version is longer.  Otherwise create a new row.

        Args:
            db: Async SQLAlchemy session.
            family_id: PatternFamily PK.
            pattern_text: Meta-pattern text extracted by Haiku.

        Returns:
            True if merged into existing pattern, False if new pattern created.
        """
        try:
            result = await db.execute(
                select(MetaPattern).where(MetaPattern.family_id == family_id)
            )
            existing = result.scalars().all()

            pattern_embedding = await self._embedding.aembed_single(pattern_text)

            if existing:
                embeddings: list[np.ndarray] = []
                for mp in existing:
                    if mp.embedding:
                        embeddings.append(
                            np.frombuffer(mp.embedding, dtype=np.float32)
                        )
                    else:
                        embeddings.append(
                            np.zeros(self._embedding.dimension, dtype=np.float32)
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
                family_id=family_id,
                pattern_text=pattern_text,
                embedding=pattern_embedding.astype(np.float32).tobytes(),
                source_count=1,
            )
            db.add(mp)
            logger.debug(
                "Created new MetaPattern for family=%s: '%s'",
                family_id,
                pattern_text[:50],
            )
            return False

        except Exception as exc:
            logger.error(
                "Failed to merge meta-pattern into family=%s: %s",
                family_id,
                exc,
                exc_info=True,
            )
            return False

    async def _compute_pattern_centroid(
        self, db: AsyncSession, pattern_ids: list[str]
    ) -> np.ndarray | None:
        """Compute mean centroid of TaxonomyNodes linked via MetaPattern → PatternFamily.

        Looks up MetaPatterns by ID, gets their families' taxonomy_node_id,
        loads the corresponding TaxonomyNode centroids, and returns the mean.

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

        # Collect unique family IDs
        family_ids = list({mp.family_id for mp in meta_patterns if mp.family_id})
        if not family_ids:
            return None

        # Load families to get taxonomy_node_ids
        fam_result = await db.execute(
            select(PatternFamily).where(PatternFamily.id.in_(family_ids))
        )
        families = fam_result.scalars().all()

        node_ids = list(
            {f.taxonomy_node_id for f in families if f.taxonomy_node_id}
        )
        if not node_ids:
            return None

        # Load TaxonomyNodes and collect their centroids
        node_result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.id.in_(node_ids))
        )
        nodes = node_result.scalars().all()

        vecs: list[np.ndarray] = []
        for node in nodes:
            try:
                c = np.frombuffer(node.centroid_embedding, dtype=np.float32)
                vecs.append(c)
            except (ValueError, TypeError):
                continue

        if not vecs:
            return None

        mean = np.mean(np.stack(vecs, axis=0), axis=0).astype(np.float32)
        norm = np.linalg.norm(mean)
        if norm > 0:
            mean = mean / norm
        return mean

    async def _build_breadcrumb(
        self, db: AsyncSession, node: TaxonomyNode
    ) -> list[str]:
        """Walk parent_id chain upward and return labels from root to leaf.

        Args:
            db: Async SQLAlchemy session.
            node: The leaf TaxonomyNode to start from.

        Returns:
            List of label strings ordered from root to leaf.
        """
        labels: list[str] = []
        current: TaxonomyNode | None = node
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
                select(TaxonomyNode).where(TaxonomyNode.id == current.parent_id)
            )
            current = parent_result.scalar_one_or_none()

        # Reverse so list goes root → leaf
        labels.reverse()
        return labels

    # ------------------------------------------------------------------
    # Read API (Spec Section 6.3)
    # ------------------------------------------------------------------

    async def get_tree(
        self,
        db: AsyncSession,
        min_persistence: float = 0.0,
    ) -> list[dict]:
        query = select(TaxonomyNode).where(
            TaxonomyNode.state.in_(["confirmed", "candidate"])
        )
        if min_persistence > 0:
            query = query.where(TaxonomyNode.persistence >= min_persistence)
        result = await db.execute(query)
        nodes = result.scalars().all()
        return [self._node_to_dict(n) for n in nodes]

    async def get_node(
        self,
        node_id: str,
        db: AsyncSession,
    ) -> dict | None:
        result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.id == node_id)
        )
        node = result.scalar_one_or_none()
        if not node:
            return None
        node_dict = self._node_to_dict(node)
        # Add children
        children_result = await db.execute(
            select(TaxonomyNode).where(TaxonomyNode.parent_id == node_id)
        )
        children = children_result.scalars().all()
        node_dict["children"] = [self._node_to_dict(c) for c in children]
        # Add breadcrumb
        node_dict["breadcrumb"] = await self._build_breadcrumb(db, node)
        # Add family count
        fam_result = await db.execute(
            select(PatternFamily).where(PatternFamily.taxonomy_node_id == node_id)
        )
        node_dict["family_count"] = len(fam_result.scalars().all())
        return node_dict

    async def get_stats(self, db: AsyncSession) -> dict:
        import json

        all_result = await db.execute(select(TaxonomyNode))
        all_nodes = all_result.scalars().all()

        # Node state counts
        confirmed = sum(1 for n in all_nodes if n.state == "confirmed")
        candidate = sum(1 for n in all_nodes if n.state == "candidate")
        retired = sum(1 for n in all_nodes if n.state == "retired")

        # max_depth: walk parent_id chains (with cycle guard)
        id_to_node = {n.id: n for n in all_nodes}
        max_depth = 0
        for node in all_nodes:
            depth = 0
            current = node
            visited: set[str] = set()
            while current.parent_id and current.parent_id in id_to_node:
                if current.parent_id in visited:
                    break  # cycle detected — stop walking
                visited.add(current.parent_id)
                depth += 1
                current = id_to_node[current.parent_id]
            if depth > max_depth:
                max_depth = depth

        # leaf_count: active nodes with no children
        active_ids = {n.id for n in all_nodes if n.state != "retired"}
        parent_ids = {n.parent_id for n in all_nodes if n.parent_id}
        leaf_count = sum(1 for nid in active_ids if nid not in parent_ids)

        # Recent snapshots (last 30, ascending chronological)
        from app.services.taxonomy.snapshot import get_snapshot_history

        recent = await get_snapshot_history(db, limit=30)
        snapshots = list(reversed(recent))  # newest-first → oldest-first

        # Latest snapshot metrics
        latest = snapshots[-1] if snapshots else None
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

        # last_warm_path and last_cold_path timestamps
        last_warm_path: str | None = None
        last_cold_path: str | None = None
        for snap in reversed(snapshots):
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
            "nodes": {
                "confirmed": confirmed,
                "candidate": candidate,
                "retired": retired,
                "max_depth": max_depth,
                "leaf_count": leaf_count,
            },
            "q_history": q_history,
            "q_sparkline": sparkline,
            "last_warm_path": last_warm_path,
            "last_cold_path": last_cold_path,
            "warm_path_age": self._warm_path_age,
        }

    async def increment_usage(self, family_id: str, db: AsyncSession) -> None:
        """Increment usage on the family and propagate up the taxonomy tree.

        Spec Section 7.8 — usage count flows upward so that ancestor
        nodes reflect aggregate activity from their subtree.

        Args:
            family_id: ID of the PatternFamily whose patterns were applied.
            db: Async SQLAlchemy session.
        """
        family = await db.get(PatternFamily, family_id)
        if not family:
            logger.warning("increment_usage: family %s not found", family_id)
            return

        family.usage_count = (family.usage_count or 0) + 1

        # Walk up the taxonomy tree
        node_id = family.taxonomy_node_id
        while node_id:
            node = await db.get(TaxonomyNode, node_id)
            if not node:
                break
            node.usage_count = (node.usage_count or 0) + 1
            node_id = node.parent_id

        await db.flush()
        logger.debug(
            "Usage incremented: family=%s (usage=%d)",
            family_id,
            family.usage_count,
        )

    @staticmethod
    def _node_to_dict(node: TaxonomyNode) -> dict:
        return {
            "id": node.id,
            "label": node.label,
            "parent_id": node.parent_id,
            "state": node.state,
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
            "created_at": node.created_at.isoformat() if node.created_at else None,
        }
