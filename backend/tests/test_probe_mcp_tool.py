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
- AC-C6-3 was retired in Cycle 13 then **restored in Cycle 14 follow-up
  (v0.4.18-p3-PR2)** via a bus → ctx bridge inside ``handle_probe``: the
  shim subscribes to ``event_bus.subscribe_for_run(run_id)`` BEFORE
  dispatching the orchestrator and forwards every ``probe_prompt_completed``
  event into ``ctx.report_progress(progress, total, message)``. The
  ``TestProbeMcpToolBusToCtxBridge`` class below covers the restored
  contract; the dispatch-path smoke test in ``TestSynthesisProbe`` is
  retained for shape parity.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.schemas.probes import ProbeError
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
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


# ---------------------------------------------------------------------------
# Cycle 14 follow-up — bus → ctx progress bridge (AC-C6-3 restoration)
# ---------------------------------------------------------------------------


class _PublishingProbeGenerator:
    """Stub TopicProbeGenerator that publishes ``probe_prompt_completed``
    events to ``event_bus`` during its ``run`` method, exactly mirroring
    the production publisher in ``TopicProbeGenerator._publish_prompt_completed``.

    The handle_probe bridge under test must subscribe BEFORE this generator
    starts publishing (race-free pattern from routers/probes.py Cycle 11) and
    forward every event into ``ctx.report_progress``. The generator yields
    control via ``await asyncio.sleep(0)`` between publishes so the bridge
    task gets scheduled deterministically.
    """

    def __init__(self, *, n_prompts: int = 3) -> None:
        self.calls: list[tuple[Any, str]] = []
        self.n_prompts = n_prompts

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        # Yield once so the bridge task can register itself before any
        # events are published. Mirrors test_probe_router's stub pattern.
        await asyncio.sleep(0)

        for i in range(self.n_prompts):
            event_bus.publish("probe_prompt_completed", {
                "run_id": run_id,
                "probe_id": run_id,
                "current": i + 1,
                "total": self.n_prompts,
                "optimization_id": str(uuid.uuid4()),
                "intent_label": f"prompt {i + 1} label",
                "overall_score": 7.0 + i * 0.1,
                "status": "completed",
            })
            # Give the bridge a chance to drain the queue between publishes.
            await asyncio.sleep(0)

        # Terminal event so the bridge knows to stop.
        event_bus.publish("probe_completed", {
            "run_id": run_id,
            "probe_id": run_id,
            "status": "completed",
            "mean_overall": 7.0,
            "prompts_generated": self.n_prompts,
        })
        await asyncio.sleep(0)

        return GeneratorResult(
            terminal_status="completed",
            prompts_generated=self.n_prompts,
            prompt_results=[
                {
                    "prompt_idx": i,
                    "prompt_text": f"p{i}",
                    "status": "completed",
                    "overall_score": 7.0 + i * 0.1,
                }
                for i in range(self.n_prompts)
            ],
            aggregate={
                "mean_overall": 7.0,
                "completed_count": self.n_prompts,
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


class _RecordingCtx:
    """Stub FastMCP Context recording every ``report_progress`` invocation.

    The real FastMCP Context exposes ``report_progress(progress, total, message)``
    as an async method. The bridge must accept either an awaitable or a sync
    callable for forward-compat; using ``async def`` here covers the canonical
    case. Bridge fail-soft is verified by the ``test_handle_probe_skips_progress_when_ctx_lacks_method``
    test using a plain object().
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def report_progress(
        self,
        progress: float,
        total: float | None = None,
        message: str | None = None,
    ) -> None:
        self.calls.append({
            "progress": progress,
            "total": total,
            "message": message,
        })


@pytest_asyncio.fixture
async def publishing_orchestrator(
    writer_engine_for_probe_mcp,
    patched_session_factory_for_probe_mcp,
):
    """Orchestrator wired to ``_PublishingProbeGenerator`` for bridge tests."""
    from app.services.run_orchestrator import RunOrchestrator
    from app.services.write_queue import WriteQueue

    queue = WriteQueue(writer_engine_for_probe_mcp)
    await queue.start()
    try:
        gen = _PublishingProbeGenerator(n_prompts=3)
        orchestrator = RunOrchestrator(
            write_queue=queue,
            generators={"topic_probe": gen, "seed_agent": gen},
        )
        yield orchestrator, gen
    finally:
        await queue.stop(drain_timeout=2.0)


class TestProbeMcpToolBusToCtxBridge:
    """AC-C6-3 restored: handle_probe forwards probe_prompt_completed events
    to ctx.report_progress for every prompt that completes during the run.

    Spec: docs/specs/topic-probe-2026-04-29.md § 8 Cycle 6 (original AC-C6-3),
    re-introduced via bus → ctx bridge per Cycle 14 follow-up plan.
    """

    @pytest.mark.asyncio
    async def test_handle_probe_reports_progress_per_prompt(
        self, publishing_orchestrator,
    ):
        """The bridge forwards every probe_prompt_completed event into
        ctx.report_progress. Three published events ⇒ three calls with
        progress=1,2,3 total=3.
        """
        from app.tools.probe import handle_probe

        orchestrator, gen = publishing_orchestrator
        ctx = _RecordingCtx()

        await handle_probe(
            topic="probe-mcp-bridge-topic",
            n_prompts=5,
            repo_full_name="o/r",
            ctx=ctx,
            _orchestrator=orchestrator,
        )

        assert gen.calls, "stub generator never invoked"
        assert len(ctx.calls) == 3, (
            f"expected 3 ctx.report_progress calls (one per "
            f"probe_prompt_completed), got {len(ctx.calls)}: {ctx.calls}"
        )
        progresses = [c["progress"] for c in ctx.calls]
        totals = [c["total"] for c in ctx.calls]
        assert progresses == [1, 2, 3]
        assert totals == [3, 3, 3]
        # Message should mention the per-prompt counter (string-shape only;
        # exact format is implementation choice).
        assert all(c["message"] for c in ctx.calls), (
            f"every progress call must carry a non-empty message: {ctx.calls}"
        )

    @pytest.mark.asyncio
    async def test_handle_probe_skips_progress_when_ctx_is_none(
        self, publishing_orchestrator,
    ):
        """Bridge MUST be fail-soft when ``ctx is None`` — the run still
        completes successfully without raising.
        """
        from app.tools.probe import handle_probe

        orchestrator, gen = publishing_orchestrator
        result = await handle_probe(
            topic="probe-mcp-bridge-no-ctx",
            n_prompts=5,
            repo_full_name="o/r",
            ctx=None,
            _orchestrator=orchestrator,
        )
        # No assertion error — run completed cleanly even without a ctx.
        assert result is not None
        assert gen.calls, "stub generator never invoked"

    @pytest.mark.asyncio
    async def test_handle_probe_skips_progress_when_ctx_lacks_method(
        self, publishing_orchestrator,
    ):
        """Bridge MUST be fail-soft when ``ctx`` lacks ``report_progress``.

        Some Context shims (e.g. minimal test clients) expose nothing more
        than an opaque object; the bridge must skip silently rather than
        raising AttributeError mid-run.
        """
        from app.tools.probe import handle_probe

        orchestrator, gen = publishing_orchestrator
        # Plain object — no report_progress attribute.
        opaque_ctx = object()
        result = await handle_probe(
            topic="probe-mcp-bridge-opaque-ctx",
            n_prompts=5,
            repo_full_name="o/r",
            ctx=opaque_ctx,
            _orchestrator=orchestrator,
        )
        assert result is not None
        assert gen.calls, "stub generator never invoked"

    @pytest.mark.asyncio
    async def test_handle_probe_swallows_ctx_report_progress_errors(
        self, publishing_orchestrator,
    ):
        """Bridge MUST swallow exceptions from ``ctx.report_progress``.

        Progress reporting is best-effort; if the IDE-side bridge errors on
        any single forward, the probe run must still complete successfully.
        """
        from app.tools.probe import handle_probe

        class _ExplodingCtx:
            def __init__(self) -> None:
                self.calls = 0

            async def report_progress(self, *args, **kwargs):
                self.calls += 1
                raise RuntimeError("simulated transport error")

        orchestrator, gen = publishing_orchestrator
        ctx = _ExplodingCtx()
        result = await handle_probe(
            topic="probe-mcp-bridge-exploding-ctx",
            n_prompts=5,
            repo_full_name="o/r",
            ctx=ctx,
            _orchestrator=orchestrator,
        )
        assert result is not None
        assert gen.calls, "stub generator never invoked"
        # The bridge attempted at least one forward — proving it tried —
        # and did not propagate the failure.
        assert ctx.calls >= 1

    @pytest.mark.asyncio
    async def test_handle_probe_cleans_up_subscription_and_bridge_task(
        self, publishing_orchestrator,
    ):
        """Bridge subscription + bridge task MUST be cleaned up on completion.

        Verified via ``event_bus._subscribers`` count: the count after the
        call must equal the count before (no leaked subscribers). The bridge
        task is implicitly verified because the call returns — a leaked,
        never-cancelled task would block the await chain or raise on the
        next event-loop tick.
        """
        from app.tools.probe import handle_probe

        orchestrator, _gen = publishing_orchestrator
        before = len(event_bus._subscribers)

        await handle_probe(
            topic="probe-mcp-bridge-cleanup",
            n_prompts=5,
            repo_full_name="o/r",
            ctx=_RecordingCtx(),
            _orchestrator=orchestrator,
        )
        # Give any deferred cleanup a tick to settle.
        await asyncio.sleep(0)

        after = len(event_bus._subscribers)
        assert after == before, (
            f"event_bus subscriber leak: before={before}, after={after}"
        )
