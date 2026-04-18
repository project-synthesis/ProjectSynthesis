"""PromptLifecycleService — auto-curation, state promotion, and temporal decay
for PromptClusters.

Called by the taxonomy engine at different cadences:
  - check_promotion: after hot path (per optimization)
  - curate + decay_usage: after warm path (periodic maintenance)
  - backfill_orphans: at startup (link legacy optimizations)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, OptimizationPattern, PromptCluster

# NOTE: ``EXCLUDED_STRUCTURAL_STATES`` and ``TemplateService`` are imported
# lazily inside the functions that need them.  A top-level
# ``from app.services.taxonomy._constants import ...`` forces
# ``taxonomy/__init__.py`` to initialize, which eagerly loads
# ``warm_phases`` → back here and breaks with a partially-initialized
# module on cold-start (the ``AUTO_RETIRE_SOURCE_FLOOR`` constant
# defined below is needed by ``warm_phases``).

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Promotion thresholds
# ---------------------------------------------------------------------------
ACTIVE_TO_MATURE_MEMBER_COUNT = 5
ACTIVE_TO_MATURE_COHERENCE = 0.7
ACTIVE_TO_MATURE_AVG_SCORE = 7.0

FORK_TEMPLATE_USAGE_COUNT = 3  # was MATURE_TO_TEMPLATE_USAGE_COUNT
FORK_TEMPLATE_AVG_SCORE = 7.5  # was MATURE_TO_TEMPLATE_AVG_SCORE
AUTO_RETIRE_SOURCE_FLOOR = 6.0  # 1.5-pt hysteresis below FORK_TEMPLATE_AVG_SCORE

# ---------------------------------------------------------------------------
# Curation thresholds
# ---------------------------------------------------------------------------
STALE_DAYS = 90
QUALITY_PRUNE_SCORE = 4.0
QUALITY_PRUNE_MIN_MEMBERS = 3
PRUNE_FLAG_ARCHIVE_THRESHOLD = 2

# ---------------------------------------------------------------------------
# Decay settings
# ---------------------------------------------------------------------------
DECAY_AFTER_DAYS = 30
DECAY_FACTOR = 0.9

# ---------------------------------------------------------------------------
# Backfill settings
# ---------------------------------------------------------------------------
# Backfill uses a lower threshold than the hot-path merge (0.72) because
# cluster centroids are averaged embeddings — a single prompt-to-centroid
# cosine similarity is naturally lower than prompt-to-prompt similarity.
# 0.45 matches the auto-injection threshold in pattern_injection.py.
BACKFILL_THRESHOLD = 0.45


def _utcnow() -> datetime:
    """Naive UTC timestamp — matches SQLAlchemy DateTime() round-trip on SQLite.

    SQLAlchemy's ``DateTime()`` (without ``timezone=True``) strips timezone info
    on storage and returns naive datetimes on read.  Using naive UTC here ensures
    all in-memory comparisons (e.g. ``activity_time < stale_cutoff``) succeed
    without ``TypeError: can't compare offset-naive and offset-aware datetimes``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PromptLifecycleService:
    """Auto-curation, state promotion, and temporal decay for PromptClusters."""

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    async def check_promotion(self, db: AsyncSession, cluster_id: str) -> str | None:
        """Check if cluster should be promoted to a higher state.

        Promotion rules:
        - active -> mature: member_count >= 5, coherence >= 0.7, avg_score >= 7.0
        - mature (fork threshold met): usage_count >= 3, avg_score >= 7.5
          → calls TemplateService.fork_from_cluster(auto=True); cluster stays
          at state='mature', a new PromptTemplate row is created.
          Returns "template_forked" on success, None if no optimizations to fork.

        Returns new state string if promoted, None otherwise.
        Sets promoted_at timestamp on promotion.

        Callers may discard the return value — it is a diagnostic sentinel only.
        The only meaningful distinction is whether a state transition occurred
        (any non-None value) vs nothing changed (None).
        """
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.id == cluster_id)
        )
        cluster = result.scalar_one_or_none()
        if cluster is None:
            return None

        new_state: str | None = None

        # Safety net: promote any legacy candidates to active.
        # Warm-path emerge/split now create as "active" directly, but
        # older candidates may exist from before this fix.
        if cluster.state == "candidate":
            new_state = "active"

        elif cluster.state == "active":
            if (
                (cluster.member_count or 0) >= ACTIVE_TO_MATURE_MEMBER_COUNT
                and (cluster.coherence or 0) >= ACTIVE_TO_MATURE_COHERENCE
                and (cluster.avg_score or 0) >= ACTIVE_TO_MATURE_AVG_SCORE
            ):
                new_state = "mature"

        elif cluster.state == "mature":
            if (
                (cluster.usage_count or 0) >= FORK_TEMPLATE_USAGE_COUNT
                and (cluster.avg_score or 0) >= FORK_TEMPLATE_AVG_SCORE
            ):
                # Fork-on-promotion: cluster stays at state='mature', template row created.
                # Lazy import — see module-level note on circular dependency.
                from app.services.template_service import TemplateService

                tpl = await TemplateService().fork_from_cluster(cluster.id, db, auto=True)
                if tpl is not None:
                    logger.info(
                        "Cluster %s auto-forked template %s (members=%d, score=%.1f)",
                        cluster.id, tpl.id, cluster.member_count or 0, cluster.avg_score or 0,
                    )
                    return "template_forked"
                return None

        if new_state is not None:
            cluster.state = new_state
            cluster.promoted_at = _utcnow()
            await db.flush()
            logger.info(
                "Cluster %s promoted to %s (members=%d, score=%.1f)",
                cluster_id,
                new_state,
                cluster.member_count or 0,
                cluster.avg_score or 0,
            )

        return new_state

    # ------------------------------------------------------------------
    # Strategy affinity
    # ------------------------------------------------------------------

    async def update_strategy_affinity(self, db: AsyncSession, cluster_id: str) -> None:
        """Set preferred_strategy to the most successful strategy for this cluster.

        Finds strategies used in 3+ optimizations linked to this cluster that scored >= 7.0,
        picks the one with the highest average score.
        """
        result = await db.execute(
            select(Optimization.strategy_used, func.count(), func.avg(Optimization.overall_score))
            .join(OptimizationPattern, OptimizationPattern.optimization_id == Optimization.id)
            .where(OptimizationPattern.cluster_id == cluster_id)
            .where(Optimization.overall_score >= 7.0)
            .group_by(Optimization.strategy_used)
            .having(func.count() >= 3)
            .order_by(func.avg(Optimization.overall_score).desc())
            .limit(1)
        )
        row = result.first()
        if row:
            await db.execute(
                update(PromptCluster).where(PromptCluster.id == cluster_id)
                .values(preferred_strategy=row[0])
            )
            await db.flush()
            logger.info(
                "Cluster %s preferred_strategy set to %s (avg_score=%.1f, count=%d)",
                cluster_id, row[0], row[2], row[1],
            )

    # ------------------------------------------------------------------
    # Curation
    # ------------------------------------------------------------------

    async def curate(
        self,
        db: AsyncSession,
        embedding_index: Any = None,
    ) -> dict:
        """Run curation checks on all clusters.

        Curation checks:
        - Stale detection: clusters with no activity for 90+ days
          (last_used_at or updated_at) and usage_count=0 -> archived
        - Quality pruning: avg_score < 4.0 AND member_count >= 3 ->
          increment prune_flag_count. If prune_flag_count >= 2 -> archived
        - Reset prune_flag_count to 0 for clusters above quality threshold

        On archival, clears stale metrics and removes the cluster from the
        embedding index so the hot path doesn't merge new prompts into
        defunct clusters.

        Args:
            db: Async database session.
            embedding_index: EmbeddingIndex instance for cleanup (optional).

        Returns dict with summary:
            {"archived": [cluster_ids], "flagged": [cluster_ids], "unflagged": [cluster_ids]}
        """
        from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES

        now = _utcnow()
        stale_cutoff = now - timedelta(days=STALE_DAYS)

        # Exclude structural nodes (domain, archived, project).  These have
        # usage_count=0 and could be old enough to trigger stale detection,
        # but they're organizational and must never be curated.
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
            )
        )
        clusters = list(result.scalars().all())

        archived: list[str] = []
        flagged: list[str] = []
        unflagged: list[str] = []

        for cluster in clusters:
            # --- Stale detection ---
            activity_time = cluster.last_used_at or cluster.updated_at or cluster.created_at
            is_stale = activity_time is not None and activity_time < stale_cutoff
            is_unused = (cluster.usage_count or 0) == 0

            if is_stale and is_unused:
                self._archive_cluster(cluster, now, embedding_index)
                archived.append(cluster.id)
                continue

            # --- Quality pruning ---
            has_low_score = (
                cluster.avg_score is not None
                and cluster.avg_score < QUALITY_PRUNE_SCORE
            )
            has_enough_members = (cluster.member_count or 0) >= QUALITY_PRUNE_MIN_MEMBERS

            if has_low_score and has_enough_members:
                cluster.prune_flag_count = (cluster.prune_flag_count or 0) + 1
                if cluster.prune_flag_count >= PRUNE_FLAG_ARCHIVE_THRESHOLD:
                    self._archive_cluster(cluster, now, embedding_index)
                    archived.append(cluster.id)
                else:
                    flagged.append(cluster.id)
            elif not has_low_score and (cluster.prune_flag_count or 0) > 0:
                # Quality recovered — reset flag
                cluster.prune_flag_count = 0
                unflagged.append(cluster.id)

        await db.flush()

        logger.info(
            "Curation complete: archived=%d, flagged=%d, unflagged=%d",
            len(archived),
            len(flagged),
            len(unflagged),
        )

        return {
            "archived": archived,
            "flagged": flagged,
            "unflagged": unflagged,
        }

    @staticmethod
    def _archive_cluster(
        cluster: PromptCluster,
        now: datetime,
        embedding_index: Any,
    ) -> None:
        """Centralized archival: state + metrics + embedding index cleanup.

        Every archival path (curate stale, curate quality, retire, split,
        zombie) should use this or match its behavior to avoid stale
        phantom metrics and embedding index divergence.
        """
        cluster.state = "archived"
        cluster.archived_at = now
        cluster.member_count = 0
        cluster.usage_count = 0
        cluster.avg_score = None
        cluster.scored_count = 0
        # Remove from embedding index so hot-path assign_cluster
        # doesn't return this archived cluster as a nearest match.
        if embedding_index is not None:
            try:
                embedding_index.remove(cluster.id)
            except Exception:
                pass  # non-fatal — index rebuilt on cold path

    # ------------------------------------------------------------------
    # Backfill orphans
    # ------------------------------------------------------------------

    async def backfill_orphans(
        self,
        db: AsyncSession,
        embedding_index,
        embedding_svc=None,
    ) -> int:
        """Link optimizations with null cluster_id to nearest cluster.

        For each orphan optimization:
        1. Embed the raw_prompt
        2. Search embedding_index for nearest match (threshold=0.72)
        3. If match found, set optimization.cluster_id and create
           OptimizationPattern row

        Args:
            db: Async database session.
            embedding_index: EmbeddingIndex for cosine search.
            embedding_svc: Optional EmbeddingService override (for testing).

        Returns count of linked optimizations.
        """
        if embedding_svc is None:
            from app.services.embedding_service import EmbeddingService

            embedding_svc = EmbeddingService()

        # Find optimizations with NULL cluster_id OR stale cluster_id
        # pointing to archived/non-existent clusters.  The hot path now
        # writes back opt.cluster_id (RC1 fix), but older optimizations
        # may still reference archived clusters from cold-path splits or
        # warm-path retirements that didn't reassign.
        active_cluster_ids = select(PromptCluster.id).where(
            PromptCluster.state.in_(["active", "candidate", "mature"])
        )
        result = await db.execute(
            select(Optimization).where(
                Optimization.status == "completed",
                Optimization.raw_prompt.isnot(None),
                or_(
                    Optimization.cluster_id.is_(None),
                    ~Optimization.cluster_id.in_(active_cluster_ids),
                ),
            )
        )
        orphans = list(result.scalars().all())

        if not orphans:
            return 0

        linked_count = 0
        for orphan in orphans:
            # Embed the prompt
            try:
                embedding = await embedding_svc.aembed_single(orphan.raw_prompt)
            except Exception:
                logger.warning("Failed to embed orphan %s, skipping", orphan.id)
                continue

            # Search for nearest cluster.
            # First try the standard threshold; if no match, fall back to
            # a same-domain search with a lower floor.  This handles edge
            # cases where an optimization's centroid distance is low but
            # it clearly belongs to a specific domain.
            matches = embedding_index.search(
                embedding, k=1, threshold=BACKFILL_THRESHOLD
            )
            if not matches:
                # Fallback: find the nearest active cluster in the same domain
                domain = orphan.domain or "general"
                domain_clusters = await db.execute(
                    select(PromptCluster).where(
                        PromptCluster.state.in_(["active", "candidate", "mature"]),
                        PromptCluster.domain == domain,
                    )
                )
                import numpy as _np

                best_id, best_sim = None, -1.0
                for cl in domain_clusters.scalars().all():
                    if cl.centroid_embedding:
                        try:
                            cl_emb = _np.frombuffer(cl.centroid_embedding, dtype=_np.float32)
                            sim = float(_np.dot(embedding, cl_emb) / (
                                _np.linalg.norm(embedding) * _np.linalg.norm(cl_emb) + 1e-9
                            ))
                            if sim > best_sim:
                                best_sim, best_id = sim, cl.id
                        except (ValueError, TypeError):
                            continue
                if best_id and best_sim > 0.25:
                    matches = [(best_id, best_sim)]
                    logger.info(
                        "Backfill domain fallback: '%s' → cluster %s (sim=%.3f, domain=%s)",
                        orphan.intent_label, best_id[:8], best_sim, domain,
                    )
                else:
                    continue

            cluster_id, similarity = matches[0]

            # Link the optimization
            orphan.cluster_id = cluster_id
            db.add(
                OptimizationPattern(
                    optimization_id=orphan.id,
                    cluster_id=cluster_id,
                    relationship="source",
                    similarity=similarity,
                )
            )
            linked_count += 1

        await db.flush()

        logger.info("Backfill complete: %d orphans linked", linked_count)
        return linked_count

    # ------------------------------------------------------------------
    # Temporal decay
    # ------------------------------------------------------------------

    async def decay_usage(self, db: AsyncSession) -> int:
        """Apply temporal decay to cluster usage counts.

        For clusters where last_used_at > 30 days ago:
        - usage_count = max(0, int(usage_count * 0.9))
        - Update last_used_at = now() to prevent re-decay next cycle

        Returns count of decayed clusters.
        """
        from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES

        now = _utcnow()
        decay_cutoff = now - timedelta(days=DECAY_AFTER_DAYS)

        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.last_used_at.isnot(None),
                PromptCluster.last_used_at < decay_cutoff,
                PromptCluster.usage_count > 0,
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
        )
        clusters = list(result.scalars().all())

        decayed_count = 0
        for cluster in clusters:
            old_usage = cluster.usage_count or 0
            new_usage = max(0, int(old_usage * DECAY_FACTOR))
            cluster.usage_count = new_usage
            cluster.last_used_at = now
            decayed_count += 1

        await db.flush()

        if decayed_count > 0:
            logger.info("Decay applied to %d clusters", decayed_count)

        return decayed_count
