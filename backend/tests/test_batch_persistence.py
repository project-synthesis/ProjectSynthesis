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


def _make_passing_pending(batch_id: str = "test-batch", *, opt_id: str | None = None):
    """Build a minimal ``PendingOptimization`` passing ID-shape + quality gates.

    Per plan task 2.1 — fields chosen so ``bulk_persist`` accepts the row:
    valid uuid4 ``id``, ``status='completed'``, ``overall_score >= 5.0``.

    ``opt_id`` allows callers (e.g. idempotency tests) to pin the row's UUID
    so two parallel ``bulk_persist`` calls collide on the same primary key.
    """
    from app.services.batch_pipeline import PendingOptimization
    return PendingOptimization(
        id=opt_id or str(_uuid.uuid4()),
        trace_id=str(_uuid.uuid4()),
        raw_prompt="test prompt",
        optimized_prompt="optimized test prompt",
        task_type="general",
        strategy_used="auto",
        changes_summary="test",
        score_clarity=7.0,
        score_specificity=7.0,
        score_structure=7.0,
        score_faithfulness=7.0,
        score_conciseness=7.0,
        overall_score=7.0,
        improvement_score=1.0,
        scoring_mode="hybrid",
        intent_label="test",
        domain="general",
        domain_raw="general",
        embedding=None,
        optimized_embedding=None,
        transformation_embedding=None,
        models_by_phase={},
        original_scores={},
        score_deltas={},
        duration_ms=100,
        status="completed",
        provider="test",
        model_used="test-model",
        routing_tier="internal",
        heuristic_flags={},
        suggestions=[],
        repo_full_name=None,
        project_id=None,
        context_sources={"batch_id": batch_id},
        auto_injected_patterns=[],
        auto_injected_cluster_ids=[],
        auto_injected_similarity_map={},
    )


def _make_failing_pending(batch_id: str = "test-batch", overall_score: float = 3.0):
    """Build a ``PendingOptimization`` that the quality gate must reject.

    Used by ``test_bulk_persist_quality_gate_under_load`` to verify the
    score < 5.0 rejection still fires under concurrent load.
    """
    p = _make_passing_pending(batch_id=batch_id)
    p.overall_score = overall_score
    return p


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
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.models import PromptCluster
        from app.services import batch_persistence

        # Pre-create a cluster row so the OptimizationPattern FK on
        # cluster_id resolves cleanly.  FK enforcement is OFF on the
        # in-memory test engine by default, but the row is needed so we
        # can verify the join in the assertion below.  Use the ORM so
        # all NOT NULL columns get their model-level defaults (raw SQL
        # would have to spell every default explicitly).
        cluster_id = "test-cluster-prov"
        sf = async_sessionmaker(
            writer_engine_inmem, class_=AsyncSession, expire_on_commit=False,
        )
        async with sf() as setup_db:
            setup_db.add(PromptCluster(
                id=cluster_id,
                label="test-cluster",
                state="active",
                domain="general",
                task_type="general",
            ))
            await setup_db.commit()

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
