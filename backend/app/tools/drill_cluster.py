"""synthesis_drill_into_cluster MCP tool handler.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.30-t3.3-drill-into-cluster-design.md`` §3.3.

MCP-process handler accesses the orchestrator via
``app.tools._shared.get_run_orchestrator()`` (singleton accessor, NOT
``app.state``). Opens its own short read session via
``_database_mod.async_session_factory()`` for the PromptCluster lookup —
no per-request ``db: AsyncSession`` parameter from the MCP wrapper.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app import database as _database_mod
from app.models import PromptCluster
from app.schemas.probes import DrillInitiatedOutput, DrillRequest
from app.schemas.runs import RunRequest
from app.tools._shared import get_run_orchestrator


async def handle_drill_into_cluster(
    *,
    cluster_id: str,
    topic: str,
) -> DrillInitiatedOutput:
    """Implementation handler — invoked from the MCP wrapper.

    Gates:
      1. Topic constraints (DrillRequest constructor) — raises
         ``ValidationError`` on shape failure.
      2. Cluster must exist → raises ``ValueError("cluster_not_found")``.
      3. Cluster state != 'archived' → raises ``ValueError("cluster_archived")``.

    On all gates passing: mints ``run_id``, builds ``RunRequest``, calls
    ``dispatch_async``. Returns ``DrillInitiatedOutput`` per
    ``routers/clusters.py`` drill endpoint contract.
    """
    # Validate topic constraints — raises ValidationError on shape failure.
    DrillRequest(topic=topic)

    # Own short read session for cluster lookup (canonical short-session
    # pattern matches ValidationSuiteService.get + replay_suite.py).
    async with _database_mod.async_session_factory() as db:
        cluster = await db.get(PromptCluster, cluster_id)
    if cluster is None:
        raise ValueError("cluster_not_found")
    if cluster.state == "archived":
        raise ValueError("cluster_archived")

    orchestrator = get_run_orchestrator()
    # get_run_orchestrator raises ValueError("RunOrchestrator not initialized")
    # on uninitialized singleton — analogous to the REST endpoint's 503 path.

    run_id = uuid.uuid4().hex
    started_at = datetime.now(UTC).replace(tzinfo=None)

    run_request = RunRequest(
        mode="topic_probe",
        payload={
            "topic": topic,
            "grounding_mode": "codebase",
            "source_cluster_id": cluster_id,
        },
    )

    await orchestrator.dispatch_async(
        mode="topic_probe",
        request=run_request,
        run_id=run_id,
    )

    return DrillInitiatedOutput(
        run_id=run_id,
        poll_url=f"/api/runs/{run_id}",
        source_cluster_id=cluster_id,
        started_at=started_at.replace(tzinfo=UTC),
    )
