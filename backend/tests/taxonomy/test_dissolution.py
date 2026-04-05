"""Tests for cluster dissolution — small incoherent clusters dissolved and members reassigned."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select

from app.models import Optimization, PromptCluster
from app.services.taxonomy.event_logger import TaxonomyEventLogger, set_event_logger

EMBEDDING_DIM = 384


def _make_embedding(seed: int = 0) -> bytes:
    rng = np.random.RandomState(seed)
    v = rng.randn(EMBEDDING_DIM).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


@pytest.fixture(autouse=True)
def setup_event_logger(tmp_path):
    logger = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
    set_event_logger(logger)
    yield logger


class TestDissolution:
    @pytest.mark.asyncio
    async def test_incoherent_small_cluster_dissolved(
        self, session_factory, mock_embedding, mock_provider,
    ) -> None:
        """A small cluster with coherence below DISSOLVE_COHERENCE_CEILING is dissolved."""
        from app.services.taxonomy.engine import TaxonomyEngine

        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        async with session_factory() as db:
            # Create an active target for reassignment
            target = PromptCluster(
                label="Good Target",
                state="active",
                domain="test",
                centroid_embedding=_make_embedding(1),
                member_count=10,
                coherence=0.8,
                created_at=datetime.now(timezone.utc) - timedelta(hours=24),
            )
            db.add(target)

            # Create a small incoherent cluster (qualifies for dissolution)
            bad_cluster = PromptCluster(
                label="Bad Incoherent",
                state="active",
                domain="test",
                centroid_embedding=_make_embedding(2),
                member_count=2,
                coherence=0.15,  # well below DISSOLVE_COHERENCE_CEILING (0.30)
                created_at=datetime.now(timezone.utc) - timedelta(hours=6),
            )
            db.add(bad_cluster)
            await db.flush()

            # Add 2 optimizations to the bad cluster
            for i in range(2):
                opt = Optimization(
                    raw_prompt=f"bad prompt {i}",
                    cluster_id=bad_cluster.id,
                    embedding=_make_embedding(100 + i),
                )
                db.add(opt)
            await db.flush()

            # Run retire phase (which now includes dissolution)
            from app.services.taxonomy.warm_phases import phase_retire

            result = await phase_retire(engine, db)
            await db.flush()

            # The bad cluster should be archived
            await db.refresh(bad_cluster)
            assert bad_cluster.state == "archived"
            assert bad_cluster.member_count == 0

            # Members should be reassigned (not orphaned)
            opts_q = await db.execute(
                select(Optimization.cluster_id).where(
                    Optimization.raw_prompt.like("bad prompt%")
                )
            )
            for (cid,) in opts_q.all():
                assert cid != bad_cluster.id, "Optimization still points to dissolved cluster"
                assert cid is not None, "Optimization has null cluster_id after dissolution"

            assert result.ops_accepted >= 1

    @pytest.mark.asyncio
    async def test_coherent_cluster_not_dissolved(
        self, session_factory, mock_embedding, mock_provider,
    ) -> None:
        """A cluster with coherence above the ceiling is NOT dissolved."""
        from app.services.taxonomy.engine import TaxonomyEngine

        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        async with session_factory() as db:
            good_cluster = PromptCluster(
                label="Good Small",
                state="active",
                domain="test",
                centroid_embedding=_make_embedding(3),
                member_count=3,
                coherence=0.60,  # above DISSOLVE_COHERENCE_CEILING
                created_at=datetime.now(timezone.utc) - timedelta(hours=6),
            )
            db.add(good_cluster)
            await db.flush()

            for i in range(3):
                opt = Optimization(
                    raw_prompt=f"good prompt {i}",
                    cluster_id=good_cluster.id,
                    embedding=_make_embedding(200 + i),
                )
                db.add(opt)
            await db.flush()

            from app.services.taxonomy.warm_phases import phase_retire

            await phase_retire(engine, db)
            await db.flush()

            await db.refresh(good_cluster)
            assert good_cluster.state == "active"

    @pytest.mark.asyncio
    async def test_new_cluster_not_dissolved(
        self, session_factory, mock_embedding, mock_provider,
    ) -> None:
        """A recently created incoherent cluster is NOT dissolved (age guard)."""
        from app.services.taxonomy.engine import TaxonomyEngine

        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        async with session_factory() as db:
            # Create target
            target = PromptCluster(
                label="Target",
                state="active",
                domain="test",
                centroid_embedding=_make_embedding(4),
                member_count=10,
                created_at=datetime.now(timezone.utc) - timedelta(hours=24),
            )
            db.add(target)

            # New incoherent cluster (created 30 minutes ago — below DISSOLVE_MIN_AGE_HOURS)
            new_bad = PromptCluster(
                label="New But Bad",
                state="active",
                domain="test",
                centroid_embedding=_make_embedding(5),
                member_count=2,
                coherence=0.10,
                created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            )
            db.add(new_bad)
            await db.flush()

            for i in range(2):
                opt = Optimization(
                    raw_prompt=f"new bad {i}",
                    cluster_id=new_bad.id,
                    embedding=_make_embedding(300 + i),
                )
                db.add(opt)
            await db.flush()

            from app.services.taxonomy.warm_phases import phase_retire

            await phase_retire(engine, db)
            await db.flush()

            await db.refresh(new_bad)
            assert new_bad.state == "active", "New cluster should not be dissolved yet"

    @pytest.mark.asyncio
    async def test_large_cluster_not_dissolved(
        self, session_factory, mock_embedding, mock_provider,
    ) -> None:
        """A cluster above DISSOLVE_MAX_MEMBERS is NOT dissolved (too large)."""
        from app.services.taxonomy.engine import TaxonomyEngine

        engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

        async with session_factory() as db:
            large_bad = PromptCluster(
                label="Large But Incoherent",
                state="active",
                domain="test",
                centroid_embedding=_make_embedding(6),
                member_count=10,  # above DISSOLVE_MAX_MEMBERS (5)
                coherence=0.20,
                created_at=datetime.now(timezone.utc) - timedelta(hours=6),
            )
            db.add(large_bad)
            await db.flush()

            from app.services.taxonomy.warm_phases import phase_retire

            await phase_retire(engine, db)
            await db.flush()

            await db.refresh(large_bad)
            assert large_bad.state == "active", "Large cluster should not be dissolved"
