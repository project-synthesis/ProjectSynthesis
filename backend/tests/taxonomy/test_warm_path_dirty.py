"""Tests for warm path dirty-set integration."""

from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_warm_path_passes_dirty_set_to_phases():
    """Warm path should snapshot dirty set and pass to phase functions."""
    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    engine.mark_dirty("cluster-1")
    engine.mark_dirty("cluster-2")

    # Snapshot should return the dirty IDs
    snapshot = engine.snapshot_dirty_set()
    assert snapshot == {"cluster-1", "cluster-2"}
    assert len(engine._dirty_set) == 0

    # After snapshot, marking new IDs starts a fresh set
    engine.mark_dirty("cluster-3")
    assert set(engine._dirty_set.keys()) == {"cluster-3"}


@pytest.mark.asyncio
async def test_first_cycle_returns_none_dirty_set():
    """First warm cycle (age=0) should signal full-scan via None dirty set."""
    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    assert engine.is_first_warm_cycle()
    # On first cycle, caller should treat dirty_set as None (= process all)


@pytest.mark.asyncio
async def test_maintenance_pending_flag_lifecycle():
    """Engine._maintenance_pending starts False, can be set and cleared."""
    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Starts False
    assert engine._maintenance_pending is False

    # Can be set
    engine._maintenance_pending = True
    assert engine._maintenance_pending is True

    # Can be cleared
    engine._maintenance_pending = False
    assert engine._maintenance_pending is False


@pytest.mark.asyncio
async def test_idle_cycle_runs_maintenance_on_cadence(db):
    """When no dirty clusters exist, maintenance runs every MAINTENANCE_CYCLE_INTERVAL cycles."""
    from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL
    from app.services.taxonomy.engine import TaxonomyEngine
    from app.services.taxonomy.warm_path import execute_warm_path

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Simulate age at the cadence boundary
    engine._warm_path_age = MAINTENANCE_CYCLE_INTERVAL

    maintenance_called = []

    async def fake_maintenance(
        eng, sf=None, phase_results=None, q_baseline=None, **_kwargs,
    ):
        maintenance_called.append(True)
        from app.services.taxonomy.warm_path import WarmPathResult
        return WarmPathResult(
            snapshot_id="maint-idle",
            q_baseline=None, q_final=0.5,
            phase_results=[], operations_attempted=0,
            operations_accepted=0, deadlock_breaker_used=False,
            deadlock_breaker_phase=None,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.warm_path.execute_maintenance_phases",
        fake_maintenance,
    ):
        result = await execute_warm_path(engine, session_factory)

    assert len(maintenance_called) == 1
    assert result.snapshot_id == "maint-idle"


@pytest.mark.asyncio
async def test_idle_cycle_skips_maintenance_off_cadence(db):
    """When no dirty clusters and not on cadence, maintenance is skipped."""
    from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL
    from app.services.taxonomy.engine import TaxonomyEngine
    from app.services.taxonomy.warm_path import execute_warm_path

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Off-cadence age (not a multiple of interval)
    engine._warm_path_age = MAINTENANCE_CYCLE_INTERVAL + 1

    maintenance_called = []

    async def fake_maintenance(
        eng, sf=None, phase_results=None, q_baseline=None, **_kwargs,
    ):
        maintenance_called.append(True)
        from app.services.taxonomy.warm_path import WarmPathResult
        return WarmPathResult(
            snapshot_id="should-not-run",
            q_baseline=None, q_final=None,
            phase_results=[], operations_attempted=0,
            operations_accepted=0, deadlock_breaker_used=False,
            deadlock_breaker_phase=None,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.warm_path.execute_maintenance_phases",
        fake_maintenance,
    ):
        result = await execute_warm_path(engine, session_factory)

    assert len(maintenance_called) == 0
    assert result.snapshot_id == "skipped"


@pytest.mark.asyncio
async def test_idle_cycle_runs_maintenance_on_retry(db):
    """When _maintenance_pending is True, idle cycle runs maintenance regardless of cadence."""
    from app.services.taxonomy._constants import MAINTENANCE_CYCLE_INTERVAL
    from app.services.taxonomy.engine import TaxonomyEngine
    from app.services.taxonomy.warm_path import execute_warm_path

    mock_embedding = MagicMock()
    mock_provider = MagicMock()
    engine = TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)

    # Off-cadence, but pending retry
    engine._warm_path_age = MAINTENANCE_CYCLE_INTERVAL + 1
    engine._maintenance_pending = True

    maintenance_called = []

    async def fake_maintenance(
        eng, sf=None, phase_results=None, q_baseline=None, **_kwargs,
    ):
        maintenance_called.append(True)
        # Simulate successful discovery clearing the flag
        eng._maintenance_pending = False
        from app.services.taxonomy.warm_path import WarmPathResult
        return WarmPathResult(
            snapshot_id="maint-retry",
            q_baseline=None, q_final=0.5,
            phase_results=[], operations_attempted=0,
            operations_accepted=0, deadlock_breaker_used=False,
            deadlock_breaker_phase=None,
        )

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.warm_path.execute_maintenance_phases",
        fake_maintenance,
    ):
        result = await execute_warm_path(engine, session_factory)

    assert len(maintenance_called) == 1
    assert result.snapshot_id == "maint-retry"
