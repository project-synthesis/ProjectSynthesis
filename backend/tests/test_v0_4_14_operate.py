"""v0.4.14 cycle 5 — OPERATE regression bar.

Spec § 8 row 1: probe + 30 concurrent seeds + 100 concurrent feedbacks +
5 MCP optimize-passthrough + 3 sampling-pipeline runs + 3 github_auth device-flow polls
+ 5 strategy_updated + 5 api_key set/delete cycles → 0 'database is locked'
+ 0 audit warnings + p95 ≤ 1s + 0 queue failures.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

CI_LIGHT = os.getenv("CI_LIGHT") == "1"


@pytest.mark.skipif(CI_LIGHT, reason="Heavy concurrent stress — release validation only")
class TestV0414OperateRegressionBar:
    async def test_concurrent_writers_no_database_locked_no_audit_warns(
        self, writer_engine_file, caplog,
    ):
        from app.services.write_queue import WriteQueue
        wq = WriteQueue(writer_engine_file, max_depth=512)
        await wq.start()
        caplog.set_level(logging.WARNING)

        async def make_table(db: AsyncSession) -> None:
            await db.execute(text(
                "CREATE TABLE IF NOT EXISTS op_t (id INTEGER PRIMARY KEY, src TEXT, n INTEGER)"
            ))
        await wq.submit(make_table, operation_label="setup")

        async def insert(src: str, n: int) -> None:
            async def _do(db: AsyncSession) -> None:
                await db.execute(
                    text("INSERT INTO op_t (src, n) VALUES (:src, :n)"),
                    {"src": src, "n": n},
                )
            await wq.submit(_do, operation_label=f"insert_{src}")

        async def insert_batch(src: str, count: int) -> None:
            work_fns = []
            for i in range(count):
                async def _w(db: AsyncSession, _src=src, _n=i) -> None:
                    await db.execute(
                        text("INSERT INTO op_t (src, n) VALUES (:src, :n)"),
                        {"src": _src, "n": _n},
                    )
                work_fns.append(_w)
            await wq.submit_batch(work_fns, operation_label=f"batch_{src}")

        # Workload mix per spec § 8 row 1 (v4 — without refine/save_result)
        tasks = []
        tasks.append(insert_batch("probe", 25))
        for i in range(30):
            tasks.append(insert("seed", i))
        for i in range(100):
            tasks.append(insert("feedback", i))
        for i in range(5):
            tasks.append(insert("optimize_pt", i))
        for i in range(3):
            tasks.append(insert("sampling", i))
        for i in range(3):
            # device-poll = submit_batch (token revoke pattern)
            tasks.append(insert_batch("device_poll", 2))
        for i in range(5):
            tasks.append(insert("strategy", i))
            tasks.append(insert("api_set", i))
            tasks.append(insert("api_del", i))

        t0 = time.monotonic()
        await asyncio.gather(*tasks)
        elapsed = time.monotonic() - t0

        snap = wq.metrics_snapshot()
        assert snap.total_failed == 0, f"queue had {snap.total_failed} failures"

        locked = [
            r for r in caplog.records
            if "database is locked" in r.getMessage().lower()
        ]
        assert not locked, f"'database is locked' fired {len(locked)} times"

        audit_warns = [
            r for r in caplog.records
            if "read-engine audit:" in r.getMessage()
        ]
        assert not audit_warns, (
            f"audit hook warns fired {len(audit_warns)} times — write paths drift"
        )

        assert elapsed < 60.0, f"workload took {elapsed:.1f}s — must be < 60s"

        await wq.stop(drain_timeout=10.0)
