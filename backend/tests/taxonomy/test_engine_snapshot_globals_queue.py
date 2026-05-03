"""Cycle 7b RED → GREEN: hot-path engine + snapshot + global_patterns
route through the WriteQueue.

Sites migrated this cycle:

  * ``engine.process_optimization`` — hot-path entry, commit at end.
  * ``engine.rebuild_sub_domains`` — operator recovery; SAVEPOINT stays
    INSIDE the submit callback (single-transaction semantics preserved).
  * ``snapshot.create_snapshot`` — TaxonomySnapshot persist.
  * ``snapshot.prune_snapshots`` — retention DELETE + commit.
  * ``global_patterns.repair_legacy_only_promotions`` — startup repair.

Each function gains an Option C dual-typed ``write_queue`` keyword. Until
cycle 9 wires the lifespan, the legacy ``db: AsyncSession`` form stays so
existing callers don't break in the same PR. Detection is
``write_queue is not None``; mypy narrows the parameter accordingly.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Base, Optimization
from tests.taxonomy.conftest import EMBEDDING_DIM


# ---------------------------------------------------------------------------
# Snapshot — create + prune routes through queue
# ---------------------------------------------------------------------------


class TestSnapshotQueueRouting:
    """Cycle 7b: ``snapshot.create_snapshot`` + ``prune_snapshots`` accept
    ``write_queue=`` and route writes through the worker."""

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_create_snapshot_routes_through_queue(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: passing ``write_queue=`` to ``create_snapshot`` must
        produce one ``submit()`` with ``operation_label='snapshot_persist'``.

        FAILS pre-GREEN with ``TypeError: create_snapshot() got an
        unexpected keyword argument 'write_queue'``.
        """
        from app.services.taxonomy.snapshot import create_snapshot

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        snap = await create_snapshot(  # type: ignore[call-arg]
            None,  # db not needed when write_queue supplied
            trigger="warm_path",
            q_system=0.85,
            q_coherence=0.5,
            q_separation=0.5,
            q_coverage=1.0,
            write_queue=write_queue_inmem,
        )
        assert snap.id is not None
        assert "snapshot_persist" in captured

    @pytest.mark.asyncio
    async def test_prune_snapshots_routes_through_queue(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: ``prune_snapshots`` accepts ``write_queue=`` and labels
        the submit ``snapshot_record`` per spec."""
        from app.services.taxonomy.snapshot import prune_snapshots

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        # Empty DB -> 0 deletes; we just want to confirm the dispatch path.
        deleted = await prune_snapshots(  # type: ignore[call-arg]
            None, write_queue=write_queue_inmem,
        )
        assert isinstance(deleted, int)
        assert "snapshot_record" in captured


# ---------------------------------------------------------------------------
# Global patterns — startup repair routes through queue
# ---------------------------------------------------------------------------


class TestGlobalPatternsQueueRouting:
    """Cycle 7b: ``repair_legacy_only_promotions`` accepts ``write_queue=``."""

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_repair_legacy_only_promotions_routes_through_queue(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: ``repair_legacy_only_promotions(write_queue=q)`` produces
        one submit labelled ``global_pattern_persist``."""
        from app.services.taxonomy.global_patterns import (
            repair_legacy_only_promotions,
        )

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        stats = await repair_legacy_only_promotions(  # type: ignore[call-arg]
            None, write_queue=write_queue_inmem,
        )
        assert isinstance(stats, dict)
        assert "global_pattern_persist" in captured


# ---------------------------------------------------------------------------
# Hot path — process_optimization routes through queue
# ---------------------------------------------------------------------------


class TestHotPathQueueRouting:
    """Cycle 7b: ``engine.process_optimization`` accepts ``write_queue=``
    and routes the final commit through the worker callback."""

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_process_optimization_routes_through_queue(
        self, write_queue_inmem, monkeypatch, mock_embedding, mock_provider,
    ):
        """RED: ``process_optimization(opt_id, write_queue=q)`` produces
        a submit with ``operation_label='hot_path_assign_cluster'``."""
        from app.services.taxonomy.engine import TaxonomyEngine

        # Schema is already created on the writer engine by the
        # ``writer_engine_inmem`` fixture (see conftest), so we don't
        # need an extra ``create_all`` here.
        sf = async_sessionmaker(
            write_queue_inmem._writer_engine,
            class_=AsyncSession, expire_on_commit=False,
        )
        async with sf() as setup_db:
            opt = Optimization(
                raw_prompt="Build a REST API with FastAPI",
                optimized_prompt="Build a REST API with proper error handling",
                status="completed",
                intent_label="REST API",
                domain="backend",
                domain_raw="backend",
            )
            setup_db.add(opt)
            await setup_db.commit()
            opt_id = opt.id

        engine = TaxonomyEngine(
            embedding_service=mock_embedding, provider=mock_provider,
        )

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        # Pre-GREEN: TypeError because process_optimization signature is
        # ``(opt_id, db, repo_full_name=None)``.
        await engine.process_optimization(  # type: ignore[call-arg]
            opt_id, write_queue=write_queue_inmem,
        )

        assert "hot_path_assign_cluster" in captured, (
            f"expected 'hot_path_assign_cluster' label; got {captured!r}"
        )


# ---------------------------------------------------------------------------
# rebuild_sub_domains — SAVEPOINT inside submit
# ---------------------------------------------------------------------------


class TestRebuildSubDomainsQueueRouting:
    """Cycle 7b: ``engine.rebuild_sub_domains`` accepts ``write_queue=``;
    the SAVEPOINT stays INSIDE the submit callback (single-transaction
    semantics preserved)."""

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_rebuild_sub_domains_routes_through_queue(
        self, write_queue_inmem, monkeypatch, mock_embedding, mock_provider,
    ):
        """RED: rebuild_sub_domains under queue dispatch produces one
        submit labelled ``engine_rebuild_sub_domains``."""
        from sqlalchemy import select

        from app.models import PromptCluster
        from app.services.taxonomy.engine import TaxonomyEngine

        sf = async_sessionmaker(
            write_queue_inmem._writer_engine,
            class_=AsyncSession, expire_on_commit=False,
        )
        async with sf() as setup_db:
            domain_node = PromptCluster(
                label="testdomain",
                state="domain",
                domain="testdomain",
                centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
                color_hex="#a855f7",
            )
            setup_db.add(domain_node)
            await setup_db.commit()
            await setup_db.refresh(domain_node)
            domain_id = domain_node.id

        engine = TaxonomyEngine(
            embedding_service=mock_embedding, provider=mock_provider,
        )

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        # dry_run keeps the test cheap — we just need the dispatch path.
        result = await engine.rebuild_sub_domains(  # type: ignore[call-arg]
            None,
            domain_id,
            dry_run=True,
            write_queue=write_queue_inmem,
        )
        assert isinstance(result, dict)
        assert "engine_rebuild_sub_domains" in captured
