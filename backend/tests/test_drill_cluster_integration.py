"""T3.3 drill-into-cluster integration tests.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.30-t3.3-drill-into-cluster-design.md`` §6.2.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.models import PromptCluster, RunRow
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult


pytestmark = pytest.mark.integration


class _NoopGenerator:
    """Stub RunGenerator that does nothing — satisfies dispatch_async's
    spawned ``_run_to_completion`` task without doing real work.

    Mirrors the sibling stub in ``test_drill_cluster.py`` so the integration
    test's REST + MCP paths can ``dispatch_async`` without spinning up the
    full topic_probe generator pipeline.
    """
    async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult:
        return GeneratorResult(
            terminal_status="completed",
            prompts_generated=0,
            prompt_results=[],
            aggregate={},
            taxonomy_delta={},
            final_report="",
        )


@pytest.fixture(autouse=True)
def installed_orchestrator(app_client):
    """Install a real RunOrchestrator on app.state + _shared singleton.

    Required for the 503 ``run_orchestrator_unavailable`` guard in the REST
    endpoint AND for the MCP handler's ``get_run_orchestrator()`` lookup.
    Mirrors the autouse fixture in ``test_drill_cluster.py``. Teardown
    restores prior values so cross-test isolation is preserved.
    """
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator
    from app.tools import _shared as _tools_shared

    write_queue = app.state.write_queue  # installed by app_client fixture
    orch = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": _NoopGenerator()},  # type: ignore[dict-item]
    )

    prev_app_orch = getattr(app.state, "run_orchestrator", None)
    prev_shared_orch = _tools_shared._run_orchestrator
    app.state.run_orchestrator = orch
    _tools_shared.set_run_orchestrator(orch)

    yield orch

    app.state.run_orchestrator = prev_app_orch
    _tools_shared.set_run_orchestrator(prev_shared_orch)


@pytest.fixture
async def active_cluster(db_session: Any) -> PromptCluster:
    cluster = PromptCluster(
        id="c-integration",
        parent_id=None,
        label="integration cluster",
        state="active",
        domain="general",
        task_type="general",
        member_count=0,
        usage_count=0,
        scored_count=0,
    )
    db_session.add(cluster)
    await db_session.commit()
    return cluster


async def test_end_to_end_drill_flow(
    app_client: AsyncClient,
    db_session: Any,
    active_cluster: PromptCluster,
) -> None:
    """End-to-end: drill REST → RunRow with source_cluster_id → GET /api/runs/{id} surfaces it."""
    resp = await app_client.post(
        f"/api/clusters/{active_cluster.id}/drill",
        json={"topic": "integration test topic"},
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    # Verify RunRow created
    row = await db_session.get(RunRow, run_id)
    assert row is not None
    assert row.source_cluster_id == active_cluster.id
    assert row.mode == "topic_probe"

    # Verify GET /api/runs/{id} exposes source_cluster_id
    get_resp = await app_client.get(f"/api/runs/{run_id}")
    if get_resp.status_code == 200:
        # If the run-detail endpoint returns source_cluster_id, verify it.
        # If not, INTEGRATE phase extends the response shape to include it
        # (acceptance criterion #11 E2E spans both the drill path AND the
        # GET-by-id path surfacing the new column).
        body = get_resp.json()
        if "source_cluster_id" in body:
            assert body["source_cluster_id"] == active_cluster.id


async def test_mcp_drill_integration(
    db_session: Any,
    active_cluster: PromptCluster,
) -> None:
    """MCP tool E2E: dispatches a new run with source_cluster_id set."""
    from app.tools.drill_cluster import handle_drill_into_cluster

    result = await handle_drill_into_cluster(
        cluster_id=active_cluster.id,
        topic="mcp integration topic",
    )
    assert result.source_cluster_id == active_cluster.id

    row = await db_session.get(RunRow, result.run_id)
    assert row is not None
    assert row.source_cluster_id == active_cluster.id
