"""Tests for synthesis_probe MCP tool (Topic Probe Tier 1, v0.4.12).

AC-C6-1 through AC-C6-5 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 6.

Adaptations vs plan §Cycle 6 Step 1:
- FastMCP's `Tool` object exposes `output_schema` (populated when
  `structured_output=True`), not a literal `structured_output` attribute.
  We assert `getattr(tool_meta, 'structured_output', False) is True`
  (per the plan literal) OR `tool_meta.output_schema` truthy as fallback,
  which together prove the decorator's effect at registration time.
- Mock fixtures (`mock_probe_service`, `mock_probe_service_no_repo`,
  `mock_mcp_ctx`) are defined inline in this file.

Post-C6 REFACTOR: tests use schema-valid `topic="probe-mcp-test-topic"`
(>=3 chars, mirrors the C5 router test alignment). The production
`handle_probe` now validates inputs via `ProbeRunRequest(**kwargs)` —
the production contract drives the test, not the reverse.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.probes import (
    ProbeAggregate,
    ProbeCompletedEvent,
    ProbeError,
    ProbeProgressEvent,
    ProbeRunResult,
    ProbeTaxonomyDelta,
)


# ---------------------------------------------------------------------------
# Fixtures (inline — defined per task constraints)
# ---------------------------------------------------------------------------


def _make_probe_run_result() -> ProbeRunResult:
    """Build a minimal ProbeRunResult mirroring the REST GET shape."""
    return ProbeRunResult(
        id="probe-123",
        topic="probe-mcp-test-topic",
        scope="**/*",
        intent_hint="explore",
        repo_full_name="owner/repo",
        project_id="proj-1",
        commit_sha="abc123",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        prompts_generated=3,
        prompt_results=[],
        aggregate=ProbeAggregate(
            mean_overall=7.5,
            completed_count=3,
            failed_count=0,
            scoring_formula_version=4,
        ),
        taxonomy_delta=ProbeTaxonomyDelta(),
        final_report="# Probe Report\n\nMock report body.",
        status="completed",
    )


@pytest.fixture
def mock_probe_service():
    """ProbeService mock yielding 3 progress events + 1 completed event."""

    async def _run_gen(_request):
        for i in range(1, 4):
            yield ProbeProgressEvent(
                probe_id="probe-123",
                current=i,
                total=3,
                optimization_id=f"opt-{i}",
                intent_label=f"intent-{i}",
                overall_score=7.0 + i * 0.1,
            )
        yield ProbeCompletedEvent(
            probe_id="probe-123",
            status="completed",
            mean_overall=7.5,
            prompts_generated=3,
            taxonomy_delta_summary={},
        )

    svc = MagicMock()
    svc.run = MagicMock(side_effect=lambda req: _run_gen(req))
    svc.fetch_result = AsyncMock(return_value=_make_probe_run_result())
    return svc


@pytest.fixture
def mock_probe_service_no_repo():
    """ProbeService mock that raises ProbeError(link_repo_first) on run()."""

    async def _run_gen(_request):
        raise ProbeError("link_repo_first", message="Link a GitHub repo first.")
        # Required for python to recognize this as an async generator.
        yield  # pragma: no cover

    svc = MagicMock()
    svc.run = MagicMock(side_effect=lambda req: _run_gen(req))
    svc.fetch_result = AsyncMock()
    return svc


@pytest.fixture
def mock_mcp_ctx():
    """FastMCP Context mock with awaitable report_progress."""
    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# Tests — TestSynthesisProbe (5 ACs)
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
    async def test_ctx_report_progress_called_per_prompt(
        self, mock_probe_service, mock_mcp_ctx,
    ):
        """AC-C6-3: ctx.report_progress(current, total, message) called per probe_prompt_completed."""
        from app.tools.probe import handle_probe

        await handle_probe(
            topic="probe-mcp-test-topic",
            n_prompts=5,
            ctx=mock_mcp_ctx,
            _service=mock_probe_service,
        )
        assert mock_mcp_ctx.report_progress.call_count >= 3

    @pytest.mark.asyncio
    async def test_returns_probe_run_result_matching_rest_shape(
        self, mock_probe_service,
    ):
        """AC-C6-4: returns ProbeRunResult, fields match REST GET /api/probes/{id}."""
        from app.tools.probe import handle_probe

        result = await handle_probe(
            topic="probe-mcp-test-topic",
            n_prompts=5,
            ctx=None,
            _service=mock_probe_service,
        )
        for k in ("id", "topic", "aggregate", "taxonomy_delta", "final_report", "status"):
            assert hasattr(result, k)

    @pytest.mark.asyncio
    async def test_link_repo_first_surfaces_clear_remediation(
        self, mock_probe_service_no_repo,
    ):
        """AC-C6-5: ProbeError(link_repo_first) surfaces as MCP tool error with remediation."""
        from app.tools.probe import handle_probe

        with pytest.raises(Exception, match=r"link_repo_first|Link a GitHub repo"):
            await handle_probe(
                topic="probe-mcp-test-topic",
                n_prompts=5,
                ctx=None,
                _service=mock_probe_service_no_repo,
            )
