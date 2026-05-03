"""Tests for ``batch_persistence.bulk_persist`` write-queue routing.

v0.4.13 cycle 2 RED phase: pin the new
``bulk_persist(results, write_queue, batch_id)`` signature that GREEN will
implement. Under the v0.4.12 ``session_factory`` signature the test fails
with ``TypeError`` — confirming the migration target before any production
code changes.

v0.4.13 cycle 2 OPERATE phase: the ``TestBulkPersistOperate`` class verifies
the migrated ``bulk_persist`` actually delivers on the queue's promises under
realistic concurrent load. Per ``feedback_tdd_protocol.md`` Phase 5:

* O1 (User-visible end-state never queried) — every test SELECTs the rows
  it expects, never trusts the return value alone.
* O2 (SQLite writer contention) — tests #1 and #2 prove the queue
  eliminates ``database is locked`` under concurrent file-mode WAL.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid as _uuid

import pytest
from sqlalchemy import text

from tests._write_queue_helpers import (
    _make_failing_pending,
    _make_passing_pending,
    create_prestaged_cluster,
)


class TestBulkPersistViaWriteQueue:
    @pytest.mark.asyncio
    async def test_bulk_persist_routes_through_write_queue(
        self, write_queue_inmem, monkeypatch, db_session,
    ):
        """RED: bulk_persist must call write_queue.submit with operation_label='bulk_persist'.

        Currently FAILS because ``bulk_persist`` still has its v0.4.12
        ``session_factory`` signature. GREEN will swap the signature so
        the queue receives the work.
        """
        from app.services import batch_persistence

        captured: list[str] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label or "")
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        pending = [_make_passing_pending(batch_id="rt-test")]
        # Migration target: bulk_persist now takes write_queue, not session_factory
        inserted = await batch_persistence.bulk_persist(
            pending, write_queue_inmem, batch_id="rt-test",
        )
        assert "bulk_persist" in captured
        assert inserted == 1


# ---------------------------------------------------------------------------
# OPERATE — concurrency + idempotency + provenance + event ordering
# ---------------------------------------------------------------------------


async def _create_schema_on_engine(engine) -> None:
    """Materialize ``Base.metadata`` on a writer engine. Used by tests that
    consume ``writer_engine_file`` directly (file-mode WAL — no implicit
    schema, unlike ``writer_engine_inmem``)."""
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


class TestBulkPersistOperate:
    """OPERATE phase: dynamic concurrency + idempotency + provenance under
    realistic load.

    Tests #1 + #2 use ``writer_engine_file`` so real WAL contention applies
    (the failure mode the queue exists to eliminate — ``database is
    locked`` — only manifests against on-disk SQLite). Tests #3-5 use
    in-memory shared cache (logic-only, no WAL contention required).
    """

    # -- Test #1: N=5 concurrent callers, real WAL contention ----------------

    @pytest.mark.asyncio
    async def test_bulk_persist_n5_concurrent_callers_serialize_via_queue(
        self, writer_engine_file, caplog,
    ):
        """N=5 concurrent ``bulk_persist`` callers, each persisting 3 rows.

        The queue's serialization is the only defense against SQLite writer
        contention — without it, file-mode WAL with concurrent writers
        surfaces 'database is locked' in SQLAlchemy's ERROR-level logs.

        Asserts:
        - All 15 rows land in the DB (3 per caller × 5 callers).
        - Zero 'database is locked' log records anywhere in the run.
        - Each caller's batch fired its own ``optimization_created`` events.
        """
        from app.services import batch_persistence
        from app.services.event_bus import event_bus
        from app.services.write_queue import WriteQueue

        await _create_schema_on_engine(writer_engine_file)

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()

        # Subscribe to event_bus before any persists fire.
        ev_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        event_bus._subscribers.add(ev_queue)

        try:
            # 5 separate batches (different batch_id), 3 pendings each.
            batches = [
                [_make_passing_pending(batch_id=f"op-{i}") for _ in range(3)]
                for i in range(5)
            ]

            t0 = time.monotonic()
            with caplog.at_level(logging.WARNING):
                results = await asyncio.gather(*[
                    batch_persistence.bulk_persist(
                        batches[i], queue, batch_id=f"op-{i}",
                    )
                    for i in range(5)
                ])
            elapsed = time.monotonic() - t0

            # Each call returned 3 inserted rows, total 15.
            assert results == [3, 3, 3, 3, 3], (
                f"expected each call to insert 3 rows, got {results}"
            )

            # O1: SELECT to verify user-visible state. Use a fresh session
            # via the engine to read back what landed.
            async with writer_engine_file.connect() as conn:
                count_result = await conn.execute(text(
                    "SELECT COUNT(*) FROM optimizations "
                    "WHERE json_extract(context_sources, '$.batch_id') LIKE 'op-%'"
                ))
                row = count_result.first()
                total_rows = int(row[0]) if row else 0
            assert total_rows == 15, (
                f"expected 15 rows from op-* batches, got {total_rows}"
            )

            # O2: zero 'database is locked' anywhere in captured log records.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            # All 15 ``optimization_created`` events fired (one per pending,
            # batched after each call's submit returns).
            events: list[dict] = []
            while True:
                try:
                    events.append(ev_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            created_events = [
                e for e in events if e.get("event") == "optimization_created"
            ]
            assert len(created_events) == 15, (
                f"expected 15 optimization_created events, got {len(created_events)}"
            )
            # Each batch_id appears exactly 3 times in event payloads.
            batch_ids_in_events = [
                e["data"].get("batch_id") for e in created_events
            ]
            for i in range(5):
                assert batch_ids_in_events.count(f"op-{i}") == 3, (
                    f"expected 3 events for op-{i}, "
                    f"got {batch_ids_in_events.count(f'op-{i}')}"
                )

            # Wall-clock budget: 15 rows under N=5 concurrency should
            # comfortably finish in under 30s.
            assert elapsed < 30.0, (
                f"stress run took {elapsed:.1f}s, > 30s budget"
            )
        finally:
            event_bus._subscribers.discard(ev_queue)
            await queue.stop(drain_timeout=5.0)

    # -- Test #2: idempotency under concurrent retry --------------------------

    @pytest.mark.asyncio
    async def test_bulk_persist_idempotent_under_concurrent_retry(
        self, writer_engine_file, caplog,
    ):
        """Two concurrent ``bulk_persist`` calls with the SAME batch_id and
        SAME pending IDs must collapse to a single insert per row.

        The idempotency check inside ``_do_persist`` reads existing rows for
        the batch_id and skips already-persisted IDs. Because the queue
        serializes the two callbacks, the second call sees the first's
        committed rows and inserts 0.

        Asserts:
        - Total inserted across both callers == N (not 2*N).
        - Exactly N rows present in DB for the batch.
        - The second call observed at least one already-persisted row.
        """
        from app.services import batch_persistence
        from app.services.write_queue import WriteQueue

        await _create_schema_on_engine(writer_engine_file)

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()

        try:
            # Pre-build pendings with PINNED ids so both calls submit the
            # exact same primary keys — the idempotency check operates
            # on (batch_id, optimization.id).
            n_rows = 4
            pinned_ids = [str(_uuid.uuid4()) for _ in range(n_rows)]
            batch_a = [
                _make_passing_pending(batch_id="dup-test", opt_id=pid)
                for pid in pinned_ids
            ]
            batch_b = [
                _make_passing_pending(batch_id="dup-test", opt_id=pid)
                for pid in pinned_ids
            ]

            with caplog.at_level(logging.WARNING):
                inserted_a, inserted_b = await asyncio.gather(
                    batch_persistence.bulk_persist(
                        batch_a, queue, batch_id="dup-test",
                    ),
                    batch_persistence.bulk_persist(
                        batch_b, queue, batch_id="dup-test",
                    ),
                )

            # Total writes must equal N (not 2*N) — one of the two callers
            # got there first, the other found the rows already present
            # and skipped them.
            assert inserted_a + inserted_b == n_rows, (
                f"expected total inserts == {n_rows}, "
                f"got A={inserted_a} B={inserted_b}"
            )
            # And the SECOND call (loser of the race) inserted 0 — full
            # idempotency, not a partial overlap.
            assert {inserted_a, inserted_b} == {n_rows, 0}, (
                f"expected one call to insert {n_rows} and the other 0, "
                f"got A={inserted_a} B={inserted_b}"
            )

            # O1: only N rows actually exist in the DB for this batch_id.
            async with writer_engine_file.connect() as conn:
                count_result = await conn.execute(text(
                    "SELECT COUNT(*) FROM optimizations "
                    "WHERE json_extract(context_sources, '$.batch_id') = 'dup-test'"
                ))
                row = count_result.first()
                actual_rows = int(row[0]) if row else 0
            assert actual_rows == n_rows, (
                f"expected exactly {n_rows} rows persisted, got {actual_rows}"
            )

            # No 'database is locked' surfaced even though both callers
            # raced on the same pinned IDs.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records "
                f"under concurrent same-ID submission"
            )
        finally:
            await queue.stop(drain_timeout=5.0)

    # -- Test #3: quality gate under load -------------------------------------

    @pytest.mark.asyncio
    async def test_bulk_persist_quality_gate_under_load(
        self, write_queue_inmem, writer_engine_inmem, caplog,
    ):
        """The score-< 5.0 quality gate must reject the right rows under
        concurrent submission. 3 callers × (5 pass + 5 fail) = 30 input
        rows, 15 expected DB rows, 15 quality_rejected log records.

        Asserts:
        - Each ``bulk_persist`` call returned 5 (passing rows only).
        - Exactly 15 rows landed in the DB (5 × 3 callers).
        - 15 quality-rejected log records emitted (5 × 3 callers).
        - No failed pendings ended up in the DB.
        """
        from app.services import batch_persistence

        # Build 3 batches, each with 5 passing + 5 failing pendings.
        batches = []
        for i in range(3):
            mix = []
            for _ in range(5):
                mix.append(_make_passing_pending(batch_id=f"qgate-{i}"))
            for _ in range(5):
                mix.append(_make_failing_pending(batch_id=f"qgate-{i}", overall_score=3.0))
            batches.append(mix)

        with caplog.at_level(logging.INFO):
            results = await asyncio.gather(*[
                batch_persistence.bulk_persist(
                    batches[i], write_queue_inmem, batch_id=f"qgate-{i}",
                )
                for i in range(3)
            ])

        # Every caller saw exactly 5 inserts (the passing half).
        assert results == [5, 5, 5], (
                f"expected each call to insert 5 (passing only), got {results}"
        )

        # O1: SELECT confirms 15 rows total — the failing 15 never made it.
        async with writer_engine_inmem.connect() as conn:
            count_result = await conn.execute(text(
                "SELECT COUNT(*) FROM optimizations "
                "WHERE json_extract(context_sources, '$.batch_id') LIKE 'qgate-%'"
            ))
            row = count_result.first()
            total_rows = int(row[0]) if row else 0
        assert total_rows == 15, f"expected 15 passing rows, got {total_rows}"

        # 15 quality-rejected log records — one per failing pending.
        # Filter on the human-readable per-row INFO line, not the bulk summary.
        rejected_records = [
            r for r in caplog.records
            if "Seed quality gate: rejected" in r.getMessage()
            and "score=3.00" in r.getMessage()
        ]
        assert len(rejected_records) == 15, (
            f"expected 15 quality-rejected log records, "
            f"got {len(rejected_records)}: "
            f"{[r.getMessage() for r in rejected_records[:3]]}"
        )

        # No row with overall_score < 5.0 made it through (defense in depth).
        async with writer_engine_inmem.connect() as conn:
            failed_check = await conn.execute(text(
                "SELECT COUNT(*) FROM optimizations WHERE overall_score < 5.0"
            ))
            row2 = failed_check.first()
            failed_rows = int(row2[0]) if row2 else 0
        assert failed_rows == 0, (
            f"quality gate let {failed_rows} sub-5.0 rows through to DB"
        )

    # -- Test #4: provenance writes post-commit -------------------------------

    @pytest.mark.asyncio
    async def test_bulk_persist_provenance_writes_post_commit(
        self, write_queue_inmem, writer_engine_inmem,
    ):
        """A pending with ``auto_injected_*`` fields populated must result
        in ``OptimizationPattern(relationship='injected')`` rows being
        written post-commit.

        Pins the v0.4.5 invariant: the FK on ``Optimization.id`` requires
        the parent row to be durable BEFORE provenance SAVEPOINTs run, so
        ``bulk_persist`` commits, then calls ``record_injection_provenance``.

        Asserts:
        - Optimization row landed.
        - At least one OptimizationPattern row with relationship='injected'
          and similarity matching the supplied map.
        """
        from app.services import batch_persistence

        # Pre-create a cluster row so the OptimizationPattern FK on
        # cluster_id resolves cleanly. Uses the shared helper so cycles 3+
        # share the same pre-staging primitive across taxonomy +
        # injection-provenance tests.
        cluster_id = await create_prestaged_cluster(
            writer_engine_inmem, cluster_id="test-cluster-prov",
        )

        # Build a pending with non-empty auto_inject fields.
        pending = _make_passing_pending(batch_id="prov-test")
        pending.auto_injected_cluster_ids = [cluster_id]
        pending.auto_injected_similarity_map = {cluster_id: 0.87}
        pending.auto_injected_patterns = []  # topic provenance only

        inserted = await batch_persistence.bulk_persist(
            [pending], write_queue_inmem, batch_id="prov-test",
        )
        assert inserted == 1

        # O1: SELECT both the parent + the join row.
        async with writer_engine_inmem.connect() as conn:
            opt_check = await conn.execute(text(
                "SELECT id FROM optimizations WHERE id = :oid"
            ), {"oid": pending.id})
            assert opt_check.first() is not None, (
                "Optimization row was not committed"
            )

            prov_check = await conn.execute(text(
                "SELECT cluster_id, relationship, similarity "
                "FROM optimization_patterns "
                "WHERE optimization_id = :oid AND relationship = 'injected'"
            ), {"oid": pending.id})
            prov_rows = list(prov_check.fetchall())

        assert len(prov_rows) >= 1, (
            "expected >=1 OptimizationPattern row with relationship='injected', "
            f"got {len(prov_rows)} — provenance write was skipped or rolled back"
        )
        # The injected row carries the supplied cluster_id + similarity.
        assert prov_rows[0].cluster_id == cluster_id
        assert prov_rows[0].relationship == "injected"
        assert prov_rows[0].similarity is not None
        assert abs(prov_rows[0].similarity - 0.87) < 1e-6, (
            f"expected similarity=0.87 (from similarity_map), "
            f"got {prov_rows[0].similarity}"
        )

    # -- Test #5: event emission AFTER queue resolves -------------------------

    @pytest.mark.asyncio
    async def test_bulk_persist_event_emission_after_queue_resolves(
        self, write_queue_inmem, writer_engine_inmem,
    ):
        """``optimization_created`` events fire AFTER ``bulk_persist`` returns
        (i.e. AFTER the queue has resolved the work + the function emits
        events synchronously in its own coroutine).

        Asserts:
        - No events present BEFORE bulk_persist starts.
        - All N events present AFTER bulk_persist returns.
        - ``rate_limit_cleared`` events fire for non-passthrough_fallback
          tier (the fixture defaults to ``routing_tier='internal'``).
        """
        from app.services import batch_persistence
        from app.services.event_bus import event_bus

        # Subscribe BEFORE invocation.
        ev_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        event_bus._subscribers.add(ev_queue)

        try:
            # Confirm the queue is empty before kicking off persistence.
            assert ev_queue.qsize() == 0, (
                "test setup error: subscriber queue had pre-existing events"
            )

            n_rows = 3
            pendings = [
                _make_passing_pending(batch_id="evt-test")
                for _ in range(n_rows)
            ]

            inserted = await batch_persistence.bulk_persist(
                pendings, write_queue_inmem, batch_id="evt-test",
            )
            assert inserted == n_rows

            # Events are emitted synchronously inside bulk_persist's body
            # AFTER the queue resolves the persist work, but the
            # `event_bus.publish` calls themselves are sync put_nowait.
            # By the time `bulk_persist` returns, all events are visible.
            collected: list[dict] = []
            while True:
                try:
                    collected.append(ev_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            created_events = [
                e for e in collected if e.get("event") == "optimization_created"
            ]
            assert len(created_events) == n_rows, (
                f"expected {n_rows} optimization_created events after submit "
                f"returned, got {len(created_events)}"
            )
            # Each event carries the batch_id + source='batch_seed' marker.
            for ev in created_events:
                assert ev["data"].get("batch_id") == "evt-test"
                assert ev["data"].get("source") == "batch_seed"

            # Rate-limit auto-clear: routing_tier='internal' (NOT
            # 'passthrough_fallback') with provider='test' triggers the
            # cleared event.  Should fire exactly once per provider in the
            # batch (deduped via set in bulk_persist).
            cleared_events = [
                e for e in collected if e.get("event") == "rate_limit_cleared"
            ]
            assert len(cleared_events) == 1, (
                f"expected 1 rate_limit_cleared event "
                f"(routing_tier='internal' + provider='test' present), "
                f"got {len(cleared_events)}"
            )
            assert cleared_events[0]["data"].get("provider") == "test"
            assert cleared_events[0]["data"].get("source") == "batch_seed"
            assert cleared_events[0]["data"].get("batch_id") == "evt-test"
        finally:
            event_bus._subscribers.discard(ev_queue)

        # Suppress unused-fixture warning — the engine is reachable via
        # the queue, but we want pytest to keep the fixture in scope so
        # writer cleanup runs deterministically.
        _ = writer_engine_inmem


# ---------------------------------------------------------------------------
# Cycle 3: batch_taxonomy_assign migration to WriteQueue
# ---------------------------------------------------------------------------


class TestBatchTaxonomyAssignViaWriteQueue:
    """RED phase: pin the new
    ``batch_taxonomy_assign(results, write_queue, batch_id)`` signature that
    GREEN will implement. Under the v0.4.12 ``session_factory`` signature the
    test fails — confirming the migration target before any production code
    changes.
    """

    @pytest.mark.asyncio
    async def test_batch_taxonomy_assign_routes_through_queue(
        self, write_queue_inmem, writer_engine_inmem, monkeypatch,
    ):
        """RED: batch_taxonomy_assign must call write_queue.submit with
        operation_label='batch_taxonomy_assign'.

        Currently FAILS because ``batch_taxonomy_assign`` still has its
        v0.4.12 ``session_factory`` signature ((``WriteQueue`` is not
        callable as a context manager). GREEN will swap the signature so
        the queue receives the work via the same Option C dual-typed
        pattern ``bulk_persist`` adopted in cycle 2.
        """
        from app.services import batch_persistence

        # Pre-stage a cluster row so the OptimizationPattern FK on
        # cluster_id resolves cleanly when assign_cluster reuses an
        # existing centroid (defensive — the per-prompt path also adds
        # new clusters from scratch, but we want both paths covered).
        await create_prestaged_cluster(
            writer_engine_inmem, cluster_id="ta-prestaged-cluster",
        )

        # Capture submit() invocations on the queue so we can assert that
        # the canonical path went through it.
        captured: list[str] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label or "")
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        # Pre-persist a row so taxonomy_assign has something to operate on.
        # ``with_embedding=True`` populates the three embedding fields with
        # zero-vector bytes — ``batch_taxonomy_assign`` filters on
        # ``r.embedding``, so a None embedding silently skips the row.
        pending = _make_passing_pending(batch_id="ta-test", with_embedding=True)
        await batch_persistence.bulk_persist(
            [pending], write_queue_inmem, batch_id="ta-test",
        )
        # Discard the bulk_persist label so we only assert on the
        # taxonomy-assign submission below.
        captured.clear()

        # Migration target: batch_taxonomy_assign now takes write_queue,
        # not session_factory.
        result = await batch_persistence.batch_taxonomy_assign(
            [pending], write_queue_inmem, batch_id="ta-test",
        )

        assert "batch_taxonomy_assign" in captured, (
            f"expected 'batch_taxonomy_assign' in captured submit labels, "
            f"got {captured!r}"
        )
        # The function returns a dict summary; clusters_assigned should be
        # >= 0 (could be 0 if the assign_cluster path raised internally,
        # 1 if a new cluster was created — both acceptable for a routing
        # check that just confirms the queue received the work).
        assert isinstance(result, dict)
        assert result.get("clusters_assigned", -1) >= 0


# ---------------------------------------------------------------------------
# Cycle 3 OPERATE: dynamic concurrency + duplicate-detection + per-pending
#                  isolation under realistic load
# ---------------------------------------------------------------------------


class TestBatchTaxonomyAssignOperate:
    """OPERATE phase: mirrors cycle 2 ``TestBulkPersistOperate`` structure for
    the cycle 3 ``batch_taxonomy_assign`` migration.

    Per ``feedback_tdd_protocol.md`` Phase 5, dynamic verification under
    realistic concurrent load — proves the migrated code actually delivers on
    the queue's promises (no ``database is locked`` under N=5 contention) AND
    pins four invariants the integrate review surfaced:

    * Test #1 — N=5 stress: queue serialization eliminates writer contention.
    * Test #2 — duplicate-detection: ``batch_taxonomy_assign`` does NOT have
      idempotency (unlike ``bulk_persist``); calling it twice on the same
      batch creates 2× ``OptimizationPattern(relationship='source')`` rows.
      Pinning the v0.4.12 behavior so a future safety-net retry loop won't
      be silently added.
    * Test #3 — event ordering: ``taxonomy_changed`` event fires AFTER the
      queue resolves, never before.
    * Test #4 — per-pending isolation: a single corrupt embedding does NOT
      poison the rest of the batch (existing ``try/except Exception``
      handler pinned).

    Test #1 uses ``writer_engine_file`` for real WAL contention. Tests #2-4
    use the in-memory queue fixture (logic-only, no contention required).
    The autouse ``_reset_taxonomy_engine`` fixture below ensures every test
    starts with a fresh ``TaxonomyEngine`` singleton + ``EmbeddingIndex`` so
    accumulated centroids from prior tests don't bleed into assert paths.
    """

    @pytest.fixture(autouse=True)
    def _reset_taxonomy_engine(self):
        """Each test gets a fresh process singleton.

        ``batch_taxonomy_assign`` reads ``engine._embedding_index`` and
        every successful ``assign_cluster()`` call upserts the new cluster
        into that index. Reusing a singleton across tests makes the
        embedding-index state path-dependent on test ordering, which is
        exactly the kind of flake the autouse reset prevents.
        """
        from app.services.taxonomy import reset_engine
        reset_engine()
        yield
        reset_engine()

    # -- Test #1: N=5 concurrent callers, real WAL contention ----------------

    @pytest.mark.asyncio
    async def test_batch_taxonomy_assign_n5_concurrent_callers_serialize_via_queue(
        self, writer_engine_file, caplog,
    ):
        """N=5 concurrent ``batch_taxonomy_assign`` callers, each assigning
        3 pre-persisted optimizations to clusters.

        Each caller's batch is pre-persisted via ``bulk_persist`` first
        (parent rows must be durable before ``OptimizationPattern`` FKs
        resolve), then 5 concurrent ``batch_taxonomy_assign`` callers race
        on the same engine + write_queue.

        Asserts:
        - Total ``clusters_assigned`` across 5 summaries == 15 (5×3).
        - Zero ``database is locked`` log records.
        - 15 ``OptimizationPattern(relationship='source')`` rows in DB.
        """
        from app.services import batch_persistence
        from app.services.write_queue import WriteQueue

        await _create_schema_on_engine(writer_engine_file)

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()

        try:
            # Pre-persist 5 separate batches (3 rows each). Each row needs
            # ``with_embedding=True`` — taxonomy_assign filters on
            # ``r.embedding`` truthiness.
            batches = [
                [
                    _make_passing_pending(
                        batch_id=f"ta-op-{i}", with_embedding=True,
                    )
                    for _ in range(3)
                ]
                for i in range(5)
            ]
            for i, batch in enumerate(batches):
                inserted = await batch_persistence.bulk_persist(
                    batch, queue, batch_id=f"ta-op-{i}",
                )
                assert inserted == 3, (
                    f"pre-persist batch {i} expected 3 rows, got {inserted}"
                )

            t0 = time.monotonic()
            with caplog.at_level(logging.WARNING):
                summaries = await asyncio.gather(*[
                    batch_persistence.batch_taxonomy_assign(
                        batches[i], queue, batch_id=f"ta-op-{i}",
                    )
                    for i in range(5)
                ])
            elapsed = time.monotonic() - t0

            # Sum of ``clusters_assigned`` across all 5 summaries == 15.
            total_assigned = sum(s["clusters_assigned"] for s in summaries)
            assert total_assigned == 15, (
                f"expected 15 total clusters_assigned across 5 callers, "
                f"got {total_assigned}: per-caller={[s['clusters_assigned'] for s in summaries]}"
            )

            # O2: zero 'database is locked' anywhere in captured records.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            # O1: SELECT to verify the OptimizationPattern(relationship='source')
            # rows actually landed for every assigned pending.
            async with writer_engine_file.connect() as conn:
                source_check = await conn.execute(text(
                    "SELECT COUNT(*) FROM optimization_patterns "
                    "WHERE relationship = 'source'"
                ))
                row = source_check.first()
                source_rows = int(row[0]) if row else 0
            assert source_rows == 15, (
                f"expected 15 OptimizationPattern(relationship='source') rows, "
                f"got {source_rows}"
            )

            # Wall-clock budget: 5×3 = 15 assigns under N=5 concurrency
            # should comfortably finish under 30s.
            assert elapsed < 30.0, (
                f"taxonomy-assign stress run took {elapsed:.1f}s, > 30s budget"
            )
        finally:
            await queue.stop(drain_timeout=5.0)

    # -- Test #2: NOT-idempotent (calling twice creates duplicate sources) ---

    @pytest.mark.asyncio
    async def test_batch_taxonomy_assign_does_not_skip_duplicate_calls(
        self, write_queue_inmem, writer_engine_inmem,
    ):
        """``batch_taxonomy_assign`` does NOT have an idempotency check
        (unlike ``bulk_persist``). Submitting the same batch twice in
        serial creates duplicate ``OptimizationPattern(relationship='source')``
        rows — one per call.

        Pinning this v0.4.12 behavior so a future safety-net retry loop
        won't be silently added without explicit dedup. If cycle 7+ adds
        idempotency, this test breaks loudly and forces an explicit
        decision (rename to ``test_..._is_idempotent`` and flip the
        assertions).

        Asserts:
        - After call #1: exactly 1 source pattern row per pending.
        - After call #2: exactly 2 source pattern rows per pending.
        """
        from app.services import batch_persistence

        # Pre-persist a 3-pending batch.
        pendings = [
            _make_passing_pending(batch_id="dup-tax", with_embedding=True)
            for _ in range(3)
        ]
        inserted = await batch_persistence.bulk_persist(
            pendings, write_queue_inmem, batch_id="dup-tax",
        )
        assert inserted == 3

        # Call #1.
        summary_1 = await batch_persistence.batch_taxonomy_assign(
            pendings, write_queue_inmem, batch_id="dup-tax",
        )
        assert summary_1["clusters_assigned"] == 3, (
            f"call #1 expected 3 assignments, got {summary_1['clusters_assigned']}"
        )

        # After call #1: 1 source row per pending.
        opt_ids = [p.id for p in pendings]
        async with writer_engine_inmem.connect() as conn:
            for opt_id in opt_ids:
                src_count = await conn.execute(text(
                    "SELECT COUNT(*) FROM optimization_patterns "
                    "WHERE optimization_id = :oid AND relationship = 'source'"
                ), {"oid": opt_id})
                row = src_count.first()
                count_1 = int(row[0]) if row else 0
                assert count_1 == 1, (
                    f"after call #1, opt {opt_id[:8]} expected 1 source row, "
                    f"got {count_1}"
                )

        # Call #2 — same batch_id, same pendings. No idempotency check;
        # a second source row per pending is the EXPECTED v0.4.12 outcome.
        summary_2 = await batch_persistence.batch_taxonomy_assign(
            pendings, write_queue_inmem, batch_id="dup-tax",
        )
        assert summary_2["clusters_assigned"] == 3, (
            f"call #2 expected 3 assignments (no skip), "
            f"got {summary_2['clusters_assigned']}"
        )

        # After call #2: 2 source rows per pending — duplicate, intentional.
        async with writer_engine_inmem.connect() as conn:
            for opt_id in opt_ids:
                src_count = await conn.execute(text(
                    "SELECT COUNT(*) FROM optimization_patterns "
                    "WHERE optimization_id = :oid AND relationship = 'source'"
                ), {"oid": opt_id})
                row = src_count.first()
                count_2 = int(row[0]) if row else 0
                assert count_2 == 2, (
                    f"after call #2, opt {opt_id[:8]} expected 2 source "
                    f"rows (NOT 1 — no idempotency), got {count_2}. "
                    f"If a dedup pass landed, rename this test."
                )

    # -- Test #3: event emission AFTER queue resolves -------------------------

    @pytest.mark.asyncio
    async def test_batch_taxonomy_assign_event_emission_after_queue_resolves(
        self, write_queue_inmem, writer_engine_inmem, tmp_path,
    ):
        """``taxonomy_changed`` event fires AFTER ``batch_taxonomy_assign``
        returns — i.e. AFTER the queue has resolved the assign work.

        Also pins ``seed_taxonomy_complete`` decision-event ordering
        (recorded via ``log_decision`` AFTER ``submit()`` returns).

        Asserts:
        - Subscriber queue empty before ``batch_taxonomy_assign`` invoked.
        - Exactly 1 ``taxonomy_changed`` event AFTER the call returns.
        - Event payload carries ``trigger='batch_seed'`` + correct
          ``batch_id`` + ``clusters_created``.
        - Event-logger ring buffer contains a ``seed_taxonomy_complete``
          decision (path='hot', op='seed') after the call returns.
        """
        from app.services import batch_persistence
        from app.services.event_bus import event_bus
        from app.services.taxonomy.event_logger import (
            TaxonomyEventLogger,
            reset_event_logger,
            set_event_logger,
        )

        # Install an isolated TaxonomyEventLogger so we can observe
        # ``seed_taxonomy_complete`` deterministically (the conftest does
        # not set one — without this, ``get_event_logger()`` raises
        # RuntimeError and the log_decision call is silently swallowed
        # by the ``except RuntimeError: pass`` in batch_persistence).
        tel = TaxonomyEventLogger(
            events_dir=tmp_path / "tax_events",
            publish_to_bus=False,
        )
        set_event_logger(tel)

        # Subscribe to event_bus BEFORE invocation.
        ev_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        event_bus._subscribers.add(ev_queue)

        try:
            # Pre-persist a 3-pending batch.
            pendings = [
                _make_passing_pending(batch_id="evt-tax", with_embedding=True)
                for _ in range(3)
            ]
            await batch_persistence.bulk_persist(
                pendings, write_queue_inmem, batch_id="evt-tax",
            )

            # Drain subscriber queue (bulk_persist fired its own events).
            while True:
                try:
                    ev_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            assert ev_queue.qsize() == 0, (
                "test setup error: subscriber queue not drained before "
                "batch_taxonomy_assign was invoked"
            )

            summary = await batch_persistence.batch_taxonomy_assign(
                pendings, write_queue_inmem, batch_id="evt-tax",
            )
            assert summary["clusters_assigned"] == 3

            # AFTER the call returns: exactly 1 ``taxonomy_changed`` event
            # is in the subscriber queue. ``event_bus.publish`` is sync
            # ``put_nowait``, so by the time the function returns the
            # event is visible.
            collected: list[dict] = []
            while True:
                try:
                    collected.append(ev_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            tax_changed = [
                e for e in collected if e.get("event") == "taxonomy_changed"
            ]
            assert len(tax_changed) == 1, (
                f"expected exactly 1 taxonomy_changed event after submit "
                f"returned, got {len(tax_changed)}"
            )
            payload = tax_changed[0]["data"]
            assert payload.get("trigger") == "batch_seed"
            assert payload.get("batch_id") == "evt-tax"
            assert payload.get("clusters_created") == summary["clusters_created"]

            # ``seed_taxonomy_complete`` decision recorded post-submit.
            recent = tel.get_recent(limit=50, path="hot", op="seed")
            seed_decisions = [
                e for e in recent if e.get("decision") == "seed_taxonomy_complete"
            ]
            assert len(seed_decisions) >= 1, (
                f"expected >=1 'seed_taxonomy_complete' decision in ring "
                f"buffer, got {len(seed_decisions)}: "
                f"recent ops={[(e.get('op'), e.get('decision')) for e in recent[:5]]}"
            )
            ctx = seed_decisions[0].get("context", {})
            assert ctx.get("batch_id") == "evt-tax"
            assert ctx.get("clusters_assigned") == 3
        finally:
            event_bus._subscribers.discard(ev_queue)
            reset_event_logger()

    # -- Test #4: per-pending failure isolates --------------------------------

    @pytest.mark.asyncio
    async def test_batch_taxonomy_assign_per_pending_failure_isolates(
        self, write_queue_inmem, writer_engine_inmem, caplog,
    ):
        """A single per-pending failure (corrupt embedding) does NOT
        poison the rest of the batch.

        The v0.4.12 ``_do_assign`` body wraps each per-pending block in
        ``try: ... except Exception as exc: logger.warning(...)`` — so
        partial-batch progress is durable. Pin this invariant so a future
        refactor doesn't accidentally hoist the try/except outside the
        loop and turn one bad embedding into a full-batch abort.

        Asserts:
        - 2 of 3 pendings successfully assigned.
        - 1 warning log record cites the corrupt pending's id prefix.
        - Summary's ``clusters_assigned`` == 2.
        - DB has 2 ``OptimizationPattern(relationship='source')`` rows
          for this batch (not 0, not 3).
        """
        from app.services import batch_persistence

        # Build 3 pendings, corrupt the middle one's embedding.
        pendings = [
            _make_passing_pending(batch_id="iso-tax", with_embedding=True)
            for _ in range(3)
        ]
        # Corrupt: 0-byte buffer fails ``np.frombuffer(..., dtype=float32)``
        # cleanly with a ValueError raised by the assign_cluster downstream
        # path (or fails the cosine search dim check). Either way the
        # per-pending except block catches it.
        # NB: pending.embedding is checked truthy by the
        # ``r.embedding`` filter at the top of batch_taxonomy_assign;
        # ``b""`` is falsy and would silently skip the row entirely (no
        # warning, no failure isolation observable). We need a non-empty
        # but malformed buffer instead — 7 bytes is not divisible by
        # float32's 4-byte stride, so np.frombuffer raises ValueError.
        pendings[1].embedding = b"\x00" * 7

        inserted = await batch_persistence.bulk_persist(
            pendings, write_queue_inmem, batch_id="iso-tax",
        )
        assert inserted == 3

        with caplog.at_level(logging.WARNING):
            summary = await batch_persistence.batch_taxonomy_assign(
                pendings, write_queue_inmem, batch_id="iso-tax",
            )

        # Summary reflects partial success.
        assert summary["clusters_assigned"] == 2, (
            f"expected 2 successful assigns (1 corrupt embedding skipped), "
            f"got {summary['clusters_assigned']}"
        )

        # The corrupt row's id-prefix shows up in a warning log.
        bad_id_prefix = pendings[1].id[:8]
        warn_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "Taxonomy assign failed" in r.getMessage()
            and bad_id_prefix in r.getMessage()
        ]
        assert len(warn_records) >= 1, (
            f"expected >=1 warning citing {bad_id_prefix} after corrupt-embedding "
            f"per-pending failure, got {len(warn_records)}"
        )

        # O1: SELECT — exactly 2 source rows for this batch's pendings.
        async with writer_engine_inmem.connect() as conn:
            ids_param = ",".join(f"'{p.id}'" for p in pendings)
            count_q = await conn.execute(text(
                f"SELECT COUNT(*) FROM optimization_patterns "  # noqa: S608
                f"WHERE optimization_id IN ({ids_param}) "
                f"  AND relationship = 'source'"
            ))
            row = count_q.first()
            source_rows = int(row[0]) if row else 0
        assert source_rows == 2, (
            f"expected exactly 2 OptimizationPattern(relationship='source') "
            f"rows after partial-batch isolation, got {source_rows}"
        )
