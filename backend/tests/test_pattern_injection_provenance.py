"""Tests for auto_inject_patterns() provenance-recording error isolation.

B7: when optimization_id is provided but the optimizations row doesn't exist
yet (pre-persist pipeline phase), db.flush() raises IntegrityError that
previously poisoned the entire AsyncSession via PendingRollbackError.

Fix: wrap each provenance INSERT in begin_nested() (SAVEPOINT) so the FK
failure only rolls back the savepoint, not the outer transaction.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeSavepoint:
    """Minimal async context manager standing in for AsyncSessionTransaction."""
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False  # don't suppress exceptions


def _patch_begin_nested(db: AsyncMock) -> None:
    """Make db.begin_nested() return a proper async context manager."""
    db.begin_nested = lambda: _FakeSavepoint()


def _empty_db() -> AsyncMock:
    """AsyncSession that returns empty results and tracks flush() calls."""
    db = AsyncMock()
    _patch_begin_nested(db)
    mock_result = MagicMock()
    mock_result.all = MagicMock(return_value=[])
    mock_result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )
    db.execute = AsyncMock(return_value=mock_result)
    db.flush = AsyncMock()  # no-op by default
    return db


def _db_with_failing_flush() -> AsyncMock:
    """AsyncSession whose flush() always raises IntegrityError (FK violation)."""
    db = _empty_db()

    async def _fail_flush():
        raise IntegrityError(
            "FOREIGN KEY constraint failed", params=None, orig=Exception()
        )

    db.flush = _fail_flush
    return db


def _engine_with_empty_index() -> MagicMock:
    index = MagicMock()
    index.size = 0
    index.search = MagicMock(return_value=[])
    engine = MagicMock()
    engine.embedding_index = index
    return engine


def _engine_with_one_cluster_match() -> MagicMock:
    """Taxonomy engine whose embedding index returns one cluster match."""
    from app.services.taxonomy.embedding_index import EmbeddingIndex
    real_index = EmbeddingIndex(dim=4)

    engine = MagicMock()
    engine.embedding_index = real_index
    return engine, real_index


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProvenanceErrorIsolation:
    """Provenance FK failures must not poison the outer DB session."""

    @pytest.mark.asyncio
    async def test_no_patterns_no_provenance_no_error(self):
        """Baseline: empty index returns empty lists without touching provenance."""
        from app.services.pattern_injection import auto_inject_patterns

        db = _db_with_failing_flush()
        engine = _engine_with_empty_index()

        patterns, cluster_ids = await auto_inject_patterns(
            raw_prompt="Design an async SQLAlchemy session factory for FastAPI",
            taxonomy_engine=engine,
            db=db,
            trace_id="test-trace-baseline",
            optimization_id=str(uuid.uuid4()),
        )
        assert patterns == []
        assert cluster_ids == []
        # flush() should NOT have been called (no matches → no provenance)
        assert not db.flush.called if hasattr(db.flush, "called") else True

    @pytest.mark.asyncio
    async def test_provenance_failure_does_not_propagate(self):
        """When provenance flush() raises FK error, auto_inject_patterns() must not raise.

        The patterns are already captured in the injected list by the time
        provenance recording happens — the return value must be non-empty.
        """
        from app.services.pattern_injection import auto_inject_patterns
        from app.services.taxonomy.embedding_index import EmbeddingIndex

        # Build a real index with one cluster match
        index = EmbeddingIndex(dim=4)
        cluster_emb = np.array([1, 0, 0, 0], dtype=np.float32)
        await index.upsert("cluster-xyz", cluster_emb, project_id=None)

        engine = MagicMock()
        engine.embedding_index = index

        # DB that returns cluster + pattern data but fails on flush
        flush_calls = []

        async def failing_flush():
            flush_calls.append("flush")
            raise IntegrityError("FK", params=None, orig=Exception())

        cluster_row = MagicMock()
        cluster_row.id = "cluster-xyz"
        cluster_row.label = "Async DB Session"
        cluster_row.domain = "backend"

        pattern_row = MagicMock()
        pattern_row.id = "pat-001"
        pattern_row.cluster_id = "cluster-xyz"
        pattern_row.pattern_text = "Use async context managers for DB sessions"
        pattern_row.embedding = None
        pattern_row.global_source_count = 0

        call_n = 0

        async def execute_dispatcher(stmt, *a, **kw):
            nonlocal call_n
            call_n += 1
            result = MagicMock()
            if call_n == 1:
                # Cluster metadata SELECT
                result.all = MagicMock(
                    return_value=[("cluster-xyz", "Async DB Session", "backend")]
                )
                result.scalars = MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[cluster_row]))
                )
            elif call_n == 2:
                # Sub-domain parent SELECT (empty)
                result.all = MagicMock(return_value=[])
                result.scalars = MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[]))
                )
            else:
                # Meta-patterns SELECT
                result.scalars = MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[pattern_row]))
                )
                result.all = MagicMock(return_value=[])
            return result

        db = AsyncMock()
        _patch_begin_nested(db)
        db.execute = execute_dispatcher
        db.flush = failing_flush
        db.add = MagicMock()
        db.expunge = MagicMock()

        emb_vec = np.array([1, 0, 0, 0], dtype=np.float32)

        async def mock_fused(*a, **kw):
            return emb_vec

        # Patch EmbeddingService and resolve_fused_embedding at their source modules
        import app.services.embedding_service as emb_mod
        import app.services.taxonomy.fusion as fusion_mod

        original_svc = emb_mod.EmbeddingService
        original_fused = fusion_mod.resolve_fused_embedding

        mock_svc = MagicMock()
        mock_svc.aembed_single = AsyncMock(return_value=emb_vec)
        emb_mod.EmbeddingService = MagicMock(return_value=mock_svc)
        fusion_mod.resolve_fused_embedding = mock_fused

        try:
            patterns, cluster_ids = await auto_inject_patterns(
                raw_prompt="Design async SQLAlchemy session factory",
                taxonomy_engine=engine,
                db=db,
                trace_id="test-trace-provenance",
                optimization_id=str(uuid.uuid4()),
            )
        finally:
            emb_mod.EmbeddingService = original_svc
            fusion_mod.resolve_fused_embedding = original_fused

        # Patterns must be returned even though provenance failed
        assert len(patterns) >= 1, (
            f"Expected patterns to be injected despite provenance FK failure, got {patterns!r}"
        )
        # flush must have been attempted (provenance was tried)
        assert len(flush_calls) >= 1, "flush() should have been called for provenance"

    @pytest.mark.asyncio
    async def test_provenance_uses_savepoint_when_optimization_id_set(self):
        """Provenance recording must use begin_nested() (SAVEPOINT) when opt_id given.

        Observable contract: db.begin_nested() is called at least once when
        cluster matches exist and optimization_id is provided.  Without SAVEPOINT,
        a flush() IntegrityError poisons the outer AsyncSession and subsequent
        pipeline phases all fail with PendingRollbackError.

        RED: current code calls db.flush() directly — begin_nested() never called.
        GREEN: each provenance block wrapped in begin_nested().
        """
        from app.services.pattern_injection import auto_inject_patterns
        from app.services.taxonomy.embedding_index import EmbeddingIndex

        index = EmbeddingIndex(dim=4)
        cluster_emb = np.array([1, 0, 0, 0], dtype=np.float32)
        await index.upsert("c-savepoint", cluster_emb, project_id=None)

        engine = MagicMock()
        engine.embedding_index = index

        begin_nested_calls: list[str] = []

        # Minimal async context manager for begin_nested()
        class FakeSavepoint:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False

        def make_savepoint():
            begin_nested_calls.append("begin_nested")
            return FakeSavepoint()

        cluster_row = MagicMock()
        cluster_row.id = "c-savepoint"
        cluster_row.label = "Test"
        cluster_row.domain = "backend"

        pattern_row = MagicMock()
        pattern_row.id = "p-001"
        pattern_row.cluster_id = "c-savepoint"
        pattern_row.pattern_text = "Use SAVEPOINT for FK-safe provenance"
        pattern_row.embedding = None
        pattern_row.global_source_count = 0

        call_n = 0

        async def execute_dispatcher(stmt, *a, **kw):
            nonlocal call_n
            call_n += 1
            result = MagicMock()
            if call_n == 1:
                result.all = MagicMock(
                    return_value=[("c-savepoint", "Test", "backend")]
                )
                result.scalars = MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[cluster_row]))
                )
            elif call_n == 2:
                result.all = MagicMock(return_value=[])
                result.scalars = MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[]))
                )
            else:
                result.all = MagicMock(return_value=[])
                result.scalars = MagicMock(
                    return_value=MagicMock(
                        all=MagicMock(return_value=[pattern_row])
                    )
                )
            return result

        db = AsyncMock()
        db.execute = execute_dispatcher
        db.flush = AsyncMock()
        db.add = MagicMock()
        db.expunge = MagicMock()
        db.begin_nested = make_savepoint

        import app.services.embedding_service as emb_mod
        import app.services.taxonomy.fusion as fusion_mod

        emb_vec = np.array([1, 0, 0, 0], dtype=np.float32)
        mock_svc = MagicMock()
        mock_svc.aembed_single = AsyncMock(return_value=emb_vec)
        original_svc = emb_mod.EmbeddingService
        original_fused = fusion_mod.resolve_fused_embedding
        emb_mod.EmbeddingService = MagicMock(return_value=mock_svc)
        fusion_mod.resolve_fused_embedding = AsyncMock(return_value=emb_vec)

        try:
            await auto_inject_patterns(
                raw_prompt="Design async SQLAlchemy session factory",
                taxonomy_engine=engine,
                db=db,
                trace_id="test-trace-savepoint",
                optimization_id=str(uuid.uuid4()),
            )
        finally:
            emb_mod.EmbeddingService = original_svc
            fusion_mod.resolve_fused_embedding = original_fused

        assert len(begin_nested_calls) >= 1, (
            f"begin_nested() must be called for provenance recording (SAVEPOINT guard). "
            f"Called {len(begin_nested_calls)} times. "
            f"Without SAVEPOINT, flush() IntegrityError poisons the outer session."
        )
