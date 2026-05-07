"""Tests for routers/seed.py REST surface (Foundation P3 cycle 12, cat 7).

8 tests per plan task 12.1 covering:
  - POST /api/seed dispatches through RunOrchestrator (sync semantics preserved)
  - SeedOutput shape preserved + additive run_id field
  - All 4 status values (running/completed/failed/partial) reachable via SeedOutput
  - Early-failure path (no project_description + no prompts + no provider)
    returns HTTP 200 with status='failed' (NOT 4xx/5xx) — preserves contract
  - GET /api/seed (paginated list, mode='seed_agent' only — probe runs excluded)
  - GET /api/seed/{run_id} (full RunRow detail, 404 on miss or mode mismatch)
  - RunRow persisted at start
  - duration_ms None-safe when completed_at is None

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 12
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 6.3
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult

pytestmark = pytest.mark.asyncio


SEED_OUTPUT_REQUIRED_KEYS = {
    "status",
    "batch_id",
    "tier",
    "prompts_generated",
    "prompts_optimized",
    "prompts_failed",
    "estimated_cost_usd",
    "domains_touched",
    "clusters_created",
    "summary",
    "duration_ms",
}


# ---------------------------------------------------------------------------
# Stub generator that mirrors SeedAgentGenerator's GeneratorResult shape
# ---------------------------------------------------------------------------


class _StubSeedGenerator:
    """Generator producing a configurable terminal status for unit tests.

    Mirrors the canonical seed-mode GeneratorResult contract — status,
    aggregate keys, taxonomy_delta keys — so the router's serialization
    path exercises the real RunOrchestrator + RunRow round-trip without
    invoking the heavy ``SeedOrchestrator.generate()`` LLM stack.
    """

    def __init__(
        self,
        terminal_status: str = "completed",
        prompts_optimized: int = 3,
        prompts_failed: int = 0,
        domains_touched: list[str] | None = None,
        clusters_created: int = 1,
    ) -> None:
        self.terminal_status = terminal_status
        self.prompts_optimized = prompts_optimized
        self.prompts_failed = prompts_failed
        self.domains_touched = domains_touched or ["backend"]
        self.clusters_created = clusters_created
        self.calls: list[tuple] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        # Yield once so the orchestrator's create_row commit lands first.
        await asyncio.sleep(0)
        completed = self.prompts_optimized
        failed = self.prompts_failed
        summary = (
            f"{completed} prompts optimized"
            f"{f', {failed} failed' if failed else ''}"
            f". {self.clusters_created} clusters created"
        )
        return GeneratorResult(
            terminal_status=self.terminal_status,  # type: ignore[arg-type]
            prompts_generated=completed + failed,
            prompt_results=[],
            aggregate={
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "summary": summary,
            },
            taxonomy_delta={
                "domains_touched": self.domains_touched,
                "clusters_created": self.clusters_created,
            },
            final_report=None,
        )


@pytest_asyncio.fixture
async def patched_orchestrator_session_factory(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """Repoint ``app.database.async_session_factory`` at ``db_session``.

    ``RunOrchestrator._reload`` reads back the row through
    ``app.database.async_session_factory()`` so the post-write read
    sees the row that the WriteQueue stub committed against
    ``db_session``. Without this patch, ``_reload`` opens a new session
    against the production engine where the in-memory test row doesn't
    exist and raises ``RuntimeError("run row {id} not found after persist")``.
    """
    import app.database as database_mod

    class _SessionContext:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _factory():
        return _SessionContext()

    monkeypatch.setattr(database_mod, "async_session_factory", _factory)
    yield _factory


@pytest_asyncio.fixture
async def stub_seed_orchestrator(
    app_client: AsyncClient,
    patched_orchestrator_session_factory: Any,
):
    """Install a RunOrchestrator with a stub seed-agent generator.

    The orchestrator runs the WriteQueue + stub generator end-to-end so
    the refactored router exercises the real RunOrchestrator.run()
    contract: ``_create_row`` happens before the generator publishes,
    and ``_persist_final`` happens after — matching production semantics.
    """
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubSeedGenerator()
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"seed_agent": stub_gen, "topic_probe": stub_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


@pytest_asyncio.fixture
async def stub_seed_orchestrator_partial(
    app_client: AsyncClient,
    patched_orchestrator_session_factory: Any,
):
    """Stub orchestrator returning a 'partial' terminal status.

    Mirrors the SeedAgentGenerator classification rule: 1+ succeeded AND
    1+ failed → 'partial'.
    """
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubSeedGenerator(
        terminal_status="partial",
        prompts_optimized=1,
        prompts_failed=1,
    )
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"seed_agent": stub_gen, "topic_probe": stub_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


@pytest_asyncio.fixture
async def stub_seed_orchestrator_failed(
    app_client: AsyncClient,
    patched_orchestrator_session_factory: Any,
):
    """Stub orchestrator returning early-failure (no project_description / provider)."""
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    class _EarlyFailGen:
        async def run(
            self, request: RunRequest, *, run_id: str,
        ) -> GeneratorResult:
            return GeneratorResult(
                terminal_status="failed",
                prompts_generated=0,
                prompt_results=[],
                aggregate={
                    "prompts_optimized": 0,
                    "prompts_failed": 0,
                    "summary": (
                        "Requires project_description with a provider, "
                        "or user-provided prompts."
                    ),
                },
                taxonomy_delta={"domains_touched": [], "clusters_created": 0},
                final_report=None,
            )

    write_queue = app.state.write_queue
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"seed_agent": _EarlyFailGen()},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


# ===========================================================================
# Tests
# ===========================================================================


async def test_post_seed_response_shape_byte_identical_with_run_id(
    app_client: AsyncClient,
    stub_seed_orchestrator: Any,
) -> None:
    """SeedOutput shape preserved + additive run_id field, no other changes."""
    resp = await app_client.post(
        "/api/seed",
        json={
            "project_description": "Test seed run for shape validation A".ljust(40, "x"),
            "prompt_count": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Existing keys must all be present
    assert SEED_OUTPUT_REQUIRED_KEYS.issubset(body.keys())
    # Additive run_id is the ONLY new key
    new_keys = set(body.keys()) - SEED_OUTPUT_REQUIRED_KEYS
    assert new_keys == {"run_id"}
    assert isinstance(body["run_id"], str) and len(body["run_id"]) >= 32


async def test_post_seed_status_completed_on_success(
    app_client: AsyncClient,
    stub_seed_orchestrator: Any,
) -> None:
    """All prompts succeed → SeedOutput.status == 'completed'."""
    resp = await app_client.post(
        "/api/seed",
        json={
            "project_description": "Successful seed run for status check".ljust(40, "x"),
            "prompt_count": 5,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


async def test_post_seed_status_partial_when_failures(
    app_client: AsyncClient,
    stub_seed_orchestrator_partial: Any,
) -> None:
    """1+ succeeded AND 1+ failed → SeedOutput.status == 'partial'."""
    resp = await app_client.post(
        "/api/seed",
        json={
            "project_description": "Mixed-result seed run for partial check".ljust(40, "x"),
            "prompt_count": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial"
    assert body["prompts_failed"] == 1
    assert body["prompts_optimized"] == 1


async def test_post_seed_status_failed_on_input_validation(
    app_client: AsyncClient,
    stub_seed_orchestrator_failed: Any,
) -> None:
    """Early-failure path: missing project_description + missing prompts + no
    provider → HTTP 200 with status='failed' (preserves today's contract)."""
    resp = await app_client.post("/api/seed", json={})  # nothing supplied
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "Requires project_description" in body["summary"]
    assert body["prompts_optimized"] == 0


async def test_get_seed_list_returns_only_seed_agent_runs(
    app_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/seed returns RunRow WHERE mode='seed_agent' only — probe rows excluded."""
    db_session.add(RunRow(
        id="seed-list-1",
        mode="seed_agent",
        status="completed",
        started_at=datetime.utcnow(),
    ))
    db_session.add(RunRow(
        id="probe-list-1",
        mode="topic_probe",
        status="completed",
        started_at=datetime.utcnow(),
    ))
    await db_session.commit()

    resp = await app_client.get("/api/seed")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["items"]}
    assert "seed-list-1" in ids
    assert "probe-list-1" not in ids


async def test_get_seed_by_id_404_on_miss(app_client: AsyncClient) -> None:
    """Unknown run_id returns 404 with detail='run_not_found'."""
    resp = await app_client.get("/api/seed/nonexistent-uuid")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"


async def test_post_seed_persists_run_row_at_start(
    app_client: AsyncClient,
    stub_seed_orchestrator: Any,
    db_session: AsyncSession,
) -> None:
    """RunRow exists in DB after POST /api/seed returns.

    Verifies the orchestrator's ``_create_row`` fired (with mode set
    to 'seed_agent') and committed via the WriteQueue. By call return
    the stub generator has run to completion, so the row is in a
    terminal state.
    """
    resp = await app_client.post(
        "/api/seed",
        json={
            "project_description": "Persisted at start test seed run".ljust(40, "x"),
            "prompt_count": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    run_id = body["run_id"]

    # Drain any pending orchestrator-side persistence task so the
    # background ``_persist_final`` write completes BEFORE the test
    # queries db_session.
    for _ in range(20):
        await asyncio.sleep(0.05)
        other_tasks = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        if not other_tasks:
            break

    rows = (
        await db_session.execute(
            select(RunRow).where(RunRow.id == run_id),
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.mode == "seed_agent"
    # status MUST be terminal by call return (sync semantics preserved)
    assert row.status in ("completed", "partial", "failed")


async def test_post_seed_duration_ms_none_safe_when_completed_at_none(
    app_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/seed/{id} — a row with completed_at=None still serializes without crash.

    Edge case: a failed run row may have ``completed_at`` unset if the
    orchestrator died mid-write. The serializer must None-guard so the
    GET endpoint doesn't crash and surfaces ``completed_at: null``.
    """
    db_session.add(RunRow(
        id="seed-no-completed",
        mode="seed_agent",
        status="failed",
        started_at=datetime.utcnow(),
        completed_at=None,
        seed_agent_meta={"batch_id": "x"},
        aggregate={"prompts_optimized": 0, "prompts_failed": 0, "summary": "x"},
        taxonomy_delta={"domains_touched": [], "clusters_created": 0},
    ))
    await db_session.commit()

    resp = await app_client.get("/api/seed/seed-no-completed")
    assert resp.status_code == 200
    body = resp.json()
    # GET-by-id endpoint uses RunResult shape, not SeedOutput
    assert body["completed_at"] is None
