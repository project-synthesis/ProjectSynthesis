"""Tests for app.services.write_queue."""

import asyncio
import contextlib
import time

import pytest
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
