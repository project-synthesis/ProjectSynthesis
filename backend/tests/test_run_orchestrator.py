"""Tests for RunOrchestrator (Foundation P3, v0.4.18) — 14 tests, cat 2.

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.2
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, RunRow
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult
from app.services.run_orchestrator import RunOrchestrator


pytestmark = pytest.mark.asyncio


# ---------- Local fixtures (per-file pattern shared with test_run_row_model.py) ----------

# Use a shared in-memory database URI so both the WriteQueue's writer engine
# and this file's read session see the same data — mirrors writer_engine_inmem
# in conftest.py but with a unique ``memdb`` name to avoid cross-test bleed.
_SHARED_URI = (
    "sqlite+aiosqlite:///"
    "file:memdb_run_orchestrator_test?mode=memory&cache=shared&uri=true"
)


@pytest_asyncio.fixture
async def writer_engine():
    """In-memory writer engine bound to the shared URI."""
    engine = create_async_engine(_SHARED_URI)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def write_queue(writer_engine):
    """Started WriteQueue bound to the in-memory writer engine."""
    from app.services.write_queue import WriteQueue
    queue = WriteQueue(writer_engine)
    await queue.start()
    try:
        yield queue
    finally:
        await queue.stop(drain_timeout=2.0)


@pytest_asyncio.fixture
async def db(writer_engine) -> AsyncGenerator[AsyncSession, None]:
    """Read session against the same shared URI so commits from the WriteQueue
    are immediately visible. Created AFTER writer_engine to ensure schema
    materialization (Base.metadata.create_all) has happened."""
    read_engine = create_async_engine(_SHARED_URI)
    factory = async_sessionmaker(read_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await read_engine.dispose()


# ---------- Stub generator ----------


class StubProbeGenerator:
    def __init__(self, terminal_status: str = "completed", raise_exc: BaseException | None = None) -> None:
        self.terminal_status = terminal_status
        self.raise_exc = raise_exc
        self.calls: list[tuple] = []

    async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult:
        self.calls.append((request, run_id))
        if self.raise_exc:
            raise self.raise_exc
        return GeneratorResult(
            terminal_status=self.terminal_status,  # type: ignore[arg-type]
            prompts_generated=3,
            prompt_results=[{"id": "p1"}],
            aggregate={"prompts_optimized": 3, "prompts_failed": 0, "summary": "ok"},
            taxonomy_delta={"domains_touched": [], "clusters_created": 0},
            final_report="report",
        )


async def _build(write_queue, generator) -> RunOrchestrator:
    return RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": generator, "seed_agent": StubProbeGenerator()},
    )


# ---------- 14 tests per spec § 9 cat 2 ----------


# Test 1: row create via WriteQueue with caller-supplied run_id
async def test_create_row_with_caller_supplied_run_id(write_queue, db) -> None:
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={"topic": "x"})
    row = await orch.run("topic_probe", req, run_id="my-uuid-1")
    assert row.id == "my-uuid-1"


# Test 2: row create with internally-minted run_id when none supplied
async def test_create_row_with_internal_run_id_when_omitted(write_queue, db) -> None:
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert isinstance(row.id, str) and len(row.id) >= 32


# Test 3: status transition running → completed
async def test_status_transition_running_to_completed(write_queue, db) -> None:
    gen = StubProbeGenerator(terminal_status="completed")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert row.status == "completed"


# Test 4: status transition running → partial (generator-classified)
async def test_status_transition_running_to_partial(write_queue, db) -> None:
    gen = StubProbeGenerator(terminal_status="partial")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert row.status == "partial"


# Test 5: status transition running → failed (generator-classified)
async def test_status_transition_running_to_failed(write_queue, db) -> None:
    gen = StubProbeGenerator(terminal_status="failed")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req)
    assert row.status == "failed"


# Test 6: cancellation under shield marks failed before re-raise
async def test_cancellation_marks_failed_under_shield(write_queue, db) -> None:
    class HangingGenerator:
        async def run(self, request, *, run_id):
            await asyncio.sleep(10)
            return GeneratorResult(
                terminal_status="completed", prompts_generated=0, prompt_results=[],
                aggregate={}, taxonomy_delta={}, final_report=None,
            )
    orch = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": HangingGenerator(), "seed_agent": StubProbeGenerator()},
    )
    req = RunRequest(mode="topic_probe", payload={})
    task = asyncio.create_task(orch.run("topic_probe", req, run_id="cancel-1"))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Row should be marked failed — read fresh from a different session
    from sqlalchemy import select
    await db.commit()  # flush any pending read-session state
    row = (await db.execute(select(RunRow).where(RunRow.id == "cancel-1"))).scalar_one()
    assert row.status == "failed"
    assert row.error == "cancelled"


# Test 7: exception capture — generator-raised exception marks failed
async def test_exception_capture_marks_failed(write_queue, db) -> None:
    gen = StubProbeGenerator(raise_exc=ValueError("boom"))
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    with pytest.raises(ValueError):
        await orch.run("topic_probe", req, run_id="exc-1")
    from sqlalchemy import select
    await db.commit()
    row = (await db.execute(select(RunRow).where(RunRow.id == "exc-1"))).scalar_one()
    assert row.status == "failed"
    assert "ValueError: boom" in row.error


# Test 8: audit-hook clean (no direct writes) — generator never touches RunRow
async def test_audit_hook_clean_no_direct_writes_from_generator(
    write_queue, db, audit_hook
) -> None:
    """Generators MUST NOT write to RunRow directly."""
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    audit_hook.reset()
    await orch.run("topic_probe", req)
    audit_hook.populate_from_caplog()
    # No audit warnings — all writes went through WriteQueue
    assert audit_hook.warnings == []


# Test 9: unknown mode raises before row created
async def test_unknown_mode_raises_no_row_created(write_queue, db) -> None:
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest.model_construct(mode="bogus", payload={})  # bypass validation
    with pytest.raises(ValueError, match="unknown mode"):
        await orch.run("bogus", req)
    # Row should NOT exist
    from sqlalchemy import select
    rows = (await db.execute(select(RunRow))).scalars().all()
    assert all(r.mode != "bogus" for r in rows)


# Test 10: ContextVar set + reset around run
async def test_context_var_set_and_reset(write_queue, db) -> None:
    from app.services.probe_common import current_run_id
    captured: list = []

    class CapturingGenerator:
        async def run(self, request, *, run_id):
            captured.append(current_run_id.get())
            return GeneratorResult(
                terminal_status="completed", prompts_generated=0, prompt_results=[],
                aggregate={}, taxonomy_delta={}, final_report=None,
            )
    orch = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": CapturingGenerator(), "seed_agent": StubProbeGenerator()},
    )
    req = RunRequest(mode="topic_probe", payload={})
    await orch.run("topic_probe", req, run_id="ctx-1")
    assert captured == ["ctx-1"]
    assert current_run_id.get() is None  # reset


# Test 11: double-cancellation idempotent
async def test_double_cancellation_idempotent(write_queue, db) -> None:
    """Second cancellation does not double-write status='failed'."""
    class HangingGenerator:
        async def run(self, request, *, run_id):
            await asyncio.sleep(10)
            return GeneratorResult(
                terminal_status="completed", prompts_generated=0, prompt_results=[],
                aggregate={}, taxonomy_delta={}, final_report=None,
            )
    orch = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": HangingGenerator(), "seed_agent": StubProbeGenerator()},
    )
    req = RunRequest(mode="topic_probe", payload={})
    task = asyncio.create_task(orch.run("topic_probe", req, run_id="dbl-1"))
    await asyncio.sleep(0.1)
    task.cancel()
    task.cancel()  # second cancel — should be a no-op
    with pytest.raises(asyncio.CancelledError):
        await task
    from sqlalchemy import select
    await db.commit()
    row = (await db.execute(select(RunRow).where(RunRow.id == "dbl-1"))).scalar_one()
    assert row.status == "failed"


# Test 12: _persist_final writes terminal_status from GeneratorResult
async def test_persist_final_writes_terminal_status_from_generator_result(
    write_queue, db
) -> None:
    gen = StubProbeGenerator(terminal_status="partial")
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req, run_id="tf-1")
    assert row.status == "partial"
    assert row.aggregate == {"prompts_optimized": 3, "prompts_failed": 0, "summary": "ok"}


# Test 13: WriteQueue.submit lambdas commit before returning
async def test_write_queue_lambdas_commit_before_returning(write_queue, db) -> None:
    """Verify each submit() lambda invokes db.commit() — required by WriteQueue contract."""
    gen = StubProbeGenerator()
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    row = await orch.run("topic_probe", req, run_id="commit-1")
    # The fact that the row is readable from a different session proves commit happened
    from sqlalchemy import select
    await db.commit()
    fresh = (await db.execute(select(RunRow).where(RunRow.id == "commit-1"))).scalar_one()
    assert fresh.id == "commit-1"
    assert fresh.status == "completed"


# Test 14: error message truncated to 2000 chars
async def test_error_message_truncation(write_queue, db) -> None:
    huge = "x" * 5000
    gen = StubProbeGenerator(raise_exc=RuntimeError(huge))
    orch = await _build(write_queue, gen)
    req = RunRequest(mode="topic_probe", payload={})
    with pytest.raises(RuntimeError):
        await orch.run("topic_probe", req, run_id="trunc-1")
    from sqlalchemy import select
    await db.commit()
    row = (await db.execute(select(RunRow).where(RunRow.id == "trunc-1"))).scalar_one()
    assert len(row.error) <= 2000 + len("RuntimeError: ")  # type prefix + truncated msg
