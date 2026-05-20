"""T3.2 + T3.5 integration tests.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.29-t3.2-t3.5-design.md`` §6.2.

3 tests exercising: orchestrator-dispatched promotion path (Test 15), the
MCP-tool round-trip (Test 16), and the fail-safe invariant (Test 17).

Tests 15 + 17 construct a local ``RunOrchestrator`` instance rather than
relying on ``app.state.run_orchestrator`` (the ``app_client`` fixture in
``backend/tests/conftest.py:129-283`` does NOT install run_orchestrator on
app.state). Pattern mirrors the canonical sibling at
``test_mcp_tools_save_replay.py:323-328`` which constructs orchestrators
directly for test isolation.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.models import RunRow
from app.services.run_orchestrator import RunOrchestrator
from app.services.seed_agent_promoter import SeedAgentPromoter

pytestmark = pytest.mark.integration


@pytest.fixture
def local_orchestrator(app_client: AsyncClient, tmp_path: Path) -> RunOrchestrator:
    """Construct a RunOrchestrator wired to the test write_queue + sandbox promoter.

    The ``app_client`` fixture monkey-patches ``async_session_factory`` and
    installs a ``write_queue`` on ``app.state``; this fixture builds a fresh
    orchestrator over those, with a sandbox-scoped promoter so file writes
    land in ``tmp_path/prompts/seed-agents/``.
    """
    write_queue = app_client._transport.app.state.write_queue  # type: ignore[attr-defined]
    sandbox_promoter = SeedAgentPromoter(
        write_queue=write_queue,
        prompts_dir=tmp_path / "prompts",
    )
    # generators={} is fine — these tests only exercise _dispatch_promotion,
    # never _run_to_completion.
    return RunOrchestrator(
        write_queue=write_queue,
        generators={},  # type: ignore[arg-type]
        seed_agent_promoter=sandbox_promoter,
    )


# ---------------------------------------------------------------------------
# Test 15: end-to-end orchestrator-dispatched promotion → file lands → AgentLoader sees
# ---------------------------------------------------------------------------


async def test_end_to_end_probe_promotion(
    db_session: Any,
    app_client: AsyncClient,
    local_orchestrator: RunOrchestrator,
    tmp_path: Path,
) -> None:
    """Pin acceptance #1, #3, #6: probe completion → file written via orchestrator dispatch."""
    from app.services.agent_loader import AgentLoader

    # Seed a completed topic_probe row
    row = RunRow(
        id="rr-integration-promote",
        mode="topic_probe",
        status="completed",
        prompts_generated=2,
        prompt_results=[
            {"raw_prompt": "P1", "overall_score": 8.5},
            {"raw_prompt": "P2", "overall_score": 8.0},
        ],
        aggregate={"mean_overall": 8.25},
        topic="integration topic",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        topic_probe_meta={
            "scope": "**/*",
            "commit_sha": None,
            "grounding_mode": "codebase",
            "suggested_agent_name": "integration-agent",
        },
    )
    db_session.add(row)
    await db_session.commit()

    # Fire the orchestrator's dispatch directly
    await local_orchestrator._dispatch_promotion("rr-integration-promote")

    sandbox = tmp_path / "prompts" / "seed-agents"
    target = sandbox / "integration-agent.md"
    assert target.exists()

    # AgentLoader can load it
    loader = AgentLoader(sandbox)
    agent = loader.load("integration-agent")
    assert agent is not None
    assert agent.name == "integration-agent"
    assert agent.enabled is True


# ---------------------------------------------------------------------------
# Test 16: synthesis_refresh_seed_agent MCP tool round-trip
# ---------------------------------------------------------------------------


async def test_mcp_refresh_tool_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin acceptance #10: MCP tool returns correct RefreshResult shape."""
    from app.tools.refresh_seed_agent import handle_refresh_seed_agent

    sandbox = tmp_path / "prompts" / "seed-agents"
    sandbox.mkdir(parents=True)

    # Patch PROMPTS_DIR so the tool's SeedAgentPromoter looks at the sandbox.
    import app.services.seed_agent_promoter as _spm
    monkeypatch.setattr(_spm, "PROMPTS_DIR", tmp_path / "prompts")

    # Patch get_write_queue to return a no-op queue.
    class _NoopQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            pass

    import app.tools._shared as _shared
    monkeypatch.setattr(_shared, "get_write_queue", lambda: _NoopQueue())

    # No file in sandbox → tool returns skipped_reason=file_not_found
    result = await handle_refresh_seed_agent(agent_name="nonexistent-agent")
    assert result.refreshed is False
    assert result.skipped_reason == "file_not_found"
    assert result.agent_name == "nonexistent-agent"


# ---------------------------------------------------------------------------
# Test 17: fail-safe — promoter exception does NOT change probe status
# ---------------------------------------------------------------------------


async def test_promoter_exception_does_not_affect_probe_status(
    db_session: Any,
    app_client: AsyncClient,
    local_orchestrator: RunOrchestrator,
) -> None:
    """Pin acceptance #7: _dispatch_promotion catches all exceptions."""

    class _BrokenPromoter:
        async def maybe_promote(self, run_id: str):
            raise RuntimeError("simulated promoter crash")

    # Replace the local orchestrator's promoter with the broken one
    local_orchestrator._seed_agent_promoter = _BrokenPromoter()  # type: ignore[assignment]

    # Seed a topic_probe row
    row = RunRow(
        id="rr-fail-safe",
        mode="topic_probe",
        status="completed",
        prompts_generated=0,
        prompt_results=[],
        aggregate={"mean_overall": 9.0},
        topic="fail-safe topic",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        topic_probe_meta={
            "scope": "**/*",
            "commit_sha": None,
            "grounding_mode": "codebase",
            "suggested_agent_name": "fail-safe-agent",
        },
    )
    db_session.add(row)
    await db_session.commit()

    # The dispatch must NOT raise; the probe row stays completed
    await local_orchestrator._dispatch_promotion("rr-fail-safe")

    refreshed = await db_session.get(RunRow, "rr-fail-safe")
    assert refreshed.status == "completed"
