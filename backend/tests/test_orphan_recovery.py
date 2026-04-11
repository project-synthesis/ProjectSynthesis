"""Tests for OrphanRecoveryService — orphan detection, recovery, and retry budget."""

import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization
from app.services.orphan_recovery import OrphanRecoveryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 384


def _fake_embedding(seed: float = 1.0) -> np.ndarray:
    """Return a deterministic 384-dim float32 vector with distinct direction per seed."""
    rng = np.random.RandomState(int(seed * 1000))
    vec = rng.randn(_DIM).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def _make_orphan(
    *,
    age_minutes: int = 10,
    heuristic_flags: dict | None = None,
    has_embedding: bool = False,
) -> Optimization:
    """Create an Optimization that looks like an orphan."""
    created = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=age_minutes)
    opt = Optimization(
        id=str(uuid.uuid4()),
        created_at=created,
        raw_prompt="Write a function to sort a list",
        optimized_prompt="Write an efficient function to sort a list using quicksort",
        overall_score=7.5,
        status="completed",
        task_type="coding",
        domain="backend",
        intent_label="sort list function",
        heuristic_flags=heuristic_flags,
    )
    if has_embedding:
        opt.embedding = _fake_embedding(2.0).tobytes()
    return opt


def _mock_engine() -> MagicMock:
    """Create a mock TaxonomyEngine with embedding service.

    Returns different embeddings for each call so the transformation
    vector (optimized - raw) is non-zero.
    """
    engine = MagicMock()
    engine._embedding = AsyncMock()
    # Return different embeddings on successive calls (raw, optimized)
    engine._embedding.aembed_single = AsyncMock(
        side_effect=[_fake_embedding(1.0), _fake_embedding(2.0)]
    )
    engine._embedding_index = MagicMock()
    engine.mark_dirty = MagicMock()
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScanOrphans:
    async def test_scan_finds_orphans_older_than_threshold(self, db_session: AsyncSession) -> None:
        """Orphan >5min old with no embedding should be found."""
        svc = OrphanRecoveryService()
        opt = _make_orphan(age_minutes=10)
        db_session.add(opt)
        await db_session.commit()

        orphans = await svc._scan_orphans(db_session)
        assert len(orphans) == 1
        assert orphans[0].id == opt.id

    async def test_scan_skips_recent_optimizations(self, db_session: AsyncSession) -> None:
        """Optimization created < 5min ago should NOT be found."""
        svc = OrphanRecoveryService()
        opt = _make_orphan(age_minutes=1)
        db_session.add(opt)
        await db_session.commit()

        orphans = await svc._scan_orphans(db_session)
        assert len(orphans) == 0

    async def test_scan_skips_recovery_exhausted(self, db_session: AsyncSession) -> None:
        """Optimization flagged recovery_exhausted should be post-filtered."""
        svc = OrphanRecoveryService()
        opt = _make_orphan(
            age_minutes=10,
            heuristic_flags={"_recovery": {"exhausted": True}},
        )
        db_session.add(opt)
        await db_session.commit()

        orphans = await svc._scan_orphans(db_session)
        assert len(orphans) == 0


class TestRecoverOne:
    async def test_recover_single_orphan_computes_embeddings(self, db_session: AsyncSession) -> None:
        """Recovery should compute all 3 embeddings (raw, optimized, transformation)."""
        svc = OrphanRecoveryService()
        opt = _make_orphan(age_minutes=10)
        db_session.add(opt)
        await db_session.commit()

        engine = _mock_engine()

        # Mock assign_cluster to return a fake cluster
        mock_cluster = MagicMock()
        mock_cluster.id = "cluster-1"
        mock_cluster.state = "active"

        with patch(
            "app.services.orphan_recovery.assign_cluster",
            new_callable=AsyncMock,
            return_value=mock_cluster,
        ):
            success = await svc._recover_one(opt.id, db_session, engine)

        assert success is True

        # Re-read to verify
        from sqlalchemy import select
        result = await db_session.execute(
            select(Optimization).where(Optimization.id == opt.id)
        )
        refreshed = result.scalar_one()
        assert refreshed.embedding is not None
        assert refreshed.optimized_embedding is not None
        assert refreshed.transformation_embedding is not None

        # Verify dimensions
        raw_arr = np.frombuffer(refreshed.embedding, dtype=np.float32)
        assert raw_arr.shape == (_DIM,)

    async def test_recover_skips_already_embedded(self, db_session: AsyncSession) -> None:
        """If embedding is already set, recovery should return False."""
        svc = OrphanRecoveryService()
        opt = _make_orphan(age_minutes=10, has_embedding=True)
        db_session.add(opt)
        await db_session.commit()

        engine = _mock_engine()
        success = await svc._recover_one(opt.id, db_session, engine)
        assert success is False


class TestScanAndRecover:
    async def test_scan_and_recover_processes_orphans(self, db_session: AsyncSession) -> None:
        """Full cycle: scan finds orphan, recover processes it."""
        svc = OrphanRecoveryService()
        opt = _make_orphan(age_minutes=10)
        db_session.add(opt)
        await db_session.commit()

        engine = _mock_engine()

        mock_cluster = MagicMock()
        mock_cluster.id = "cluster-1"
        mock_cluster.state = "active"

        # session_factory must return an async context manager
        @contextlib.asynccontextmanager
        async def session_factory():
            yield db_session

        with (
            patch(
                "app.services.orphan_recovery.assign_cluster",
                new_callable=AsyncMock,
                return_value=mock_cluster,
            ),
            patch(
                "app.services.orphan_recovery.get_event_logger",
                return_value=MagicMock(log_decision=MagicMock()),
            ),
        ):
            stats = await svc.scan_and_recover(session_factory, engine)

        assert stats["scanned"] == 1
        assert stats["recovered"] == 1
        assert stats["failed"] == 0
        assert stats["recovered_total"] == 1


class TestRetryBudget:
    async def test_retry_budget_exhausted_flags_optimization(self, db_session: AsyncSession) -> None:
        """When recovery_attempts >= 3, optimization should be flagged exhausted."""
        svc = OrphanRecoveryService()
        opt = _make_orphan(
            age_minutes=10,
            heuristic_flags={"_recovery": {"attempts": 3}},
        )
        db_session.add(opt)
        await db_session.commit()

        engine = _mock_engine()
        # Make embedding raise to trigger failure path
        engine._embedding.aembed_single = AsyncMock(side_effect=RuntimeError("model error"))

        # _recover_one should detect budget exhaustion and return False
        success = await svc._recover_one(opt.id, db_session, engine)
        assert success is False

        # Verify the flag was set
        from sqlalchemy import select
        result = await db_session.execute(
            select(Optimization).where(Optimization.id == opt.id)
        )
        refreshed = result.scalar_one()
        assert isinstance(refreshed.heuristic_flags, dict)
        rec = refreshed.heuristic_flags.get("_recovery", {})
        assert rec.get("exhausted") is True
