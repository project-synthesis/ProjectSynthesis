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

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Optimization
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


# ---------------------------------------------------------------------------
# Cycle 7 OPERATE: live concurrency stress on migrated cycle 7 surfaces
# ---------------------------------------------------------------------------


class TestCycle7Operate:
    """OPERATE phase (v0.4.13 cycle 7): dynamic concurrency stress on the
    five migrated cycle-7 production surfaces under realistic file-mode
    WAL semantics.

    Per ``feedback_tdd_protocol.md`` Phase 5: the GREEN tests above pin
    the dispatch CONTRACT (operation_label captured, body returns a value).
    These OPERATE tests pin the dispatch BEHAVIOUR under N=5 concurrent
    callers against a real on-disk SQLite engine where ``database is locked``
    can manifest if the queue's serialization broke.

    Anti-patterns covered:
      - O1: every test SELECTs the user-visible end state, never trusts
        return value alone (e.g. row IDs verified via direct query).
      - O2: file-mode WAL writer-slot contention — only on-disk SQLite
        surfaces ``database is locked`` records under concurrent writers.
      - O5: test #6 covers concurrent dependency resolution on a shared
        ``app.state`` to verify singleton semantics under request fan-out.

    Wall-clock budget: each stress test < 5s, total < 30s.
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    # ------------------------------------------------------------------
    # Test #1: hot-path persistence under N=5 concurrent callers
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_hot_path_optimization_persistence_n5_concurrent_via_queue(
        self, writer_engine_file, mock_embedding, mock_provider, caplog,
    ):
        """N=5 concurrent ``engine.process_optimization()`` calls under
        file-mode WAL contention must serialize through the write queue
        with zero ``database is locked`` records.

        The test pre-creates 5 ``Optimization`` rows on the file-mode writer
        engine, then drives 5 hot-path calls concurrently via the queue.
        Per spec § 3.6 the queue's single-writer worker serializes the
        ``hot_path_assign_cluster`` callbacks; under WAL semantics ``database
        is locked`` would surface in the SQLAlchemy logs if serialization
        broke.

        Assertions:
          * All 5 calls complete (return None, no exception).
          * Cluster assignments stick — every Optimization row has
            ``cluster_id IS NOT NULL`` after the run (verified via direct
            SELECT against the file-mode engine, NOT the queue session).
          * Zero ``database is locked`` records in caplog at WARNING+.
          * Wall-clock < 5s.
        """
        import asyncio as _asyncio
        import logging as _logging
        import time as _time
        import uuid as _uuid

        from sqlalchemy import select as _select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models import Base, Optimization
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.write_queue import WriteQueue

        # Pre-create 5 Optimization rows on the file-mode engine — schema
        # already materialized by writer_engine_file fixture? No — file-mode
        # is bare. Materialize first.
        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sf = async_sessionmaker(
            writer_engine_file, class_=AsyncSession, expire_on_commit=False,
        )
        opt_ids: list[str] = []
        async with sf() as setup_db:
            for i in range(5):
                opt = Optimization(
                    id=str(_uuid.uuid4()),
                    raw_prompt=f"Build a REST API endpoint #{i}",
                    optimized_prompt=f"Build a robust REST API endpoint #{i}",
                    status="completed",
                    intent_label="REST API",
                    domain="backend",
                    domain_raw="backend",
                )
                setup_db.add(opt)
                opt_ids.append(opt.id)
            await setup_db.commit()

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()
        try:
            engine = TaxonomyEngine(
                embedding_service=mock_embedding, provider=mock_provider,
            )

            t0 = _time.monotonic()
            with caplog.at_level(_logging.WARNING):
                await _asyncio.gather(
                    *[
                        engine.process_optimization(
                            oid, write_queue=queue,
                        )
                        for oid in opt_ids
                    ],
                )
            elapsed = _time.monotonic() - t0

            # O1: SELECT through a fresh session to verify the writes landed.
            async with sf() as verify_db:
                result = await verify_db.execute(
                    _select(Optimization).where(Optimization.id.in_(opt_ids))
                )
                rows = list(result.scalars().all())
                assigned = [r for r in rows if r.cluster_id is not None]
                assert len(assigned) == 5, (
                    f"expected 5 cluster assignments, got {len(assigned)}; "
                    f"cluster_ids={[r.cluster_id for r in rows]}"
                )

            # O2: zero "database is locked" records under file-mode WAL.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            assert elapsed < 5.0, (
                f"hot-path stress took {elapsed:.2f}s, > 5s budget"
            )
        finally:
            await queue.stop(drain_timeout=5.0)

    # ------------------------------------------------------------------
    # Test #2: rebuild_sub_domains atomicity under concurrent load
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_engine_rebuild_sub_domains_savepoint_atomicity_under_concurrent_load(
        self, writer_engine_file, mock_embedding, mock_provider, caplog,
    ):
        """``rebuild_sub_domains`` runs inside a single ``submit()`` callback
        with SAVEPOINT-protected partial-failure rollback. Concurrent writes
        on the same queue must not poison the rebuild's transaction.

        Per spec § 7c: the SAVEPOINT lives INSIDE the submit callback so
        atomicity is preserved (either all proposed sub-domains created or
        none — partial failures roll back). This test fires
        ``rebuild_sub_domains`` (dry_run=True keeps the test cheap and side-
        effect free) concurrently with N=4 unrelated snapshot persists on
        the same queue. Asserts:

          * rebuild call returns a dict with ``dry_run=True``.
          * 4 snapshots committed via direct SELECT.
          * Zero ``database is locked`` records.
          * Wall-clock < 5s.
        """
        import asyncio as _asyncio
        import logging as _logging
        import time as _time

        from sqlalchemy import select as _select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models import Base, PromptCluster, TaxonomySnapshot
        from app.services.taxonomy.engine import TaxonomyEngine
        from app.services.taxonomy.snapshot import create_snapshot
        from app.services.write_queue import WriteQueue

        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sf = async_sessionmaker(
            writer_engine_file, class_=AsyncSession, expire_on_commit=False,
        )

        # Pre-stage a domain node with vocabulary so the rebuild has a
        # legitimate target. We don't need real generated_qualifiers for
        # dry_run — the function still returns a non-empty result dict.
        async with sf() as setup_db:
            domain_node = PromptCluster(
                label="opdomain",
                state="domain",
                domain="opdomain",
                centroid_embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(),
                color_hex="#a855f7",
                cluster_metadata={
                    "generated_qualifiers": {
                        "v1": ["security", "auth", "tokens"],
                    },
                },
            )
            setup_db.add(domain_node)
            await setup_db.commit()
            await setup_db.refresh(domain_node)
            domain_id = domain_node.id

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()
        try:
            engine = TaxonomyEngine(
                embedding_service=mock_embedding, provider=mock_provider,
            )

            async def _do_snapshot(label: str) -> str:
                snap = await create_snapshot(
                    None,
                    trigger=label,
                    q_system=0.7,
                    q_coherence=0.5,
                    q_separation=0.5,
                    q_coverage=0.9,
                    write_queue=queue,
                )
                return snap.id

            t0 = _time.monotonic()
            with caplog.at_level(_logging.WARNING):
                rebuild_task = _asyncio.create_task(
                    engine.rebuild_sub_domains(
                        None, domain_id, dry_run=True, write_queue=queue,
                    )
                )
                snapshot_tasks = [
                    _asyncio.create_task(_do_snapshot(f"concurrent_{i}"))
                    for i in range(4)
                ]
                rebuild_result, *snapshot_ids = await _asyncio.gather(
                    rebuild_task, *snapshot_tasks,
                )
            elapsed = _time.monotonic() - t0

            assert isinstance(rebuild_result, dict)
            assert rebuild_result.get("dry_run") is True

            # O1: SELECT to verify the 4 concurrent snapshots actually landed.
            async with sf() as verify_db:
                result = await verify_db.execute(
                    _select(TaxonomySnapshot).where(
                        TaxonomySnapshot.id.in_(snapshot_ids),
                    )
                )
                rows = list(result.scalars().all())
                assert len(rows) == 4, (
                    f"expected 4 concurrent snapshots persisted, got {len(rows)}"
                )

            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records under "
                f"rebuild + concurrent snapshot stress"
            )

            assert elapsed < 5.0, (
                f"rebuild stress took {elapsed:.2f}s, > 5s budget"
            )
        finally:
            await queue.stop(drain_timeout=5.0)

    # ------------------------------------------------------------------
    # Test #3: snapshot writer N=5 concurrent
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_snapshot_writer_n5_concurrent_via_queue(
        self, writer_engine_file, caplog,
    ):
        """N=5 concurrent ``create_snapshot`` calls produce 5 distinct
        ``TaxonomySnapshot`` rows under file-mode WAL contention.

        Per spec § 7b: the snapshot persist is wrapped in a submit()
        callback labelled ``snapshot_persist`` and the queue's worker
        serializes against every other writer.

        Assertions:
          * 5 distinct ``TaxonomySnapshot.id`` values returned.
          * Direct SELECT confirms 5 rows persisted.
          * Zero ``database is locked`` records.
          * Wall-clock < 5s.
        """
        import asyncio as _asyncio
        import logging as _logging
        import time as _time

        from sqlalchemy import select as _select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models import Base, TaxonomySnapshot
        from app.services.taxonomy.snapshot import create_snapshot
        from app.services.write_queue import WriteQueue

        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()
        try:
            t0 = _time.monotonic()
            with caplog.at_level(_logging.WARNING):
                snaps = await _asyncio.gather(
                    *[
                        create_snapshot(
                            None,
                            trigger=f"warm_path_{i}",
                            q_system=0.5 + i * 0.05,
                            q_coherence=0.5,
                            q_separation=0.5,
                            q_coverage=0.9,
                            write_queue=queue,
                        )
                        for i in range(5)
                    ],
                )
            elapsed = _time.monotonic() - t0

            # All 5 distinct ids returned.
            ids = [s.id for s in snaps]
            assert len(ids) == 5
            assert len(set(ids)) == 5, f"snapshot ids not unique: {ids}"

            # O1: direct SELECT to verify durable state.
            sf = async_sessionmaker(
                writer_engine_file, class_=AsyncSession, expire_on_commit=False,
            )
            async with sf() as verify_db:
                result = await verify_db.execute(
                    _select(TaxonomySnapshot).where(
                        TaxonomySnapshot.id.in_(ids),
                    )
                )
                rows = list(result.scalars().all())
                assert len(rows) == 5

            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records "
                f"under N=5 concurrent snapshot stress"
            )

            assert elapsed < 5.0, (
                f"snapshot stress took {elapsed:.2f}s, > 5s budget"
            )
        finally:
            await queue.stop(drain_timeout=5.0)

    # ------------------------------------------------------------------
    # Test #4: global pattern lifecycle via queue
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_global_pattern_lifecycle_via_queue(
        self, writer_engine_file, caplog,
    ):
        """``repair_legacy_only_promotions`` routes through the queue and
        produces the expected state transitions.

        Pre-stages two ``GlobalPattern`` rows that violate the tightened
        ``MIN_PROJECTS=2`` gate (each has a single source project). The
        repair must demote the active row and retire the demoted row.

        Per spec § 7b: the audit runs inside a single ``submit()`` callback
        labelled ``global_pattern_persist`` so the SELECT + UPDATE +
        commit sequence is atomic against backend writers.

        Assertions:
          * Repair returns ``{"demoted": 1, "retired": 1}``.
          * Direct SELECT confirms states are ``"demoted"`` and ``"retired"``.
          * Zero ``database is locked`` records.
        """
        import logging as _logging
        import uuid as _uuid

        from sqlalchemy import select as _select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models import Base, GlobalPattern
        from app.services.taxonomy.global_patterns import (
            repair_legacy_only_promotions,
        )
        from app.services.write_queue import WriteQueue

        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sf = async_sessionmaker(
            writer_engine_file, class_=AsyncSession, expire_on_commit=False,
        )

        # Pre-stage two patterns: one active (will be demoted), one demoted
        # (will be retired). Both have a single source project, violating
        # the MIN_PROJECTS=2 gate.
        active_id = str(_uuid.uuid4())
        demoted_id = str(_uuid.uuid4())
        async with sf() as setup_db:
            zero_emb = np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes()
            setup_db.add(
                GlobalPattern(
                    id=active_id,
                    pattern_text="legacy active single-project",
                    embedding=zero_emb,
                    source_cluster_ids=[str(_uuid.uuid4())],
                    source_project_ids=["only_one_project"],
                    cross_project_count=1,
                    avg_cluster_score=7.0,
                    state="active",
                )
            )
            setup_db.add(
                GlobalPattern(
                    id=demoted_id,
                    pattern_text="legacy demoted single-project",
                    embedding=zero_emb,
                    source_cluster_ids=[str(_uuid.uuid4())],
                    source_project_ids=["only_one_project"],
                    cross_project_count=1,
                    avg_cluster_score=4.0,
                    state="demoted",
                )
            )
            await setup_db.commit()

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()
        try:
            with caplog.at_level(_logging.WARNING):
                stats = await repair_legacy_only_promotions(
                    None, write_queue=queue,
                )

            assert stats == {"demoted": 1, "retired": 1}, (
                f"unexpected repair stats: {stats}"
            )

            # O1: SELECT against fresh session — verify state transitions
            # actually committed.
            async with sf() as verify_db:
                result = await verify_db.execute(
                    _select(GlobalPattern).where(
                        GlobalPattern.id.in_([active_id, demoted_id]),
                    )
                )
                rows = {r.id: r for r in result.scalars().all()}
                assert rows[active_id].state == "demoted"
                assert rows[demoted_id].state == "retired"

            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records"
            )
        finally:
            await queue.stop(drain_timeout=5.0)

    # ------------------------------------------------------------------
    # Test #6: get_write_queue dependency under concurrent requests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_write_queue_dependency_under_concurrent_requests(
        self, write_queue_inmem,
    ):
        """N=5 concurrent FastAPI request contexts call ``get_write_queue``
        and all receive the SAME singleton instance.

        Verifies the dependency is a pure ``app.state`` lookup with no race
        on object identity. A buggy implementation that constructed a new
        ``WriteQueue`` per request would surface here as multiple distinct
        instances.

        Pin spec § 7a: ``get_write_queue`` is a no-allocation singleton
        accessor — every caller observes the SAME ``WriteQueue`` instance.
        """
        import asyncio as _asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from app.dependencies.write_queue import get_write_queue

        # Build a single shared app.state holding the queue singleton, then
        # 5 distinct Request objects pointing at the same app.state.
        app_state = SimpleNamespace(write_queue=write_queue_inmem)
        shared_app = SimpleNamespace(state=app_state)

        def _make_request() -> MagicMock:
            req = MagicMock()
            req.app = shared_app
            return req

        async def _resolve(req) -> object:
            # Simulate the FastAPI request scope — get_write_queue is sync,
            # but exercising it from N concurrent tasks proves any internal
            # mutation would surface as a race.
            return get_write_queue(req)

        results = await _asyncio.gather(
            *[_resolve(_make_request()) for _ in range(5)],
        )

        # All 5 results MUST be identical (object identity).
        assert all(r is write_queue_inmem for r in results), (
            "get_write_queue returned non-singleton instances under "
            f"concurrent resolution; ids={[id(r) for r in results]}"
        )

        # Sanity: the unset case must raise — guard against accidental
        # silent degradation.
        empty_state = SimpleNamespace(write_queue=None)
        empty_app = SimpleNamespace(state=empty_state)
        empty_req = MagicMock()
        empty_req.app = empty_app
        with pytest.raises(RuntimeError, match="not initialized"):
            get_write_queue(empty_req)
