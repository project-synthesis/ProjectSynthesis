"""MCP tool regression for Foundation P3 RunOrchestrator dispatch (Cycle 13).

Covers spec § 9 cat 8 (6 tests) + spec gap C (1 test).

These tests assert that the MCP tool handlers ``synthesis_probe`` and
``synthesis_seed`` dispatch through ``RunOrchestrator`` (not the legacy
``ProbeService`` / inline ``handle_seed`` orchestration). All response
schemas are preserved byte-for-byte except the additive ``run_id`` field
on ``synthesis_seed``.

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 13
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 7
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult

pytestmark = pytest.mark.asyncio


# Public schema keys for synthesis_seed (must remain stable).
_SEED_OUTPUT_REQUIRED_KEYS = {
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


# Public schema keys for synthesis_probe (must remain stable).
_PROBE_RUN_RESULT_REQUIRED_KEYS = {
    "id",
    "topic",
    "scope",
    "intent_hint",
    "repo_full_name",
    "started_at",
    "completed_at",
    "prompts_generated",
    "prompt_results",
    "aggregate",
    "taxonomy_delta",
    "final_report",
    "status",
    "suite_id",
}


# ---------------------------------------------------------------------------
# Stub generators — produce canonical GeneratorResult shapes
# ---------------------------------------------------------------------------


class _StubProbeGenerator:
    """Stub TopicProbeGenerator that returns a canonical-shape GeneratorResult.

    Mirrors TopicProbeGenerator's GeneratorResult contract (terminal_status,
    prompt_results dicts, aggregate scoring_formula_version) so the MCP
    response wrapping is exercised end-to-end without LLM calls.
    """

    def __init__(self, terminal_status: str = "completed") -> None:
        self.terminal_status = terminal_status
        self.calls: list[tuple[Any, str]] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        await asyncio.sleep(0)  # let _create_row commit land first
        return GeneratorResult(
            terminal_status=self.terminal_status,  # type: ignore[arg-type]
            prompts_generated=1,
            prompt_results=[
                {
                    "prompt_idx": 0,
                    "prompt_text": "p",
                    "status": "completed",
                    "overall_score": 7.0,
                },
            ],
            aggregate={
                "mean_overall": 7.0,
                "completed_count": 1,
                "failed_count": 0,
                "scoring_formula_version": 4,
            },
            taxonomy_delta={
                "domains_created": [],
                "sub_domains_created": [],
                "clusters_created": [],
                "clusters_split": [],
                "proposal_rejected_min_source_clusters": 0,
            },
            final_report="# Probe Report\n\n_stub_",
        )


class _StubSeedGenerator:
    """Stub SeedAgentGenerator that returns a canonical-shape GeneratorResult.

    Mirrors SeedAgentGenerator's GeneratorResult contract (aggregate keys
    ``prompts_optimized`` / ``prompts_failed`` / ``summary``;
    taxonomy_delta keys ``domains_touched`` / ``clusters_created``).
    """

    def __init__(
        self,
        terminal_status: str = "completed",
        prompts_optimized: int = 1,
        prompts_failed: int = 0,
        summary: str | None = None,
    ) -> None:
        self.terminal_status = terminal_status
        self.prompts_optimized = prompts_optimized
        self.prompts_failed = prompts_failed
        self.summary = summary
        self.calls: list[tuple[Any, str]] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        await asyncio.sleep(0)
        completed = self.prompts_optimized
        failed = self.prompts_failed
        # Honour the early-failure aggregate summary so the spec's
        # "Requires project_description..." preservation works through
        # the seed shim's RunRow → SeedOutput.summary path.
        summary = self.summary
        if summary is None:
            summary = (
                f"{completed} prompts optimized"
                f"{f', {failed} failed' if failed else ''}"
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
                "domains_touched": [],
                "clusters_created": 0,
            },
            final_report=None,
        )


# ---------------------------------------------------------------------------
# Fixtures: per-test in-memory engine + WriteQueue + RunOrchestrator
# ---------------------------------------------------------------------------

# Distinct SQLite shared-cache URI per file so concurrent tests don't share
# the same in-memory database instance.
_SHARED_URI = (
    "sqlite+aiosqlite:///"
    "file:memdb_mcp_tools_p3?mode=memory&cache=shared&uri=true"
)


@pytest.fixture
async def writer_engine_for_mcp():
    """In-memory writer engine bound to a shared URI for this test file."""
    engine = create_async_engine(_SHARED_URI)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def patched_async_session_factory(writer_engine_for_mcp, monkeypatch):
    """Repoint app.database.async_session_factory at the shared in-memory DB.

    RunOrchestrator._reload reads back the row through
    app.database.async_session_factory() so the post-write read sees
    rows committed by the WriteQueue against the same engine.
    """
    import app.database as database_mod
    factory = async_sessionmaker(
        writer_engine_for_mcp, expire_on_commit=False,
    )
    monkeypatch.setattr(database_mod, "async_session_factory", factory)
    yield factory


@pytest.fixture
async def write_queue_for_mcp(writer_engine_for_mcp):
    """Started WriteQueue bound to the writer engine."""
    from app.services.write_queue import WriteQueue
    queue = WriteQueue(writer_engine_for_mcp)
    await queue.start()
    try:
        yield queue
    finally:
        await queue.stop(drain_timeout=2.0)


@pytest.fixture
async def stub_orchestrator(
    write_queue_for_mcp,
    patched_async_session_factory,
    monkeypatch,
):
    """Construct a RunOrchestrator with stub generators + install on the
    MCP-process singleton so handle_probe / handle_seed pick it up.

    Cycle 13 introduces ``set_run_orchestrator`` / ``get_run_orchestrator``
    in ``app.tools._shared`` mirroring the pattern used by other DI
    helpers (``set_routing``, ``set_write_queue``, etc.).
    """
    from app.services.run_orchestrator import RunOrchestrator
    from app.tools import _shared as tools_shared

    probe_gen = _StubProbeGenerator()
    seed_gen = _StubSeedGenerator()
    orchestrator = RunOrchestrator(
        write_queue=write_queue_for_mcp,
        generators={
            "topic_probe": probe_gen,
            "seed_agent": seed_gen,
        },
    )

    previous = getattr(tools_shared, "_run_orchestrator", None)
    tools_shared.set_run_orchestrator(orchestrator)
    try:
        yield orchestrator, probe_gen, seed_gen
    finally:
        tools_shared.set_run_orchestrator(previous)


# ---------------------------------------------------------------------------
# Test 1: synthesis_probe result schema preserved (no shape drift)
# ---------------------------------------------------------------------------


async def test_synthesis_probe_result_schema_preserved(
    stub_orchestrator: Any,
) -> None:
    """All today's ProbeRunResult keys are preserved by the refactored handler.

    The shim must convert RunOrchestrator.run() return (a RunRow) into a
    ProbeRunResult preserving every field the legacy ``ProbeService``
    path returned. The ``probe_id`` field name is kept but the value is
    now ``RunRow.id`` (spec § 6.1 backward-compat).
    """
    from app.tools.probe import handle_probe

    result = await handle_probe(
        topic="schema-probe-test-topic",
        n_prompts=5,
        repo_full_name="o/r",
    )
    # Pydantic ProbeRunResult — coerce to dict for set comparison.
    body = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    assert _PROBE_RUN_RESULT_REQUIRED_KEYS.issubset(body.keys()), (
        f"missing keys: {_PROBE_RUN_RESULT_REQUIRED_KEYS - set(body.keys())}"
    )


# ---------------------------------------------------------------------------
# Test 2: synthesis_seed result schema preserved + additive run_id
# ---------------------------------------------------------------------------


async def test_synthesis_seed_result_schema_with_run_id(
    stub_orchestrator: Any,
) -> None:
    """SeedOutput keys preserved + documented additive surfaces:
    ``run_id`` (Foundation P3 cycle 12 v0.4.18) + ``clusters`` (T3.3 v0.4.30).
    """
    # Provide a routing stub so the gate doesn't divert into early-failure;
    # the synthesis_seed shim must compute tier from routing and supply a
    # provider so SeedAgentGenerator's input-validation passes.
    from unittest.mock import MagicMock

    from app.tools.seed import handle_seed
    routing = MagicMock()
    routing.resolve.return_value = MagicMock(
        tier="passthrough",
        provider=MagicMock(name="claude_cli"),
    )

    result = await handle_seed(
        project_description="Schema check seed run with sufficient length",
        prompt_count=5,
        routing=routing,
    )
    body = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    assert _SEED_OUTPUT_REQUIRED_KEYS.issubset(body.keys()), (
        f"missing keys: {_SEED_OUTPUT_REQUIRED_KEYS - set(body.keys())}"
    )
    # Both additive surfaces must be present, and no others.
    assert "run_id" in body
    assert "clusters" in body
    new_keys = set(body.keys()) - _SEED_OUTPUT_REQUIRED_KEYS
    assert new_keys == {"run_id", "clusters"}, (
        f"unexpected new keys: {new_keys - {'run_id', 'clusters'}}"
    )
    assert isinstance(body["run_id"], str)
    assert len(body["run_id"]) >= 32
    assert isinstance(body["clusters"], list)


# ---------------------------------------------------------------------------
# Test 3: synthesis_probe routes through RunOrchestrator
# ---------------------------------------------------------------------------


async def test_synthesis_probe_routes_through_run_orchestrator(
    stub_orchestrator: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP handler must call ``RunOrchestrator.run('topic_probe', ...)``."""
    from app.services import run_orchestrator as ro_mod
    from app.tools.probe import handle_probe

    calls: list[tuple[str, Any]] = []
    real_run = ro_mod.RunOrchestrator.run

    async def _spy(self, mode, request, *, run_id=None):
        calls.append((mode, run_id))
        return await real_run(self, mode, request, run_id=run_id)

    monkeypatch.setattr(ro_mod.RunOrchestrator, "run", _spy)

    await handle_probe(
        topic="dispatch-probe-test",
        n_prompts=5,
        repo_full_name="o/r",
    )
    assert calls, "RunOrchestrator.run never invoked"
    assert calls[0][0] == "topic_probe", (
        f"expected mode='topic_probe', got {calls[0][0]}"
    )


# ---------------------------------------------------------------------------
# Test 4: synthesis_seed routes through RunOrchestrator
# ---------------------------------------------------------------------------


async def test_synthesis_seed_routes_through_run_orchestrator(
    stub_orchestrator: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP handler must call ``RunOrchestrator.run('seed_agent', ...)``."""
    from unittest.mock import MagicMock

    from app.services import run_orchestrator as ro_mod
    from app.tools.seed import handle_seed

    calls: list[tuple[str, Any]] = []
    real_run = ro_mod.RunOrchestrator.run

    async def _spy(self, mode, request, *, run_id=None):
        calls.append((mode, run_id))
        return await real_run(self, mode, request, run_id=run_id)

    monkeypatch.setattr(ro_mod.RunOrchestrator, "run", _spy)

    routing = MagicMock()
    routing.resolve.return_value = MagicMock(
        tier="passthrough",
        provider=MagicMock(name="claude_cli"),
    )

    await handle_seed(
        project_description="Dispatch test seed with adequate description",
        prompt_count=5,
        routing=routing,
    )
    assert calls, "RunOrchestrator.run never invoked"
    assert calls[0][0] == "seed_agent", (
        f"expected mode='seed_agent', got {calls[0][0]}"
    )


# ---------------------------------------------------------------------------
# Test 5: synthesis_probe surfaces ValueError on missing repo (link_repo_first)
# ---------------------------------------------------------------------------


async def test_synthesis_probe_link_repo_first_error_preserved(
    stub_orchestrator: Any,
) -> None:
    """No linked repo → ValueError with reason ``link_repo_first``.

    Pre-Foundation contract: probe with no ``repo_full_name`` raises a
    ``ProbeError('link_repo_first')`` (which is a subclass of Exception
    with reason code). Cycle 13 keeps this surface — the shim resolves
    the absence of a repo BEFORE constructing the RunRequest so the
    canonical error reason flows to the FastMCP runtime as a
    user-visible error.

    The plan template says ``ValueError, match="link_repo_first"``; the
    production exception is ``ProbeError`` (subclass of ``Exception``,
    not ``ValueError``). We allow either to satisfy the contract — the
    shim may translate.
    """
    from app.schemas.probes import ProbeError
    from app.tools.probe import handle_probe

    with pytest.raises((ValueError, ProbeError), match=r"link_repo_first"):
        await handle_probe(
            topic="x" * 5,  # min_length=3
            n_prompts=5,
            repo_full_name=None,
        )


# ---------------------------------------------------------------------------
# Test 6: synthesis_seed early-failure returns SeedOutput(status='failed')
# ---------------------------------------------------------------------------


async def test_synthesis_seed_early_failure_returns_failed_status(
    stub_orchestrator: Any,
) -> None:
    """Empty input + no provider → SeedOutput(status='failed') (not raise).

    Mirrors the legacy contract: missing project_description + missing
    prompts + no provider yields a ``SeedOutput`` with ``status='failed'``
    and a summary mentioning ``Requires project_description``. The shim
    must surface this through the RunOrchestrator-routed path.
    """
    from unittest.mock import MagicMock

    from app.tools.seed import handle_seed

    routing = MagicMock()
    routing.resolve.return_value = MagicMock(
        tier="passthrough",
        provider=None,  # no provider available
    )

    # Use the early-failure stub generator path for this test only —
    # override the seed_agent generator on the orchestrator.
    orchestrator, _probe_gen, _seed_gen = stub_orchestrator
    early_fail_gen = _StubSeedGenerator(
        terminal_status="failed",
        prompts_optimized=0,
        prompts_failed=0,
        summary=(
            "Requires project_description with a provider, "
            "or user-provided prompts."
        ),
    )
    orchestrator._generators["seed_agent"] = early_fail_gen

    result = await handle_seed(
        project_description=None,
        prompts=None,
        routing=routing,
    )
    body = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    assert body["status"] == "failed"
    assert "Requires project_description" in body["summary"]


# ---------------------------------------------------------------------------
# Test 7 (spec gap C): MCP SDK strict-validation accepts additive run_id
# ---------------------------------------------------------------------------


async def test_mcp_sdk_strict_validation_accepts_additive_run_id(
    mcp_test_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec § 11 risk verification: real fastmcp Client accepts additive run_id.

    Pydantic's ``extra='ignore'`` is for INPUT validation; output models always
    emit declared fields, but the FastMCP SDK validator on the CLIENT side
    might reject unexpected fields in the server's tool result. This test
    exercises the actual MCP transport (via fastmcp.Client) and asserts that
    the additive ``run_id`` field is accepted.

    If this test ever fails, gate the run_id emission behind a feature flag
    per spec § 11 backup plan.

    Strategy: install a stub orchestrator on the MCP process's _shared
    singleton so the synthesis_seed tool dispatches through it. Since the
    fastmcp Client lives in the same process for in-memory transport
    (``Client(mcp)``), we reuse the same _shared module-level state.
    """
    from app.services.run_orchestrator import RunOrchestrator
    from app.services.write_queue import WriteQueue
    from app.tools import _shared as tools_shared

    # Build an isolated writer engine + queue + orchestrator for this test.
    engine = create_async_engine(
        "sqlite+aiosqlite:///"
        "file:memdb_mcp_sdk_validate?mode=memory&cache=shared&uri=true",
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    queue = WriteQueue(engine)
    await queue.start()
    try:
        # Repoint the read-side factory so _reload sees the test rows.
        import app.database as database_mod
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(database_mod, "async_session_factory", factory)

        seed_gen = _StubSeedGenerator()
        orchestrator = RunOrchestrator(
            write_queue=queue,
            generators={"seed_agent": seed_gen, "topic_probe": _StubProbeGenerator()},
        )
        previous = getattr(tools_shared, "_run_orchestrator", None)
        tools_shared.set_run_orchestrator(orchestrator)

        # Ensure the routing path returns a non-None provider so the
        # generator-internal early-failure gate does not fire (the
        # SeedAgentGenerator we installed is a stub, but tools/seed.py
        # still does its own routing.resolve() to extract tier).
        from unittest.mock import MagicMock
        routing = MagicMock()
        routing.resolve.return_value = MagicMock(
            tier="passthrough",
            provider=MagicMock(name="claude_cli"),
        )
        previous_routing = getattr(tools_shared, "_routing", None)
        tools_shared.set_routing(routing)

        try:
            # Round-trip via real fastmcp Client. If the SDK validator
            # rejected the additive run_id field, the call would raise.
            result = await mcp_test_client.call_tool(
                "synthesis_seed",
                arguments={
                    "project_description": (
                        "SDK validation test seed with sufficient length"
                    ),
                    "prompt_count": 5,
                },
            )
            assert not result.is_error, f"call_tool errored: {result}"

            # Extract structured payload — fastmcp exposes parsed result on `data`.
            data = result.data
            if hasattr(data, "model_dump"):
                payload = data.model_dump()
            elif isinstance(data, dict):
                payload = data
            else:
                payload = result.structured_content or {}

            assert "run_id" in payload, (
                f"run_id missing from MCP-sdk result payload: {payload!r}"
            )
            assert isinstance(payload["run_id"], str)
        finally:
            tools_shared.set_run_orchestrator(previous)
            tools_shared.set_routing(previous_routing)
    finally:
        await queue.stop(drain_timeout=2.0)
        await engine.dispose()
