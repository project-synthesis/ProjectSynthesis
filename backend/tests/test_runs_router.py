"""Tests for /api/runs endpoints — Foundation P3 cat 9, 7 tests.

Uses async patterns (httpx.AsyncClient via ``app_client`` fixture +
AsyncSession via ``db_session``) matching the conftest convention used
elsewhere in this suite.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow

pytestmark = pytest.mark.asyncio


async def test_get_runs_pagination_envelope(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/runs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert {"total", "count", "offset", "items", "has_more", "next_offset"}.issubset(body.keys())


async def test_get_runs_filter_by_mode(app_client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(RunRow(id="r-probe", mode="topic_probe", status="completed", started_at=datetime.utcnow()))
    db_session.add(RunRow(id="r-seed", mode="seed_agent", status="completed", started_at=datetime.utcnow()))
    await db_session.commit()

    resp = await app_client.get("/api/runs?mode=topic_probe")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert "r-probe" in ids and "r-seed" not in ids


async def test_get_runs_filter_by_status(app_client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(RunRow(id="r-running", mode="topic_probe", status="running", started_at=datetime.utcnow()))
    db_session.add(RunRow(id="r-failed", mode="topic_probe", status="failed", started_at=datetime.utcnow()))
    await db_session.commit()

    resp = await app_client.get("/api/runs?status=failed")
    assert resp.status_code == 200
    statuses = {r["status"] for r in resp.json()["items"]}
    assert statuses == {"failed"}


async def test_get_runs_filter_by_project_id(app_client: AsyncClient, db_session: AsyncSession) -> None:
    from app.models import PromptCluster
    proj = PromptCluster(id="proj-x", state="project", label="x")
    db_session.add(proj)
    db_session.add(RunRow(id="r-with-proj", mode="topic_probe", status="completed", started_at=datetime.utcnow(), project_id="proj-x"))
    db_session.add(RunRow(id="r-no-proj", mode="topic_probe", status="completed", started_at=datetime.utcnow()))
    await db_session.commit()

    resp = await app_client.get("/api/runs?project_id=proj-x")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert ids == ["r-with-proj"]


async def test_get_runs_ordered_started_at_desc(app_client: AsyncClient, db_session: AsyncSession) -> None:
    base = datetime.utcnow()
    for i in range(3):
        db_session.add(RunRow(
            id=f"r-{i}", mode="topic_probe", status="completed",
            started_at=base - timedelta(minutes=i),
        ))
    await db_session.commit()

    resp = await app_client.get("/api/runs?limit=3")
    items = resp.json()["items"]
    ids = [r["id"] for r in items]
    assert ids == ["r-0", "r-1", "r-2"]  # newest first


async def test_get_run_by_id_returns_full_detail(app_client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(RunRow(
        id="r-detail", mode="topic_probe", status="completed",
        started_at=datetime.utcnow(),
        topic="testtopic", topic_probe_meta={"scope": "**/*"},
    ))
    await db_session.commit()

    resp = await app_client.get("/api/runs/r-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "r-detail"
    assert body["topic"] == "testtopic"
    assert body["topic_probe_meta"] == {"scope": "**/*"}


async def test_get_run_by_id_404_on_miss(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/runs/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"
