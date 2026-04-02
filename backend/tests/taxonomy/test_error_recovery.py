"""Tests for error-recovery paths in engine.py.

Verifies that run_warm_path and run_cold_path construct valid result
dataclasses with all required fields when the underlying implementation
raises an exception.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from app.services.taxonomy.cold_path import ColdPathResult
from app.services.taxonomy.warm_path import WarmPathResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(mock_embedding, mock_provider):
    from app.services.taxonomy.engine import TaxonomyEngine

    return TaxonomyEngine(embedding_service=mock_embedding, provider=mock_provider)


# ---------------------------------------------------------------------------
# run_warm_path error recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_warm_path_error_recovery_returns_warm_path_result(
    db, mock_embedding, mock_provider
):
    """run_warm_path returns a WarmPathResult even when execute_warm_path raises."""
    engine = _make_engine(mock_embedding, mock_provider)

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.engine.execute_warm_path",
        side_effect=RuntimeError("simulated warm path failure"),
    ):
        result = await engine.run_warm_path(session_factory)

    assert result is not None
    assert isinstance(result, WarmPathResult)


@pytest.mark.asyncio
async def test_run_warm_path_error_recovery_has_snapshot_id(
    db, mock_embedding, mock_provider
):
    """Error-recovery WarmPathResult must have a non-empty snapshot_id."""
    engine = _make_engine(mock_embedding, mock_provider)

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.engine.execute_warm_path",
        side_effect=RuntimeError("failure"),
    ):
        result = await engine.run_warm_path(session_factory)

    assert result is not None
    assert result.snapshot_id  # non-empty string


@pytest.mark.asyncio
async def test_run_warm_path_error_recovery_new_fields_valid(
    db, mock_embedding, mock_provider
):
    """Error-recovery WarmPathResult populates new fields with valid defaults.

    Fields added in restructuring: q_baseline, q_final, phase_results,
    operations_attempted, operations_accepted, deadlock_breaker_phase.
    """
    engine = _make_engine(mock_embedding, mock_provider)

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.engine.execute_warm_path",
        side_effect=RuntimeError("failure"),
    ):
        result = await engine.run_warm_path(session_factory)

    assert result is not None
    # q_baseline is None on error
    assert result.q_baseline is None
    # q_final is 0.0 on error
    assert result.q_final == 0.0
    # phase_results is an empty list
    assert result.phase_results == []
    # operation counts are 0
    assert result.operations_attempted == 0
    assert result.operations_accepted == 0
    # deadlock_breaker_used is False
    assert result.deadlock_breaker_used is False
    # deadlock_breaker_phase is None
    assert result.deadlock_breaker_phase is None


@pytest.mark.asyncio
async def test_run_warm_path_error_recovery_q_system_auto_populated(
    db, mock_embedding, mock_provider
):
    """Error-recovery WarmPathResult: q_system populated from q_final."""
    engine = _make_engine(mock_embedding, mock_provider)

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.engine.execute_warm_path",
        side_effect=RuntimeError("failure"),
    ):
        result = await engine.run_warm_path(session_factory)

    assert result is not None
    # q_system should equal q_final (0.0) via __post_init__
    assert result.q_system == result.q_final


@pytest.mark.asyncio
async def test_run_warm_path_error_recovery_releases_lock(
    db, mock_embedding, mock_provider
):
    """Warm path lock must be released even when an error occurs."""
    engine = _make_engine(mock_embedding, mock_provider)

    @asynccontextmanager
    async def session_factory():
        yield db

    with patch(
        "app.services.taxonomy.engine.execute_warm_path",
        side_effect=RuntimeError("lock test failure"),
    ):
        await engine.run_warm_path(session_factory)

    assert not engine._warm_path_lock.locked()


@pytest.mark.asyncio
async def test_run_warm_path_error_recovery_snapshot_also_fails(
    db, mock_embedding, mock_provider
):
    """Error recovery snapshot also failing must still return a valid WarmPathResult."""
    engine = _make_engine(mock_embedding, mock_provider)

    @asynccontextmanager
    async def session_factory():
        yield db

    with (
        patch(
            "app.services.taxonomy.engine.execute_warm_path",
            side_effect=RuntimeError("primary failure"),
        ),
        patch.object(
            engine,
            "_create_warm_snapshot",
            side_effect=RuntimeError("snapshot failure"),
        ),
    ):
        result = await engine.run_warm_path(session_factory)

    assert result is not None
    assert result.snapshot_id == "error-no-snapshot"


# ---------------------------------------------------------------------------
# run_cold_path error recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cold_path_error_recovery_returns_cold_path_result(
    db, mock_embedding, mock_provider
):
    """run_cold_path returns a ColdPathResult even when execute_cold_path raises."""
    engine = _make_engine(mock_embedding, mock_provider)

    with patch(
        "app.services.taxonomy.engine.execute_cold_path",
        side_effect=RuntimeError("simulated cold path failure"),
    ):
        result = await engine.run_cold_path(db)

    assert result is not None
    assert isinstance(result, ColdPathResult)


@pytest.mark.asyncio
async def test_run_cold_path_error_recovery_new_fields_valid(
    db, mock_embedding, mock_provider
):
    """Error-recovery ColdPathResult has valid new field values.

    New fields (q_before, q_after, accepted) introduced in cold_path.py.
    """
    engine = _make_engine(mock_embedding, mock_provider)

    with patch(
        "app.services.taxonomy.engine.execute_cold_path",
        side_effect=RuntimeError("failure"),
    ):
        result = await engine.run_cold_path(db)

    assert result is not None
    assert result.q_before is None
    assert result.q_after == 0.0
    assert result.accepted is False
    assert result.nodes_created == 0
    assert result.nodes_updated == 0
    assert result.umap_fitted is False


@pytest.mark.asyncio
async def test_run_cold_path_error_recovery_q_system_auto_populated(
    db, mock_embedding, mock_provider
):
    """Error-recovery ColdPathResult: q_system populated from q_after via __post_init__."""
    engine = _make_engine(mock_embedding, mock_provider)

    with patch(
        "app.services.taxonomy.engine.execute_cold_path",
        side_effect=RuntimeError("failure"),
    ):
        result = await engine.run_cold_path(db)

    assert result is not None
    # q_system should be populated from q_after (0.0)
    assert result.q_system == result.q_after


@pytest.mark.asyncio
async def test_run_cold_path_error_recovery_has_snapshot_id(
    db, mock_embedding, mock_provider
):
    """Error-recovery ColdPathResult must have a non-empty snapshot_id."""
    engine = _make_engine(mock_embedding, mock_provider)

    with patch(
        "app.services.taxonomy.engine.execute_cold_path",
        side_effect=RuntimeError("failure"),
    ):
        result = await engine.run_cold_path(db)

    assert result is not None
    assert result.snapshot_id


@pytest.mark.asyncio
async def test_run_cold_path_error_recovery_releases_lock(
    db, mock_embedding, mock_provider
):
    """Warm path lock must be released even when cold path fails."""
    engine = _make_engine(mock_embedding, mock_provider)

    with patch(
        "app.services.taxonomy.engine.execute_cold_path",
        side_effect=RuntimeError("lock test"),
    ):
        await engine.run_cold_path(db)

    assert not engine._warm_path_lock.locked()


@pytest.mark.asyncio
async def test_run_cold_path_error_recovery_snapshot_also_fails(
    db, mock_embedding, mock_provider
):
    """Snapshot failure during cold path error recovery returns safe ColdPathResult.

    engine.py imports create_snapshot locally inside the except block, so we
    patch it at the snapshot module level.
    """
    engine = _make_engine(mock_embedding, mock_provider)

    with (
        patch(
            "app.services.taxonomy.engine.execute_cold_path",
            side_effect=RuntimeError("primary failure"),
        ),
        patch(
            "app.services.taxonomy.snapshot.create_snapshot",
            side_effect=RuntimeError("snapshot failure"),
        ),
    ):
        result = await engine.run_cold_path(db)

    assert result is not None
    assert result.snapshot_id == "error-no-snapshot"
    assert result.accepted is False


# ---------------------------------------------------------------------------
# Regression: error-recovery dataclasses satisfy their own __post_init__
# ---------------------------------------------------------------------------


def test_warm_path_result_post_init_on_error_values():
    """WarmPathResult error-recovery values satisfy __post_init__ invariant."""
    # Replicate the exact values engine.py sets on error recovery
    result = WarmPathResult(
        snapshot_id="error-no-snapshot",
        q_baseline=None,
        q_final=0.0,
        phase_results=[],
        operations_attempted=0,
        operations_accepted=0,
        deadlock_breaker_used=False,
        deadlock_breaker_phase=None,
    )
    # __post_init__ should set q_system = q_final = 0.0
    assert result.q_system == 0.0


def test_cold_path_result_post_init_on_error_values():
    """ColdPathResult error-recovery values satisfy __post_init__ invariant."""
    # Replicate the exact values engine.py sets on error recovery
    result = ColdPathResult(
        snapshot_id="error-no-snapshot",
        q_before=None,
        q_after=0.0,
        accepted=False,
        nodes_created=0,
        nodes_updated=0,
        umap_fitted=False,
    )
    # __post_init__ should set q_system = q_after = 0.0
    assert result.q_system == 0.0
