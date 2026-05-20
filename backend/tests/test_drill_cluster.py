"""T3.3 drill-into-cluster unit tests.

Spec: ``docs/superpowers/specs/2026-05-19-v0.4.30-t3.3-drill-into-cluster-design.md`` §6.1.

The ``app_client`` fixture from conftest.py installs ``write_queue`` +
``validation_suite_service`` on app.state but does NOT install
``run_orchestrator`` (v0.4.29 lesson). This test module adds an autouse
``installed_orchestrator`` fixture that constructs a local orchestrator,
installs it on BOTH ``app.state.run_orchestrator`` (REST endpoint path)
AND ``app.tools._shared._run_orchestrator`` (MCP handler path), tears
down to the prior values. Mirrors the canonical sibling patterns in
``test_seed_router.py:155-165`` + v0.4.29 ``test_seed_agent_promotion_integration.py:9-34``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.models import PromptCluster, RunRow
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult

# asyncio_mode = "auto" in pyproject.toml handles async tests — no pytestmark needed.


class _NoopGenerator:
    """Stub RunGenerator that does nothing — satisfies dispatch_async's
    spawned ``_run_to_completion`` task without doing real work.

    `_run_to_completion` calls generator.run(request, run_id=run_id) which
    returns a GeneratorResult. We return a minimal completed result so the
    spawned task wraps up cleanly. The RunRow INSERT (awaited by
    dispatch_async BEFORE spawning) is what tests verify.
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
    Mirrors v0.4.29 `local_orchestrator` precedent. Teardown restores prior
    values so cross-test isolation is preserved.
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
        id="c-active",
        parent_id=None,
        label="react testing",
        state="active",
        domain="frontend",
        task_type="coding",
        member_count=0,
        usage_count=0,
        scored_count=0,
    )
    db_session.add(cluster)
    await db_session.commit()
    return cluster


@pytest.fixture
async def archived_cluster(db_session: Any) -> PromptCluster:
    cluster = PromptCluster(
        id="c-archived",
        parent_id=None,
        label="dead cluster",
        state="archived",
        domain="general",
        task_type="general",
        member_count=0,
        usage_count=0,
        scored_count=0,
    )
    db_session.add(cluster)
    await db_session.commit()
    return cluster


async def test_drill_404_on_missing_cluster(app_client: AsyncClient) -> None:
    resp = await app_client.post(
        "/api/clusters/does-not-exist/drill",
        json={"topic": "any topic here"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "cluster_not_found"}


async def test_drill_409_on_archived_cluster(
    app_client: AsyncClient,
    archived_cluster: PromptCluster,
) -> None:
    resp = await app_client.post(
        f"/api/clusters/{archived_cluster.id}/drill",
        json={"topic": "any topic here"},
    )
    assert resp.status_code == 409
    assert resp.json() == {"detail": "cluster_archived"}


async def test_drill_400_on_missing_topic(
    app_client: AsyncClient,
    active_cluster: PromptCluster,
) -> None:
    resp = await app_client.post(
        f"/api/clusters/{active_cluster.id}/drill",
        json={},
    )
    assert resp.status_code == 422  # Pydantic validation error
    # If the implementer uses _classify_validation_error to map to 400 instead,
    # update this assertion to 400 with detail="invalid_topic".


async def test_drill_400_on_empty_topic(
    app_client: AsyncClient,
    active_cluster: PromptCluster,
) -> None:
    resp = await app_client.post(
        f"/api/clusters/{active_cluster.id}/drill",
        json={"topic": ""},
    )
    assert resp.status_code in (400, 422)


async def test_drill_400_on_topic_over_500_chars(
    app_client: AsyncClient,
    active_cluster: PromptCluster,
) -> None:
    resp = await app_client.post(
        f"/api/clusters/{active_cluster.id}/drill",
        json={"topic": "x" * 501},
    )
    assert resp.status_code in (400, 422)


async def test_drill_happy_path_returns_202_and_creates_run_row(
    app_client: AsyncClient,
    db_session: Any,
    active_cluster: PromptCluster,
) -> None:
    resp = await app_client.post(
        f"/api/clusters/{active_cluster.id}/drill",
        json={"topic": "react testing edge cases"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["source_cluster_id"] == active_cluster.id
    assert body["poll_url"] == f"/api/runs/{body['run_id']}"
    assert "started_at" in body

    row = await db_session.get(RunRow, body["run_id"])
    assert row is not None
    assert row.mode == "topic_probe"
    assert row.source_cluster_id == active_cluster.id
    assert row.topic == "react testing edge cases"


async def test_create_row_threads_source_cluster_id_from_payload(
    db_session: Any,
    installed_orchestrator: Any,  # autouse fixture provides the orchestrator
    active_cluster: PromptCluster,
) -> None:
    """Pin acceptance #3 — payload.get('source_cluster_id') → RunRow.source_cluster_id.

    Mirrors the existing payload.get('suite_id') pattern at run_orchestrator.py
    lines 518-522.

    Uses the autouse-installed orchestrator (which wraps the app_client-provided
    write_queue) — no redundant test queue construction.
    """
    run_id = "rr-test-drill"
    await installed_orchestrator._create_row(
        mode="topic_probe",
        request=RunRequest(
            mode="topic_probe",
            payload={
                "topic": "any topic",
                "source_cluster_id": active_cluster.id,
            },
        ),
        run_id=run_id,
    )

    row = await db_session.get(RunRow, run_id)
    assert row is not None
    assert row.source_cluster_id == active_cluster.id


async def test_source_cluster_id_set_null_on_cluster_delete(
    enable_sqlite_foreign_keys: Any,
    active_cluster: PromptCluster,
) -> None:
    """Pin acceptance #10 — ondelete=SET NULL FK semantics.

    Uses ``enable_sqlite_foreign_keys`` fixture (conftest.py:91) to apply
    ``PRAGMA foreign_keys=ON`` to the test session — production
    ``app/database.py`` applies this pragma on every pool checkout, but the
    test ``db_session`` omits it by default (see conftest.py:68-75).

    After the DELETE, the session's identity-map copy of the RunRow holds
    the stale ``source_cluster_id`` value (the DB-level SET NULL fires
    independently). ``expunge_all()`` forces a fresh fetch on the next
    ``get()`` so the test observes the canonical DB state.
    """
    from sqlalchemy import delete

    db_session = enable_sqlite_foreign_keys  # same session, FK enforcement on

    row = RunRow(
        id="rr-fk-test",
        mode="topic_probe",
        status="completed",
        prompts_generated=0,
        prompt_results=[],
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        source_cluster_id=active_cluster.id,
    )
    db_session.add(row)
    await db_session.commit()

    # Delete the cluster
    await db_session.execute(delete(PromptCluster).where(PromptCluster.id == active_cluster.id))
    await db_session.commit()

    # Force a fresh read — identity-map copy holds the stale FK value.
    db_session.expunge_all()
    refreshed = await db_session.get(RunRow, "rr-fk-test")
    assert refreshed is not None
    assert refreshed.source_cluster_id is None


async def test_mcp_tool_synthesis_drill_into_cluster_round_trip(
    db_session: Any,
    active_cluster: PromptCluster,
) -> None:
    """Pin acceptance #9 — MCP tool returns DrillInitiatedOutput shape."""
    from app.tools.drill_cluster import handle_drill_into_cluster

    result = await handle_drill_into_cluster(
        cluster_id=active_cluster.id,
        topic="mcp drill topic",
    )
    assert result.source_cluster_id == active_cluster.id
    assert result.poll_url == f"/api/runs/{result.run_id}"

    # Error envelopes
    with pytest.raises(ValueError, match="cluster_not_found"):
        await handle_drill_into_cluster(cluster_id="nonexistent", topic="x" * 5)


async def test_two_drills_create_distinct_run_rows(
    app_client: AsyncClient,
    db_session: Any,
    active_cluster: PromptCluster,
) -> None:
    """Pin acceptance — multiple drills create distinct rows.

    Test executes drills sequentially (the test ``_TestWriteQueue`` from
    conftest.py:233-242 serializes via asyncio.Lock; true parallel execution
    isn't observable in this harness). What this test pins is the absence
    of a unique-constraint conflict on `source_cluster_id` — each drill
    creates a distinct `RunRow.id` regardless of repeated source cluster.
    """
    resp_a = await app_client.post(
        f"/api/clusters/{active_cluster.id}/drill",
        json={"topic": "first drill"},
    )
    resp_b = await app_client.post(
        f"/api/clusters/{active_cluster.id}/drill",
        json={"topic": "second drill"},
    )
    assert resp_a.status_code == 202
    assert resp_b.status_code == 202
    assert resp_a.json()["run_id"] != resp_b.json()["run_id"]
