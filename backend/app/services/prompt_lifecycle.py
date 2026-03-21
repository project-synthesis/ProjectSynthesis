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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, OptimizationPattern, PromptCluster

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Promotion thresholds
# ---------------------------------------------------------------------------
ACTIVE_TO_MATURE_MEMBER_COUNT = 5
ACTIVE_TO_MATURE_COHERENCE = 0.7
ACTIVE_TO_MATURE_AVG_SCORE = 7.0

MATURE_TO_TEMPLATE_USAGE_COUNT = 3
MATURE_TO_TEMPLATE_AVG_SCORE = 7.5

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
BACKFILL_THRESHOLD = 0.72


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PromptLifecycleService:
    """Auto-curation, state promotion, and temporal decay for PromptClusters."""

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    async def check_promotion(self, db: AsyncSession, cluster_id: str) -> str | None:
        """Check if cluster should be promoted to a higher state.

        Promotion rules:
        - active -> mature: member_count >= 5, coherence >= 0.7, avg_score >= 7.0
        - mature -> template: usage_count >= 3, avg_score >= 7.5

        Returns new state string if promoted, None otherwise.
        Sets promoted_at timestamp on promotion.
        """
        result = await db.execute(
            select(PromptCluster).where(PromptCluster.id == cluster_id)
        )
        cluster = result.scalar_one_or_none()
        if cluster is None:
            return None

        new_state: str | None = None

        if cluster.state == "active":
            if (
                (cluster.member_count or 0) >= ACTIVE_TO_MATURE_MEMBER_COUNT
                and (cluster.coherence or 0) >= ACTIVE_TO_MATURE_COHERENCE
                and (cluster.avg_score or 0) >= ACTIVE_TO_MATURE_AVG_SCORE
            ):
                new_state = "mature"

        elif cluster.state == "mature":
            if (
                (cluster.usage_count or 0) >= MATURE_TO_TEMPLATE_USAGE_COUNT
                and (cluster.avg_score or 0) >= MATURE_TO_TEMPLATE_AVG_SCORE
            ):
                new_state = "template"

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
    # Curation
    # ------------------------------------------------------------------

    async def curate(self, db: AsyncSession) -> dict:
        """Run curation checks on all clusters.

        Curation checks:
        - Stale detection: clusters with no activity for 90+ days
          (last_used_at or updated_at) and usage_count=0 -> archived
        - Quality pruning: avg_score < 4.0 AND member_count >= 3 ->
          increment prune_flag_count. If prune_flag_count >= 2 -> archived
        - Reset prune_flag_count to 0 for clusters above quality threshold

        Returns dict with summary:
            {"archived": [cluster_ids], "flagged": [cluster_ids], "unflagged": [cluster_ids]}
        """
        now = _utcnow()
        stale_cutoff = now - timedelta(days=STALE_DAYS)

        # Fetch all non-archived clusters
        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state.notin_(["archived"])
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
                cluster.state = "archived"
                cluster.archived_at = now
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
                    cluster.state = "archived"
                    cluster.archived_at = now
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

        result = await db.execute(
            select(Optimization).where(
                Optimization.cluster_id.is_(None),
                Optimization.raw_prompt.isnot(None),
                Optimization.status == "completed",
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

            # Search for nearest cluster
            matches = embedding_index.search(
                embedding, k=1, threshold=BACKFILL_THRESHOLD
            )
            if not matches:
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
        now = _utcnow()
        decay_cutoff = now - timedelta(days=DECAY_AFTER_DAYS)

        result = await db.execute(
            select(PromptCluster).where(
                PromptCluster.last_used_at.isnot(None),
                PromptCluster.last_used_at < decay_cutoff,
                PromptCluster.usage_count > 0,
                PromptCluster.state.notin_(["archived"]),
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
