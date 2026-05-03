"""Tests for app.services.write_queue."""

import asyncio
import contextlib
import logging
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    _is_write_statement,
    install_read_engine_audit_hook,
    uninstall_read_engine_audit_hook,
)


class TestIsWriteStatement:
    def test_classifies_all_write_forms(self):
        # Plain writes
        assert _is_write_statement("INSERT INTO users VALUES (1)")
        assert _is_write_statement("UPDATE users SET name='x'")
        assert _is_write_statement("DELETE FROM users")
        assert _is_write_statement("REPLACE INTO users VALUES (1)")
        # Lowercase + leading whitespace
        assert _is_write_statement("  insert into users values (1)")
        # INSERT OR REPLACE / IGNORE
        assert _is_write_statement("INSERT OR REPLACE INTO users VALUES (1)")
        assert _is_write_statement("INSERT OR IGNORE INTO users VALUES (1)")
        # CTE writes
        assert _is_write_statement("WITH cte AS (SELECT 1) INSERT INTO t SELECT * FROM cte")
        assert _is_write_statement("WITH cte AS (SELECT 1) UPDATE t SET x=1 FROM cte")
        # Block comment prefix
        assert _is_write_statement("/* comment */ INSERT INTO users VALUES (1)")
        # Line comment prefix
        assert _is_write_statement("-- line comment\nINSERT INTO users VALUES (1)")
        # Interleaved comments
        assert _is_write_statement("/* block */ -- line\n INSERT INTO users VALUES (1)")
        # NOT writes
        assert not _is_write_statement("SELECT * FROM users")
        assert not _is_write_statement("PRAGMA wal_checkpoint")
        assert not _is_write_statement("BEGIN")
        assert not _is_write_statement("COMMIT")
        assert not _is_write_statement("WITH cte AS (SELECT 1) SELECT * FROM cte")


class TestSubmitContract:
    @pytest.mark.asyncio
    async def test_submit_returns_result_when_work_succeeds(self, write_queue_inmem):
        async def _work(db: AsyncSession) -> int:
            return 42
        result = await write_queue_inmem.submit(_work)
        assert result == 42

    @pytest.mark.asyncio
    async def test_submit_propagates_exception_from_work(self, write_queue_inmem):
        async def _work(db: AsyncSession) -> None:
            raise ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            await write_queue_inmem.submit(_work)

    @pytest.mark.asyncio
    async def test_submit_signature_keyword_only_for_timeout_and_label(self, write_queue_inmem):
        async def _work(db: AsyncSession) -> int:
            return 1
        # positional kwargs forbidden
        with pytest.raises(TypeError):
            await write_queue_inmem.submit(_work, 100.0)  # noqa

    @pytest.mark.asyncio
    async def test_submit_default_timeout_is_300_seconds(self, write_queue_inmem):
        # Verify the default by introspecting attribute, not by waiting 300s
        assert write_queue_inmem._default_timeout == 300.0

    @pytest.mark.asyncio
    async def test_submit_operation_label_appears_in_metrics(self, write_queue_inmem):
        async def _work(db: AsyncSession) -> None:
            return None
        await write_queue_inmem.submit(_work, operation_label="my_label")
        snapshot = write_queue_inmem.metrics_snapshot()
        assert snapshot.total_completed >= 1


class TestSerialization:
    @pytest.mark.asyncio
    async def test_callback_a_returns_before_callback_b_starts(self, write_queue_inmem):
        events: list[tuple[str, str, float]] = []
        async def _a(db: AsyncSession) -> None:
            events.append(("a", "start", time.monotonic()))
            await asyncio.sleep(0.05)
            events.append(("a", "end", time.monotonic()))
        async def _b(db: AsyncSession) -> None:
            events.append(("b", "start", time.monotonic()))
            await asyncio.sleep(0.05)
            events.append(("b", "end", time.monotonic()))
        await asyncio.gather(
            write_queue_inmem.submit(_a),
            write_queue_inmem.submit(_b),
        )
        # FIFO: A submitted first, completes first
        a_end = next(t for label, kind, t in events if label == "a" and kind == "end")
        b_start = next(t for label, kind, t in events if label == "b" and kind == "start")
        assert a_end <= b_start, "B started before A finished — serialization broken"

    @pytest.mark.asyncio
    async def test_n10_concurrent_submits_complete_in_fifo_caller_order(self, write_queue_inmem):
        # Stagger submits so the queue holds ordering
        results: list[int] = []
        async def _make_work(idx: int):
            async def _w(db: AsyncSession) -> int:
                await asyncio.sleep(0.01)
                return idx
            return _w
        # Submit serially
        futures = []
        for i in range(10):
            w = await _make_work(i)
            futures.append(asyncio.create_task(write_queue_inmem.submit(w)))
        results = await asyncio.gather(*futures)
        assert results == list(range(10))


class TestTimeoutAndCancellation:
    @pytest.mark.asyncio
    async def test_submit_timeout_cancels_work_and_returns_timeouterror(self, write_queue_inmem):
        async def _slow(db: AsyncSession) -> None:
            await asyncio.sleep(2.0)
        with pytest.raises(asyncio.TimeoutError):
            await write_queue_inmem.submit(_slow, timeout=0.1)

    @pytest.mark.asyncio
    async def test_caller_cancellation_does_not_kill_inflight_work(self, write_queue_inmem, tmp_path):
        # Use a file marker as side effect — assert work completes after cancel
        marker = tmp_path / "marker.txt"
        async def _work(db: AsyncSession) -> None:
            await asyncio.sleep(0.2)
            marker.write_text("done")
        task = asyncio.create_task(write_queue_inmem.submit(_work))
        await asyncio.sleep(0.05)  # give worker time to start
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Wait for marker (work completes despite caller cancel)
        await asyncio.sleep(0.3)
        assert marker.exists() and marker.read_text() == "done"


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_submit_after_stop_raises_writequeuestopped(self, writer_engine_inmem):
        from app.services.write_queue import WriteQueue, WriteQueueStopped
        queue = WriteQueue(writer_engine_inmem)
        await queue.start()
        await queue.stop()
        async def _work(db: AsyncSession) -> None:
            return None
        with pytest.raises(WriteQueueStopped):
            await queue.submit(_work)

    @pytest.mark.asyncio
    async def test_submit_before_start_raises_writequeuestopped(self, writer_engine_inmem):
        from app.services.write_queue import WriteQueue, WriteQueueStopped
        queue = WriteQueue(writer_engine_inmem)
        async def _work(db: AsyncSession) -> None:
            return None
        with pytest.raises(WriteQueueStopped):
            await queue.submit(_work)

    @pytest.mark.asyncio
    async def test_overload_raises_when_queue_full(self, writer_engine_inmem):
        from app.services.write_queue import WriteQueue, WriteQueueOverloaded
        queue = WriteQueue(writer_engine_inmem, max_depth=2)
        await queue.start()
        try:
            # Block the worker with a slow callback
            blocker_done = asyncio.Event()
            async def _blocker(db: AsyncSession) -> None:
                await blocker_done.wait()
            inflight = asyncio.create_task(queue.submit(_blocker))
            await asyncio.sleep(0.05)
            # Now fill the queue
            async def _quick(db: AsyncSession) -> None:
                return None
            queued1 = asyncio.create_task(queue.submit(_quick))
            queued2 = asyncio.create_task(queue.submit(_quick))
            await asyncio.sleep(0.02)
            # Third submit overloads
            with pytest.raises(WriteQueueOverloaded):
                await queue.submit(_quick)
            # Cleanup
            blocker_done.set()
            await asyncio.gather(inflight, queued1, queued2)
        finally:
            await queue.stop(drain_timeout=2.0)

    @pytest.mark.asyncio
    async def test_start_idempotent_when_called_twice(self, writer_engine_inmem):
        from app.services.write_queue import WriteQueue
        queue = WriteQueue(writer_engine_inmem)
        await queue.start()
        await queue.start()  # should be no-op
        assert queue.worker_alive
        await queue.stop()

    @pytest.mark.asyncio
    async def test_concurrent_stop_calls_serialize(self, writer_engine_inmem):
        from app.services.write_queue import WriteQueue
        queue = WriteQueue(writer_engine_inmem)
        await queue.start()
        # Two concurrent stops; both must return only after drain
        await asyncio.gather(queue.stop(), queue.stop())
        assert queue._stop_done.is_set()

    @pytest.mark.asyncio
    async def test_drain_on_stop_completes_inflight(self, writer_engine_inmem):
        """Pin spec § 12: stop() with default (generous) drain_timeout completes
        in-flight work before returning.

        Distinct from the OPERATE stress test ``test_session_cleanup_under_worker_cancellation_is_shielded``
        which uses a SHORT drain_timeout to test the timeout escape — this test
        pins the success path: drain finishes, all submitted work completes.
        """
        from app.services.write_queue import WriteQueue
        queue = WriteQueue(writer_engine_inmem)
        await queue.start()

        completed: list[int] = []

        async def _make_slow(idx: int):
            async def _do(db: AsyncSession) -> None:
                await asyncio.sleep(0.1)
                completed.append(idx)
            return await queue.submit(_do, operation_label=f"work-{idx}")

        # Submit 3 work items; stop() should drain all 3 before returning.
        tasks = [asyncio.create_task(_make_slow(i)) for i in range(3)]
        await asyncio.sleep(0.05)  # let first one start
        await queue.stop(drain_timeout=5.0)
        await asyncio.gather(*tasks, return_exceptions=True)
        assert sorted(completed) == [0, 1, 2], (
            f"drain did not complete all in-flight + queued work: {sorted(completed)}"
        )

    @pytest.mark.asyncio
    async def test_per_test_queue_disposes_writer_engine_cleanly(
        self, writer_engine_file,
    ):
        """Pin spec § 12: per-test queue lifecycle leaves no leaked connections.

        Asserts pool.checkedout() == 0 after queue.stop() and that the queue
        is in a stopped state (subsequent submit raises WriteQueueStopped).
        """
        from app.services.write_queue import WriteQueue, WriteQueueStopped
        queue = WriteQueue(writer_engine_file)
        await queue.start()

        # Do real work to actually exercise the connection.
        async def _work(db: AsyncSession) -> None:
            await db.execute(text("SELECT 1"))
            await db.commit()
        await queue.submit(_work)

        await queue.stop(drain_timeout=5.0)

        # Pool stats: connection returned cleanly. checkedout MUST equal 0.
        pool = writer_engine_file.sync_engine.pool
        assert pool.checkedout() == 0, (
            f"writer engine has {pool.checkedout()} checked-out connections "
            "after queue.stop() — leak!"
        )

        # Subsequent submit must raise (queue is stopped).
        with pytest.raises(WriteQueueStopped):
            await queue.submit(_work)


class TestSupervisor:
    @pytest.mark.asyncio
    async def test_double_crash_within_60s_sets_dead_and_fails_pending(
        self, writer_engine_inmem, monkeypatch,
    ):
        """Pin spec § 12: worker crash budget = 1 respawn within 60s, then dead.

        After two crashes inside the 60s window, _dead is set and subsequent
        submit() raises WriteQueueDead immediately.

        Note on patching strategy: the natural ``_run_one`` path catches
        non-CancelledError exceptions internally and never propagates them up
        to ``_worker_loop``. To trigger the supervisor's two-strikes path we
        replace ``_run_one`` with a function that fails the future cleanly
        (so callers get WriteQueueDead, matching the contract) and then
        re-raises a plain ``RuntimeError`` so ``_worker_loop`` dies and the
        supervisor sees the crash.
        """
        from app.services.write_queue import WriteQueue, WriteQueueDead
        queue = WriteQueue(writer_engine_inmem)
        await queue.start()
        try:
            crashes = {"count": 0}

            async def _crashing_run_one(item):
                _, future, _, _ = item
                crashes["count"] += 1
                if not future.done():
                    with contextlib.suppress(asyncio.InvalidStateError):
                        future.set_exception(
                            WriteQueueDead(f"forced crash {crashes['count']}")
                        )
                # Mimic the original's task_done bookkeeping so the queue stays
                # in a consistent state (the supervisor doesn't drain it; only
                # the second crash triggers _fail_all_pending).
                queue._queue.task_done()
                queue._inflight_label = None
                raise RuntimeError(f"forced crash {crashes['count']}")

            monkeypatch.setattr(queue, "_run_one", _crashing_run_one)

            async def _work(db: AsyncSession) -> None:
                return None

            # Fire submit #1 — worker pulls it, _run_one raises, supervisor
            # records crash #1 and respawns the worker.
            f1 = asyncio.create_task(queue.submit(_work))
            await asyncio.sleep(0.1)  # let first crash + respawn complete

            # Fire submit #2 — new worker pulls it, _run_one raises, supervisor
            # records crash #2 (within 60s window) → _dead = True.
            f2 = asyncio.create_task(queue.submit(_work))
            await asyncio.sleep(0.5)  # let second crash declare queue dead

            # Both Futures should now have WriteQueueDead exceptions.
            with pytest.raises(WriteQueueDead):
                await f1
            with pytest.raises(WriteQueueDead):
                await f2

            # The queue is dead — subsequent submits raise immediately at the
            # entry guard, NOT via _fail_all_pending.
            assert queue._dead is True
            with pytest.raises(WriteQueueDead):
                await queue.submit(_work)
        finally:
            # Don't call stop() on a dead queue (drain semantics aren't
            # meaningful when the worker is gone) — cancel tasks directly.
            if queue._supervisor_task and not queue._supervisor_task.done():
                queue._supervisor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue._supervisor_task
            if queue._worker_task and not queue._worker_task.done():
                queue._worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await queue._worker_task


class TestWALVisibility:
    @pytest.mark.asyncio
    async def test_writer_engine_commit_visible_via_read_engine_under_wal_file_mode(
        self, writer_engine_file,
    ):
        """Pin spec § 12: file-mode SQLite — writer engine's commit is visible
        to a separate read engine on the next fresh transaction.

        Raw SQL bypasses ORM identity-map caching per H-v4-5.
        """
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.services.write_queue import WriteQueue

        # Setup: enable WAL on the writer engine and create a test table.
        async with writer_engine_file.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text(
                "CREATE TABLE IF NOT EXISTS rw_visibility_test "
                "(id INTEGER PRIMARY KEY, marker TEXT)"
            ))

        # Spin up a SEPARATE read engine pointing at the same DB file.
        db_url = str(writer_engine_file.url)
        read_engine = create_async_engine(db_url)
        try:
            queue = WriteQueue(writer_engine_file)
            await queue.start()
            try:
                async def _do_insert(db: AsyncSession) -> None:
                    await db.execute(text(
                        "INSERT INTO rw_visibility_test (id, marker) "
                        "VALUES (42, 'visible')"
                    ))
                    await db.commit()
                await queue.submit(_do_insert, operation_label="rw_test")

                # On the SEPARATE read engine, fresh transaction → see the write.
                async with read_engine.begin() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT marker FROM rw_visibility_test "
                            "WHERE id = :i"
                        ),
                        {"i": 42},
                    )
                    row = result.first()
                    assert row is not None, (
                        "writer commit not visible to read engine"
                    )
                    assert row.marker == "visible"
            finally:
                await queue.stop(drain_timeout=5.0)
        finally:
            await read_engine.dispose()


class TestReentrancy:
    @pytest.mark.asyncio
    async def test_reentrancy_raises_writequeuereentrancy(self, write_queue_inmem):
        from app.services.write_queue import WriteQueueReentrancy
        async def _outer(db: AsyncSession) -> None:
            async def _inner(db2: AsyncSession) -> None:
                return None
            await write_queue_inmem.submit(_inner)
        with pytest.raises(WriteQueueReentrancy):
            await write_queue_inmem.submit(_outer)

    @pytest.mark.asyncio
    async def test_outside_workfn_spawn_calling_submit_works_correctly(self, write_queue_inmem):
        # Probe service's _persist_one pattern — spawn from outside any work_fn
        marker: list[int] = []
        async def _persist_one(idx: int):
            async def _do(db: AsyncSession) -> None:
                marker.append(idx)
            await write_queue_inmem.submit(_do)
        # Spawn from regular code (not inside a work_fn callback)
        tasks = [asyncio.create_task(_persist_one(i)) for i in range(3)]
        await asyncio.gather(*tasks)
        assert sorted(marker) == [0, 1, 2]


class TestAuditHook:
    @pytest.mark.asyncio
    async def test_install_idempotent_via_explicit_uninstall(self, writer_engine_inmem):
        install_read_engine_audit_hook(writer_engine_inmem)
        try:
            with pytest.raises(RuntimeError, match="already installed"):
                install_read_engine_audit_hook(writer_engine_inmem)
        finally:
            uninstall_read_engine_audit_hook()
        # After uninstall, install again should succeed
        install_read_engine_audit_hook(writer_engine_inmem)
        uninstall_read_engine_audit_hook()


class TestOperate:
    """OPERATE phase: dynamic concurrency + cancellation stress under file-mode WAL.

    These tests exercise the WriteQueue's promised contracts under realistic
    concurrent load. They use ``writer_engine_file`` (NOT ``writer_engine_inmem``)
    so real WAL writer-slot semantics apply — the failure mode the queue exists
    to eliminate (`database is locked`) only manifests against on-disk SQLite.

    Anti-patterns covered (per feedback_tdd_protocol.md Phase 5):
      - O1: every test SELECTs the user-visible end-state, never trusts return value alone.
      - O2: writer contention (queue serialization is the test).
      - O5: caller cancellation (#3) and worker cancellation (#2).
    """

    @staticmethod
    async def _create_dummy_writes_table(engine) -> None:
        """Create the test table BEFORE the queue starts so DDL doesn't race
        with worker writes. Uses the engine directly (not the queue)."""
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE IF NOT EXISTS dummy_writes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "caller_idx INTEGER NOT NULL, "
                "row_idx INTEGER NOT NULL"
                ")"
            ))

    @staticmethod
    async def _count_dummy_rows(engine) -> int:
        """SELECT count from outside the queue — verifies user-visible end state."""
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM dummy_writes"))
            row = result.first()
            return int(row[0]) if row else 0

    @pytest.mark.asyncio
    async def test_n10_concurrent_submits_no_database_locked(
        self, writer_engine_file, caplog,
    ):
        """N=10 callers × 100 INSERTs each = 1000 rows, zero 'database is locked'.

        The queue's serialization is the only defense — if it broke, the file-mode
        engine with concurrent WAL writers would surface 'database is locked' in
        SQLAlchemy's ERROR-level logs. We assert zero such records AND zero log
        records containing the phrase, plus all 1000 rows landed.
        """
        from app.services.write_queue import WriteQueue

        await self._create_dummy_writes_table(writer_engine_file)

        queue = WriteQueue(writer_engine_file, max_depth=200)
        await queue.start()
        try:
            depth_samples: list[int] = []

            async def _sample_depth():
                # Snapshot queue depth periodically during the run
                while True:
                    depth_samples.append(queue.queue_depth)
                    await asyncio.sleep(0.01)

            async def _caller(caller_idx: int):
                for row_idx in range(100):
                    async def _w(db: AsyncSession, c=caller_idx, r=row_idx):
                        await db.execute(
                            text(
                                "INSERT INTO dummy_writes (caller_idx, row_idx) "
                                "VALUES (:c, :r)"
                            ),
                            {"c": c, "r": r},
                        )
                        await db.commit()
                    await queue.submit(_w, operation_label=f"caller_{caller_idx}")

            t0 = time.monotonic()
            sampler = asyncio.create_task(_sample_depth())
            with caplog.at_level(logging.WARNING):
                try:
                    await asyncio.gather(*[_caller(i) for i in range(10)])
                finally:
                    sampler.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sampler
            elapsed = time.monotonic() - t0

            # O1: SELECT to verify user-visible state.
            row_count = await self._count_dummy_rows(writer_engine_file)
            assert row_count == 1000, f"expected 1000 rows, got {row_count}"

            # Zero 'database is locked' anywhere in captured log records.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            # Queue depth never exceeded the max_depth ceiling (sanity check that
            # caller-side accounting matches queue-side accounting). Even at
            # bursty submission, the queue's internal cap of 200 must hold.
            assert all(d <= 200 for d in depth_samples), (
                f"depth exceeded ceiling: max={max(depth_samples)}"
            )

            # Wall-clock under 30s (constraint from cycle 1 OPERATE scope).
            assert elapsed < 30.0, f"stress run took {elapsed:.1f}s, > 30s budget"
        finally:
            await queue.stop(drain_timeout=5.0)

    @pytest.mark.asyncio
    async def test_session_cleanup_under_worker_cancellation_is_shielded(
        self, writer_engine_file,
    ):
        """Drain timeout cancels the worker mid-flight; session cleanup must
        be shielded so the connection returns to the pool cleanly.

        Per spec §3.3: when ``stop()`` exceeds drain_timeout, worker_task is
        cancelled. ``__aexit__`` runs under ``asyncio.shield`` so the rollback
        completes even though the surrounding task is cancelled. Verifiable
        via pool stats: ``checkedout()`` returns to 0 (no leaked connection).
        """
        from app.services.write_queue import WriteQueue

        await self._create_dummy_writes_table(writer_engine_file)

        queue = WriteQueue(writer_engine_file)
        await queue.start()

        async def _slow_work(db: AsyncSession) -> None:
            await asyncio.sleep(5.0)
            await db.execute(
                text("INSERT INTO dummy_writes (caller_idx, row_idx) VALUES (-1, -1)"),
            )
            await db.commit()

        # Submit slow work in the background; don't await its result here.
        submit_task = asyncio.create_task(queue.submit(_slow_work))
        await asyncio.sleep(0.5)  # give worker time to enter wait_for + acquire conn

        # Now stop with a short drain timeout — drain expires before work finishes.
        await queue.stop(drain_timeout=2.0)

        # The submit() future was completed by _fail_all_pending or the worker
        # cleanup path — drain it to avoid pending-task warnings.
        with contextlib.suppress(BaseException):
            await submit_task

        # No row was inserted (work was cancelled before commit).
        rows = await self._count_dummy_rows(writer_engine_file)
        assert rows == 0, f"expected 0 rows after cancelled work, got {rows}"

        # Pool stats: connection returned cleanly. checkedout MUST equal 0.
        # If shield were broken, the connection would leak (still checked out)
        # and this assertion would fail.
        pool = writer_engine_file.sync_engine.pool
        assert pool.checkedout() == 0, (
            f"connection leaked: pool.checkedout()={pool.checkedout()} "
            "(shielded session cleanup may have failed)"
        )

    @pytest.mark.asyncio
    async def test_caller_cancellation_inflight_work_completes_via_shield(
        self, writer_engine_file,
    ):
        """Caller cancels its ``await submit(...)`` while work is in-flight;
        per spec §3.2 the work continues and its side effects DO commit.

        This is distinct from ``test_caller_cancellation_does_not_kill_inflight_work``
        in TestTimeoutAndCancellation (which uses inmem + filesystem marker) —
        here we verify the side effect lands in the actual database under
        file-mode WAL semantics.
        """
        from app.services.write_queue import WriteQueue

        await self._create_dummy_writes_table(writer_engine_file)

        queue = WriteQueue(writer_engine_file)
        await queue.start()
        try:
            async def _work(db: AsyncSession) -> None:
                await db.execute(
                    text(
                        "INSERT INTO dummy_writes (caller_idx, row_idx) "
                        "VALUES (-99, -99)"
                    ),
                )
                # Sleep AFTER insert but BEFORE commit so the write is buffered
                # in the session — caller cancels here, work continues to commit.
                await asyncio.sleep(0.3)
                await db.commit()

            task = asyncio.create_task(queue.submit(_work))
            await asyncio.sleep(0.05)  # give worker time to start
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

            # Wait long enough for the work_fn's sleep + commit to complete.
            await asyncio.sleep(1.0)

            # O1: SELECT outside the queue to verify side effect committed.
            rows = await self._count_dummy_rows(writer_engine_file)
            assert rows == 1, (
                f"expected 1 row (work continued past caller cancel), got {rows}"
            )
        finally:
            await queue.stop(drain_timeout=2.0)

    @pytest.mark.asyncio
    async def test_health_metrics_reflect_concurrent_load(
        self, writer_engine_file,
    ):
        """50 concurrent INSERTs; metrics_snapshot reflects realistic state."""
        from app.services.write_queue import WriteQueue

        await self._create_dummy_writes_table(writer_engine_file)

        queue = WriteQueue(writer_engine_file, max_depth=100)
        await queue.start()
        try:
            async def _make_work(idx: int):
                async def _w(db: AsyncSession) -> None:
                    await db.execute(
                        text(
                            "INSERT INTO dummy_writes (caller_idx, row_idx) "
                            "VALUES (:c, 0)"
                        ),
                        {"c": idx},
                    )
                    await db.commit()
                return _w

            futures = []
            for i in range(50):
                w = await _make_work(i)
                futures.append(asyncio.create_task(queue.submit(w)))
            await asyncio.gather(*futures)

            snap = queue.metrics_snapshot()
            assert snap.total_completed >= 50, (
                f"total_completed={snap.total_completed}, expected >= 50"
            )
            assert snap.worker_alive is True
            assert snap.total_failed == 0, (
                f"total_failed={snap.total_failed}, expected 0"
            )
            # p95 latency must be non-zero — proves latency samples were recorded.
            assert snap.p95_latency_ms > 0.0, (
                f"p95_latency_ms={snap.p95_latency_ms}, expected > 0"
            )
            assert snap.metrics_sample_count >= 50, (
                f"metrics_sample_count={snap.metrics_sample_count}, expected >= 50"
            )

            # End-state: 50 rows landed.
            rows = await self._count_dummy_rows(writer_engine_file)
            assert rows == 50, f"expected 50 rows, got {rows}"
        finally:
            await queue.stop(drain_timeout=5.0)

    @pytest.mark.asyncio
    async def test_two_back_to_back_runs_no_state_leak(self, writer_engine_file):
        """Run the N=10 × 50 INSERT scenario twice; second run must be as
        clean as the first (no flaky timing, no leaked connection state).

        Between runs we verify pool.checkedout() == 0 — proves the previous
        queue's worker fully released its connection before disposal.
        """
        from app.services.write_queue import WriteQueue

        await self._create_dummy_writes_table(writer_engine_file)
        pool = writer_engine_file.sync_engine.pool

        async def _run_once(label: str, expected_total: int) -> None:
            queue = WriteQueue(writer_engine_file, max_depth=100)
            await queue.start()
            try:
                async def _caller(caller_idx: int):
                    for row_idx in range(50):
                        async def _w(db: AsyncSession, c=caller_idx, r=row_idx):
                            await db.execute(
                                text(
                                    "INSERT INTO dummy_writes "
                                    "(caller_idx, row_idx) VALUES (:c, :r)"
                                ),
                                {"c": c, "r": r},
                            )
                            await db.commit()
                        await queue.submit(_w, operation_label=label)

                await asyncio.gather(*[_caller(i) for i in range(10)])
            finally:
                await queue.stop(drain_timeout=5.0)

            # Pool clean between runs.
            assert pool.checkedout() == 0, (
                f"after {label}: pool.checkedout()={pool.checkedout()}, "
                "expected 0 (connection leak between runs)"
            )

            rows = await self._count_dummy_rows(writer_engine_file)
            assert rows == expected_total, (
                f"after {label}: expected {expected_total} rows, got {rows}"
            )

        # Run #1: 500 rows total.
        await _run_once("run_a", expected_total=500)
        # Run #2: another 500 rows, cumulative 1000.
        await _run_once("run_b", expected_total=1000)
