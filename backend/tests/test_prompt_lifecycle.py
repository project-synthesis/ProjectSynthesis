"""Tests for PromptLifecycleService — promotion, curation, backfill, decay."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Optimization, OptimizationPattern, PromptCluster, PromptTemplate
from app.services.prompt_lifecycle import PromptLifecycleService

EMBEDDING_DIM = 384


def _utcnow() -> datetime:
    """Naive UTC — matches service and SQLAlchemy DateTime() on SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def svc() -> PromptLifecycleService:
    return PromptLifecycleService()


def _make_cluster(
    state: str = "active",
    member_count: int = 0,
    coherence: float | None = None,
    avg_score: float | None = None,
    usage_count: int = 0,
    prune_flag_count: int = 0,
    last_used_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> PromptCluster:
    """Helper to create a PromptCluster with specified fields."""
    return PromptCluster(
        label="test-cluster",
        state=state,
        member_count=member_count,
        coherence=coherence,
        avg_score=avg_score,
        usage_count=usage_count,
        prune_flag_count=prune_flag_count,
        last_used_at=last_used_at,
        updated_at=updated_at or _utcnow(),
    )


# =========================================================================
# check_promotion tests
# =========================================================================

class TestCheckPromotion:
    """Tests for check_promotion — active->mature and mature->template."""

    async def test_active_to_mature_meets_thresholds(self, db: AsyncSession, svc: PromptLifecycleService):
        """Active cluster meeting all thresholds should promote to mature."""
        cluster = _make_cluster(
            state="active",
            member_count=5,
            coherence=0.75,
            avg_score=7.5,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result == "mature"
        # Verify DB state updated
        await db.refresh(cluster)
        assert cluster.state == "mature"
        assert cluster.promoted_at is not None

    async def test_active_to_mature_below_member_count(self, db: AsyncSession, svc: PromptLifecycleService):
        """Active cluster with too few members should not promote."""
        cluster = _make_cluster(
            state="active",
            member_count=3,
            coherence=0.75,
            avg_score=7.5,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result is None
        await db.refresh(cluster)
        assert cluster.state == "active"

    async def test_active_to_mature_below_coherence(self, db: AsyncSession, svc: PromptLifecycleService):
        """Active cluster with low coherence should not promote."""
        cluster = _make_cluster(
            state="active",
            member_count=10,
            coherence=0.5,
            avg_score=8.0,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result is None

    async def test_active_to_mature_below_avg_score(self, db: AsyncSession, svc: PromptLifecycleService):
        """Active cluster with low avg_score should not promote."""
        cluster = _make_cluster(
            state="active",
            member_count=10,
            coherence=0.8,
            avg_score=5.0,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result is None

    async def test_mature_to_template_meets_thresholds(self, db: AsyncSession, svc: PromptLifecycleService):
        """Mature cluster meeting usage and score should fork a template (not transition state)."""
        import uuid
        cluster = _make_cluster(
            state="mature",
            member_count=10,
            coherence=0.8,
            avg_score=8.0,
            usage_count=5,
        )
        db.add(cluster)
        await db.flush()
        # Seed a top optimization so fork_from_cluster has something to work with
        opt = Optimization(
            id=uuid.uuid4().hex,
            cluster_id=cluster.id,
            raw_prompt="r",
            optimized_prompt="o",
            strategy_used="auto",
            overall_score=8.0,
        )
        db.add(opt)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result == "template_forked"
        await db.refresh(cluster)
        # Cluster stays mature — fork does NOT mutate cluster.state
        assert cluster.state == "mature"

    async def test_mature_to_template_below_usage(self, db: AsyncSession, svc: PromptLifecycleService):
        """Mature cluster with low usage should not promote."""
        cluster = _make_cluster(
            state="mature",
            member_count=10,
            coherence=0.8,
            avg_score=8.0,
            usage_count=1,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result is None

    async def test_mature_to_template_below_score(self, db: AsyncSession, svc: PromptLifecycleService):
        """Mature cluster with low avg_score should not promote to template."""
        cluster = _make_cluster(
            state="mature",
            member_count=10,
            coherence=0.8,
            avg_score=6.0,
            usage_count=10,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result is None

    async def test_template_cluster_no_further_promotion(self, db: AsyncSession, svc: PromptLifecycleService):
        """Template clusters should not promote further."""
        cluster = _make_cluster(
            state="template",
            member_count=20,
            coherence=0.9,
            avg_score=9.0,
            usage_count=50,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.check_promotion(db, cluster.id)

        assert result is None

    async def test_nonexistent_cluster(self, db: AsyncSession, svc: PromptLifecycleService):
        """Nonexistent cluster_id should return None."""
        result = await svc.check_promotion(db, "nonexistent-id")
        assert result is None


# =========================================================================
# curate tests
# =========================================================================

class TestCurate:
    """Tests for curate — stale archival, quality pruning, flag reset."""

    async def test_stale_archival(self, db: AsyncSession, svc: PromptLifecycleService):
        """Clusters inactive for 90+ days with 0 usage should be archived."""
        old_time = _utcnow() - timedelta(days=100)
        cluster = _make_cluster(
            state="active",
            usage_count=0,
            last_used_at=old_time,
            updated_at=old_time,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.curate(db)

        assert cluster.id in result["archived"]
        await db.refresh(cluster)
        assert cluster.state == "archived"
        assert cluster.archived_at is not None

    async def test_stale_but_used_not_archived(self, db: AsyncSession, svc: PromptLifecycleService):
        """Clusters with usage > 0 should NOT be archived even if stale."""
        old_time = _utcnow() - timedelta(days=100)
        cluster = _make_cluster(
            state="active",
            usage_count=3,
            last_used_at=old_time,
            updated_at=old_time,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.curate(db)

        assert cluster.id not in result["archived"]
        await db.refresh(cluster)
        assert cluster.state == "active"

    async def test_quality_pruning_first_flag(self, db: AsyncSession, svc: PromptLifecycleService):
        """Low-quality cluster with enough members should get flagged."""
        cluster = _make_cluster(
            state="active",
            avg_score=3.0,
            member_count=5,
            prune_flag_count=0,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.curate(db)

        assert cluster.id in result["flagged"]
        await db.refresh(cluster)
        assert cluster.prune_flag_count == 1
        assert cluster.state == "active"

    async def test_quality_pruning_second_flag_archives(self, db: AsyncSession, svc: PromptLifecycleService):
        """Low-quality cluster with prune_flag_count=1 should be archived on second flag."""
        cluster = _make_cluster(
            state="active",
            avg_score=3.0,
            member_count=5,
            prune_flag_count=1,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.curate(db)

        assert cluster.id in result["archived"]
        await db.refresh(cluster)
        assert cluster.state == "archived"
        assert cluster.prune_flag_count == 2

    async def test_quality_pruning_insufficient_members(self, db: AsyncSession, svc: PromptLifecycleService):
        """Low-score cluster with < 3 members should not be flagged."""
        cluster = _make_cluster(
            state="active",
            avg_score=2.0,
            member_count=2,
            prune_flag_count=0,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.curate(db)

        assert cluster.id not in result["flagged"]
        assert cluster.id not in result["archived"]

    async def test_flag_reset_good_quality(self, db: AsyncSession, svc: PromptLifecycleService):
        """Cluster with recovered quality should have prune_flag_count reset to 0."""
        cluster = _make_cluster(
            state="active",
            avg_score=7.0,
            member_count=5,
            prune_flag_count=1,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.curate(db)

        assert cluster.id in result["unflagged"]
        await db.refresh(cluster)
        assert cluster.prune_flag_count == 0

    async def test_already_archived_skipped(self, db: AsyncSession, svc: PromptLifecycleService):
        """Archived clusters should be excluded from curation."""
        old_time = _utcnow() - timedelta(days=200)
        cluster = _make_cluster(
            state="archived",
            usage_count=0,
            last_used_at=old_time,
            updated_at=old_time,
        )
        db.add(cluster)
        await db.flush()

        result = await svc.curate(db)

        assert cluster.id not in result["archived"]
        assert cluster.id not in result["flagged"]
        assert cluster.id not in result["unflagged"]


# =========================================================================
# backfill_orphans tests
# =========================================================================

class TestBackfillOrphans:
    """Tests for backfill_orphans — linking unassigned optimizations to clusters."""

    async def test_orphan_linked_to_nearest_cluster(self, db: AsyncSession, svc: PromptLifecycleService):
        """Orphan optimization should be linked to the nearest cluster."""
        # Create a cluster with centroid
        centroid = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        centroid /= np.linalg.norm(centroid)

        cluster = PromptCluster(
            label="test-cluster",
            state="active",
            centroid_embedding=centroid.tobytes(),
        )
        db.add(cluster)
        await db.flush()

        # Create an orphan optimization
        orphan = Optimization(
            raw_prompt="Write a REST API handler",
            status="completed",
            cluster_id=None,
        )
        db.add(orphan)
        await db.flush()

        # Mock embedding index to return the cluster
        mock_index = MagicMock()
        mock_index.search.return_value = [(cluster.id, 0.85)]

        # Mock embedding service
        mock_embed = MagicMock()
        mock_embed.aembed_single = AsyncMock(return_value=centroid)

        count = await svc.backfill_orphans(db, mock_index, embedding_svc=mock_embed)

        assert count == 1
        await db.refresh(orphan)
        assert orphan.cluster_id == cluster.id

        # Verify OptimizationPattern was created
        from sqlalchemy import select

        result = await db.execute(
            select(OptimizationPattern).where(
                OptimizationPattern.optimization_id == orphan.id
            )
        )
        pattern = result.scalar_one()
        assert pattern.cluster_id == cluster.id
        assert pattern.similarity == 0.85
        assert pattern.relationship == "source"

    async def test_orphan_no_match_not_linked(self, db: AsyncSession, svc: PromptLifecycleService):
        """Orphan with no matching cluster should remain unlinked."""
        orphan = Optimization(
            raw_prompt="Some unique prompt",
            status="completed",
            cluster_id=None,
        )
        db.add(orphan)
        await db.flush()

        mock_index = MagicMock()
        mock_index.search.return_value = []  # No matches

        mock_embed = MagicMock()
        mock_embed.aembed_single = AsyncMock(
            return_value=np.random.randn(EMBEDDING_DIM).astype(np.float32)
        )

        count = await svc.backfill_orphans(db, mock_index, embedding_svc=mock_embed)

        assert count == 0
        await db.refresh(orphan)
        assert orphan.cluster_id is None

    async def test_no_orphans_returns_zero(self, db: AsyncSession, svc: PromptLifecycleService):
        """No orphan optimizations should return 0."""
        mock_index = MagicMock()
        count = await svc.backfill_orphans(db, mock_index)
        assert count == 0

    async def test_backfill_with_real_embedding_index(self, db: AsyncSession, svc: PromptLifecycleService):
        """Integration test: orphan linked via real EmbeddingIndex search."""
        from app.services.taxonomy.embedding_index import EmbeddingIndex

        # Create a cluster with centroid embedding
        centroid = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        centroid /= np.linalg.norm(centroid)

        cluster = PromptCluster(
            label="integration-test-cluster",
            state="active",
            centroid_embedding=centroid.tobytes(),
        )
        db.add(cluster)
        await db.flush()

        # Build a real EmbeddingIndex and upsert the cluster
        index = EmbeddingIndex(dim=EMBEDDING_DIM)
        await index.upsert(cluster.id, centroid)

        # Create an orphan optimization
        orphan = Optimization(
            raw_prompt="Test prompt for integration",
            status="completed",
            cluster_id=None,
        )
        db.add(orphan)
        await db.flush()

        # Mock only the embedding service (to return a vector similar to the centroid)
        mock_embed = MagicMock()
        # Return a vector very close to the centroid so it will match
        similar = centroid + np.random.randn(EMBEDDING_DIM).astype(np.float32) * 0.01
        similar /= np.linalg.norm(similar)
        mock_embed.aembed_single = AsyncMock(return_value=similar)

        count = await svc.backfill_orphans(db, index, embedding_svc=mock_embed)

        assert count == 1
        await db.refresh(orphan)
        assert orphan.cluster_id == cluster.id


# =========================================================================
# decay_usage tests
# =========================================================================

class TestDecayUsage:
    """Tests for decay_usage — temporal usage count reduction."""

    async def test_old_cluster_decayed(self, db: AsyncSession, svc: PromptLifecycleService):
        """Cluster last used > 30 days ago should have usage decayed."""
        old_time = _utcnow() - timedelta(days=45)
        cluster = _make_cluster(
            state="active",
            usage_count=100,
            last_used_at=old_time,
        )
        db.add(cluster)
        await db.flush()

        count = await svc.decay_usage(db)

        assert count == 1
        await db.refresh(cluster)
        assert cluster.usage_count == 90  # 100 * 0.9
        # last_used_at should be updated to prevent re-decay
        # SQLite may return naive datetimes, so compare without tz
        last_used = cluster.last_used_at
        refreshed_last_used = last_used.replace(tzinfo=None) if last_used.tzinfo else last_used
        old_naive = old_time.replace(tzinfo=None)
        assert refreshed_last_used > old_naive

    async def test_recent_cluster_not_decayed(self, db: AsyncSession, svc: PromptLifecycleService):
        """Cluster last used recently should not be decayed."""
        recent_time = _utcnow() - timedelta(days=5)
        cluster = _make_cluster(
            state="active",
            usage_count=100,
            last_used_at=recent_time,
        )
        db.add(cluster)
        await db.flush()

        count = await svc.decay_usage(db)

        assert count == 0
        await db.refresh(cluster)
        assert cluster.usage_count == 100

    async def test_zero_usage_not_decayed(self, db: AsyncSession, svc: PromptLifecycleService):
        """Cluster with 0 usage should not be decayed even if old."""
        old_time = _utcnow() - timedelta(days=60)
        cluster = _make_cluster(
            state="active",
            usage_count=0,
            last_used_at=old_time,
        )
        db.add(cluster)
        await db.flush()

        count = await svc.decay_usage(db)

        assert count == 0

    async def test_archived_cluster_not_decayed(self, db: AsyncSession, svc: PromptLifecycleService):
        """Archived clusters should not be decayed."""
        old_time = _utcnow() - timedelta(days=60)
        cluster = _make_cluster(
            state="archived",
            usage_count=50,
            last_used_at=old_time,
        )
        db.add(cluster)
        await db.flush()

        count = await svc.decay_usage(db)

        assert count == 0

    async def test_decay_small_usage_floors_at_zero(self, db: AsyncSession, svc: PromptLifecycleService):
        """Decay of small usage counts should floor at 0."""
        old_time = _utcnow() - timedelta(days=60)
        cluster = _make_cluster(
            state="active",
            usage_count=1,
            last_used_at=old_time,
        )
        db.add(cluster)
        await db.flush()

        count = await svc.decay_usage(db)

        assert count == 1
        await db.refresh(cluster)
        assert cluster.usage_count == 0  # int(1 * 0.9) = 0

    async def test_multiple_clusters_decayed(self, db: AsyncSession, svc: PromptLifecycleService):
        """Multiple eligible clusters should all be decayed."""
        old_time = _utcnow() - timedelta(days=60)
        c1 = _make_cluster(state="active", usage_count=100, last_used_at=old_time)
        c2 = _make_cluster(state="mature", usage_count=50, last_used_at=old_time)
        db.add_all([c1, c2])
        await db.flush()

        count = await svc.decay_usage(db)

        assert count == 2
        await db.refresh(c1)
        await db.refresh(c2)
        assert c1.usage_count == 90
        assert c2.usage_count == 45


# =========================================================================
# strategy affinity tests
# =========================================================================

class TestStrategyAffinity:
    """Tests for update_strategy_affinity."""

    async def test_strategy_affinity_set(self, db: AsyncSession, svc: PromptLifecycleService):
        """Cluster preferred_strategy set after 3+ high-score optimizations with same strategy."""
        cluster = _make_cluster(state="active")
        db.add(cluster)
        await db.flush()

        # Create 3 optimizations with same strategy, high score, linked via OptimizationPattern
        for _ in range(3):
            opt = Optimization(
                raw_prompt="test prompt",
                status="completed",
                strategy_used="chain-of-thought",
                overall_score=8.5,
                cluster_id=cluster.id,
            )
            db.add(opt)
            await db.flush()
            db.add(OptimizationPattern(
                optimization_id=opt.id,
                cluster_id=cluster.id,
                relationship="source",
                similarity=0.9,
            ))
        await db.flush()

        await svc.update_strategy_affinity(db, cluster.id)
        await db.refresh(cluster)
        assert cluster.preferred_strategy == "chain-of-thought"

    async def test_strategy_affinity_not_set_below_count(self, db: AsyncSession, svc: PromptLifecycleService):
        """Preferred strategy NOT set with fewer than 3 matching optimizations."""
        cluster = _make_cluster(state="active")
        db.add(cluster)
        await db.flush()

        # Only 2 optimizations
        for _ in range(2):
            opt = Optimization(
                raw_prompt="test prompt",
                status="completed",
                strategy_used="chain-of-thought",
                overall_score=8.5,
                cluster_id=cluster.id,
            )
            db.add(opt)
            await db.flush()
            db.add(OptimizationPattern(
                optimization_id=opt.id,
                cluster_id=cluster.id,
                relationship="source",
                similarity=0.9,
            ))
        await db.flush()

        await svc.update_strategy_affinity(db, cluster.id)
        await db.refresh(cluster)
        assert cluster.preferred_strategy is None

    async def test_strategy_affinity_not_set_low_scores(self, db: AsyncSession, svc: PromptLifecycleService):
        """Preferred strategy NOT set when scores are below 7.0."""
        cluster = _make_cluster(state="active")
        db.add(cluster)
        await db.flush()

        for _ in range(3):
            opt = Optimization(
                raw_prompt="test prompt",
                status="completed",
                strategy_used="chain-of-thought",
                overall_score=5.0,
                cluster_id=cluster.id,
            )
            db.add(opt)
            await db.flush()
            db.add(OptimizationPattern(
                optimization_id=opt.id,
                cluster_id=cluster.id,
                relationship="source",
                similarity=0.9,
            ))
        await db.flush()

        await svc.update_strategy_affinity(db, cluster.id)
        await db.refresh(cluster)
        assert cluster.preferred_strategy is None

    async def test_strategy_affinity_picks_highest_avg(self, db: AsyncSession, svc: PromptLifecycleService):
        """When multiple strategies qualify, picks the one with highest avg score."""
        cluster = _make_cluster(state="active")
        db.add(cluster)
        await db.flush()

        # 3 chain-of-thought at 7.5 avg
        for _ in range(3):
            opt = Optimization(
                raw_prompt="test prompt",
                status="completed",
                strategy_used="chain-of-thought",
                overall_score=7.5,
                cluster_id=cluster.id,
            )
            db.add(opt)
            await db.flush()
            db.add(OptimizationPattern(
                optimization_id=opt.id,
                cluster_id=cluster.id,
                relationship="source",
                similarity=0.9,
            ))

        # 3 meta-prompting at 9.0 avg (higher)
        for _ in range(3):
            opt = Optimization(
                raw_prompt="test prompt",
                status="completed",
                strategy_used="meta-prompting",
                overall_score=9.0,
                cluster_id=cluster.id,
            )
            db.add(opt)
            await db.flush()
            db.add(OptimizationPattern(
                optimization_id=opt.id,
                cluster_id=cluster.id,
                relationship="source",
                similarity=0.9,
            ))
        await db.flush()

        await svc.update_strategy_affinity(db, cluster.id)
        await db.refresh(cluster)
        assert cluster.preferred_strategy == "meta-prompting"


# =========================================================================
# Task 10 RED-phase tests — fork instead of state transition
# =========================================================================

@pytest.mark.asyncio
async def test_mature_cluster_meeting_thresholds_forks_instead_of_transitioning(db: AsyncSession, svc: PromptLifecycleService):
    """Mature cluster meeting thresholds must FORK (create PromptTemplate row),
    NOT mutate cluster.state to 'template'. Returns 'template_forked'."""
    from sqlalchemy import select
    import uuid

    cluster = PromptCluster(
        id="c_mature", label="tpl", state="mature",
        member_count=5, coherence=0.8, avg_score=7.6, usage_count=3,
    )
    db.add(cluster)
    opt = Optimization(
        id=uuid.uuid4().hex, cluster_id="c_mature",
        raw_prompt="r", optimized_prompt="o",
        strategy_used="auto", overall_score=7.6,
    )
    db.add(opt)
    await db.flush()

    result = await svc.check_promotion(db, "c_mature")

    # Cluster state must NOT change to 'template'
    reloaded = (await db.execute(
        select(PromptCluster).where(PromptCluster.id == "c_mature")
    )).scalar_one()
    assert reloaded.state == "mature"
    assert reloaded.template_count == 1

    # A PromptTemplate row must exist
    tpl_rows = (await db.execute(
        select(PromptTemplate).where(PromptTemplate.source_cluster_id == "c_mature")
    )).scalars().all()
    assert len(tpl_rows) == 1
    assert result == "template_forked"


@pytest.mark.asyncio
async def test_re_fork_suppressed_until_new_top_optimization(db: AsyncSession, svc: PromptLifecycleService):
    """Calling check_promotion twice on the same mature cluster must only produce
    one PromptTemplate row (idempotent fork guard)."""
    from sqlalchemy import select
    import uuid

    cluster = PromptCluster(
        id="c_mature2", label="tpl", state="mature",
        member_count=5, coherence=0.8, avg_score=7.6, usage_count=3,
    )
    db.add(cluster)
    db.add(Optimization(
        id=uuid.uuid4().hex, cluster_id="c_mature2",
        raw_prompt="r", optimized_prompt="o",
        strategy_used="auto", overall_score=7.6,
    ))
    await db.flush()

    await svc.check_promotion(db, "c_mature2")
    await svc.check_promotion(db, "c_mature2")  # second call: no new fork

    reloaded = (await db.execute(
        select(PromptCluster).where(PromptCluster.id == "c_mature2")
    )).scalar_one()
    assert reloaded.template_count == 1
