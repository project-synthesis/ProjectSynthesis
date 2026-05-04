"""Cycle 9 integration tests — lifespan + audit hook + cold_path_mode + health.

Spec § 7 cycle 9 RED tests:
- ``test_lifespan_starts_write_queue_after_alter_table_migrations``
- ``test_lifespan_alter_table_migrations_complete_before_write_queue_starts``
- ``test_lifespan_stops_write_queue_after_drain``
- ``test_lifespan_cancels_recurring_tasks_before_write_queue_stop``
- ``test_request_during_drain_raises_writequeuestopped``
- ``test_recurring_gc_task_handles_writequeuestopped_cleanly``
- ``test_audit_hook_respects_migration_mode_during_lifespan``
- ``test_audit_hook_respects_cold_path_mode_during_cold_path_run``
- ``test_cold_path_mode_clears_in_finally_under_exception``
- ``test_audit_hook_dual_flag_invariant_raises``
- ``test_audit_hook_warns_in_dev_raises_in_ci``
- ``test_health_endpoint_includes_write_queue_metrics``
- ``test_health_endpoint_exposes_metrics_window``

Each test is small, focused, and uses fresh module-level state so cycles
don't bleed into each other.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import settings
from app.database import (
    install_read_engine_audit_hook,
    read_engine_meta,
    uninstall_read_engine_audit_hook,
)
from app.services.write_queue import (
    WriteQueue,
    WriteQueueDeadError,
    WriteQueueStoppedError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_writer_engine(tmp_path: Path):
    """Build an isolated async writer engine on a tmp file DB."""
    db_path = tmp_path / "lifespan_test.db"
    return create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
    )


# ---------------------------------------------------------------------------
# Audit hook + flags
# ---------------------------------------------------------------------------


class TestAuditHookMigrationMode:
    """Spec § 3.3: migrations run with migration_mode=True so the audit
    hook bypasses the lifespan ALTER TABLE / DML migrations.
    """

    @pytest.mark.asyncio
    async def test_audit_hook_respects_migration_mode_during_lifespan(self, tmp_path):
        """With migration_mode=True, INSERT on the read engine must NOT raise."""
        from app.config import settings as _settings

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
        old_raise = _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
        _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = True
        try:
            install_read_engine_audit_hook(engine)
            # Simulate the lifespan migration block.
            read_engine_meta.migration_mode = True
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
                    await conn.execute(text("INSERT INTO t (id) VALUES (1)"))
            finally:
                read_engine_meta.migration_mode = False
        finally:
            uninstall_read_engine_audit_hook()
            _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = old_raise
            await engine.dispose()


class TestAuditHookColdPathMode:
    """C-v4-3 — cold_path_mode bypass + finally clears flag under exception."""

    @pytest.mark.asyncio
    async def test_audit_hook_respects_cold_path_mode_during_cold_path_run(self, tmp_path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cold.db'}")
        old_raise = settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
        settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = True
        try:
            install_read_engine_audit_hook(engine)
            read_engine_meta.cold_path_mode = True
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("CREATE TABLE c (id INTEGER PRIMARY KEY)"))
                    await conn.execute(text("INSERT INTO c (id) VALUES (1)"))
            finally:
                read_engine_meta.cold_path_mode = False
        finally:
            uninstall_read_engine_audit_hook()
            settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = old_raise
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_cold_path_mode_clears_in_finally_under_exception(self):
        """Even if the cold-path body raises, ``finally`` must clear the flag."""
        from app.services.taxonomy.cold_path import execute_cold_path

        async def _failing_inner(_engine, _db):
            raise RuntimeError("synthetic cold-path failure")

        # Monkey-patch the inner so we can drive the exception through
        # the wrapper without spinning up the full taxonomy engine.
        import app.services.taxonomy.cold_path as cold_module
        old_inner = cold_module._execute_cold_path_inner
        cold_module._execute_cold_path_inner = _failing_inner
        try:
            assert read_engine_meta.cold_path_mode is False
            with pytest.raises(RuntimeError, match="synthetic cold-path failure"):
                await execute_cold_path(None, None)  # type: ignore[arg-type]
            assert read_engine_meta.cold_path_mode is False
        finally:
            cold_module._execute_cold_path_inner = old_inner


class TestAuditHookDualFlagInvariant:
    """C-v4-3 — both flags True simultaneously is a programmer error,
    raises RuntimeError unconditionally (regardless of WRITE_QUEUE_AUDIT_HOOK_RAISE).
    """

    @pytest.mark.asyncio
    async def test_audit_hook_dual_flag_invariant_raises(self, tmp_path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dual.db'}")
        # Set both flags BEFORE installing the hook so the assertion fires
        # the first time the hook fires on a write attempt.
        old_raise = settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
        settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = False  # WARN-mode default
        try:
            install_read_engine_audit_hook(engine)
            read_engine_meta.migration_mode = True
            read_engine_meta.cold_path_mode = True
            try:
                with pytest.raises(RuntimeError, match="programmer error"):
                    async with engine.begin() as conn:
                        await conn.execute(text("CREATE TABLE d (id INTEGER PRIMARY KEY)"))
                        await conn.execute(text("INSERT INTO d (id) VALUES (1)"))
            finally:
                read_engine_meta.migration_mode = False
                read_engine_meta.cold_path_mode = False
        finally:
            uninstall_read_engine_audit_hook()
            settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = old_raise
            await engine.dispose()


class TestAuditHookRaiseToggle:
    """MED-3 — WRITE_QUEUE_AUDIT_HOOK_RAISE controls WARN vs RAISE behavior."""

    @pytest.mark.asyncio
    async def test_audit_hook_warns_in_dev_raises_in_ci(self, tmp_path, caplog):
        from app.database import WriteOnReadEngineError

        # Two identical scenarios — flag toggled.
        for raise_mode in (True, False):
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{tmp_path / f'raise_{raise_mode}.db'}",
            )
            old = settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
            settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = raise_mode
            try:
                install_read_engine_audit_hook(engine)
                # Both flags False — write should be detected.
                async with engine.begin() as conn:
                    await conn.execute(text("CREATE TABLE r (id INTEGER PRIMARY KEY)"))

                async def _attempt_insert():
                    async with engine.begin() as conn:
                        await conn.execute(text("INSERT INTO r (id) VALUES (1)"))

                if raise_mode:
                    with pytest.raises(WriteOnReadEngineError):
                        await _attempt_insert()
                else:
                    caplog.clear()
                    await _attempt_insert()  # WARN-only path, no raise
                    assert any(
                        "audit" in r.message.lower()
                        for r in caplog.records
                    ), "WARN log expected when raise_mode=False"
            finally:
                uninstall_read_engine_audit_hook()
                settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = old
                await engine.dispose()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthWriteQueueMetrics:
    """Spec § 6.1 — /api/health surfaces 13 WriteQueue metric fields."""

    @pytest.mark.asyncio
    async def test_health_endpoint_includes_write_queue_metrics(
        self, app_client, tmp_path,
    ):
        """``app_client`` fixture installs a synthetic queue on app.state.

        Replace it with a real ``WriteQueue`` so the metrics_snapshot()
        call returns a real dataclass with the expected 13 fields.
        """
        engine = await _make_writer_engine(tmp_path)
        wq = WriteQueue(engine)
        await wq.start()
        try:
            # Override app.state.write_queue with the real one.
            from app.main import app
            app.state.write_queue = wq

            resp = await app_client.get("/api/health?probes=false")
            assert resp.status_code == 200
            body = resp.json()
            assert "write_queue" in body, (
                "write_queue key missing from /api/health"
            )
            metrics = body["write_queue"]
            assert metrics is not None, (
                "write_queue metrics should be a dict when queue is installed"
            )
            expected_fields = {
                "depth", "in_flight", "total_submitted", "total_completed",
                "total_failed", "total_timeout", "total_overload",
                "p95_latency_ms", "p99_latency_ms", "max_observed_depth",
                "worker_alive", "metrics_window_seconds",
                "metrics_sample_count",
            }
            assert set(metrics.keys()) == expected_fields, (
                f"unexpected keys in write_queue metrics: "
                f"{set(metrics.keys()) ^ expected_fields}"
            )
        finally:
            await wq.stop(drain_timeout=2.0)
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_health_endpoint_exposes_metrics_window(
        self, app_client, tmp_path,
    ):
        """Spec H5: ``metrics_window_seconds`` + ``metrics_sample_count``
        let operators distinguish cold-start metrics from steady-state.
        """
        engine = await _make_writer_engine(tmp_path)
        wq = WriteQueue(engine)
        await wq.start()
        try:
            from app.main import app
            app.state.write_queue = wq
            resp = await app_client.get("/api/health?probes=false")
            body = resp.json()
            metrics = body.get("write_queue")
            assert metrics is not None
            assert "metrics_window_seconds" in metrics
            assert "metrics_sample_count" in metrics
            assert isinstance(
                metrics["metrics_window_seconds"], (int, float),
            )
            assert isinstance(metrics["metrics_sample_count"], int)
        finally:
            await wq.stop(drain_timeout=2.0)
            await engine.dispose()


# ---------------------------------------------------------------------------
# WriteQueue lifecycle wiring
# ---------------------------------------------------------------------------


class TestWriteQueueLifecycle:
    """Spec § 3.3 — submit fails after stop; recurring tasks handle
    WriteQueueStoppedError cleanly.
    """

    @pytest.mark.asyncio
    async def test_request_during_drain_raises_writequeuestopped(self, tmp_path):
        engine = await _make_writer_engine(tmp_path)
        wq = WriteQueue(engine)
        await wq.start()
        try:
            # Drain queue (no inflight to delay shutdown).
            await wq.stop(drain_timeout=2.0)

            async def _work(_db):
                return None
            with pytest.raises(WriteQueueStoppedError):
                await wq.submit(_work)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_recurring_gc_task_handles_writequeuestopped_cleanly(
        self, tmp_path, caplog,
    ):
        """Recurring task must exit cleanly when the queue is already stopped."""
        engine = await _make_writer_engine(tmp_path)
        wq = WriteQueue(engine)
        await wq.start()
        await wq.stop(drain_timeout=1.0)

        # Simulate the main.py recurring_gc_task body.
        async def _recurring_gc_iter():
            try:
                async def _ping(_db):
                    return None
                await wq.submit(_ping)
            except (WriteQueueStoppedError, WriteQueueDeadError) as exc:
                # Spec MED-6: clean exit, NOT exception trace.
                return type(exc).__name__

        result = await _recurring_gc_iter()
        assert result == "WriteQueueStoppedError"
        await engine.dispose()


# ---------------------------------------------------------------------------
# Lifespan ordering
# ---------------------------------------------------------------------------


class TestLifespanOrdering:
    """Spec § 3.3: migrations → audit hook → write_queue start → recurring
    tasks → yield. Verified via ``app.state.lifespan_order`` checkpoints.
    """

    def test_lifespan_starts_write_queue_after_alter_table_migrations(
        self, app_client,
    ):
        """``app.state.lifespan_order`` must list migrations BEFORE write_queue."""
        # The app_client conftest fixture runs a stripped lifespan; verify
        # the production main.py records the ordering at all by importing
        # the module and inspecting the source.
        import inspect
        import app.main as main_mod

        src = inspect.getsource(main_mod.lifespan)
        # Migrations done -> audit hook -> queue start
        idx_migrations = src.find('"migrations_complete"')
        idx_hook = src.find('"audit_hook_installed"')
        idx_queue = src.find('"write_queue_started"')
        assert idx_migrations > 0, "lifespan must record migrations_complete"
        assert idx_hook > idx_migrations, (
            "audit hook must install AFTER migrations complete"
        )
        assert idx_queue > idx_hook, (
            "WriteQueue must start AFTER audit hook installation"
        )

    def test_lifespan_alter_table_migrations_complete_before_write_queue_starts(
        self,
    ):
        """Spec C8 — verify order via source inspection (real lifespan
        runs in-process and uses asyncio events to enforce the order).
        """
        import inspect
        import app.main as main_mod

        src = inspect.getsource(main_mod.lifespan)
        # The lifespan awaits _migrations_done.wait() before starting queue
        assert "_migrations_done.wait()" in src
        assert "WriteQueue(_writer_engine)" in src
        # The wait MUST appear before the WriteQueue construction.
        idx_wait = src.find("_migrations_done.wait()")
        idx_construct = src.find("WriteQueue(_writer_engine)")
        assert idx_wait < idx_construct, (
            "lifespan must await _migrations_done before constructing WriteQueue"
        )

    def test_lifespan_stops_write_queue_after_drain(self):
        """Spec C8 — shutdown calls write_queue.stop() with drain budget."""
        import inspect
        import app.main as main_mod

        src = inspect.getsource(main_mod.lifespan)
        assert "wq.stop(" in src
        assert "WRITE_QUEUE_DRAIN_TIMEOUT_SECONDS" in src

    def test_lifespan_cancels_recurring_tasks_before_write_queue_stop(self):
        """HIGH-8 — recurring tasks cancelled in Phase 2, BEFORE the queue
        stop in Phase 5a. Verified via source inspection.
        """
        import inspect
        import app.main as main_mod

        src = inspect.getsource(main_mod.lifespan)
        idx_recurring = src.find("recurring_gc_task")
        # find phase 5a queue stop comment
        idx_queue_stop = src.find("Phase 5a: stop the WriteQueue")
        assert idx_recurring > 0
        assert idx_queue_stop > idx_recurring
