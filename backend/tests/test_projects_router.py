"""Tests for POST /api/projects/migrate (ADR-005 B3)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Optimization, PromptCluster


async def _seed_projects(db, labels=("Legacy", "user/repo")):
    ids: dict[str, str] = {}
    for label in labels:
        node = PromptCluster(
            label=label,
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db.add(node)
        await db.flush()
        ids[label] = node.id
    await db.commit()
    return ids


async def _seed_opt(db, *, project_id, repo_full_name=None):
    opt = Optimization(
        raw_prompt="test",
        status="completed",
        project_id=project_id,
        repo_full_name=repo_full_name,
    )
    db.add(opt)
    await db.flush()
    await db.commit()
    return opt


@pytest.mark.asyncio
async def test_b3_migrate_endpoint_happy_path(
    app_client: AsyncClient, db_session
):
    ids = await _seed_projects(db_session)
    await _seed_opt(db_session, project_id=ids["Legacy"])
    await _seed_opt(db_session, project_id=ids["Legacy"])

    resp = await app_client.post(
        "/api/projects/migrate",
        json={
            "from_project_id": ids["Legacy"],
            "to_project_id": ids["user/repo"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["migrated"] == 2
    assert body["dry_run"] is False

    rows = (await db_session.execute(select(Optimization))).scalars().all()
    assert all(o.project_id == ids["user/repo"] for o in rows)


@pytest.mark.asyncio
async def test_b3_migrate_endpoint_dry_run_leaves_rows_alone(
    app_client: AsyncClient, db_session
):
    ids = await _seed_projects(db_session)
    await _seed_opt(db_session, project_id=ids["Legacy"])

    resp = await app_client.post(
        "/api/projects/migrate",
        json={
            "from_project_id": ids["Legacy"],
            "to_project_id": ids["user/repo"],
            "dry_run": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["migrated"] == 1
    assert body["dry_run"] is True

    rows = (await db_session.execute(select(Optimization))).scalars().all()
    assert all(o.project_id == ids["Legacy"] for o in rows)


@pytest.mark.asyncio
async def test_b3_migrate_endpoint_invalid_destination_returns_400(
    app_client: AsyncClient, db_session
):
    ids = await _seed_projects(db_session, labels=("Legacy",))

    resp = await app_client.post(
        "/api/projects/migrate",
        json={
            "from_project_id": ids["Legacy"],
            "to_project_id": "missing-project-id",
        },
    )
    assert resp.status_code == 400
    assert "not a valid project node" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_b3_migrate_endpoint_emits_taxonomy_changed(
    app_client: AsyncClient, db_session
):
    import asyncio

    from app.services.event_bus import event_bus

    # Prior tests that exercise lifespan may have flipped ``_shutting_down``,
    # which makes ``publish()`` silently return.  Reset so this test is
    # order-independent in the full suite.
    event_bus._shutting_down = False  # type: ignore[attr-defined]

    ids = await _seed_projects(db_session)
    await _seed_opt(db_session, project_id=ids["Legacy"])

    queue: asyncio.Queue = asyncio.Queue()
    event_bus._subscribers.add(queue)
    try:
        resp = await app_client.post(
            "/api/projects/migrate",
            json={
                "from_project_id": ids["Legacy"],
                "to_project_id": ids["user/repo"],
            },
        )
        assert resp.status_code == 200

        events: list[str] = []
        while not queue.empty():
            evt = queue.get_nowait()
            events.append(evt.get("event"))
        assert "taxonomy_changed" in events
    finally:
        event_bus._subscribers.discard(queue)


@pytest.mark.asyncio
async def test_b3_migrate_endpoint_validates_body_schema(
    app_client: AsyncClient,
):
    # Missing required fields — FastAPI/Pydantic returns 422.
    resp = await app_client.post("/api/projects/migrate", json={})
    assert resp.status_code == 422
