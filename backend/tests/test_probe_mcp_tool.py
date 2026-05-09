"""Tests for synthesis_probe MCP tool (Topic Probe Tier 1, v0.4.12 + P3, v0.4.18).

AC-C6-1 through AC-C6-5 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 6,
adapted for Foundation P3 Cycle 13 dispatch refactor (v0.4.18). The legacy
``_service`` injection path was retired by Cycle 13 (handle_probe now
dispatches through ``RunOrchestrator``); these tests verify the surface
contract — registration, schema decoration, response shape, and
``link_repo_first`` remediation — under the new dispatch.

Adaptations vs plan §Cycle 6 Step 1:
- FastMCP's `Tool` object exposes `output_schema` (populated when
  `structured_output=True`), not a literal `structured_output` attribute.
  We assert `getattr(tool_meta, 'structured_output', False) is True`
  (per the plan literal) OR `tool_meta.output_schema` truthy as fallback,
  which together prove the decorator's effect at registration time.
- AC-C6-3 (``ctx.report_progress`` per prompt) was retired in Cycle 13
  because the TopicProbeGenerator publishes progress to ``event_bus``,
  not through the MCP context. Replaced with a Cycle-13 dispatch
  smoke test asserting the response shape under stub orchestration.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.schemas.probes import ProbeError
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult

# ---------------------------------------------------------------------------
# Stub generator + orchestrator wiring (mirrors test_mcp_tools_p3.py)
# ---------------------------------------------------------------------------


class _StubProbeGenerator:
    """Stub TopicProbeGenerator returning a canonical-shape GeneratorResult."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        await asyncio.sleep(0)
        return GeneratorResult(
            terminal_status="completed",
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


_SHARED_URI = (
    "sqlite+aiosqlite:///"
    "file:memdb_probe_mcp_tool_legacy?mode=memory&cache=shared&uri=true"
)


@pytest_asyncio.fixture
async def writer_engine_for_probe_mcp():
    engine = create_async_engine(_SHARED_URI)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def patched_session_factory_for_probe_mcp(
    writer_engine_for_probe_mcp, monkeypatch,
):
    import app.database as database_mod
    factory = async_sessionmaker(
        writer_engine_for_probe_mcp, expire_on_commit=False,
    )
    monkeypatch.setattr(database_mod, "async_session_factory", factory)
    yield factory


@pytest_asyncio.fixture
async def stub_orchestrator(
    writer_engine_for_probe_mcp,
    patched_session_factory_for_probe_mcp,
):
    from app.services.run_orchestrator import RunOrchestrator
    from app.services.write_queue import WriteQueue

    queue = WriteQueue(writer_engine_for_probe_mcp)
    await queue.start()
    try:
        gen = _StubProbeGenerator()
        orchestrator = RunOrchestrator(
            write_queue=queue,
            generators={"topic_probe": gen, "seed_agent": gen},
        )
        yield orchestrator, gen
    finally:
        await queue.stop(drain_timeout=2.0)


# ---------------------------------------------------------------------------
# Tests — TestSynthesisProbe
# ---------------------------------------------------------------------------


class TestSynthesisProbe:
    @pytest.mark.asyncio
    async def test_15th_mcp_tool_registered(self):
        """AC-C6-1: synthesis_probe is the 15th tool registered on FastMCP server."""
        from app import mcp_server
        tools = list(mcp_server.mcp._tool_manager._tools.keys())  # type: ignore[attr-defined]
        assert "synthesis_probe" in tools
        assert len(tools) == 15

    @pytest.mark.asyncio
    async def test_structured_output_decorator(self):
        """AC-C6-2: registered with structured_output=True (auto-derived JSON schema).

        FastMCP's Tool object materializes `structured_output=True` as a populated
        `output_schema` dict. Either attribute proves the decorator was applied.
        """
        from app import mcp_server
        tool_meta = mcp_server.mcp._tool_manager._tools["synthesis_probe"]  # type: ignore[attr-defined]
        has_flag = getattr(tool_meta, "structured_output", False) is True
        has_schema = bool(getattr(tool_meta, "output_schema", None))
        assert has_flag or has_schema, (
            "synthesis_probe must be registered with structured_output=True "
            "(no structured_output attribute and no output_schema present)"
        )

    @pytest.mark.asyncio
    async def test_dispatch_routes_through_run_orchestrator(
        self, stub_orchestrator,
    ):
        """AC-C6-3 (replaced for Cycle 13): handle_probe dispatches via RunOrchestrator.

        The original test asserted ``ctx.report_progress`` was called per
        prompt. That contract was retired by Cycle 13 — TopicProbeGenerator
        publishes progress to ``event_bus`` rather than the MCP context.
        Replaced with a dispatch-path assertion verifying the orchestrator
        receives a ``topic_probe`` mode RunRequest.
        """
        from app.tools.probe import handle_probe

        orchestrator, gen = stub_orchestrator
        await handle_probe(
            topic="probe-mcp-test-topic",
            n_prompts=5,
            repo_full_name="o/r",
            _orchestrator=orchestrator,
        )
        assert gen.calls, "stub generator never invoked"
        request, _run_id = gen.calls[0]
        assert request.mode == "topic_probe"
        assert request.payload.get("topic") == "probe-mcp-test-topic"

    @pytest.mark.asyncio
    async def test_returns_probe_run_result_matching_rest_shape(
        self, stub_orchestrator,
    ):
        """AC-C6-4: returns ProbeRunResult; fields match REST GET /api/probes/{id}."""
        from app.tools.probe import handle_probe

        orchestrator, _gen = stub_orchestrator
        result = await handle_probe(
            topic="probe-mcp-test-topic",
            n_prompts=5,
            repo_full_name="o/r",
            ctx=None,
            _orchestrator=orchestrator,
        )
        for k in (
            "id", "topic", "aggregate", "taxonomy_delta",
            "final_report", "status",
        ):
            assert hasattr(result, k)

    @pytest.mark.asyncio
    async def test_link_repo_first_surfaces_clear_remediation(self):
        """AC-C6-5: ProbeError(link_repo_first) surfaces with remediation message.

        The shim short-circuits BEFORE calling RunOrchestrator when
        ``repo_full_name`` is missing, so no orchestrator stub is needed.
        Mirrors the routers/probes ``link_repo_first`` 400 short-circuit.
        """
        from app.tools.probe import handle_probe

        with pytest.raises((Exception, ProbeError), match=r"link_repo_first"):
            await handle_probe(
                topic="probe-mcp-test-topic",
                n_prompts=5,
                ctx=None,
                # repo_full_name omitted → triggers link_repo_first
            )
